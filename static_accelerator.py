"""Bounded ordinary-board candidate generation and proved conflict reuse.

Partial beam routes are hints ONLY. Only independent complete replay makes a
route executable; only CP-SAT infeasibility cores become persistent nogoods.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import time
import os
from types import SimpleNamespace

from color_rules import initial_cards
from desktop_assistant import validate
from rolling_play import replay_prefix
from constraint_conflicts import ConflictStore
from solve_constraints import solve_board
from solve_verified_map import atomic_json

ROOT=Path(__file__).resolve().parent
PROOF_KIND='independent_dead_boundary_or_cp_sat_core_v1'


def read(path):
    try:return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):return {}


def boundary_cut(data,prefix,route,seconds=1,backtrack=True):
    """An exhausted state excludes all permutations of its picked ideal.

    Its maximal picked elements imply all their ancestors were picked. With
    exactly k click positions before k, these few literals force the entire
    bad set, so one sound cut can exclude k! equivalent click orders.
    """
    from standard_plan import audit_static_boundary
    started=time.monotonic();best_route=list(route)
    audit=audit_static_boundary(data,best_route,seconds=min(seconds,.15) if backtrack else seconds,state_limit=100000)
    if audit['state']=='complete':
        audit['witness']=best_route+audit['sequence'];return [],audit
    if audit['state']!='dead_prefix':return [],audit
    if backtrack:
        step=6
        while time.monotonic()-started<seconds:
            at=max(len(prefix),len(route)-step)
            trial=list(route[:at]);left=seconds-(time.monotonic()-started)
            earlier=audit_static_boundary(data,trial,seconds=min(.5,max(0,left)),state_limit=100000)
            if earlier['state']=='complete':
                earlier['witness']=trial+earlier['sequence'];return [],earlier
            if earlier['state']=='dead_prefix':best_route=trial;audit=earlier
            if at==len(prefix):break
            step*=2
    base=replay_prefix(data,prefix);picked=set(best_route[len(prefix):]);k=len(picked)
    if not k:return [],audit
    nonmax=set().union(*(base.parents[id]&picked for id in picked))
    frontier=sorted(picked-nonmax)
    if not frontier:raise ValueError('Dead boundary has no maximal elements')
    facts=[{'kind':'picked_before','card':id,'step':k} for id in frontier]
    audit.update(removed_in_candidate=k,cut_literals=len(facts))
    return facts,audit


def native_worker_count(workers,memory_budget,cpus=None):
    """Use the requested CPUs when RAM can fund each independent search."""
    return max(1,min(workers,cpus or os.cpu_count() or 1,int(memory_budget//(768*1024**2))))


def prepare(map_path,data,prefix,folder,seconds,workers,cancel,notify):
    folder.mkdir(parents=True,exist_ok=True);start=time.monotonic()
    digest=hashlib.sha256(map_path.read_bytes()).hexdigest()
    scope={'map_sha256':digest,'confirmed_prefix':list(prefix),'candidate_version':2}
    store=ConflictStore(folder/'proved_conflicts.json',digest,prefix,'cumulative',proof_kind=PROOF_KIND)
    cache=read(folder/'hints.json');routes=[];complete=None;owned=[];logs=[];trials=[]
    def accept(route):
        if list(route[:len(prefix)])!=list(prefix):raise ValueError('Candidate changed confirmed clicks')
        board=replay_prefix(data,route)
        if not board.state.remaining and not board.tray:
            cards,rules=initial_cards(data);validate(cards,route,rules);return True
        return False
    if cache.get('scope')==scope:
        for route in cache.get('routes',[]):
            try:accept(route)
            except (ValueError,KeyError,TypeError):continue
            routes.append(route)
    reused=bool(routes);exe=ROOT/'StaticFastSolver.exe';attempt=cache.get('native_attempt',0)
    serial=int(cache.get('native_serial',0)) if cache.get('scope')==scope else 0
    completed=0;launches=[];active=[];failures=0
    if routes and accept(routes[0]):
        return {'state':'solved','method':'static_candidate_cache','elapsed_seconds':time.monotonic()-start},{'sequence':routes[0]},[],store
    from search_resources import ResourceSampler
    sampler=ResourceSampler();self_process=SimpleNamespace(pid=os.getpid(),_handle=-1)
    sampler.previous_process[os.getpid()]=sampler.process_times(self_process)
    try:
        if exe.exists() and seconds>=1:
            pp=folder/'prefix.json';atomic_json(pp,{'sequence':list(prefix),'map_sha256':digest})
            # Complete results have already been replayed above. Partial hints
            # must never suppress a fresh native attempt. Advance seeds on retry.
            from search_resources import available_memory,state_cap
            attempt=int(cache.get('native_attempt',0))+1 if cache.get('scope')==scope else 1
            memory_budget=max(128*1024**2,available_memory()-2*1024**3)
            count=native_worker_count(workers,memory_budget)
            memory_limits={}
            initial=((1997,.8),(7,0),(1,.8),(5,0),(6,.8),(8,0),(3,.8),(4,0))
            deadline=start+seconds
            def launch():
                nonlocal serial
                index=serial;serial+=1
                seed,first=initial[index] if index<len(initial) else (7919+index*104729,(.8,0,.6,.3,1)[index%5])
                # Refill vacated slots within the native deadline. Each restart
                # gets a new seed and a bounded Luby time slice.
                def luby(i):
                    k=1
                    while (1<<k)-1<i:k+=1
                    return 1<<(k-1) if i==(1<<k)-1 else luby(i-(1<<(k-1))+1)
                budget=min(max(.01,deadline-time.monotonic()),10*luby(index//count+1))
                cap=state_cap(memory_budget,count,index%count==0,False)
                out=folder/f'native_{index}_{time.time_ns()}.json';progress=Path(str(out)+'.progress.json')
                log=Path(str(out)+'.log').open('w',encoding='utf-8');logs.append(log)
                process=subprocess.Popen([str(exe),str(map_path),str(out),'tight-top',str(first),str(budget),
                    str(seed),str(cap),str(progress),str(pp),'512'],cwd=ROOT,stdout=log,stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS)
                # Keep the primary search's larger state budget available;
                # a flat per-process cap would kill it before that budget.
                memory_limits[id(process)]=memory_budget*.9*(2 if index%count==0 else 1)/(count+1)
                item=(process,out,progress);owned.append(item);active.append(item)
                event={'index':index,'pid':getattr(process,'pid',None),'seed':seed,'solve_first':first,
                    'state_limit':cap,'slice_seconds':budget,'elapsed_seconds':time.monotonic()-start}
                launches.append(event)
                with (folder/'native_schedule.jsonl').open('a',encoding='utf-8') as stream:stream.write(json.dumps(event)+'\n')
                atomic_json(folder/'hints.json',{'scope':scope,'routes':routes,'native_attempt':attempt,'native_serial':serial})
            # Start all requested slots before cancellation is checked, then
            # always reap them in finally, including failures during startup.
            for _ in range(count):launch()
            while time.monotonic()<deadline:
                if cancel():raise ValueError('已暂停候选搜索')
                winner=False
                for item in active[:]:
                    p,out,progress=item
                    if p.poll() is None:continue
                    active.remove(item);completed+=1
                    if p.poll()==0 and out.exists():
                        try:winner=accept(read(out).get('sequence',[]))
                        except (ValueError,KeyError,TypeError):pass
                        if winner:break
                    if p.poll() not in (0,2,-15):failures+=1
                if winner or failures>=3:break
                while len(active)<count and time.monotonic()<deadline:launch()
                notify(phase='native',workers=len(active),native_completed=completed,native_launches=len(launches),
                    native_restarts=max(0,len(launches)-count),native_active=[e for e in launches if e['pid'] in {getattr(p,'pid',None) for p,_,_ in active}],
                    beam_deepest=max((read(g).get('deepest',len(prefix)) for _,_,g in owned),default=len(prefix)),
                    beam_widths=[],resources=sampler.sample([self_process]+[p for p,_,_ in active]))
                # State estimates do not capture every CLR allocation peak.
                # Memory-stopped workers remain UNKNOWN, never proof sources.
                for p,out,progress in active:
                    if p.poll() is None and sampler.process_memory(p)[1]>memory_limits[id(p)]:
                        p.terminate()
                        atomic_json(Path(str(out)+'.external_stop.json'),{'reason':'memory_limit','state':'unknown'})
                time.sleep(.1)
    finally:
        for p,_,_ in owned:
            if p.poll() is None:p.terminate()
        for p,_,_ in owned:p.wait(timeout=5)
        for log in logs:log.close()
    if cancel():raise ValueError('已暂停候选搜索')
    for p,out,progress in owned:
        trials.append(read(progress))
        for path,is_complete in ((out,True),(Path(str(out)+'.best.json'),False)):
            answer=read(path);route=answer.get('sequence',[])
            if not route:continue
            try:cleared=accept(route)
            except (ValueError,KeyError,TypeError):continue
            if cleared and is_complete:complete=answer
            if len(route)>len(prefix) and route not in routes:routes.append(route)
    routes=sorted(routes,key=len,reverse=True)[:3]
    atomic_json(folder/'hints.json',{'scope':scope,'routes':routes,'trials':trials,'native_attempt':attempt,'native_serial':serial})
    if complete:return {'state':'solved','method':'native_fast_path','winner_mode':complete.get('mode'),
        'elapsed_seconds':time.monotonic()-start},complete,[],store
    # Existing cached legal hints may also already be complete.
    if routes and accept(routes[0]):
        return {'state':'solved','method':'static_candidate_cache','elapsed_seconds':time.monotonic()-start},{'sequence':routes[0]},[],store
    tail=routes[0][len(prefix):] if routes else []
    audits=[];known=store.load()
    if routes:notify(phase='candidate_diagnosis',workers=1,beam_deepest=len(routes[0]))
    for route in routes:
        if cancel():raise ValueError('已暂停候选核查')
        facts,audit=boundary_cut(data,prefix,route,seconds=.5)
        audits.append({k:v for k,v in audit.items() if k not in ('sequence','witness')})
        if audit.get('witness') and accept(audit['witness']):
            full=audit['witness'];atomic_json(folder/'hints.json',{'scope':scope,'routes':[full],'trials':trials})
            atomic_json(folder/'dead_boundary_audits.json',audits)
            return {'state':'solved','method':'exact_tail_repair','elapsed_seconds':time.monotonic()-start},{'sequence':full},[],store
        if audit.get('state')=='dead_prefix' and audit.get('fixed_steps')==len(prefix):
            atomic_json(folder/'dead_boundary_audits.json',audits)
            return {'state':'infeasible','method':'independent_boundary_exhaustion',
                'audit':audit,'proof_scope':'fixed_boundary' if prefix else 'whole_map','elapsed_seconds':time.monotonic()-start},None,[],store
        if facts:known.append(facts)
    if known:store.save(known)
    atomic_json(folder/'dead_boundary_audits.json',audits)
    if tail and not store.load() and time.monotonic()-start<seconds+1:
        notify(phase='candidate_diagnosis',workers=1,beam_deepest=len(prefix)+len(tail))
        info,_=solve_board(data,prefix=prefix,hint=tail,assignments=dict(enumerate(tail)),
            seconds=min(3,max(.05,seconds*.25)),encoding='cumulative',cancel=cancel,
            save_conflicts=store.save)
        atomic_json(folder/'candidate_diagnosis.json',info)
    return {'state':'unknown','method':'beam_hint_and_proved_conflicts','hint_cards':len(tail),
            'hint_cache_reused':reused,'restored_conflicts':len(store.load()),
            'elapsed_seconds':time.monotonic()-start},None,tail,store
