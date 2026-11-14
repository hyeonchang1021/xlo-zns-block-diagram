"""Runs a list of simulations in parallel and appends one JSON line per run to results/<name>.jsonl."""
import sys, json, os, time
from multiprocessing import Pool
import sim

def one(job):
    pol, seed, kw, tag = job
    t0 = time.time()
    try:
        r = sim.run(pol, seed, **kw)
    except Exception as ex:
        r = dict(policy=pol, seed=seed, error=str(ex))
    r['tag'] = tag; r['kw'] = kw; r['wall'] = round(time.time() - t0, 1)
    return r

def run_jobs(name, jobs, procs=2):
    os.makedirs('results', exist_ok=True)
    path = 'results/%s.jsonl' % name
    with Pool(procs) as p, open(path, 'w') as f:
        for r in p.imap_unordered(one, jobs):
            f.write(json.dumps(r) + '\n'); f.flush()
    return path

if __name__ == '__main__':
    which = sys.argv[1]
    S8 = list(range(1, 9)); S5 = list(range(1, 6))
    jobs = []
    if which == 'op':
        for pol in ('base', 'naive', 'strict', 'finish'):
            for s in S8: jobs.append((pol, s, {}, 'op'))
    if which == 'hold':
        for pol in ('naive', 'strict', 'finish', 'cap'):
            for s in S8: jobs.append((pol, s, dict(hold=1), 'hold'))
    if which == 'rem':
        for pol in ('cap', 'epfin', 'static'):
            for s in S8: jobs.append((pol, s, {}, 'mor14'))
        for mor in (8, 28):
            for pol in ('naive', 'strict', 'finish', 'cap', 'epfin', 'static'):
                for s in S5: jobs.append((pol, s, dict(mor=mor), 'mor%d' % mor))
        for nc in (3, 32):
            for pol in ('naive', 'strict', 'finish', 'cap'):
                for s in S5: jobs.append((pol, s, dict(nc=nc), 'nc%d' % nc))
    if which == 'ret':
        for pol in ('naive', 'strict', 'finish', 'cap', 'static'):
            for s in S5:
                jobs.append((pol, s, dict(retire=3), 'ret3'))
                jobs.append((pol, s, dict(win_at=4.0), 'ctl'))
        for c in (0, 7):
            for pol in ('naive', 'finish'):
                for s in S5: jobs.append((pol, s, dict(retire=c), 'ret%d' % c))
    if which == 'extra':
        for mor in (14, 28):
            for pol in ('naive', 'finish', 'cap', 'epfin'):
                for s in S5: jobs.append((pol, s, dict(mor=mor, rot=24576), 'rot96_mor%d' % mor))
        for pol in ('naive', 'strict', 'finish', 'cap'):
            for s in S5: jobs.append((pol, s, dict(nc=16), 'nc16'))
    if which == 'idle':
        for mor in (14, 28):
            for pol in ('naive', 'strict', 'finish', 'cap', 'static'):
                for s in S5: jobs.append((pol, s, dict(mor=mor, idle_free=1), 'idle_mor%d' % mor))
    if which == 'best':
        for pol in ('cap', 'static'):
            for s in S8: jobs.append((pol, s, dict(hold=1, idle_free=1), 'best'))
        for pol in ('naive', 'finish', 'cap'):
            for s in S5: jobs.append((pol, s, dict(nc=3, idle_free=1), 'idle_nc3'))
        for pol in ('naive', 'cap'):
            for s in S5: jobs.append((pol, s, dict(retire=3, idle_free=1), 'idle_ret3'))
    print(run_jobs(which, jobs), len(jobs))
