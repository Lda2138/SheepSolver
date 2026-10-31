"""Bounded original-policy searches with live progress and validated export."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from solve_original_batch import export
from color_rules import compatibility
from tail_search import TailPlanner
from frontier_search import FrontierPlanner
from dead_state_cache import DeadStateCache

ROOT = Path(__file__).resolve().parent


from search_resources import available_memory, worker_target, state_cap, ResourceSampler, GIB


def policies(performance, mode, first, count, cpus=None, memory=None):
    cpus = cpus or os.cpu_count() or 1
    memory = available_memory() if memory is None else memory
    workers = worker_target(performance, count, cpus, 0, max(0, memory-2*GIB))
    choices = [(mode, first), ('level-top', .6 if first != .6 else .8), ('index-descending', .8)]
    return [choices[i] if i < 3 else ('color-top', [.6,.7,.8,.9,1][i%5]) for i in range(workers)]


def atomic_json(path, payload):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    for attempt in range(12):
        try: os.replace(temp, path); return
        except PermissionError:
            if attempt == 11: raise
            time.sleep(.02)


def run(a):
    a.output.mkdir(parents=True, exist_ok=True)
    if not 0 <= a.solve_first <= 1 or not 0 < a.time_limit <= 300: raise ValueError('Invalid search bounds')
    data = json.loads(a.map.read_text(encoding='utf-8-sig'))
    rules = compatibility(data); color = rules == 'color_shuffle'
    if rules == 'contains_special_card_rules': raise ValueError('尚未实现的特殊规则，不能套用普通搜索')
    if rules=='energy_alien':
        from solve_energy_map import run_energy
        return run_energy(a,data)
    count = sum(len(cards) for cards in data['levelData'].values())
    identity = {'map_sha256': hashlib.sha256(a.map.read_bytes()).hexdigest(),
                'session': data.get('_acquisition', {}).get('session', ''), 'total': count,
                'performance': a.performance, 'time_limit': a.time_limit, 'rules': 'color_shuffle_v1' if color else 'standard'}
    best_prefix = []; generation = 1; completed_nodes = 0; completed_deepest = 0
    best_path = a.output/'search_best_prefix.json'
    if color:
        generation_path = a.output/'search_generation.json'
        if generation_path.exists():
            previous = json.loads(generation_path.read_text(encoding='utf-8'))
            if previous.get('map_sha256') == identity['map_sha256']: generation = previous['generation']+1
        atomic_json(generation_path, {'map_sha256':identity['map_sha256'], 'generation':generation})
        identity['round'] = generation
    if best_path.exists():
        previous = json.loads(best_path.read_text(encoding='utf-8'))
        if previous.get('map_sha256') == identity['map_sha256']:
            best_prefix = previous['sequence']; completed_deepest = len(best_prefix)
    planner=TailPlanner(a.output/'tail_search_cache.json',identity['map_sha256'],count,atomic_json)
    planner.offer(best_prefix)
    raw_cards=[{**card,'layer':int(layer)} for layer,items in data['levelData'].items() for card in items]
    roots=[c['id'] for c in sorted(raw_cards,key=lambda c:(-c['layer'],c['id'])) if not any(
        o['layer']>c['layer'] and abs(o['rolNum']-c['rolNum'])<8 and abs(o['rowNum']-c['rowNum'])<8 for o in raw_cards)]
    frontier=FrontierPlanner(a.output/'frontier_cache.json',identity['map_sha256'],count,roots,atomic_json)
    dead_cache=DeadStateCache(a.output/'dead_states.bin',identity['map_sha256'])
    dead_cache.save(force=True)
    dead_cache.prune_snapshots([])
    completed_dead_hits=0
    start = time.monotonic(); workers = {}; all_owned = []; stats = []; restarts = 0; serial = 0
    status_path = a.output/'search_status.json'; control_path = a.output/'search_control.json'
    control_path.unlink(missing_ok=True)
    sampler = ResourceSampler(); resources = sampler.sample([]); target = 1
    finished_since_sample = []
    last_sample = start; previous_nodes = 0; previous_time = start; rate = 0; last_control = None
    accelerated = ROOT/'FastOriginalSolver.exe'
    native = accelerated.exists() and count <= 320

    def read_progress(w):
        try: return json.loads(w['progress'].read_text(encoding='utf-8'))
        except (OSError, ValueError): return {}

    def collect_best(w):
        nonlocal best_prefix, completed_deepest
        try:
            candidate = json.loads(Path(str(w['raw'])+'.best.json').read_text(encoding='utf-8'))['sequence']
            planner.offer(candidate)
            if len(candidate) > len(best_prefix):
                best_prefix = candidate; completed_deepest = max(completed_deepest, len(candidate))
                atomic_json(best_path, {'map_sha256':identity['map_sha256'], 'sequence':candidate})
        except (OSError, ValueError, KeyError): pass

    def report(state, message='', **extra):
        nonlocal previous_nodes, previous_time, rate
        now = time.monotonic(); nodes = completed_nodes + sum(s.get('nodes', 0) for s in stats)
        if now-previous_time >= .9:
            rate = max(0, (nodes-previous_nodes)/(now-previous_time)); previous_nodes, previous_time = nodes, now
        payload = {**identity, 'state':state, 'deepest':max([completed_deepest]+[s.get('deepest',0) for s in stats]),
                   'nodes':nodes, 'nodes_per_second':round(rate), 'elapsed_seconds':round(now-start,1),
                   'workers':sum(w['process'].poll() is None for w in workers.values()), 'target_workers':target,
                   'restarts':restarts, 'strategies':stats, 'message':message,
                   'resources':{k:v for k,v in resources.items() if k != 'memory_budget'},
                   'tail_search':planner.summary([w.get('tail') for w in workers.values() if w['process'].poll() is None]),
                   'frontier_search':frontier.summary([w.get('branch') for w in workers.values() if w['process'].poll() is None]), **extra}
        payload['dead_cache']={'states':len(dead_cache.records),'hits':completed_dead_hits+sum(s.get('dead_cache_hits',0) for s in stats)}
        atomic_json(status_path, payload)

    def launch(slot, attempt):
        nonlocal serial
        serial += 1
        raw = a.output/f'worker_{slot}_answer.json'; progress = a.output/f'worker_{slot}_progress.json'
        for path in (raw, progress, Path(str(raw)+'.best.json'), Path(str(progress)+'.control.json'), Path(str(progress)+'.frontier.json'), Path(str(progress)+'.dead.bin')): path.unlink(missing_ok=True)
        error = (a.output/f'worker_{slot}_error.txt').open('w', encoding='utf-8')
        cap = state_cap(resources['memory_budget'], target, slot == 0, color)
        mode = a.mode if slot == 0 and attempt == 0 and generation == 1 else 'color-top'
        first = a.solve_first if slot == 0 and attempt == 0 else [.8,.9,.6,1,.7][(slot+attempt)%5]
        if not color and slot < 3 and attempt == 0:
            mode, first = [(a.mode,a.solve_first),('level-top',.6),('index-descending',.8)][slot]
        prefix_args = []; tail=None;branch=None
        # Keep one unrestricted route for bad early choices. Other workers
        # prioritize distinct local subproblems, excluding proven dead prefixes.
        if slot%3!=0 or target==1:tail=planner.next_task()
        if tail is None:
            for _ in range(1000):
                branch=frontier.next_task()
                if branch is None or not planner.disproved(branch["prefix"]):break
                frontier.finish(branch,"exhausted");branch=None
            if branch is None:error.close();return False
        remaining = max(.1, a.time_limit-(time.monotonic()-start))
        if tail or branch:
            prefix = a.output/f'worker_{slot}_prefix.json'
            atomic_json(prefix, {'sequence':tail['prefix'] if tail else branch['prefix'],'map_sha256':identity['map_sha256'],
                                 'allowed':branch.get('allowed') if branch else None,'dead_cache':str(dead_cache.snapshot or dead_cache.path)})
            prefix_args=[str(prefix)];mode='color-top'
            if tail:first=0;remaining=min(remaining,tail['seconds'])
        args = [str(accelerated), str(a.map), str(raw), mode, str(first), str(remaining),
                str(generation*100003+serial*7919+slot*997), str(cap), str(progress), *prefix_args]
        try:
            process = subprocess.Popen(args, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=error,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
        except BaseException: error.close(); raise
        sampler.priority(process, a.performance)
        w = {'process':process, 'raw':raw, 'progress':progress, 'error':error, 'attempt':attempt,
             'cap':cap, 'retiring':None,'tail':tail,'branch':branch,'cache_snapshot':dead_cache.snapshot}
        workers[slot] = w; all_owned.append(w);return True

    def finish_branch(w,state):
        if not w.get('branch') or w['branch']['id'] not in frontier.active:return
        try:saved=json.loads(Path(str(w['progress'])+'.frontier.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):saved=None
        frontier.finish(w['branch'],state,saved)

    def retire(w):
        if w['retiring'] is None:
            w['retiring'] = time.monotonic()
            atomic_json(Path(str(w['progress'])+'.control.json'), {'stop':True})

    report('starting')
    try:
        if color and not native: raise ValueError('变色关卡需要新版加速求解器')
        if not native:
            process = subprocess.Popen([str(ROOT/'venv/Scripts/python.exe'),str(ROOT/'desktop_assistant.py'),
                '--map',str(a.map),'--output',str(a.output),'--time-limit',str(a.time_limit),
                '--mode',a.mode,'--solve-first',str(a.solve_first),'--source-label','实际完整地图；原 Python 求解器'],
                creationflags=subprocess.CREATE_NO_WINDOW)
            w = {'process':process,'raw':None,'progress':None,'error':None}; workers[0]=w; all_owned.append(w)
        else:
            target = worker_target(a.performance,count,sampler.cpus,resources['external_cpu_percent'],resources['memory_budget'],color)
            # Ramp up after measured CPU samples instead of flooding the desktop.
            for slot in range(min(2,target)): launch(slot,0)
        while True:
            now = time.monotonic()
            if control_path.exists():
                try:
                    control = json.loads(control_path.read_text(encoding='utf-8-sig'))
                    if control.get('action') == 'stop': report('cancelled','搜索已停止'); return 2
                    if control != last_control and control.get('action') == 'settings':
                        if control.get('performance') in ('eco','fast','max'):
                            a.performance=control['performance'];identity['performance']=a.performance
                            for w in workers.values():sampler.priority(w['process'],a.performance)
                        last_control=control;last_sample=0
                except (OSError,ValueError):pass
            stats=[]
            for slot,w in list(workers.items()):
                progress = read_progress(w) if native else {}
                if progress:stats.append(progress)
                if native:collect_best(w)
                code=w['process'].poll()
                if code==0:
                    report('validating','正在独立回放遮挡、变色与牌槽规则' if color else '正在独立回放遮挡与牌槽规则')
                    if native:payload=export(a.map,json.loads(w['raw'].read_text()),a.output)
                    else:
                        payload=json.loads((a.output/'solution.json').read_text(encoding='utf-8'))
                        if not payload.get('validated') or payload['map_sha256']!=identity['map_sha256']:raise ValueError('Python answer not validated for current map')
                    report('solved','完整答案已验证',deepest=count,validated=True)
                    print(json.dumps({k:payload[k] for k in ('card_count','validated','mode','elapsed_seconds','peak_tray_after_elimination')}));return 0
                if code is not None and native:
                    dead_cache.merge(Path(str(w['progress'])+'.dead.bin'))
                    if dead_cache.save():dead_cache.prune_snapshots([x.get('cache_snapshot') for x in workers.values() if x['process'].poll() is None])
                    completed_dead_hits+=progress.get('dead_cache_hits',0)
                    completed_nodes+=progress.get('nodes',0);completed_deepest=max(completed_deepest,progress.get('deepest',0))
                    w['process'].wait();finished_since_sample.append(w['process']);w['error'].close();del workers[slot]
                    if w.get('tail'):planner.finish(w['tail'],'interrupted' if w['retiring'] is not None else progress.get('state','limited'),progress.get('stop_reason',''))
                    finish_branch(w,progress.get('state','limited'))
                    if code==1:
                        detail=(a.output/f'worker_{slot}_error.txt').read_text(encoding='utf-8').strip()
                        raise RuntimeError('搜索进程失败：'+detail)
                    if w['retiring'] is None and slot<target and now-start<a.time_limit and (color or w.get('tail') or w.get('branch') or progress.get('state')=='limited'):
                        restarts+=1;launch(slot,w['attempt']+1)
                elif native and w['retiring'] is not None and now-w['retiring']>2:
                    w['process'].terminate()
            if now-start>=a.time_limit:
                report('limited','达到本轮搜索时间上限，未找到完整答案');return 2
            if native and now-last_sample>=1.5:
                resources=sampler.sample([w['process'] for w in workers.values() if w['process'].poll() is None]+finished_since_sample)
                finished_since_sample.clear()
                desired=worker_target(a.performance,count,sampler.cpus,resources['external_cpu_percent'],resources['memory_budget'],color)
                # Ignore a one-core sample fluctuation; react immediately to RAM pressure and eco.
                if abs(desired-target)>=2 or a.performance=='eco' or resources['available_memory_gb']<1.5:target=desired
                if resources['available_memory_gb']<1.5:target=max(1,min(target,len(workers)-2))
                for slot,w in workers.items():
                    if slot>=target:retire(w)
                    else:
                        cap=state_cap(resources['memory_budget'],target,slot==0,color)
                        if cap != w['cap'] and w['retiring'] is None:
                            w['cap']=cap;atomic_json(Path(str(w['progress'])+'.control.json'),{'state_limit':cap})
                added=0
                for slot in range(target):
                    if slot not in workers and added<2:
                        if launch(slot,serial//max(1,target)):added+=1
                last_sample=now
            stats=[read_progress(w) for w in workers.values()] if native else []
            if not workers or (not native and all(w['process'].poll() is not None for w in workers.values())):
                if native and not frontier.blocks and not frontier.active:report('exhausted','完整分支已穷尽，当前规则下未找到通关路线');return 2
                report('limited','本轮搜索未找到完整答案');return 2
            planner.save();frontier.save()
            if dead_cache.save():dead_cache.prune_snapshots([w.get('cache_snapshot') for w in workers.values()])
            report('searching',progress_available=native)
            time.sleep(.3)
    except BaseException as e:
        report('error',str(e));raise
    finally:
        # Request graceful unwind first, preserving each best route. Only our
        # own child handles can be terminated, and every child is waited on.
        for w in all_owned:
            if w['process'].poll() is None:
                if native:retire(w)
                else:w['process'].terminate()
        deadline=time.monotonic()+2
        for w in all_owned:
            try:w['process'].wait(timeout=max(.01,deadline-time.monotonic()))
            except subprocess.TimeoutExpired:w['process'].terminate();w['process'].wait(timeout=5)
            if native:collect_best(w)
            if native:dead_cache.merge(Path(str(w['progress'])+'.dead.bin'))
            if w.get('tail') and w['tail']['key'] in planner.active:planner.finish(w['tail'],'interrupted','wrapper_end')
            finish_branch(w,read_progress(w).get('state','interrupted') if native else 'interrupted')
            if w['error'] and not w['error'].closed:w['error'].close()
        planner.save(force=True);frontier.save(force=True);dead_cache.save(force=True)
        dead_cache.prune_snapshots([])
        # Terminal status must describe stopped children, not the last live
        # sample. Keep aggregate nodes from completed attempts and last workers.
        try:
            final=json.loads(status_path.read_text(encoding='utf-8'))
            final.update(workers=0,target_workers=0,deepest=max(final['deepest'],completed_deepest),
                         nodes=completed_nodes+sum(read_progress(w).get('nodes',0) for w in workers.values()) if native else final['nodes'])
            final['tail_search']=planner.summary([])
            final['frontier_search']=frontier.summary([])
            final['resources'].update(solver_cpu_percent=0,solver_memory_mb=0,solver_private_mb=0,
                                      available_memory_gb=round(available_memory()/GIB,2))
            atomic_json(status_path,final)
        except (OSError,ValueError):pass


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--map', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--time-limit', type=float, default=120)
    p.add_argument('--mode', choices=['normal','index-ascending','index-descending','level-bottom','level-top','random'], default='level-top')
    p.add_argument('--solve-first', type=float, default=0.8)
    p.add_argument('--performance', choices=['eco', 'fast', 'max'], default=None)
    a = p.parse_args()
    if a.performance is None:
        config = ROOT/'auto_capture_config.json'
        settings = json.loads(config.read_text(encoding='utf-8-sig')) if config.exists() else {}
        a.performance = settings.get('solve_performance', 'fast')
        if a.performance not in ('eco', 'fast', 'max'): a.performance = 'fast'
    raise SystemExit(run(a))


if __name__ == '__main__': main()
