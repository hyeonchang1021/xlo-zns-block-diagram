"""Discrete-event simulator of key-domain-aware zone placement on an encrypted ZNS SSD.

Written from the model of Sections 3-4 of the XLO-ZNS paper. Sizes are in 4 KiB units, time in seconds.
One FIFO device serves appends, relocation writes and resets; only a reset occupies it for a fixed time.

Policies
  base     no encryption, least-full open zone
  naive    encryption, least-full open zone
  strict   four-tier rule, strict fit
  finish   four-tier rule, finish on misfit
  cap      finish on misfit, and a class that already has a zone open keeps to it after a key
           rotation instead of claiming an unbound zone (at most one open zone per class)
  epfin    finish on misfit, and a class's zone is finished when its key epoch is superseded
  static   one open zone per class and no sharing at all (needs MOR >= number of classes)
"""
import heapq, math, random, sys, json
from collections import deque

EXT = 16            # extent size: 64 KiB
LMIN = 8            # smallest batch: 32 KiB
LMAX = 128          # largest batch: 512 KiB
RELMAX = 64         # relocation write: at most 256 KiB

DEFAULT = dict(
    nz=256, cap=16384, mor=14, bw=3.0e9, tau_r=2.4e-3, nc=8, zipf=1.1, rot=6144,
    t0=13e-6, a0=3e-6, cenc=0.34e-6, tsw=8e-6, scv=0.09,
    rate=3010.0, occ=0.72, own=0.85, qspan=12.0,
    epoch=2e-3, lo_div=16, force_div=48, depth=6,
    warm=60.0, meas=24.0, hold=0, reset_behind=1, idle_free=0,
    retire=-1, retire_at=4.0, purge_par=2, retire_max=120.0,
)


class Sim:
    def __init__(self, policy, seed, **kw):
        P = dict(DEFAULT); P.update(kw); self.P = P
        self.policy = policy
        self.enc = policy != 'base'
        self.xlo = policy in ('strict', 'finish', 'cap', 'epfin', 'static')
        self.fom = policy in ('finish', 'cap', 'epfin')
        self.seed = seed
        NZ, NC, MOR = P['nz'], P['nc'], P['mor']
        self.NZ, self.NC, self.MOR, self.CAP = NZ, NC, MOR, P['cap']
        # workload stream: shared by every policy run with the same seed
        self.wr = random.Random(seed * 1000003 + 17)
        # everything state-dependent (which extent dies)
        self.sr = random.Random(seed * 7919 + 101)
        w = [1.0 / (k + 1) ** P['zipf'] for k in range(NC)]
        s = sum(w); self.p = [x / s for x in w]
        self.cum = []
        a = 0.0
        for x in self.p: a += x; self.cum.append(a)
        self.q = [P['qspan'] ** (-g / (NC - 1.0)) if NC > 1 else 1.0 for g in range(NC)]
        sg2 = math.log(1.0 + P['scv']); self.ln_mu, self.ln_sg = -sg2 / 2.0, math.sqrt(sg2)
        # zones
        self.z_state = [0] * NZ        # 0 free, 1 open, 2 full, 3 under reclaim
        self.z_wp = [0] * NZ
        self.z_live = [0] * NZ
        self.z_bc = [-1] * NZ          # bound class (-1: unbound)
        self.z_be = [0] * NZ           # bound key epoch
        self.z_cl = [[0] * NC for _ in range(NZ)]     # live units per class
        self.z_has = [0] * NZ          # bitmask: classes written since the last reset
        self.z_ext = [[] for _ in range(NZ)]
        self.free = deque(range(MOR, NZ))
        self.slots = list(range(MOR))
        for z in self.slots: self.z_state[z] = 1
        self.under = 0
        # extents
        self.e_zone = []; self.e_cls = []; self.e_size = []
        self.gl = [[] for _ in range(NC)]
        self.L = [0] * NC
        # keys
        self.rotc = [0] * NC; self.kep = [0] * NC
        # device
        self.dev_free = 0.0
        self.heap = []; self.hseq = 0
        self.victims = deque(); self.outst = 0; self.reset_busy_until = 0.0
        self.resets_inflight = 0
        # purge (tenant retirement)
        self.purge = set(); self.ret_cls = -1; self.t_ret = None; self.t_purged = None
        self.fill_target = None
        self.M = None
        self.stalls = 0; self.fallbacks = 0

    # ---------------- helpers ----------------
    def push(self, t, kind, arg=None):
        self.hseq += 1
        heapq.heappush(self.heap, (t, self.hseq, kind, arg))

    def svc(self, ln, nbk, noise):
        P = self.P
        t = P['t0'] + ln * 4096.0 / P['bw']
        if self.enc: t += P['a0'] + P['cenc'] * ln + P['tsw'] * nbk
        return t * noise

    def noise(self, r):
        return math.exp(r.gauss(self.ln_mu, self.ln_sg))

    def replenish(self):
        sl = self.slots
        for i in range(self.MOR):
            if sl[i] < 0 and self.free:
                z = self.free.popleft()
                self.z_state[z] = 1; self.z_wp[z] = 0; self.z_bc[z] = -1
                sl[i] = z

    def finish(self, i):
        z = self.slots[i]
        self.z_state[z] = 2
        self.slots[i] = -1
        if z in self.purge: pass
        self.replenish()

    # ---------------- placement ----------------
    def place(self, ln, c):
        """Returns (zone, boundary). Finishes and replenishes zones as the policy requires."""
        sl, wp, CAP = self.slots, self.z_wp, self.CAP
        zbc, zbe = self.z_bc, self.z_be
        e = self.kep[c]
        tries = 0
        while True:
            tries += 1
            if tries > 64:
                self.stalls += 1
                raise RuntimeError('placement stalled: no open zone can take the batch and no free zone')
            if not self.xlo:
                best = -1; bw = 1 << 60; full = -1; fw = -1
                for i in range(self.MOR):
                    z = sl[i]
                    if z < 0: continue
                    w = wp[z]
                    if w + ln <= CAP and w < bw: bw = w; best = i
                    if w > fw: fw = w; full = i
                if best >= 0:
                    z = sl[best]
                    return z, (1 if (zbc[z] >= 0 and (zbc[z] != c or zbe[z] != e)) else 0)
                if full < 0: self.replenish(); continue
                self.finish(full); continue
            if self.policy == 'static':
                mine = -1; unb = -1
                for i in range(self.MOR):
                    z = sl[i]
                    if z < 0: continue
                    if zbc[z] == c: mine = i
                    elif zbc[z] < 0 and unb < 0: unb = i
                if mine >= 0:
                    z = sl[mine]
                    if wp[z] + ln <= CAP: return z, (1 if zbe[z] != e else 0)
                    self.finish(mine); continue
                if unb >= 0: return sl[unb], 0
                self.replenish()
                if all(x >= 0 and zbc[x] >= 0 for x in sl):
                    raise RuntimeError('static allocation needs an open zone per class')
                continue
            i1 = i2 = i3 = i4 = -1; bw = 1 << 60; full = -1; fw = -1
            exmis = []; own_any = -1; ow = -1
            for i in range(self.MOR):
                z = sl[i]
                if z < 0: continue
                w = wp[z]; fit = (w + ln <= CAP); bc = zbc[z]
                if w > fw: fw = w; full = i
                if bc == c:
                    if w > ow: ow = w; own_any = i
                    if zbe[z] == e:
                        if fit:
                            if i1 < 0: i1 = i
                        else: exmis.append(i)
                    elif fit and i3 < 0: i3 = i
                elif bc < 0:
                    if fit and i2 < 0: i2 = i
                if fit and w < bw: bw = w; i4 = i
            if i1 >= 0: return sl[i1], 0
            if self.fom and exmis:
                nfree = len(self.free)
                for i in exmis: self.finish(i)
                self.M_misfit += len(exmis)
                if nfree > 0 or i2 >= 0: continue        # a fresh zone is (or will be) there: claim it
                self.fallbacks += 1                       # no free zone: fall through this once
            if self.policy == 'cap' and i3 >= 0:
                return sl[i3], 1
            if i2 >= 0: return sl[i2], 0
            if i3 >= 0: return sl[i3], 1
            if self.fom and own_any >= 0 and not exmis:
                if self.free:
                    self.finish(own_any); self.M_misfit += 1; continue
                self.fallbacks += 1
            if i4 >= 0:
                z = sl[i4]
                return z, (1 if zbc[z] >= 0 else 0)
            if full < 0: self.replenish(); continue
            self.finish(full)

    def write(self, ln, c, exts=None):
        """Places a write of class c; returns (boundary, zone). exts: extents being moved (relocation)."""
        z, bk = self.place(ln, c)
        e = self.kep[c]
        self.z_bc[z] = c; self.z_be[z] = e
        self.z_has[z] |= (1 << c)
        self.z_wp[z] += ln
        self.z_live[z] += ln; self.z_cl[z][c] += ln
        ze = self.z_ext[z]
        if exts is None:
            ez, ec, es, gl = self.e_zone, self.e_cls, self.e_size, self.gl[c]
            r = ln
            while r > 0:
                s = EXT if r >= EXT else r
                eid = len(ez); ez.append(z); ec.append(c); es.append(s)
                gl.append(eid); ze.append(eid); r -= s
            self.L[c] += ln
        else:
            ez = self.e_zone
            for eid in exts: ez[eid] = z
            ze.extend(exts)
        if self.CAP - self.z_wp[z] < LMIN:
            self.finish(self.slots.index(z))
        # key rotation counts every unit written under the class
        rc = self.rotc[c] + ln
        if rc >= self.P['rot']:
            self.rotc[c] = 0; self.kep[c] += 1
            if self.policy == 'epfin':
                for i in range(self.MOR):
                    x = self.slots[i]
                    if x >= 0 and self.z_bc[x] == c and self.z_wp[x] > 0: self.finish(i)
        else:
            self.rotc[c] = rc
        return bk, z

    # ---------------- data death ----------------
    def kill(self, g, vol):
        gl = self.gl[g]; es, ez = self.e_size, self.e_zone
        zl, zc = self.z_live, self.z_cl
        rnd = self.sr.random
        done = 0
        while vol > 0 and gl:
            n = len(gl)
            i = int(rnd() * n); eid = gl[i]; s = es[eid]
            if s < EXT and rnd() * EXT >= s: continue
            z = ez[eid]
            if s <= vol:
                last = gl.pop()
                if i < n - 1: gl[i] = last
                es[eid] = 0; ez[eid] = -1
                d = s
            else:
                es[eid] = s - vol; d = vol
            zl[z] -= d; zc[z][g] -= d; vol -= d; done += d
        self.L[g] -= done
        return done

    def death(self, c, ln):
        if self.fill_target is not None and c == self.ret_cls:
            if self.L[c] < self.fill_target: return        # a new tenant filling up: nothing of its own to overwrite
            self.fill_target = None
        r = self.sr.random()
        if r < self.P['own']:
            g = c
        else:
            wts = [self.q[k] * self.L[k] for k in range(self.NC)]
            x = self.sr.random() * sum(wts); g = self.NC - 1
            for k in range(self.NC):
                x -= wts[k]
                if x <= 0: g = k; break
        d = self.kill(g, ln)
        if d < ln:
            for k in sorted(range(self.NC), key=lambda k: -self.L[k]):
                d += self.kill(k, ln - d)
                if d >= ln: break

    # ---------------- reclaim ----------------
    def start_reclaim(self, z):
        self.z_state[z] = 3; self.under += 1
        ez, es, ec = self.e_zone, self.e_size, self.e_cls
        surv = [e for e in self.z_ext[z] if ez[e] == z and es[e] > 0]
        surv.sort(key=lambda e: ec[e])
        v = dict(z=z, surv=surv, ptr=0, out=0, purge=(z in self.purge))
        m = self.M
        if m is not None:
            m['victims'] += 1; m['victim_live'] += self.z_live[z] / float(self.CAP)
        self.victims.append(v)
        self.pump_now = True

    def pick_victim(self, only_purge=False):
        best = -1; bl = 1 << 60
        st, lv = self.z_state, self.z_live
        if only_purge:
            for z in self.purge:
                if st[z] == 2 and lv[z] < bl: bl = lv[z]; best = z
            return best
        for z in range(self.NZ):
            if st[z] == 2 and lv[z] < bl: bl = lv[z]; best = z
        return best

    def issue_reset(self, v, now):
        start = now if now > self.dev_free else self.dev_free
        done = start + self.P['tau_r']
        self.dev_free = done
        self.resets_inflight += 1
        self.push(done, 'reset', v['z'])
        m = self.M
        if m is not None:
            m['resets'] += 1; m['busy_reset'] += self.P['tau_r']; m['reset_times'].append(start)

    def pump(self, now):
        """Issues relocation writes closed-loop, at most `depth` outstanding."""
        P = self.P; ez, es, ec = self.e_zone, self.e_size, self.e_cls
        hold = P['hold']
        for v in list(self.victims):
            if self.outst >= P['depth']: break
            if hold and self.resets_inflight > 0 and v['out'] == 0 and v['ptr'] == 0 and len(v['surv']) > 0:
                break                                  # reset-aware hold: do not start a victim behind a reset
            z = v['z']; surv = v['surv']
            while self.outst < P['depth'] and v['ptr'] < len(surv):
                grp = []; ln = 0; c = -1
                p = v['ptr']
                while p < len(surv):
                    e = surv[p]
                    if ez[e] != z or es[e] <= 0: p += 1; continue
                    if c < 0: c = ec[e]
                    if ec[e] != c or ln + es[e] > RELMAX: break
                    grp.append(e); ln += es[e]; p += 1
                v['ptr'] = p
                if not grp: break
                self.z_live[z] -= ln; self.z_cl[z][c] -= ln
                bk, _ = self.write(ln, c, grp)
                t = self.svc(ln, bk, self.noise(self.sr))
                start = now if now > self.dev_free else self.dev_free
                self.dev_free = start + t
                self.outst += 1; v['out'] += 1
                self.push(self.dev_free, 'rel', v)
                m = self.M
                if m is not None:
                    m['rel_units'] += ln; m['rel_writes'] += 1; m['busy_rel'] += t; m['bk_rel'] += bk
                    if v['purge']: m['purge_units'] += ln
            if v['ptr'] >= len(surv) and not v.get('rst') and (v['out'] == 0 or P['reset_behind']):
                v['rst'] = True                        # the Reset is queued behind the victim's last relocation write
                self.issue_reset(v, now)
        while self.victims and self.victims[0].get('rst'):
            self.victims.popleft()

    def tick(self, now):
        P = self.P
        cnt = len(self.free) + self.under
        if P['idle_free']:                             # an open slot that has not been written is as good as a free zone
            wp = self.z_wp
            cnt += sum(1 for z in self.slots if z >= 0 and wp[z] == 0)
        started = 0
        if cnt < self.NZ / float(P['lo_div']):
            z = self.pick_victim()
            if z >= 0: self.start_reclaim(z); cnt += 1; started += 1
        while cnt <= self.NZ / float(P['force_div']):
            z = self.pick_victim()
            if z < 0: break
            self.start_reclaim(z); cnt += 1; started += 1
        if self.purge:
            # finish open zones that hold retired ciphertext, one per epoch, when a free zone can replace them
            if len(self.free) > 2:
                for i in range(self.MOR):
                    z = self.slots[i]
                    if z >= 0 and z in self.purge:
                        self.finish(i); break
            if self.under < P['purge_par']:
                z = self.pick_victim(only_purge=True)
                if z >= 0: self.start_reclaim(z)
        self.pump(now)

    def retire(self, c, now):
        """Destroys class c's key: its live data is dead at once; every zone holding its ciphertext is to be reset."""
        gl = self.gl[c]; es, ez = self.e_size, self.e_zone
        vol = 0
        for eid in gl:
            s = es[eid]; z = ez[eid]
            self.z_live[z] -= s; self.z_cl[z][c] -= s; vol += s
            es[eid] = 0; ez[eid] = -1
        self.gl[c] = []
        self.fill_target = self.L[c]; self.L[c] = 0
        self.ret_cls = c; self.t_ret = now
        bit = 1 << c
        self.purge = set(z for z in range(self.NZ) if (self.z_has[z] & bit) and self.z_state[z] != 0)
        for v in self.victims: v['purge'] = v['z'] in self.purge
        self.kep[c] += 1000; self.rotc[c] = 0           # the replacement tenant writes under a new key
        m = self.M
        m['ret_units'] = vol; m['ret_zones'] = len(self.purge)
        m['ret_coll'] = sum(self.z_live[z] for z in self.purge)   # other tenants' live data in those zones
        m['ret_pure'] = sum(1 for z in self.purge if self.z_live[z] == 0)

    # ---------------- run ----------------
    def prefill(self):
        """Fills the device to the target occupancy with the stationary class composition."""
        P = self.P
        tot = int(P['occ'] * self.NZ * self.CAP)
        wts = [self.p[g] / self.q[g] for g in range(self.NC)]
        s = sum(wts)
        target = [int(tot * w / s) for w in wts]
        left = list(target)
        r = random.Random(self.seed * 31 + 7)
        self.M_misfit = 0
        while sum(left) > 0:
            x = r.random() * sum(left); c = 0
            for k in range(self.NC):
                x -= left[k]
                if x <= 0: c = k; break
            ln = min(left[c], int(round(math.exp(r.uniform(math.log(LMIN), math.log(LMAX))))))
            if ln < LMIN and left[c] >= LMIN: ln = LMIN
            self.write(ln, c)
            left[c] -= ln
        self.target = target

    def run(self):
        P = self.P
        self.prefill()
        wr = self.wr
        T0, T1 = P['warm'], P['warm'] + P['meas']
        t_ret = T0 + P['retire_at'] if P['retire'] >= 0 else None
        Tend = T1
        now = 0.0
        nxt = wr.expovariate(P['rate'])
        self.push(P['epoch'], 'tick')
        lat = []; lat_t = []
        half = (T0 + T1) / 2.0
        m = None
        cum = self.cum; NC = self.NC
        heap = self.heap
        llo, lhi = math.log(LMIN), math.log(LMAX)
        self.M_misfit = 0
        while True:
            if heap and heap[0][0] <= nxt:
                t, _, kind, arg = heapq.heappop(heap)
                now = t
                if now >= Tend and (self.t_ret is None or self.t_purged is not None or now >= T0 + P['retire_max']): break
                if kind == 'tick':
                    if t_ret is not None and self.t_ret is None and now >= t_ret and m is not None:
                        self.retire(P['retire'], now)
                    self.tick(now)
                    self.push(now + P['epoch'], 'tick')
                elif kind == 'rel':
                    self.outst -= 1; arg['out'] -= 1
                    self.pump(now)
                elif kind == 'reset':
                    z = arg
                    self.resets_inflight -= 1
                    self.z_state[z] = 0; self.z_wp[z] = 0; self.z_live[z] = 0; self.z_bc[z] = -1
                    self.z_has[z] = 0; self.z_ext[z] = []
                    cl = self.z_cl[z]
                    for k in range(NC): cl[k] = 0
                    self.under -= 1
                    self.free.append(z)
                    self.replenish()
                    if z in self.purge:
                        self.purge.discard(z)
                        if m is not None:
                            m['purge_resets'] += 1
                            m['purge_trace'].append((round(now - self.t_ret, 4), len(self.purge)))
                        if not self.purge and self.t_purged is None:
                            self.t_purged = now
                    self.pump(now)
                continue
            now = nxt
            if now >= Tend and (self.t_ret is None or self.t_purged is not None or now >= T0 + P['retire_max']): break
            if m is None and now >= T0:
                m = self.M = dict(app_units=0, app_n=0, rel_units=0, rel_writes=0, busy_app=0.0, busy_rel=0.0,
                                  busy_reset=0.0, resets=0, victims=0, victim_live=0.0, bk_app=0, bk_rel=0,
                                  reset_times=[], h1=[0, 0], h2=[0, 0], purge_units=0, purge_resets=0, misfit0=self.M_misfit, purge_trace=[])
            # arrival
            ln = int(round(math.exp(wr.uniform(llo, lhi))))
            x = wr.random(); c = NC - 1
            for k in range(NC):
                if x < cum[k]: c = k; break
            nz = self.noise(wr)
            nxt = now + wr.expovariate(P['rate'])
            self.death(c, ln)
            bk, _ = self.write(ln, c)
            t = self.svc(ln, bk, nz)
            start = now if now > self.dev_free else self.dev_free
            self.dev_free = start + t
            if m is not None and now < T1:
                m['app_units'] += ln; m['app_n'] += 1; m['busy_app'] += t; m['bk_app'] += bk
                lat.append(self.dev_free - now); lat_t.append(now)
                if now >= half and m['h1'][0] == 0:
                    m['h1'] = [m['app_units'], m['rel_units']]
            elif m is not None:
                lat.append(self.dev_free - now); lat_t.append(now)
        return self.report(lat, lat_t, T0, T1, now)

    def report(self, lat, lat_t, T0, T1, now):
        m = self.M; P = self.P; T = P['meas']
        n1 = sum(1 for t in lat_t if t < T1)
        l1 = sorted(lat[:n1])
        def pct(a, p): return a[min(len(a) - 1, int(p * len(a)))] if a else float('nan')
        # rel_units / busy are accumulated to the end of the run; for runs that stop at T1 this is the measured interval
        wa = (m['app_units'] + m['rel_units']) / float(m['app_units'])
        a1, r1 = m['h1']; a2, r2 = m['app_units'] - a1, m['rel_units'] - r1
        wa1 = (a1 + r1) / float(a1) if a1 else float('nan'); wa2 = (a2 + r2) / float(a2) if a2 else float('nan')
        rt = m['reset_times']
        gaps = [rt[i + 1] - rt[i] for i in range(len(rt) - 1)]
        cv = float('nan')
        if len(gaps) > 2:
            mu = sum(gaps) / len(gaps); cv = math.sqrt(sum((g - mu) ** 2 for g in gaps) / len(gaps)) / mu
        # purity snapshot
        nonempty = [z for z in range(self.NZ) if self.z_live[z] > 0]
        pure = sum(1 for z in nonempty if sum(1 for x in self.z_cl[z] if x > 0) == 1)
        coll = []
        zshare = []
        for c in range(self.NC):
            own = sum(self.z_cl[z][c] for z in nonempty)
            if own <= 0: continue
            zs = [z for z in nonempty if self.z_cl[z][c] > 0]
            coll.append(sum(self.z_live[z] - self.z_cl[z][c] for z in zs) / float(own))
            zshare.append(len(zs) / float(len(nonempty)))
        out = dict(policy=self.policy, seed=self.seed,
                   util=(m['busy_app'] + m['busy_rel'] + m['busy_reset']) / T,
                   util_app=m['busy_app'] / T, util_rec=(m['busy_rel'] + m['busy_reset']) / T,
                   mean=sum(l1) / len(l1) * 1e3, p50=pct(l1, .5) * 1e3, p99=pct(l1, .99) * 1e3, p999=pct(l1, .999) * 1e3,
                   wa=wa, wa_h1=wa1, wa_h2=wa2, resets=m['resets'],
                   victim_live=m['victim_live'] / max(1, m['victims']),
                   bk_app=m['bk_app'] / float(m['app_n']),
                   ev_write=(m['bk_app'] + m['bk_rel']) / float(m['app_n'] + m['rel_writes']),
                   rel_mean_kib=4.0 * m['rel_units'] / max(1, m['rel_writes']),
                   reset_cv=cv, pure=100.0 * pure / max(1, len(nonempty)),
                   collateral=sum(coll) / max(1, len(coll)), zone_share=sum(zshare) / max(1, len(zshare)),
                   misfit=self.M_misfit - m['misfit0'], fallbacks=self.fallbacks, stalls=self.stalls,
                   live=sum(self.z_live) / float(self.NZ * self.CAP), free=len(self.free), n=len(l1))
        if self.t_ret is not None:
            W = 10.0
            lw = sorted(l for l, t in zip(lat, lat_t) if self.t_ret <= t < self.t_ret + W)
            out.update(ret_units=m['ret_units'], ret_zones=m['ret_zones'], ret_coll=m['ret_coll'], ret_pure=m['ret_pure'],
                       purge_units=m['purge_units'], purge_resets=m['purge_resets'],
                       t_purge=(self.t_purged - self.t_ret) if self.t_purged is not None else None,
                       p99_win=pct(lw, .99) * 1e3, mean_win=(sum(lw) / len(lw) * 1e3) if lw else None,
                       p999_win=pct(lw, .999) * 1e3, max_win=lw[-1] * 1e3 if lw else None,
                       purge_left=len(self.purge), purge_trace=m['purge_trace'][::max(1, len(m['purge_trace']) // 60)])
        elif P.get('win_at') is not None:
            a = T0 + P['win_at']
            lw = sorted(l for l, t in zip(lat, lat_t) if a <= t < a + 10.0)
            out.update(p99_win=pct(lw, .99) * 1e3, mean_win=(sum(lw) / len(lw) * 1e3) if lw else None,
                       p999_win=pct(lw, .999) * 1e3, max_win=lw[-1] * 1e3 if lw else None)
        return out


def run(policy, seed, **kw):
    return Sim(policy, seed, **kw).run()


if __name__ == '__main__':
    pol = sys.argv[1]; seed = int(sys.argv[2])
    kw = {}
    for a in sys.argv[3:]:
        k, v = a.split('=')
        kw[k] = float(v) if ('.' in v or 'e' in v) else int(v)
    import time
    t0 = time.time()
    r = run(pol, seed, **kw)
    r['wall'] = round(time.time() - t0, 1)
    print(json.dumps(r))
