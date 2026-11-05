"""Exact fixed-color planning, with global six-slot cumulative occupancy.

No input or screenshot APIs. Only a complete independently replayed route is
exportable. A solver INFEASIBLE is scoped to this model and confirmed boundary;
it is not an independently checked SAT proof certificate.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time

from color_rules import initial_cards
from solve_constraints import solve_board
from solve_original_batch import export
from solve_verified_map import atomic_json
from standard_plan import check_answer, read

ENGINE='static_constraint_v1'
MODEL_VERSION='ordinary-cumulative-2'
SCHEDULER_VERSION='native-refill-v3-one-move-out'
# All 19 fresh complete solves on record finished within 9.6 s and none came between
# 10 and 60 s, so a longer search only delays the one-move-out attempt or a new game.
NATIVE_SECONDS=12
CONSTRAINT_SECONDS=5
SCHEDULER_SOURCES={name:hashlib.sha256((Path(__file__).resolve().parent/name).read_bytes()).hexdigest()
                   for name in ('static_plan.py','static_accelerator.py','search_resources.py','capacity_probe.py','prop_search.py','prop_plan.py')}


def plan_current(map_path,prefix,output,seconds=60,cancel=None,performance='max'):
    map_path=Path(map_path);output=Path(output);prefix=list(prefix)
    raw=map_path.read_bytes();digest=hashlib.sha256(raw).hexdigest()
    data=json.loads(raw.decode('utf-8-sig'));cards,rules=initial_cards(data)
    if rules:raise ValueError('静态约束求解只支持普通牌；特殊关继续使用原版当前牌型算法')
    prop_seconds=min(20,max(0,seconds/3)) if len(cards)-len(prefix)>60 and seconds>=15 else 0
    normal_seconds=max(0,min(seconds-prop_seconds,NATIVE_SECONDS+CONSTRAINT_SECONDS))
    seconds=normal_seconds+prop_seconds
    output.mkdir(parents=True,exist_ok=True);start=time.monotonic()
    workers=1 if performance=='eco' else max(1,(os.cpu_count() or 1)//2) if performance=='fast' else (os.cpu_count() or 1)
    identity={'engine':ENGINE,'model_version':MODEL_VERSION,'rules':'standard',
        'scheduler_version':SCHEDULER_VERSION,'scheduler_sources':SCHEDULER_SOURCES,'solver_pid':os.getpid(),
        'total':len(cards),'committed_steps':len(prefix),'target_workers':workers,
        'map_sha256':digest,'prefix_sha256':hashlib.sha256(json.dumps(prefix).encode()).hexdigest(),
        'tray_capacity':6,'encoding':'cumulative','confirmed_prefix':prefix,
        'proof_scope':'fixed_boundary' if prefix else 'whole_map'}
    status={**identity,'workers':0,'elapsed_seconds':0}
    sampler=process=None
    if os.name=='nt':
        import ctypes
        from types import SimpleNamespace
        from search_resources import ResourceSampler
        sampler=ResourceSampler();process=SimpleNamespace(pid=os.getpid(),_handle=-1)
    def stopped():return bool(cancel and cancel())
    def report(state,message,**kw):
        live_workers=kw.pop('workers',workers if state=='searching' else 0)
        status.update(kw)
        status.update(state=state,message=message,elapsed_seconds=round(time.monotonic()-start,3),
            workers=live_workers,budget_seconds=seconds,remaining_seconds=max(0,seconds-(time.monotonic()-start)))
        if state not in ('searching','verifying'):status.update(phase=state,remaining_seconds=0)
        if sampler and 'resources' not in kw:status['resources']=sampler.sample([process])
        atomic_json(output/'search_status.json',status)
    cache=output/'complete/solution.json'
    from prop_plan import cached as cached_prop,search as search_prop
    prop_folder=output/'prop'
    def publish_prop(result):
        path,payload=result
        if stopped():raise ValueError('已暂停移出规划')
        if hashlib.sha256(map_path.read_bytes()).hexdigest()!=digest:raise ValueError('地图已改变，丢弃道具路线')
        report('prop_ready',f"一次移出路线已验证：{payload['operation_count']} 个操作；执行前需人工移出和移出区校准",workers=0,
            prop_solution_path=str(path),prop_operation_count=payload['operation_count'],normal_solvability='unknown',executable=False)
        return path,payload
    try:
        if stopped():raise ValueError('已暂停静态约束求解')
        if cache.exists():
            answer=read(cache)
            try:check_answer(answer,data,digest,prefix)
            except (ValueError,KeyError,TypeError):pass
            else:
                report('solved','复用已独立验证的完整路线',cache_reused=True)
                return cache,answer
        legacy=None
        if output.parent==map_path.parent and map_path.stem.startswith('map_'):
            legacy=map_path.parent/f"level_{map_path.stem[4:]}_prop/prop_solution.json"
        existing=cached_prop(map_path,data,prefix,prop_folder,legacy)
        if existing:return publish_prop(existing)
        report('searching','构建整局覆盖、三消与六格槽容量约束',phase='building',cache_reused=False)
        atomic_json(output/'input.json',data)
        hint=[];store=None;answer=None;acceleration={}
        if len(cards)-len(prefix)>60 and seconds>=15:
            from static_accelerator import prepare
            acceleration,answer,hint,store=prepare(map_path,data,prefix,output/'accelerator',min(NATIVE_SECONDS,normal_seconds*.8),
                workers,stopped,lambda **kw:report('searching',
                    f"原生搜索：{kw.get('workers',0)} 路运行，已补位 {kw.get('native_restarts',0)} 次；完整路线验证后执行",**kw))
            report('searching','候选仅作为提示，整局约束可修改早期选择',acceleration=acceleration)
        if answer is not None or acceleration.get('state')=='infeasible':info=dict(acceleration)
        else:
            known=store.load() if store else [];probe=None
            constraint_seconds=min(CONSTRAINT_SECONDS,max(0,normal_seconds-(time.monotonic()-start)))
            # CP-SAT uses at most two workers; idle CPUs run the relaxed
            # capacity diagnostic. Its routes are never hints or answers.
            if acceleration and workers>2 and constraint_seconds>=5 and os.name=='nt':
                import capacity_probe
                (output/'capacity_probe').mkdir(parents=True,exist_ok=True)
                atomic_json(output/'capacity_probe/prefix.json',{'sequence':prefix,'map_sha256':digest})
                try:probe=capacity_probe.start(map_path,output/'capacity_probe/prefix.json',output/'capacity_probe',min(30,constraint_seconds))
                except OSError:probe=None
            try:
                with (output/'solver.log').open('w',encoding='utf-8') as log:
                    info,answer=solve_board(data,prefix=prefix,seconds=constraint_seconds,workers=min(2,workers),
                        cancel=stopped,encoding='cumulative',model_path=output/'model.pbtxt',hint=hint,
                        known_conflicts=known,save_conflicts=store.save if store else None,
                        proved_conflicts_symmetry=bool(known),
                        notify=lambda **kw:report('searching','整局约束搜索中；候选可撤回，完整路线验证后执行',**{**kw,'workers':min(2,workers)}),
                        log=lambda line:(log.write(line),log.flush()))
            finally:
                if probe:probe=capacity_probe.finish(probe,data,wait=not stopped())
            info['restored_conflicts']=len(known)
            if probe:info['capacity_probe']=probe
        if answer is None and prop_seconds and not stopped():
            routes=read(output/'accelerator/hints.json').get('routes',[])
            for path in (output/'capacity_probe').glob('cap*_seed*.json'):
                route=read(path).get('sequence',[])
                if route and route not in routes:routes.append(route)
            left=min(prop_seconds,max(0,seconds-(time.monotonic()-start)))
            report('searching','普通搜索未取得完整解，正在尝试一次移出；本阶段最多20秒',phase='prop_search',workers=0)
            prop_result=search_prop(map_path,data,prefix,prop_folder,routes,seconds=left,cancel=stopped,
                notify=lambda **kw:report('searching','正在搜索一次移出路线；找到后先展示操作表',**kw))
            info['prop_search']={'state':'complete' if prop_result else 'unknown','budget_seconds':left,'executable':False}
            if prop_result:
                atomic_json(output/'analysis.json',{**identity,**info,'state':'prop_ready','formal_certificate':False,'normal_solvability':'unknown'})
                return publish_prop(prop_result)
        info['acceleration']=acceleration
        if stopped() or info['state']=='cancelled':raise ValueError('已暂停静态约束求解')
        if hashlib.sha256(map_path.read_bytes()).hexdigest()!=digest:raise ValueError('地图已改变，丢弃旧模型结果')
        info['constraint_seconds']=info.get('elapsed_seconds',0) if info.get('method','cp_sat')=='cp_sat' else 0
        info['elapsed_seconds']=time.monotonic()-start
        if answer is not None:answer['elapsed_seconds']=info['elapsed_seconds']
        # Store the exact model for reproduction, not as an UNSAT certificate.
        atomic_json(output/'analysis.json',{**identity,**info,'formal_certificate':False})
        if answer is None:
            if info['state']=='infeasible':
                report('no_continuation','约束模型判定当前边界无解；未生成独立证明证书',**{k:v for k,v in info.items() if k!='state'})
                raise ValueError('当前无道具模型不可满足'+('（仅限已确认前缀）' if prefix else '（整图范围；无独立证明证书）'))
            from capacity_probe import describe
            note=describe(info.get('capacity_probe'))
            report('limited','这张图短时间内解不出，可解性未知；棋盘保持不动，请重开一局换图或调低难度'+('；'+note if note else ''),**{k:v for k,v in info.items() if k!='state'})
            raise ValueError('这张图短时间内解不出，可解性未知；请重开一局换图或调低难度，重开后自动继续')
        method=info.get('method','cp_sat')
        answer.update(engine=ENGINE,model_version=MODEL_VERSION,search_method=method,
            source_label='普通牌完整路线；快速搜索或精确残局回退' if method!='cp_sat' else '普通牌整局约束；候选提示与已证明死局剪枝')
        report('verifying','完整候选已得到，正在独立回放全部步骤')
        verified=export(map_path,answer,output/'candidate')
        check_answer(verified,data,digest,prefix)
        if stopped():raise ValueError('已暂停静态约束求解')
        if hashlib.sha256(map_path.read_bytes()).hexdigest()!=digest:raise ValueError('地图已改变，丢弃旧模型结果')
        cache.parent.mkdir(exist_ok=True)
        atomic_json(cache,verified)
        report('solved','完整通关路线已独立回放，等待棋盘对齐',cache_reused=False,
            **{k:v for k,v in info.items() if k not in ('state','elapsed_seconds')})
        return cache,verified
    except Exception as exc:
        if stopped():report('cancelled','已暂停静态约束求解')
        elif status.get('state') not in ('limited','no_continuation','prop_ready'):report('error',str(exc))
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Offline ordinary-board solver; never clicks')
    parser.add_argument('--map',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--seconds',type=float,default=60)
    parser.add_argument('--performance',choices=('eco','fast','max'),default='max')
    args=parser.parse_args()
    try:
        path,answer=plan_current(args.map,[],args.output,args.seconds,performance=args.performance)
        print(json.dumps({'state':'prop_ready' if answer.get('execution_kind')=='manual_move_out_v1' else 'solved','solution':str(path),'steps':len(answer.get('sequence',[])),'prop_operations':answer.get('operation_count')},ensure_ascii=True))
    except ValueError as exc:
        print(json.dumps({'state':read(args.output/'search_status.json').get('state','error'),'message':str(exc)},ensure_ascii=True))
        raise SystemExit(2)
