"""Computes every number the paper quotes from this implementation; writes nums.json."""
import json, agg
L=lambda f: [x for x in agg.load('results/%s.jsonl'%f)]
op=L('op'); hold=L('hold'); rem=L('rem'); ret=L('ret'); extra=L('extra'); idle=L('idle'); best=L('best')
allf=dict(op=op,hold=hold,rem=rem,ret=ret,extra=extra,idle=idle,best=best)
N={}
def sel(rows,tag,pol): return [x for x in rows if x['tag']==tag and x['policy']==pol and 'error' not in x]
def mean(rows,key): 
    xs=[x[key] for x in rows if x.get(key) is not None]; return agg.ci(xs)
def pr(rows,tag,a,b,key,rel=True):
    A={x['seed']:x for x in sel(rows,tag,a)}; B={x['seed']:x for x in sel(rows,tag,b)}
    d=[((B[s][key]-A[s][key])/A[s][key]*100 if rel else B[s][key]-A[s][key]) for s in A if s in B]
    return agg.ci(d)
# --- totals
tot=sum(len(v) for v in allf.values()); err=sum(1 for v in allf.values() for x in v if 'error' in x)
steady=[x for k,v in allf.items() for x in v if 'error' not in x and x['tag'] not in ('ret0','ret3','ret7','idle_ret3')]
ns=[abs(x['wa_h1']-x['wa_h2'])/x['wa'] for x in steady]
N['runs_total']=tot; N['runs_err']=err; N['runs_ok']=tot-err; N['runs_steady']=len(steady)
N['nonstat']=sum(1 for v in ns if v>0.05); N['nonstat_max']='%.1f'%(100*max(ns))
N['nonstat_where']=sorted(set((x['tag'],x['policy']) for x in steady if abs(x['wa_h1']-x['wa_h2'])/x['wa']>0.05))
N['runs_ret']=sum(1 for x in ret+best if x['tag'] in ('ret0','ret3','ret7','idle_ret3'))
# --- cross table
for p in ('base','naive','strict','finish'):
    r=sel(op,'op',p)
    for k,f in (('util','%.3f'),('util_app','%.3f'),('util_rec','%.3f'),('mean','%.2f'),('p99','%.2f'),('p999','%.2f'),('wa','%.3f'),('resets','%.0f'),('bk_app','%.4f'),('pure','%.1f'),('collateral','%.2f'),('reset_cv','%.2f'),('ev_write','%.4f'),('misfit','%.0f')):
        N['op_%s_%s'%(p,k)]=f%mean(r,k)[0]
for p in ('strict','finish'):
    for k in ('wa','util','p99','util_app','mean'):
        m,h=pr(op,'op','naive',p,k); N['opd_%s_%s'%(p,k)]='%+.1f\\pm%.1f'%(m,h)
for p in ('strict','finish','cap'):
    for k in ('p99','p999'):
        m,h=pr(hold,'hold','naive',p,k,rel=False); N['hold_%s_%s'%(p,k)]='%+.2f\\pm%.2f'%(m,h)
# --- retirement
for x in ret+best:
    if x.get('ret_units'):
        x['coll_ratio']=x['ret_coll']/x['ret_units']; x['moved']=x['purge_units']/x['ret_units']; x['gib']=x['ret_units']*4096/2**30
        x['purepct']=100.0*x['ret_pure']/x['ret_zones']; x['zgib']=x['ret_zones']*64/1024.0
for tag in ('ret3','ret0','ret7'):
    for p in ('naive','strict','finish','cap','static'):
        r=sel(ret,tag,p)
        if not r: continue
        for k,f in (('gib','%.2f'),('ret_zones','%.0f'),('purepct','%.0f'),('coll_ratio','%.2f'),('moved','%.2f'),('t_purge','%.2f'),('p99_win','%.2f'),('p999_win','%.2f'),('max_win','%.1f'),('mean_win','%.2f'),('zgib','%.2f')):
            N['%s_%s_%s'%(tag,p,k)]=f%mean(r,k)[0]
        N['%s_%s_t_ci'%(tag,p)]='%.2f'%mean(r,'t_purge')[1]; N['%s_%s_moved_ci'%(tag,p)]='%.2f'%mean(r,'moved')[1]
        N['%s_%s_zones1'%(tag,p)]='%.1f'%mean(r,'ret_zones')[0]
for p in ('naive','strict','finish','cap','static'):
    r=sel(ret,'ctl',p)
    for k,f in (('p99_win','%.2f'),('p999_win','%.2f'),('mean_win','%.2f'),('max_win','%.1f')): N['ctl_%s_%s'%(p,k)]=f%mean(r,k)[0]
for p in ('naive','finish'):
    a=mean(sel(ret,'ret3',p),'mean_win')[0]; b=mean(sel(ret,'ctl',p),'mean_win')[0]; N['ret3_%s_mean_rel'%p]='%+.0f'%((a/b-1)*100)
for p in ('naive','cap'):
    r=sel(best,'idle_ret3',p)
    for k,f in (('ret_zones','%.0f'),('moved','%.2f'),('t_purge','%.2f'),('p999_win','%.2f')): N['iret_%s_%s'%(p,k)]=f%mean(r,k)[0]
N['ret3_naive_shortfall']='%.0f'%(100*(1-mean(sel(ret,'ret3','naive'),'moved')[0]/mean(sel(ret,'ret3','naive'),'coll_ratio')[0]))
# --- static / remedies
allr=[dict(x,tag='mor14') for x in op if x['policy']!='base']+rem+extra+idle+best
for tag in ('mor8','mor14','mor28','idle_mor14','idle_mor28','nc3','nc16','nc32','idle_nc3','rot96_mor14','rot96_mor28'):
    nv=sel(allr,tag,'naive')
    if nv: N['st_%s_naive_coll'%tag]='%.2f'%mean(nv,'collateral')[0]; N['st_%s_naive_wa'%tag]='%.3f'%mean(nv,'wa')[0]
    for p in ('strict','finish','cap','static','epfin'):
        r=sel(allr,tag,p)
        if not r: continue
        for k in ('wa','util','p99'):
            m,h=pr(allr,tag,'naive',p,k); N['st_%s_%s_%s'%(tag,p,k)]='%+.1f'%m; N['st_%s_%s_%s_ci'%(tag,p,k)]='%.1f'%h
        for k,f in (('pure','%.1f'),('collateral','%.2f'),('bk_app','%.4f'),('wa','%.3f'),('util','%.3f')): N['st_%s_%s_%s_abs'%(tag,p,k)]=f%mean(r,k)[0]
        N['st_%s_%s_n'%(tag,p)]=len(r)
N['epfin_default_err']=sum(1 for x in rem if x['policy']=='epfin' and 'error' in x); N['epfin_default_n']=sum(1 for x in rem if x['policy']=='epfin')
# --- best
b=[dict(x,tag='best') for x in hold if x['policy']=='naive']+best
for p in ('cap','static'):
    for k in ('wa','util','mean'):
        m,h=pr(b,'best','naive',p,k); N['best_%s_%s'%(p,k)]='%+.1f\\pm%.1f'%(m,h)
    for k in ('p99','p999'):
        m,h=pr(b,'best','naive',p,k,rel=False); N['best_%s_%s'%(p,k)]='%+.2f\\pm%.2f'%(m,h)
    for k,f in (('p99','%.2f'),('wa','%.3f'),('util','%.3f'),('pure','%.1f'),('bk_app','%.4f')): N['best_%s_%s_abs'%(p,k)]=f%mean(sel(best,'best',p),k)[0]
N['best_naive_p99_abs']='%.2f'%mean(sel(hold,'hold','naive'),'p99')[0]
N['sim_lines']=sum(1 for _ in open('sim.py'))
json.dump(N,open('nums.json','w'),indent=1,default=str)
for k in sorted(N): print(k,'=',N[k])
