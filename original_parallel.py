"""Partition upstream normal DFS across processes; no alternative policy."""
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from solve_verified_map import atomic_json
from search_resources import ResourceSampler,available_memory,GIB

ROOT=Path(__file__).resolve().parent


def prioritize_unsearched(folder,snapshot_hash,branches):
    """Resume the coverage queue, binding it to the exact frozen snapshot."""
    from original_snapshot import read
    previous=sorted(folder.parent.glob('run_*/partition_manifest.json'),reverse=True)
    for path in previous:
        if path.parent==folder:continue
        manifest=read(path)
        if manifest.get('snapshot_sha256')!=snapshot_hash:continue
        old=manifest.get('branches',[])
        if {tuple(p) for p in manifest.get('all_branches',old)}!={tuple(p) for p in branches}:continue
        untouched=[];interrupted=[];exhausted=list(manifest.get('resume',{}).get('exhausted_branches',[]))
        for i,branch in enumerate(old):
            progress=path.parent/f'progress_{i:04}.json'
            if not progress.exists():untouched.append(branch)
            elif read(progress).get('state')=='exhausted':exhausted.append(branch)
            else:interrupted.append(branch)
        return untouched+interrupted,{'source_run':path.parent.name,
            'unsearched_first':len(untouched),'interrupted_later':len(interrupted),
            'exhausted_skipped':len(exhausted),'exhausted_branches':exhausted,
            'generation':manifest.get('resume',{}).get('generation',0)+1}
    return branches,{}


def partitions(snapshot,target):
    from original_snapshot import bootstrap_solver
    queue=deque([[]]);finished=[]
    while queue and len(queue)<target:
        branch=queue.popleft();solver=bootstrap_solver(snapshot,branch,seconds=1)
        pool=solver._game_pool_controller
        if pool.check_game_over():finished.append(branch);continue
        heads=pool.generate_head_list();heads=pool.ensure_head_list_alive(heads)
        progress=len(solver._card_sequence.get_pick_index_list())/solver._card_count
        heads=pool.ensure_head_list_disappear(heads,progress)
        cards=pool.get_all_card_dict()
        queue.extend(branch+[cards[i].get_card_id()] for i in heads)
    return finished+list(queue)


def search(snapshot,folder,seconds,cancel,report,performance='max',max_workers=None):
    from original_snapshot import read,ENGINE
    folder.mkdir(parents=True,exist_ok=True);mp=folder/'snapshot.json';atomic_json(mp,snapshot)
    cpus=os.cpu_count() or 1;free=available_memory()
    ceiling=min(cpus,6) if performance=='fast' else cpus
    workers=max(1,min(ceiling,max_workers or cpus,max(1,int(free*.8/(512*1024**2)))))
    all_branches=partitions(snapshot,max(2,workers*3))
    snapshot_hash=hashlib.sha256(mp.read_bytes()).hexdigest()
    task_list,resume=prioritize_unsearched(folder,snapshot_hash,all_branches)
    if not task_list:raise ValueError('原版未找到可继续的分支；未证明整张地图无解')
    workers=min(workers,len(task_list));tasks=deque((i,p,0) for i,p in enumerate(task_list));retries=deque();active={};owned=[]
    policies=[.8,0,.3,.6,1]
    modes=['normal','level-top','index-descending','index-ascending','level-bottom','random']
    atomic_json(folder/'partition_manifest.json',{'engine':ENGINE,'mode':'normal','solve_first_options':policies,
        'snapshot_sha256':snapshot_hash,'workers':workers,
        'branches':task_list,'all_branches':all_branches,'mode_options':modes,
        'partition':'disjoint_prefix_subtrees','resume':resume})
    start=time.monotonic();sampler=ResourceSampler();resources={};last_sample=0
    finished_nodes=0;deepest=0;completed=0;limited=0;failures=[];serial=0;covered=set()
    # Execute the real interpreter so CPU/memory samples refer to workers,
    # rather than the Windows venv redirector waiting for its interpreter.
    python=getattr(sys,'_base_executable',sys.executable)
    env={**os.environ,'PYTHONIOENCODING':'utf-8','PYTHONPATH':os.pathsep.join([
        str(ROOT),str(ROOT/'venv/Lib/site-packages'),os.environ.get('PYTHONPATH','')])}
    memory_mb=max(256,int(free*.75/max(1,workers)/1024**2))
    # Every queued subtree receives CPU in this round. A difficult first wave
    # may not monopolize the whole global budget and starve the rest.
    slice_seconds=max(.25,seconds*workers/(len(task_list)+workers))
    def launch(index,branch,attempt=0):
        nonlocal serial
        serial+=1;tag=f'{index:04}'+(f'_retry_{attempt}' if attempt else '')
        prefix=folder/f'branch_{tag}.json';atomic_json(prefix,{'sequence':branch})
        candidate=folder/f'candidate_{tag}.json';progress=folder/f'progress_{tag}.json'
        log=(folder/f'worker_{tag}.log').open('w',encoding='utf-8')
        remaining=min(slice_seconds*(2**min(3,attempt)),max(.01,seconds-(time.monotonic()-start)))
        policy=index+7*(attempt+resume.get('generation',0))
        cmd=[str(python),str(ROOT/'original_snapshot.py'),'worker','--map',str(mp),
            '--output',str(candidate),'--progress',str(progress),'--seconds',str(remaining),
            '--branch',str(prefix),'--memory-mb',str(memory_mb),'--solve-first',str(policies[policy%len(policies)]),
            '--mode',modes[policy%len(modes)],'--seed',str(7919+policy*17)]
        child=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=log,env=env,creationflags=subprocess.CREATE_NO_WINDOW)
        sampler.kernel.SetPriorityClass(int(child._handle),0x20 if performance=='max' else 0x4000);owned.append((child,log))
        active[child.pid]={'process':child,'log':log,'candidate':candidate,'progress':progress,
            'index':index,'branch':branch,'attempt':attempt}
    try:
        while tasks or active or retries:
            if cancel and cancel():raise ValueError('已暂停原版多核求解')
            if time.monotonic()-start>=seconds:break
            while (tasks or retries) and len(active)<workers:
                launch(*(tasks.popleft() if tasks else retries.popleft()))
            for pid,w in list(active.items()):
                child=w['process']
                if child.poll() is None:continue
                w['log'].close();p=read(w['progress']);finished_nodes+=p.get('nodes',0)
                deepest=max(deepest,p.get('deepest',0));completed+=1;covered.add(w['index'])
                if child.returncode==0:
                    answer=read(w['candidate'])
                    if answer.get('engine')!=ENGINE:raise ValueError('原版并行候选标识不一致')
                    answer.update(parallel_workers=workers,winning_branch=w['index'],parallel_elapsed_seconds=time.monotonic()-start)
                    atomic_json(folder/'winner.json',answer)
                    report('searching','原版并行搜索找到完整候选，正在核对',workers=0,target_workers=workers,
                        nodes=finished_nodes,deepest=len(answer['sequence']),resources=resources)
                    return answer
                if child.returncode!=2:failures.append(w['index'])
                elif p.get('state')!='exhausted':
                    limited+=1;retries.append((w['index'],w['branch'],w['attempt']+1))
                del active[pid]
            now=time.monotonic()
            if now-last_sample>=1 or not resources:
                resources=sampler.sample([p for p,_ in owned]);last_sample=now
            stats=[read(w['progress']) for w in active.values()]
            nodes=finished_nodes+sum(p.get('nodes',0) for p in stats)
            deepest=max([deepest]+[p.get('deepest',0) for p in stats])
            report('searching','开源原版多进程分支搜索，各路使用项目自带的选牌方式',
                workers=len(active),target_workers=workers,nodes=nodes,
                nodes_per_second=round(nodes/max(.01,now-start)),deepest=deepest,
                completed_branches=len(covered),completed_attempts=completed,pending_branches=len(tasks),limited_branches=limited,
                failed_branches=failures,resources=resources,resume=resume,branch_seconds=round(slice_seconds,2),
                solve_first_options=policies,mode_options=modes)
            time.sleep(.2)
        unfinished=[read(w['progress']) for w in active.values()]
        total_nodes=finished_nodes+sum(p.get('nodes',0) for p in unfinished)
        deepest=max([deepest]+[p.get('deepest',0) for p in unfinished])
        result={'state':'limited','completed_branches':len(covered),'completed_attempts':completed,
            'limited_branches':limited,'failed_branches':failures,'nodes':total_nodes,
            'elapsed_seconds':time.monotonic()-start,'workers':workers}
        atomic_json(folder/'result.json',result)
        report('searching','本轮搜索预算已用完，正在保存覆盖记录',workers=0,target_workers=workers,
            nodes=total_nodes,deepest=deepest,completed_branches=len(covered),completed_attempts=completed,pending_branches=len(tasks),
            limited_branches=limited,failed_branches=failures,resources=resources,resume=resume)
        raise ValueError('原版多核本轮尚未找到完整路线；预算已用完，不能据此判断无解')
    finally:
        for child,log in owned:
            if child.poll() is None:child.terminate()
        for child,log in owned:
            try:child.wait(timeout=3)
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=3)
            if not log.closed:log.close()
