import heapq,itertools,json,subprocess,time,hashlib
from pathlib import Path
from truth_dataset import read,dump,parents_for,verify_route
r=Path.cwd();mp=r/'output/c7e824_research/current_map.json';data=read(mp);out=r/'output/c7e824_research/early_branches';out.mkdir(exist_ok=True);digest=hashlib.sha256(mp.read_bytes()).hexdigest()
queue=[];counter=itertools.count();seen=set();active=[];rows=[];winner=None

def add(prefix):
    key=tuple(sorted(prefix))
    if key in seen:return
    seen.add(key);heapq.heappush(queue,(len(prefix),next(counter),prefix))
for card,parents in parents_for(data).items():
    if not parents:add([card])
start=time.monotonic()
try:
    while time.monotonic()-start<60 and (queue or active) and winner is None:
        while queue and len(active)<6:
            depth,i,prefix=heapq.heappop(queue);folder=out/str(i);folder.mkdir(exist_ok=True);dump(folder/'prefix.json',{'sequence':prefix,'map_sha256':digest});log=(folder/'process.log').open('w')
            cmd=[str(r/'StaticFastSolver.exe'),str(mp),str(folder/'answer.json'),'tight-top','.8',str(min(8,max(.1,60-(time.monotonic()-start)))),'1997','1000000',str(folder/'progress.json'),str(folder/'prefix.json'),'512','6']
            p=subprocess.Popen(cmd,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS);active.append((p,folder,log,prefix))
        for item in active[:]:
            p,folder,log,prefix=item
            if p.poll() is None:continue
            active.remove(item);log.close();info=read(folder/'progress.json') if (folder/'progress.json').exists() else {};rows.append({'folder':str(folder),'fixed':len(prefix),**info})
            if p.returncode==0:
                answer=read(folder/'answer.json');metrics=verify_route(data,answer['sequence']);dump(out/'answer.json',answer);dump(out/'verification.json',metrics);winner=answer;break
            fp=folder/'progress.json.frontier.json'
            if fp.exists():
                f=read(fp);add(f['path'])
                for frame in f['frames']:
                    for card in frame['cards']:add(f['path'][:frame['depth']]+[card])
        dump(out/'status.json',{'state':'complete' if winner else 'searching','completed_jobs':len(rows),'queued':len(queue),'seconds':time.monotonic()-start})
        time.sleep(.05)
finally:
    for p,folder,log,prefix in active:
        if p.poll() is None:p.terminate()
        p.wait();log.close()
dump(out/'results.json',rows);dump(out/'status.json',{'state':'complete' if winner else 'unknown','completed_jobs':len(rows),'queued':len(queue),'seconds':time.monotonic()-start,'proof':False});print(read(out/'status.json'))
