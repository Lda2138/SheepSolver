"""Energy/alien boards on the ordinary static pipeline, with one move-out.

The current board (types fixed, batteries ignored) goes to static_plan: native
search, CP-SAT and the one-move-out fallback. Every result is then replayed
from the original map under the exact energy rules. When the alien fires the
remaining game is ordinary (all charges are cleared and cannot refill), so
planning restarts once from the post-alien board. A route without move-out is
executed in rolling segments as before; a move-out route is reviewed
instructions only, never clicks.
"""
import hashlib
import json
from pathlib import Path
import time

from color_rules import initial_cards
from energy_rules import EnergyBoard, replay_actions
from solve_verified_map import atomic_json

ENGINE='static_constraint_v1'
ROUTE_ENGINE='energy_static_v1'
KIND='manual_move_out_v1'


def read(path):
    try:return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):return {}


def apply(board,action):
    if action['kind']=='board':return board.move(action['id'])
    if action['kind']=='move_out':return board.move_out()
    if action['kind']=='return':return board.return_card(action['id'])
    raise ValueError('Unknown energy action')


def snapshot(cards,board):
    """Ordinary map of the live state. Tray and move-out cards become isolated
    top tiles; the tray ones are the planning prefix, in visual order."""
    top=max(c['layer'] for c in cards)+1;by_id={c['id']:c for c in cards};levels={}
    for c in cards:
        if c['id'] in board.remaining:
            levels.setdefault(str(c['layer']),[]).append({**c,'type':board.types[c['id']],'metaType':0,'metaData':0})
    alias={};prefix=[]
    for k,(kind,real) in enumerate([('tray',i) for i in board.slot]+[('return',i) for i in board.side]):
        rol,row=10000+k*8,10000;id=f'{top}-{rol}-{row}';alias[id]=(kind,real)
        levels.setdefault(str(top),[]).append({'id':id,'type':by_id[real]['type'],'rolNum':rol,'rowNum':row,
            'layerNum':top,'layer':top,'moldType':1,'metaType':0,'metaData':0})
        if kind=='tray':prefix.append(id)
    return {'levelData':levels,'_energy_planning_snapshot':True},alias,prefix


def tail_actions(answer,start,alias,moved_out):
    """Static result after the snapshot prefix, in real card IDs."""
    if answer.get('execution_kind')==KIND:
        steps=answer['actions'][len(start):]
        if any(a['kind']=='move_out' for a in steps) and moved_out:raise ValueError('移出已用过')
    else:steps=[{'kind':'board','id':id} for id in answer['sequence'][len(start):]]
    out=[]
    for a in steps:
        if a['kind']=='move_out':out.append({'kind':'move_out'});continue
        kind,real=alias.get(a['id'],('board',a['id']))
        # A static "return" of a tray card, or a pick of an isolated move-out
        # tile, are both clicks on the move-out area.
        out.append({'kind':'return' if a['kind']=='return' or kind=='return' else 'board','id':real})
    return out


def plan_route(data,prefix,output,seconds,cancel,performance,report):
    import static_plan
    cards,rules=initial_cards(data);deadline=time.monotonic()+seconds
    actions=[{'kind':'board','id':id} for id in prefix]
    for round in range(3):
        board,_=replay_actions(cards,rules,actions,complete=False)
        if not (board.remaining or board.tray or board.side):return actions
        snap,alias,start=snapshot(cards,board);mp=output/f'snapshot_{len(actions):04}.json';atomic_json(mp,snap)
        left=deadline-time.monotonic()
        if left<5:raise ValueError('本轮预算耗尽，飞碟后的残局尚未求解；可解性未知')
        try:_,answer=static_plan.plan_current(mp,start,output,left,cancel,performance)
        except ValueError:
            status=read(output/'search_status.json')
            if status.get('state')=='no_continuation':
                # Static infeasibility ignores the alien; it is no verdict here.
                status.update(state='limited',message='忽略飞碟的静态模型无解；电量关可解性未知')
                atomic_json(output/'search_status.json',status)
                raise ValueError('本轮静态模型无解（未计飞碟），可解性未知') from None
            raise
        tail=tail_actions(answer,start,alias,board.moved_out)
        for action in tail:
            event=apply(board,action);actions.append(action)
            if event.get('alien'):
                report('searching','飞碟已按真实种子结算，从飞碟后的棋盘继续规划',phase='alien_replan')
                break
        else:
            if board.remaining or board.tray or board.side:raise ValueError('静态路线在电量规则下未清空棋盘')
            return actions
    raise ValueError('本轮电量路线未收敛；可解性未知')


def publish(map_path,data,actions,prefix,folder,origin):
    cards,rules=initial_cards(data);by_id={c['id']:c for c in cards}
    if [a.get('id') for a in actions[:len(prefix)]]!=list(prefix) or any(a['kind']!='board' for a in actions[:len(prefix)]):
        raise ValueError('道具路线与已确认点击不一致')
    if sum(a['kind']=='move_out' for a in actions)!=1:raise ValueError('道具路线必须恰好使用一次移出')
    board=EnergyBoard(cards,rules);trace=[];peak=0
    for step,action in enumerate(actions,1):
        before=list(board.slot);event=apply(board,action);peak=max(peak,len(board.tray))
        trace.append({'operation':step,'kind':action['kind'],'id':action.get('id'),
            'type':by_id[action['id']]['type'] if action.get('id') else None,
            'tray_before':before,'tray_after':list(board.slot),'side_after':list(board.side),
            'energy_count':board.collected,'alien':bool(event.get('alien')),'auto_removed':event.get('auto_removed',[])})
    if board.remaining or board.tray or board.side:raise ValueError('道具路线未完整清空棋盘、牌槽和移出区')
    move=next(t for t in trace if t['kind']=='move_out')
    payload={'execution_kind':KIND,'engine':'energy_one_move_out_v1','rules':rules,
        'map_sha256':hashlib.sha256(map_path.read_bytes()).hexdigest(),'confirmed_prefix':list(prefix),
        'actions':[{'kind':t['kind'],**({'id':t['id']} if t['id'] else {})} for t in trace],
        'prop_validated':True,'validated':False,'executable':False,'desktop_clicks_enabled':False,
        'move_out_uses':1,'board_clicks':sum(t['kind']=='board' for t in trace),'return_clicks':sum(t['kind']=='return' for t in trace),
        'operation_count':len(trace),'peak_tray_after_elimination':peak,'move_after_board_clicks':move['operation']-1,
        'tray_types_before_move':[by_id[x]['type'] for x in move['tray_before']],
        'moved_types':[by_id[x]['type'] for x in move['side_after']],
        'return_operations':[{'operation':t['operation'],'id':t['id'],'type':t['type']} for t in trace if t['kind']=='return'],
        'alien_operations':[{'operation':t['operation'],'auto_removed':t['auto_removed']} for t in trace if t['alien']],
        'normal_solvability':'unknown',
        'rule_assumption':'电量与飞碟按真实种子；同型后插入，否则最右；移出最左至多三张，之后逐张点回（点回也扣露出电池的电量）；飞碟缺牌时从移出区补；消除后少于七张',
        'requires':['人工移出并确认广告结束','核对移出前槽内顺序','校准移出区坐标并接入道具执行器'],
        'source':origin}
    folder.mkdir(parents=True,exist_ok=True)
    atomic_json(folder/'solution.json',payload);atomic_json(folder/'replay_trace.json',trace)
    lines=['operation\tkind\tcard_id\ttype\tlayer\tx\ty\talien_removed\ttray_after\tside_after']
    for t in trace:
        c=by_id.get(t['id'],{})
        lines.append('\t'.join(str(x) for x in (t['operation'],t['kind'],t['id'] or '',t['type'] or '',c.get('layer',''),
            c.get('rolNum',''),c.get('rowNum',''),','.join(t['auto_removed']),','.join(t['tray_after']),','.join(t['side_after']))))
    (folder/'route_with_move_out.tsv').write_text('\n'.join(lines)+'\n',encoding='utf-8-sig')
    aliens=''.join(f"\n- 第 {a['operation']} 个操作后飞碟自动移除 {len(a['auto_removed'])} 张：{a['auto_removed']}" for a in payload['alien_operations'])
    (folder/'使用移出路线说明.md').write_text(f"# 电量关一次移出路线已按真实规则回放\n\n共 {len(trace)} 个操作。正常规则可解性仍为未知。\n\n"
        f"前 {payload['move_after_board_clicks']} 个操作后使用移出；此时槽内牌型为 {payload['tray_types_before_move']}，移出牌型为 {payload['moved_types']}。\n\n"
        f"点回操作：{payload['return_operations']}。\n\n飞碟：{aliens or '路线中不触发'}\n\n"
        "这是道具规划结果，当前执行器不支持自动移出和点回。实际操作前需核对牌槽顺序、完成广告并校准上方区域；飞碟动画结束后再继续。\n",encoding='utf-8')
    return folder/'solution.json',payload


def cached(map_path,data,prefix,folder):
    answer=read(folder/'solution.json')
    if not answer or answer.get('map_sha256')!=hashlib.sha256(map_path.read_bytes()).hexdigest():return None
    try:return publish(map_path,data,answer['actions'],prefix,folder,'verified_cache')
    except (ValueError,KeyError,TypeError):return None


def plan_current(map_path,prefix,output,seconds=60,cancel=None,performance='max'):
    from rolling_play import make_segment
    map_path=Path(map_path);output=Path(output);prefix=list(prefix);output.mkdir(parents=True,exist_ok=True)
    raw=map_path.read_bytes();digest=hashlib.sha256(raw).hexdigest();data=json.loads(raw.decode('utf-8-sig'))
    cards,rules=initial_cards(data)
    if (rules or {}).get('kind')!='energy_alien_v1':raise ValueError('电量规划只用于电量与飞碟关')
    started=time.monotonic();prop_folder=output/'energy_prop'
    def stopped():return bool(cancel and cancel())
    def report(state,message,**extra):
        status=read(output/'search_status.json')
        status.update(engine=ENGINE,rules='energy_alien_v1',map_sha256=digest,confirmed_prefix=prefix,
            state=state,message=message,elapsed_seconds=round(time.monotonic()-started,2),**extra)
        if state!='searching':status['workers']=0
        atomic_json(output/'search_status.json',status)
    def publish_prop(result):
        path,payload=result
        report('prop_ready',f"电量关一次移出路线已验证：{payload['operation_count']} 个操作；执行前需人工移出和移出区校准",
            prop_solution_path=str(path),prop_operation_count=payload['operation_count'],normal_solvability='unknown',executable=False)
        return path,payload
    def segment(route):
        answer=make_segment(data,digest,prefix,route,exact=True)
        answer.update(planning_engine=ROUTE_ENGINE,source_label='电量关完整路线（真实种子回放，飞碟后重算）；分段执行')
        path=output/f'execution_{len(prefix):04}.json';atomic_json(path,answer)
        return path,answer
    cache=read(output/'energy_route.json')
    if cache.get('map_sha256')==digest and cache.get('engine')==ROUTE_ENGINE:
        base=cache['confirmed_prefix'];route=cache['route']
        if prefix[:len(base)]==base and prefix[len(base):]==route[:len(prefix)-len(base)] and len(prefix)-len(base)<len(route):
            report('segment_ready','电量关完整路线未变，继续执行',cache_reused=True)
            return segment(route[len(prefix)-len(base):])
    existing=cached(map_path,data,prefix,prop_folder)
    if existing:return publish_prop(existing)
    report('searching','电量关：按当前牌面调用原生搜索、整局约束与一次移出，再按真实电量与飞碟回放',workers=0)
    try:actions=plan_route(data,prefix,output,float(seconds),stopped,performance,report)
    except Exception as exc:
        if stopped():report('cancelled','已暂停电量规划')
        elif read(output/'search_status.json').get('state') not in ('limited','no_continuation'):
            report('limited' if '本轮' in str(exc) else 'error',str(exc))
        raise
    if stopped():raise ValueError('已暂停电量规划')
    if hashlib.sha256(map_path.read_bytes()).hexdigest()!=digest:raise ValueError('地图已改变，丢弃旧路线')
    if any(a['kind']!='board' for a in actions):
        return publish_prop(publish(map_path,data,actions,prefix,prop_folder,'energy_static_one_move_out'))
    route=[a['id'] for a in actions[len(prefix):]]
    replay_actions(cards,rules,actions)
    atomic_json(output/'energy_route.json',{'engine':ROUTE_ENGINE,'map_sha256':digest,'confirmed_prefix':prefix,'route':route})
    report('segment_ready','电量关完整路线已按真实电量与飞碟回放，分段执行',cache_reused=False)
    return segment(route)
