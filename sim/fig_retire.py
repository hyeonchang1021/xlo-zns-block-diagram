"""Figure: retiring a key domain as a process (results/ret.jsonl, tag ret3)."""
import json, math
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.rcParams['font.family']='DejaVu Sans'; plt.rcParams['font.size']=8.5
import agg
rows=[r for r in agg.load('results/ret.jsonl') if r['tag']=='ret3' and 'error' not in r]
style={'naive':('Secure-Naive','#c0392b','-.','s'),'strict':('XLO-ZNS strict-fit','#7f8c8d','--','o'),'finish':('XLO-ZNS','#1f5fa8','-','^')}
fig,ax=plt.subplots(1,2,figsize=(7.2,2.5),gridspec_kw=dict(width_ratios=[1.25,1]))
a=ax[0]
for pol,(lab,col,ls,mk) in style.items():
    first=True
    for r in sorted((x for x in rows if x['policy']==pol), key=lambda x:x['seed']):
        tr=r['purge_trace']
        t=[1e-3]+[p[0] for p in tr]+[r['t_purge']]; n=[r['ret_zones']]+[p[1] for p in tr]+[0]
        a.step(t,n,where='post',color=col,ls=ls,lw=1.3 if first else 0.7,alpha=1.0 if first else 0.45,label=lab if first else None)
        first=False
a.set_xscale('log'); a.set_xlim(1e-2,12); a.set_ylim(0,256)
a.set_xlabel('time since key destruction (s)'); a.set_ylabel('zones holding the\nretired ciphertext')
a.grid(True,lw=0.4,alpha=0.5); a.legend(fontsize=7.5,loc='center left',bbox_to_anchor=(0.02,0.6))
b=ax[1]
labs=[];vals=[];errs=[];cols=[]
for pol,(lab,col,ls,mk) in style.items():
    xs=[r['purge_units']/r['ret_units'] for r in rows if r['policy']==pol]
    m,h=agg.ci(xs); labs.append(lab.replace(' strict-fit','\nstrict-fit')); vals.append(m); errs.append(h); cols.append(col)
b.bar(range(3),vals,yerr=errs,color=['#d9d9d9','#d9d9d9','#7da7d9'],edgecolor='black',lw=0.6,capsize=3,width=0.6)
for i,v in enumerate(vals): b.text(i,v+errs[i]+0.25,'%.2f'%v,ha='center',fontsize=8)
b.set_xticks(range(3)); b.set_xticklabels(labs,fontsize=8); b.set_ylim(0,10)
b.set_ylabel('bytes relocated\nper byte retired'); b.grid(True,axis='y',lw=0.4,alpha=0.5); b.set_axisbelow(True)
fig.tight_layout(); fig.savefig('fig-retire.png',dpi=300)
print(list(zip(labs,[round(v,3) for v in vals],[round(e,3) for e in errs])))
