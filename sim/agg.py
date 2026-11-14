import json, sys, math
from collections import defaultdict
T = {1:12.706,2:4.303,3:3.182,4:2.776,5:2.571,6:2.447,7:2.365}
def ci(xs):
    n=len(xs); m=sum(xs)/n
    if n<2: return m,0.0
    sd=math.sqrt(sum((x-m)**2 for x in xs)/(n-1)); return m, T[n-1]*sd/math.sqrt(n)
def load(path):
    return [json.loads(l) for l in open(path)]
def table(rows, keys, group=lambda r:(r['tag'],r['policy'])):
    g=defaultdict(list)
    for r in rows:
        if 'error' in r: print('ERROR',r['tag'],r['policy'],r['seed'],r['error']); continue
        g[group(r)].append(r)
    for k in sorted(g):
        out=[]
        for key in keys:
            xs=[r[key] for r in g[k] if r.get(key) is not None]
            if not xs: out.append('%s=NA'%key); continue
            m,h=ci(xs); out.append('%s=%.4g±%.2g'%(key,m,h))
        print(k, 'n=%d'%len(g[k]), ' '.join(out))
def paired(rows, a, b, keys, tag=None, rel=True):
    A={r['seed']:r for r in rows if r['policy']==a and (tag is None or r['tag']==tag) and 'error' not in r}
    B={r['seed']:r for r in rows if r['policy']==b and (tag is None or r['tag']==tag) and 'error' not in r}
    out=[]
    for key in keys:
        d=[((B[s][key]-A[s][key])/A[s][key]*100 if rel else B[s][key]-A[s][key]) for s in A if s in B]
        m,h=ci(d); out.append('%s=%+.2f±%.2f'%(key,m,h))
    print('%s->%s'%(a,b), tag or '', ' '.join(out))
if __name__=='__main__':
    rows=load(sys.argv[1])
    keys=sys.argv[2].split(',') if len(sys.argv)>2 else ['util','util_app','util_rec','mean','p99','p999','wa','resets','victim_live','bk_app','ev_write','pure','collateral','reset_cv','misfit']
    table(rows, keys)
