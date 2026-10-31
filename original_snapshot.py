"""Original Python solver on the current fixed faces; discard on refresh.

The upstream solver and its normal selection policy are used unchanged.
Existing rule replay only supplies the present board and invalidates stale
routes. It does not score or search future special-card events.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from solve_verified_map import atomic_json

ROOT=Path(__file__).resolve().parent
ENGINE='upstream_normal_snapshot_v1'


def read(path):
    try:return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):return {}


def current_snapshot(board):
    snapshot=board.snapshot()
    # CardDetail reads the layer from the numeric ID's first component.
    prefix=[]
    for layer,cards in snapshot['levelData'].items():
        for c in cards:
            if c['id'].startswith('tray-'):
                c['id']=f'{layer}-{c["rolNum"]}-{c["rowNum"]}'
                prefix.append(c['id'])
    snapshot['_planning_prefix']=prefix
    return snapshot


def cached_tail(cache,board,prefix,digest):
    if cache.get('engine')!=ENGINE or cache.get('map_sha256')!=digest:return None
    base=cache.get('confirmed_prefix',[]);route=cache.get('route',[])
    if prefix[:len(base)]!=base:return None
    consumed=prefix[len(base):]
    if consumed!=route[:len(consumed)]:return None
    remaining=set(cache.get('remaining_types',{}))-set(consumed)
    if remaining!=board.state.remaining:return None
    if any(board.state.types[id]!=cache['remaining_types'][id] for id in remaining):return None
    tray=list(cache.get('tray',[]))
    for id in consumed:
        t=cache['remaining_types'][id];tray.append(t)
        if tray.count(t)==3:tray=[v for v in tray if v!=t]
    if tray!=board.tray:return None
    return route[len(consumed):]


def solve_snapshot(snapshot,folder,seconds,cancel,report,performance='eco'):
    if performance!='eco' and sum(map(len,snapshot['levelData'].values()))>60:
        from original_parallel import search
        return search(snapshot,folder,seconds,cancel,report,performance=performance)
    folder.mkdir(parents=True,exist_ok=True)
    mp=folder/'snapshot.json';candidate=folder/'candidate.json';progress=folder/'progress.json'
    atomic_json(mp,snapshot)
    cmd=[getattr(sys,'_base_executable',sys.executable),str(Path(__file__).resolve()),'worker','--map',str(mp),
         '--output',str(candidate),'--progress',str(progress),'--seconds',str(seconds)]
    start=time.monotonic();child=None
    with (folder/'process.log').open('w',encoding='utf-8') as log:
        try:
            child=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW,
                env={**os.environ,'PYTHONIOENCODING':'utf-8','PYTHONPATH':os.pathsep.join([
                    str(ROOT),str(ROOT/'venv/Lib/site-packages'),os.environ.get('PYTHONPATH','')])})
            while child.poll() is None:
                if cancel and cancel():raise ValueError('已暂停原版求解')
                if time.monotonic()-start>seconds+5:raise ValueError('原版本轮预算耗尽，尚未找到完整路线')
                p=read(progress)
                report('searching','原版 normal 回溯：按当前牌型计算完整路线',
                    deepest=p.get('deepest',0),nodes=p.get('nodes',0),
                    nodes_per_second=p.get('nodes_per_second',0))
                time.sleep(.2)
            if child.returncode==2:raise ValueError('原版本轮尚未找到完整路线；可增加时间后重试')
            if child.returncode!=0:raise RuntimeError('原版求解进程异常，见 '+str(folder/'process.log'))
            answer=read(candidate)
            if answer.get('engine')!=ENGINE or not answer.get('sequence'):
                raise ValueError('原版求解器没有返回完整路线')
            return answer
        finally:
            if child and child.poll() is None:
                child.terminate();child.wait(timeout=5)


def plan_current(map_path,prefix,output,seconds=300,cancel=None,performance='eco'):
    from rolling_play import replay_prefix,make_segment
    from frozen_replan import check_static_route
    from solve_original_batch import export
    raw=map_path.read_bytes();digest=hashlib.sha256(raw).hexdigest()
    data=json.loads(raw.decode('utf-8-sig'));board=replay_prefix(data,prefix)
    output.mkdir(parents=True,exist_ok=True);started=time.monotonic()
    cache_path=output/'original_route.json';cache=read(cache_path)
    identity={'engine':ENGINE,'rules':(board.rules or {}).get('kind','standard'),
        'total':len(board.cards),'committed_steps':len(prefix),'target_workers':1,
        'strategy':'normal','solve_first':.8,'remaining_cards':len(board.state.remaining)}
    if board.rules:identity['execution_kind']='rolling_segment_v1'
    atomic_json(output/'search_status.json',dict(identity,state='starting',workers=0,elapsed_seconds=0))
    def report(state,message,**extra):
        payload=read(output/'search_status.json')
        payload.update(identity)
        payload.update(state=state,message=message,
            workers=1 if state=='searching' else 0,
            elapsed_seconds=round(time.monotonic()-started,2))
        payload.update(extra);atomic_json(output/'search_status.json',payload)
    try:
        if cancel and cancel():raise ValueError('已暂停原版求解')
        tail=cached_tail(cache,board,prefix,digest)
        reused=bool(tail)
        if not tail:
            if cache:report('searching','牌型或剩余棋盘已改变，旧路线作废，重新调用原版算法')
            else:report('searching','调用开源项目原版 Python normal 算法')
            snapshot=current_snapshot(board)
            run=output/f'run_{len(prefix):04}_{time.time_ns()}'
            answer=solve_snapshot(snapshot,run,float(seconds),cancel,report,performance=performance)
            # Ordinary geometry/tray integrity only, no future special events.
            tail=check_static_route(snapshot,snapshot['_planning_prefix'],answer['sequence'],True)
            if not tail:raise ValueError('当前棋盘没有新的可执行步骤')
            cache={'engine':ENGINE,'map_sha256':digest,'confirmed_prefix':list(prefix),
                'route':tail,'remaining_types':{id:board.state.types[id] for id in board.state.remaining},
                'tray':list(board.tray),'upstream_sources':answer['upstream_sources'],
                'solver_elapsed_seconds':answer['elapsed_seconds'],'solve_first':answer.get('solve_first',.8),
                'mode':answer.get('mode','normal'),
                'parallel_workers':answer.get('parallel_workers',1),'winning_branch':answer.get('winning_branch')}
            if cancel and cancel():raise ValueError('已暂停原版求解')
            atomic_json(cache_path,cache)
        if hashlib.sha256(map_path.read_bytes()).hexdigest()!=digest:raise ValueError('地图已改变，旧路线作废')
        if not board.rules:
            candidate={'engine':ENGINE,'mode':cache.get('mode','normal'),'solve_first':cache.get('solve_first',.8),'sequence':list(prefix)+tail,
                'source_label':'开源项目原版 Python 回溯；当前牌型完整路线',
                'upstream_sources':cache['upstream_sources'],'parallel_workers':cache.get('parallel_workers',1),
                'winning_branch':cache.get('winning_branch')}
            answer=export(map_path,candidate,output/'complete')
            path=output/'complete/solution.json'
            report('solved','继续原版完整路线' if reused else '原版已算出完整路线，等待执行',
                deepest=len(board.cards),cache_reused=reused)
        else:
            # An execution boundary is not a search horizon. All these picks
            # come from a complete static answer. Reuse that answer unless
            # actual types, tray or remaining IDs change at the next boundary.
            answer=make_segment(data,digest,prefix,tail)
            answer.update(planning_engine=ENGINE,source_label='原版当前牌型完整路线；牌型改变后重新计算')
            path=output/f'execution_{len(prefix):04}.json';atomic_json(path,answer)
            report('segment_ready','牌型未变，继续原版路线' if reused else '当前牌型完整路线已算出；改变后自动重算',
                deepest=len(board.cards),cache_reused=reused)
        return path,answer
    except Exception as exc:
        cancelled=bool(cancel and cancel())
        state='cancelled' if cancelled else 'limited' if '本轮' in str(exc) else 'error'
        report(state,str(exc));raise


def bootstrap_solver(snapshot,branch=None,seconds=300,cls=None,solve_first=.8,mode='normal'):
    from business.SheepSolver import SheepSolver
    solver=(cls or SheepSolver)({'show_progress':False,'solve_first':solve_first,'time_limit':seconds,
        'expect_progress':{'time':-1,'percentage':.8}},mode)
    solver.load_map_data(snapshot)
    cards=solver._game_pool_controller.get_all_card_dict();indices={c.get_card_id():i for i,c in cards.items()}
    for id in list(snapshot.get('_planning_prefix',[]))+list(branch or []):
        index=indices[id]
        if index not in solver._game_pool_controller.generate_head_list():raise ValueError('原版分支起点无效')
        solver._operation_pick_card(index)
        if solver._game_pool_controller._residual_pool.is_card_count_out_of_limit():raise ValueError('原版分支起点超出牌槽容量')
    return solver


def worker(args):
    from business.SheepSolver import SheepSolver
    snapshot=read(args.map);began=time.monotonic()
    class ProgressSolver(SheepSolver):
        # Observe upstream work; leave solve(), ordering, pruning and cache
        # untouched. No native solver or local lookahead is called.
        nodes=0;last_report=0;deepest=0;memory_limited=False
        def _generate_current_iteration_time(self):
            super()._generate_current_iteration_time();self.nodes+=1
        def _check_programme_can_continue(self):
            if self.nodes%32768==0 and args.memory_mb:
                from search_resources import ResourceSampler
                from types import SimpleNamespace
                import ctypes
                _,private=ResourceSampler().process_memory(SimpleNamespace(_handle=ctypes.windll.kernel32.GetCurrentProcess()))
                if private>args.memory_mb*1024**2:self.memory_limited=True
            return not self.memory_limited and super()._check_programme_can_continue()
        def _record_current_progress(self):
            super()._record_current_progress()
            self.deepest=max(self.deepest,len(self._card_sequence.get_pick_index_list()))
            elapsed=time.monotonic()-began
            if elapsed-self.last_report>=.5:
                self.last_report=elapsed
                atomic_json(args.progress,{'deepest':self.deepest,'nodes':self.nodes,
                    'nodes_per_second':round(self.nodes/max(.001,elapsed))})
    branch=read(args.branch).get('sequence',[]) if args.branch else []
    import random
    random.seed(args.seed)
    solver=bootstrap_solver(snapshot,branch,args.seconds,ProgressSolver,solve_first=args.solve_first,mode=args.mode)
    solver.solve();sequence=solver.generate_card_id_result()
    state='candidate' if sequence else 'memory_limited' if solver.memory_limited else 'limited' if solver._iteration_time>=args.seconds else 'exhausted'
    atomic_json(args.progress,{'state':state,'deepest':solver.deepest,'nodes':solver.nodes,
        'nodes_per_second':round(solver.nodes/max(.001,time.monotonic()-began)),
        'elapsed_seconds':time.monotonic()-began})
    if not sequence:return 2
    sources=['business/SheepSolver.py','core/tool/GamePoolController.py','core/pool/OperationPool.py',
        'core/pool/ResidualPool.py','core/card/CardContainer.py']
    atomic_json(args.output,{'engine':ENGINE,'sequence':sequence,'mode':args.mode,'solve_first':args.solve_first,
        'elapsed_seconds':time.monotonic()-began,
        'upstream_sources':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sources}})
    return 0


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['worker'])
    p.add_argument('--map',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--progress',type=Path,required=True);p.add_argument('--seconds',type=float,default=300)
    p.add_argument('--branch',type=Path);p.add_argument('--memory-mb',type=int,default=0)
    p.add_argument('--solve-first',type=float,default=.8)
    p.add_argument('--mode',choices=['normal','level-top','level-bottom','index-ascending','index-descending','random'],default='normal')
    p.add_argument('--seed',type=int,default=7919)
    raise SystemExit(worker(p.parse_args()))
