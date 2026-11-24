# Разбор экспорта сессии OpenCode: шаги, контекст, промахи кэша, паузы, доли стоимости.
# Запуск: python3 tools/oc_digest.py <session.json>
import json,sys,collections
d=json.load(open(sys.argv[1])); ms=d['messages']
print('INFO',json.dumps({k:d['info'][k] for k in ('model','tokens','outcome','title','location') if k in d['info']},ensure_ascii=False))
tools=collections.Counter(); toolchars=collections.Counter(); last=None
A=[]
for i,m in enumerate(ms):
    t=m['type']
    if t=='user': print(f'#{i} USER:', m.get('text','')[:250].replace('\n',' '))
    elif t!='assistant': print(f'#{i} {t}', json.dumps({k:v for k,v in m.items() if k not in('id','time','text')},ensure_ascii=False)[:220])
    else:
        tk=m.get('tokens') or {}; c=tk.get('cache',{})
        ctx=tk.get('input',0)+c.get('read',0)+c.get('write',0)
        gap=round((m['time']['created']-last)/60000,1) if last else 0
        last=m['time'].get('completed',m['time']['created'])
        if tk: A.append((i,m,gap))
        names=[]
        for p in m.get('content',[]):
            if p['type']=='tool':
                nm=p['name']; tools[nm]+=1
                out=sum(len(x.get('text','')) for x in (p.get('state',{}).get('content') or []) if isinstance(x,dict))
                toolchars[nm]+=out
                names.append(f"{nm}({out}ch) {json.dumps(p['state'].get('input',{}),ensure_ascii=False)[:80]}")
        print(f"#{i} A ctx={ctx} out={tk.get('output')} w={c.get('write')} gap={gap}m model={m['model']['id']} agent={m.get('agent')} fin={m.get('finish')} {('ERR '+str(m.get('error'))[:90]) if m.get('error') else ''}")
        for x in names: print('     ',x)
R=sum(m['tokens']['cache']['read'] for _,m,_ in A); W=sum(m['tokens']['cache']['write'] for _,m,_ in A); O=sum(m['tokens']['output'] for _,m,_ in A)
big=[(i,m['tokens']['cache']['write'],g) for i,m,g in A if m['tokens']['cache']['write']>20000]
print('STEPS',len(A),'read',R,'write',W,'out',O,'w/r %.1f%%'%(100*W/max(R,1)))
print('BIG REWRITES',big,'sum',sum(w for _,w,_ in big),'= %d%% of writes'%(100*sum(w for _,w,_ in big)/max(W,1)))
print('TOOLS',tools); print('TOOLCHARS',toolchars)
t0=ms[0]['time']['created']; t1=max(m['time'].get('completed',m['time']['created']) for m in ms)
busy=sum(m['time']['completed']-m['time']['created'] for _,m,_ in A if m['time'].get('completed'))
print('wall h %.2f busy h %.2f'%((t1-t0)/3.6e6,busy/3.6e6))
for r,w,o in ((0.1,1.25,4),(0.2,1.0,4)):
    tot=R*r+W*w+O*o; print(f'weights r={r} w={w} o={o}: read {R*r/tot:.0%} write {W*w/tot:.0%} out {O*o/tot:.0%}')
