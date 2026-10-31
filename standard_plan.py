"""Ordinary boards keep a verified complete continuation across short clicks.

Five slots are a preferred search bound, never an irreversible local policy.
No incomplete native result is eligible for playback.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import time

from color_rules import initial_cards
from desktop_assistant import validate_solution
from solve_original_batch import export
from solve_verified_map import atomic_json

ROOT=Path(__file__).resolve().parent


def read(path):
    try:return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):return {}


def check_answer(answer,data,digest,prefix):
    if answer.get('map_sha256')!=digest or answer.get('sequence',[])[:len(prefix)]!=list(prefix):
        raise ValueError('完整路线与当前地图或确认记录不一致')
    cards,rules=initial_cards(data)
    if rules or answer.get('rules') or answer.get('cards')!=cards:raise ValueError('普通关路线与真实牌面不一致')
    validate_solution(answer)


def audit_static_boundary(data,prefix,seconds=2,state_limit=200000):
    """Independent exact DFS on an ordinary boundary; no heuristic pruning."""
    from rolling_play import replay_prefix
    board=replay_prefix(data,prefix)
    if board.rules:raise ValueError('独立边界检查仅支持普通牌')
    cards=board.cards;ids=[c['id'] for c in cards];index={id:i for i,id in enumerate(ids)}
    parents=[sum(1<<index[id] for id in board.parents[c['id']]) for c in cards]
    types=[c['type'] for c in cards]
    left=sum(1<<index[id] for id in board.state.remaining);initial=tuple(sorted(board.tray))
    start=time.monotonic();seen=set();limited=False;witness=None
    def visit(mask,tray,route):
        nonlocal limited,witness
        if time.monotonic()-start>=seconds or len(seen)>=state_limit:limited=True;return False
        if not mask:
            if not tray:witness=route;return True
            return False
        # On fixed colors the remaining set determines the held counts mod 3.
        # Include tray anyway so this proof is self-contained at the boundary.
        key=(mask,tray)
        if key in seen:return False
        seen.add(key);rest=mask
        while rest:
            bit=rest&-rest;rest^=bit;i=bit.bit_length()-1
            if parents[i]&mask:continue
            t=types[i];values=list(tray)+[t]
            if values.count(t)==3:values=[v for v in values if v!=t]
            if len(values)>=7:continue
            if visit(mask^bit,tuple(sorted(values)),route+[ids[i]]):return True
            if limited:return False
        return False
    solved=visit(left,initial,[])
    return {'state':'complete' if solved else 'limited' if limited else 'dead_prefix',
        'states':len(seen),'elapsed_seconds':time.monotonic()-start,'remaining':len(board.state.remaining),
        'tray':list(board.tray),'fixed_steps':len(prefix),'sequence':witness}


def solve_continuation(map_path,prefix,output,seconds=30,cancel=None,notify=None):
    raw=map_path.read_bytes();digest=hashlib.sha256(raw).hexdigest();data=json.loads(raw.decode('utf-8-sig'))
    cards,rules=initial_cards(data)
    if rules:raise ValueError('普通关完整规划不能用于特殊规则')
    output.mkdir(parents=True,exist_ok=True);status_path=output/'search_status.json';start=time.monotonic();attempts=[]
    cache=output/'solution.json'
    identity={'engine':'standard_complete_continuation','rules':'standard','total':len(cards),
        'committed_steps':len(prefix),'target_workers':1}
    def stopped():return bool(cancel and cancel())
    def report(state,message,cap=5,**kw):
        atomic_json(status_path,dict(identity,state=state,message=message,tray_capacity=cap,
            workers=1 if state=='searching' else 0,elapsed_seconds=round(time.monotonic()-start,3),
            deepest=kw.pop('deepest',len(prefix)),nodes_per_second=kw.pop('nodes_per_second',0),
            capacity_attempts=attempts,**kw))
        if notify:notify(state=state)
    try:
        if stopped():raise ValueError('已停止完整路线规划')
        if cache.exists():
            answer=read(cache)
            try:check_answer(answer,data,digest,prefix)
            except ValueError:pass
            else:
                report('solved','复用已验证的整局路线，继续执行',answer.get('planning_capacity',6),
                    deepest=len(cards),cache_reused=True)
                return cache,answer
        report('searching','普通关优先用 5 格搜索完整路线')
        # Each native candidate must clear every remaining tile and the tray.
        # Partial .best artifacts are deliberately never read.
        budgets=[min(2,seconds*.15),min(5,seconds*.3),seconds]
        specs=[(5,'tight-top',.8,7919),(6,'tight-top',.8,7919),(6,'level-top',.6,7927)]
        tail=output/('boundary_'+str(len(prefix))+'.json');atomic_json(tail,{'sequence':prefix,'map_sha256':digest})
        for k,(cap,mode,first,seed) in enumerate(specs):
            if stopped():raise ValueError('已停止完整路线规划')
            left=seconds-(time.monotonic()-start)
            if left<=0:break
            budget=min(budgets[k],left)
            native=output/f'full_{len(prefix):04}_{time.time_ns()}_{cap}.json';progress=Path(str(native)+'.progress.json')
            cmd=[str(ROOT/'FastOriginalSolver.exe'),str(map_path),str(native),mode,str(first),str(budget),str(seed),
                '1500000',str(progress),str(tail),'512',str(cap)]
            report('searching','优先用 5 格验证完整路线' if cap==5 else '5 格尚未找到完整路线，放宽到 6 格搜索',cap)
            killed=False;began=time.monotonic()
            with Path(str(native)+'.log').open('w',encoding='utf-8') as log:
                child=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
                try:
                    while child.poll() is None:
                        if stopped() or time.monotonic()-began>budget+3:
                            killed=True;child.terminate();break
                        p=read(progress)
                        report('searching','正在验证普通关的整条通关路线',cap,deepest=p.get('deepest',len(prefix)))
                        time.sleep(.08)
                    child.wait(timeout=3)
                finally:
                    if child.poll() is None:child.kill();child.wait(timeout=3)
            if stopped():raise ValueError('已停止完整路线规划')
            p=read(progress);attempts.append({'capacity':cap,'state':'limited' if killed else p.get('state','error'),
                'elapsed_seconds':round(time.monotonic()-began,3),'nodes':p.get('nodes',0)})
            if killed:continue
            if native.exists() and child.returncode==0:
                if hashlib.sha256(map_path.read_bytes()).hexdigest()!=digest:raise ValueError('规划时地图已改变')
                candidate=read(native);candidate.update(planning_capacity=cap,
                    source_label='普通关完整路线；原项目策略，优先 5 格，必要时 6 格')
                # Validate before replacing the durable cache. A cancelled or
                # invalid candidate must not overwrite a usable old answer.
                if candidate['sequence'][:len(prefix)]!=list(prefix):raise ValueError('候选没有衔接确认记录')
                answer=export(map_path,candidate,output/'verified_candidate')
                check_answer(answer,data,digest,prefix)
                if stopped():raise ValueError('已停止完整路线规划')
                atomic_json(cache,answer)
                report('solved','完整通关路线已独立验证，逐步执行并保留后续路线',cap,deepest=len(cards),cache_reused=False)
                return cache,answer
            if p.get('state')=='exhausted' and cap==6:
                audit=audit_static_boundary(data,prefix,seconds=min(2,max(.01,seconds-(time.monotonic()-start))))
                audit.update(map_sha256=digest);atomic_json(output/'boundary_audit.json',audit)
                if audit['state']=='dead_prefix':
                    report('no_continuation','独立穷举确认当前前缀已是死局；等待新局自动使用完整路线',cap)
                    raise ValueError('当前点击前缀已是无道具死局；请重新开局，新版会保留整条通关路线')
                if audit['state']=='complete':
                    candidate={'sequence':list(prefix)+audit['sequence'],'planning_capacity':6,
                        'engine':'independent_static_boundary','source_label':'普通关独立完整续解'}
                    answer=export(map_path,candidate,output/'verified_candidate');check_answer(answer,data,digest,prefix)
                    if stopped():raise ValueError('已停止完整路线规划')
                    atomic_json(cache,answer);report('solved','完整续解已独立验证',cap,deepest=len(cards))
                    return cache,answer
            if child.returncode not in (0,2):raise RuntimeError('完整路线求解进程异常，见 '+str(native)+'.log')
        report('limited','预算内尚未找到完整路线，棋盘保持不动',6)
        raise ValueError('预算内尚未找到普通关完整路线；已保留棋盘，增加搜索时间可重试')
    except Exception as exc:
        if stopped():report('cancelled','已暂停完整路线规划')
        elif read(status_path).get('state') not in ('limited','no_continuation'):
            report('error',str(exc))
        raise
