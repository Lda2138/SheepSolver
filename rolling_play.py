"""Plan a short real-rule segment. A segment is never a full solution."""
import copy
import hashlib
import json
from pathlib import Path
import time

import subprocess
from color_rules import initial_cards
from frozen_replan import LiveBoard
from solve_verified_map import atomic_json


def replay_prefix(data,sequence):
    board=LiveBoard(data)
    for id in sequence:board.play_to_refresh([id])
    return board


def make_segment(data,map_sha256,prefix,route,planning=None,exact=False):
    board=replay_prefix(data,prefix);start=len(prefix);picked=[]
    if not route:raise ValueError('没有新的合法步骤，停止本段执行')
    if exact:
        # A route already replayed to the end under the exact rules runs
        # through, splitting only after an alien so the board is re-read.
        for id in route:
            event=board.play_to_refresh([id]);picked.append(id)
            if event.get('alien'):break
        route=[]
    # Cap commitment at the first real elimination/alien event, or at three
    # picks when no elimination is found. Never commit a stale static tail.
    for id in ([] if exact else route[:6]):
        event=board.play_to_refresh([id]);picked.append(id)
        if event['elimination'] or event.get('alien') or len(picked)>=3:break
    sequence=list(prefix)+picked
    complete=not board.state.remaining and not board.tray
    payload={'execution_kind':'rolling_segment_v1','segment_validated':True,'validated':False,
        'complete':complete,'source_label':'边算边玩：本段合法，后续能否通关尚未确定',
        'map_sha256':map_sha256,'cards':board.cards,'card_count':len(board.cards),'rules':board.rules,
        'sequence':sequence,'start_step':start,'sequence_types':[],
        'remaining_count':len(board.state.remaining),'expected_tray':list(board.tray)}
    if exact:payload['exact_route']=True
    replay=replay_prefix(data,[])
    for id in sequence:
        payload['sequence_types'].append(replay.state.types[id]);replay.play_to_refresh([id])
    if board.energy:payload['energy_events']=copy.deepcopy(board.state.events)
    if planning:
        payload.update(tray_capacity=planning['tray_capacity'],lookahead_sequence=list(route),
            lookahead_target=planning['horizon_target'],lookahead_events=planning['lookahead_events'],
            capacity_attempts=planning['attempts'])
    validate_segment(payload)
    return payload


def validate_segment(segment,data=None):
    if segment.get('execution_kind')!='rolling_segment_v1' or segment.get('validated') or not segment.get('segment_validated'):
        raise ValueError('分段计划不能作为完整答案使用')
    if data is not None:
        cards,rules=initial_cards(data)
        if cards!=segment['cards'] or rules!=segment.get('rules'):raise ValueError('分段计划与原地图规则不一致')
    sequence=segment['sequence'];start=segment['start_step']
    if not isinstance(start,int) or not 0<=start<len(sequence) or len(sequence)-start>(len(sequence) if segment.get('exact_route') else 6):
        raise ValueError('分段执行范围无效')
    if len(segment['sequence_types'])!=len(sequence):raise ValueError('分段牌型记录不完整')
    # Replay every prior confirmed pick from the original seed. State checking
    # is cheap replay, with no re-expansion of already committed search paths.
    from color_rules import ColorState
    from energy_rules import EnergyBoard
    energy=(segment.get('rules') or {}).get('kind')=='energy_alien_v1'
    runtime=EnergyBoard(segment['cards'],segment['rules']) if energy else ColorState(segment['cards'],segment.get('rules'))
    tray=[]
    parents={c['id']:{p['id'] for p in segment['cards'] if p['layer']>c['layer'] and
        abs(p['rolNum']-c['rolNum'])<8 and abs(p['rowNum']-c['rowNum'])<8} for c in segment['cards']}
    peak=0
    for i,id in enumerate(sequence):
        if id not in runtime.remaining or parents[id]&runtime.remaining:raise ValueError('分段包含不可点击的牌')
        if segment['sequence_types'][i]!=runtime.types[id]:raise ValueError('分段预测牌型不一致')
        if energy:runtime.move(id);tray=list(runtime.tray)
        else:
            t=runtime.types[id];tray.append(t);hit=tray.count(t)==3
            if hit:tray=[v for v in tray if v!=t]
            runtime.remove(id,hit)
        peak=max(peak,len(tray))
        if len(tray)>=7:raise ValueError('分段计划会填满牌槽')
    if bool(segment.get('complete'))!=bool(not runtime.remaining and not tray):raise ValueError('分段完成标记不一致')
    if segment.get('expected_tray')!=tray or segment.get('remaining_count')!=len(runtime.remaining):
        raise ValueError('分段终点状态不一致')
    if (segment.get('rules') or {}).get('kind')=='energy_alien_v1' and segment.get('energy_events')!=runtime.events:
        raise ValueError('分段飞碟事件不一致')
    if 'tray_capacity' in segment:validate_lookahead(segment)
    return peak


def validate_lookahead(segment):
    """Python replay independently checks the native planner's future route."""
    from color_rules import ColorState
    from energy_rules import EnergyBoard
    cap=segment['tray_capacity'];target=segment['lookahead_target']
    if cap not in (5,6) or not isinstance(target,int) or not 3<=target<=8:
        raise ValueError('牌槽容量或前瞻次数无效')
    route=segment['lookahead_sequence'];sequence=segment['sequence'];start=segment['start_step']
    committed=sequence[start:]
    if len(route)>64 or route[:len(committed)]!=committed:raise ValueError('前瞻路线没有衔接本段')
    energy=(segment.get('rules') or {}).get('kind')=='energy_alien_v1'
    runtime=EnergyBoard(segment['cards'],segment['rules']) if energy else ColorState(segment['cards'],segment.get('rules'))
    tray=[];events=0;future=0;peak=0
    parents={c['id']:{p['id'] for p in segment['cards'] if p['layer']>c['layer'] and
        abs(p['rolNum']-c['rolNum'])<8 and abs(p['rowNum']-c['rowNum'])<8} for c in segment['cards']}
    for k,id in enumerate(sequence[:start]+route):
        if id not in runtime.remaining or parents[id]&runtime.remaining:raise ValueError('前瞻包含不可点击的牌')
        t=runtime.types[id];next_tray=tray+[t];crush=next_tray.count(t)==3
        if crush:next_tray=[v for v in next_tray if v!=t]
        # Check before the alien, which cannot rescue a full stable tray.
        if k>=start:
            peak=max(peak,len(next_tray))
            if len(next_tray)>cap:raise ValueError('前瞻超出当前牌槽容量')
        if energy:
            event=runtime.move(id);tray=list(runtime.tray);hit=event['crush'] or event['alien']
        else:
            if len(next_tray)>=7:raise ValueError('前瞻填满牌槽')
            tray=next_tray;runtime.remove(id,crush);hit=crush
        if k>=start and hit:
            events+=1
            if k-start>=len(committed):future+=1
    complete=not runtime.remaining and not tray
    if events!=segment['lookahead_events'] or (not complete and (events<target or future<1)):
        raise ValueError('前瞻缺少本段之后的消除证据')
    return {'peak_slots':peak,'events':events,'future_events':future,'complete':complete}


def native_real_plan(board,prefix,folder,*,seconds=1,state_limit=32768,horizon_target=3,
                     notify=None,cancel=None,fixed_capacity=None):
    folder.mkdir(parents=True,exist_ok=True)
    request=folder/'request.json';result=folder/'candidate.json';progress=folder/'native_progress.json'
    payload={'cards':board.cards,'rules':board.rules,'prefix':prefix,'seconds':seconds,
             'state_limit':state_limit,'horizon_target':horizon_target}
    if fixed_capacity is not None:payload['fixed_capacity']=fixed_capacity
    atomic_json(request,payload);started=time.monotonic();stopped=False;hard_limit=False
    for path in (result,progress):path.unlink(missing_ok=True)
    root=Path(__file__).resolve().parent
    with (folder/'process.log').open('w',encoding='utf-8') as log:
        child=subprocess.Popen([str(root/'RollingPlanner.exe'),str(request),str(result),str(progress)],
            cwd=root,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            while child.poll() is None:
                if cancel and cancel():stopped=True;child.terminate();break
                if time.monotonic()-started>seconds+3:hard_limit=True;child.terminate();break
                if notify:
                    try:info=json.loads(progress.read_text(encoding='utf-8-sig'))
                    except (OSError,ValueError):info={}
                    notify(planner=info)
                time.sleep(.08)
            child.wait(timeout=3)
        finally:
            if child.poll() is None:child.kill();child.wait(timeout=3)
    if stopped or cancel and cancel():return {'state':'cancelled'},None
    if hard_limit:return {'state':'limited','reason':'process_deadline'},None
    if child.returncode not in (0,2):raise RuntimeError('短程规划进程失败，见 '+str(folder/'process.log'))
    info=json.loads(result.read_text(encoding='utf-8-sig'))
    return info,info['sequence'] or None


def plan_segment(map_path,prefix,output,seconds=1,notify=None,cancel=None,planner=native_real_plan,
                 state_limit=32768,horizon_target=3):
    raw=map_path.read_bytes();data=json.loads(raw.decode('utf-8-sig'));digest=hashlib.sha256(raw).hexdigest()
    board=replay_prefix(data,prefix);output.mkdir(parents=True,exist_ok=True)
    status=output/'search_status.json';start=time.monotonic()
    identity={'engine':'rolling_capacity_lookahead','rules':(board.rules or {}).get('kind','standard'),
        'total':len(board.cards),'committed_steps':len(prefix),'workers':1,'target_workers':1,
        'execution_kind':'rolling_segment_v1'}
    latest={'tray_capacity':5,'horizon_target':horizon_target,'lookahead_events':0}
    def update(**kw):
        latest.update(kw.get('planner',{}))
        cap=latest.get('tray_capacity',5)
        atomic_json(status,dict(identity,state='planning_segment',elapsed_seconds=round(time.monotonic()-start,2),
            deepest=len(board.cards)-len(board.state.remaining),nodes_per_second=latest.get('nodes_per_second',0),
            tray_capacity=cap,horizon_target=horizon_target,lookahead_events=latest.get('lookahead_events',0),
            lookahead_depth=latest.get('lookahead_depth',0),capacity_attempts=latest.get('attempts',[]),
            message=('优先用 5 格，验证后续消除' if cap==5 else '5 格本轮未找到前瞻路线，放宽到 6 格继续验证')))
        if notify:notify(**kw)
    update();info={}
    try:
        info,route=planner(board,prefix,output/f'plan_{len(prefix):04}_{time.time_ns()}',
            seconds=seconds,state_limit=state_limit,horizon_target=horizon_target,notify=update,cancel=cancel)
        if cancel and cancel() or info.get('state')=='cancelled':raise ValueError('已停止本段规划')
        if not route:
            raise ValueError('当前已确认局面没有下一次消除路线（无道具）' if info.get('state')=='no_next_elimination'
                else '当前容量约束下没有足够的后续消除路线，已停止托管并保留棋盘' if info.get('state')=='lookahead_exhausted'
                else '本轮尚未找到可验证的后续消除路线；保留棋盘继续搜索')
        segment=make_segment(data,digest,prefix,route,planning=info)
        if cancel and cancel():raise ValueError('已停止本段规划')
        if hashlib.sha256(map_path.read_bytes()).hexdigest()!=digest:raise ValueError('规划时地图已改变，丢弃旧计划')
        path=output/f'segment_{len(prefix):04}.json';atomic_json(path,segment)
        evidence=validate_lookahead(segment)
        atomic_json(status,dict(identity,state='segment_ready',elapsed_seconds=round(time.monotonic()-start,2),
            deepest=len(board.cards)-len(board.state.remaining),nodes_per_second=0,workers=0,
            segment_steps=len(segment['sequence'])-len(prefix),tray_capacity=info['tray_capacity'],
            capacity_attempts=info['attempts'],lookahead_events=evidence['events'],horizon_target=horizon_target,
            future_events=evidence['future_events'],message='已验证后续消除路线，核对画面后执行短段'))
        return path,segment
    except Exception as exc:
        cancelled=bool(cancel and cancel()) or info.get('state')=='cancelled'
        state='cancelled' if cancelled else info['state'] if info.get('state') in ('no_next_elimination','lookahead_exhausted','limited') else 'error'
        atomic_json(status,dict(identity,state=state,workers=0,elapsed_seconds=round(time.monotonic()-start,2),
            deepest=len(board.cards)-len(board.state.remaining),nodes_per_second=0,
            tray_capacity=info.get('tray_capacity',latest.get('tray_capacity',5)),
            lookahead_events=latest.get('lookahead_events',0),horizon_target=horizon_target,
            capacity_attempts=info.get('attempts',[]),message=str(exc)))
        raise
