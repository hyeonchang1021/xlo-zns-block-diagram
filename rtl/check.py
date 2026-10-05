"""Checks an xlo_place simulation log against an independent model.
Placement is re-derived from the paper's rule (Section 6.3) on the slot table the RTL dumped;
state, key epochs, the Bk budget, the free pool and the reclaim gate are tracked separately."""
import sys, json, collections
log, stimf = sys.argv[1], sys.argv[2]
P = dict(NSLOT=14, CAP=16384, LMIN=8, ROT=6144, NZ=256, LO_TH=32, FORCE_TH=5)
for a in sys.argv[3:]:
    k, v = a.split('='); P[k] = int(v)
NS, CAP, LMIN, ROT, NZ = P['NSLOT'], P['CAP'], P['LMIN'], P['ROT'], P['NZ']
ZSE, ZSIO, ZSF = 0, 1, 4
stim = [int(x, 16) for x in open(stimf).read().split()]

def paper_rule(slots, ln, c, e):
    fits = [i for i, s in enumerate(slots) if s['wp'] + ln <= CAP]
    if not fits: return None
    exact = [i for i in fits if slots[i]['bnd'] and (slots[i]['cls'], slots[i]['ep']) == (c, e)]
    unb   = [i for i in fits if not slots[i]['bnd']]
    same  = [i for i in fits if slots[i]['bnd'] and slots[i]['cls'] == c and slots[i]['ep'] != e]
    if exact:  t, s = 0, exact[0]
    elif unb:  t, s = 1, unb[0]
    elif same: t, s = 2, same[0]
    else:      t, s = 3, min(fits, key=lambda i: (slots[i]['wp'], i))
    z = slots[s]
    bk = 1 if (z['bnd'] and (z['cls'], z['ep']) != (c, e)) else 0   # boundary: zone bound to another key domain
    return t, s, bk

slots = [dict(zid=i, st=ZSE, wp=0, bnd=0, cls=0, ep=0) for i in range(NS)]
rotc = [0]*8; kep = [0]*8; acc = 0
boot = NS; fifo = collections.deque(); sealed = set()
st = collections.Counter(); bi = 0; errs = []
def err(cyc, msg):
    if len(errs) < 20: errs.append('cyc %s: %s' % (cyc, msg))
    st['errors'] += 1

cycles = collections.OrderedDict()
for line in open(log):
    f = line.split()
    if not f or f[0] not in 'GDRZ': 
        if f and f[0] in ('END', 'TIMEOUT'): st['end_' + f[0]] = int(f[1])
        continue
    cycles.setdefault(int(f[1]), {})[f[0]] = f

for cyc, ev in cycles.items():
    g = ev['G']; free_l, backlog, sigma, go, safe, acc_l, tick = map(int, g[2:9])
    free = NZ - boot + len(fifo)
    if free != free_l: err(cyc, 'free_cnt %d != model %d' % (free_l, free))
    if acc != acc_l: err(cyc, 'bk_acc %d != model %d' % (acc_l, acc))
    lo = free < NZ / 8; force = free < NZ / 48                       # the paper's thresholds, as fractions
    want_go = force or (lo and not (backlog > 4000 and sigma))
    if bool(go) != bool(want_go): err(cyc, 'reset_go %d, expected %d (free %d)' % (go, want_go, free))
    st['cycles'] += 1; st['reset_go_cycles'] += go
    if lo and not force and backlog > 4000 and sigma: st['deferred_cycles'] += 1
    if force: st['force_cycles'] += 1
    zsf_idx = [i for i, s in enumerate(slots) if s['st'] == ZSF]
    upd = []
    if 'D' in ev:
        d = ev['D']
        c, ln, e, rot, hit, tier, sel, bk, viol, admit, fin, wpn, nofit, idxmax = map(int, d[2:16])
        dump = [dict(zip(('zid', 'st', 'wp', 'bnd', 'cls', 'ep'), map(int, x.split(':')))) for x in d[16:16+NS]]
        for i in range(NS):
            a, b = dump[i], slots[i]
            if (a['zid'], a['st'], a['wp'], a['bnd']) != (b['zid'], b['st'], b['wp'], b['bnd']) or (b['bnd'] and (a['cls'], a['ep']) != (b['cls'], b['ep'])):
                err(cyc, 'slot %d state %s != model %s' % (i, a, b)); slots[i] = dict(a)
        if e != kep[c]: err(cyc, 'epoch %d != model %d' % (e, kep[c]))
        r = paper_rule(slots, ln, c, kep[c])
        if r is None:
            if hit or admit or bk or viol: err(cyc, 'RTL placed a batch that fits nowhere')
            want_nofit = 0 if zsf_idx else 1
            if nofit != want_nofit: err(cyc, 'nofit %d expected %d' % (nofit, want_nofit))
            if want_nofit:
                mx = max(range(NS), key=lambda i: (slots[i]['wp'], -i))
                if idxmax != mx: err(cyc, 'seal target %d expected %d' % (idxmax, mx))
                upd.append(('seal', mx)); st['seal_nofit'] += 1
            st['stall_cycles'] += 1
        else:
            t, s, b = r
            if (hit, admit, tier, sel, bk) != (1, 1, t, s, b):
                err(cyc, 'decision tier %d sel %d bk %d, paper rule gives tier %d sel %d bk %d' % (tier, sel, bk, t, s, b))
            if b and t < 2: err(cyc, 'boundary in a boundary-free tier')
            want_rot = 1 if rotc[c] + ln >= ROT else 0
            if rot != want_rot: err(cyc, 'rot %d expected %d' % (rot, want_rot))
            want_viol = 1 if (b and acc >= safe) else 0
            if viol != want_viol: err(cyc, 'viol %d expected %d' % (viol, want_viol))
            w = slots[s]['wp'] + ln; want_fin = 1 if CAP - w < LMIN else 0
            if (wpn, fin) != (w, want_fin): err(cyc, 'wp_n/fin %d/%d expected %d/%d' % (wpn, fin, w, want_fin))
            if bi >= len(stim) or ((stim[bi] >> 3) & 255, stim[bi] & 7) != (ln, c): err(cyc, 'batch out of order')
            st['rel_batches'] += (stim[bi] >> 11) & 1 if bi < len(stim) else 0
            bi += 1
            upd.append(('app', s, c, kep[c], w, want_fin, ln, want_rot))
            st['batches'] += 1; st['tier%d' % (t + 1)] += 1; st['bk'] += b; st['viol'] += want_viol; st['rot'] += want_rot
            st['fin'] += want_fin; st['units'] += ln
    want_rf = bool(zsf_idx) and free > 0
    if ('R' in ev) != want_rf: err(cyc, 'refill %s expected %s' % ('R' in ev, want_rf))
    if 'R' in ev:
        idx, old, new = map(int, ev['R'][2:5])
        exp_new = boot if boot < NZ else (fifo[0] if fifo else -1)
        if not zsf_idx or idx != zsf_idx[0] or old != slots[idx]['zid'] or new != exp_new:
            err(cyc, 'refill slot %d zid %d->%d, expected slot %s ->%d' % (idx, old, new, zsf_idx[:1], exp_new))
        if any(u[0] == 'app' and u[1] == idx for u in upd): err(cyc, 'append and refill on the same slot')
        upd.append(('rf', idx, old, new)); st['refills'] += 1
    if 'Z' in ev:
        z = int(ev['Z'][2])
        if z not in sealed: err(cyc, 'zone %d returned but was not sealed' % z)
        upd.append(('rz', z)); st['zone_resets'] += 1
    for u in upd:                                                   # clock edge
        if u[0] == 'app':
            _, s, c, e, w, fn, ln, rt = u
            slots[s].update(wp=CAP if fn else w, st=ZSF if fn else ZSIO, bnd=1, cls=c, ep=e)
            rotc[c] = 0 if rt else rotc[c] + ln
            if rt: kep[c] = (kep[c] + 1) & 255
        elif u[0] == 'seal':
            slots[u[1]].update(wp=CAP, st=ZSF)
        elif u[0] == 'rf':
            _, idx, old, new = u
            if boot < NZ: boot += 1
            else: fifo.popleft()
            sealed.add(old); slots[idx] = dict(zid=new, st=ZSE, wp=0, bnd=0, cls=0, ep=0)
        elif u[0] == 'rz':
            sealed.discard(u[1]); fifo.append(u[1])
    d_bk = 1 if ('D' in ev and int(ev['D'][9])) else 0      # field 9 is bk
    acc = 0 if tick else min(255, acc + d_bk)
    zs = [s['zid'] for s in slots]
    if len(set(zs)) != NS or sealed & set(zs) or set(fifo) & set(zs): err(cyc, 'a zone is in two places')
    if any(s['wp'] > CAP for s in slots): err(cyc, 'write pointer past capacity')
    if sum(s['st'] == ZSIO for s in slots) > NS: err(cyc, 'MOR exceeded')
    st['min_free'] = min(st.get('min_free', NZ), free)

if bi != len(stim): err('end', 'only %d of %d batches placed' % (bi, len(stim)))
out = dict(st); out['params'] = P; out['bk_per_batch'] = round(st['bk'] / max(1, st['batches']), 5)
print(json.dumps(out, ensure_ascii=False))
for e_ in errs: print('  ERR', e_)
sys.exit(1 if st['errors'] else 0)
