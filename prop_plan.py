"""Bounded one-move-out planning. Produces reviewed instructions, never clicks."""
import hashlib
import json
from pathlib import Path
import subprocess
import time

from color_rules import initial_cards
from solve_verified_map import atomic_json
from prop_search import board,synthetic,candidates,replay,MOVE

ROOT=Path(__file__).resolve().parent
KIND='manual_move_out_v1'

def read(path):
    try:return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):return {}

def verify(data,actions,prefix=()):
    """Independent identity/geometry replay; not the searcher's Game model."""
    cards,rules=initial_cards(data)
    if rules:raise ValueError('移出规划仅支持普通牌')
    if len({c['id'] for c in cards})!=len(cards):raise ValueError('地图牌 ID 重复')
    by_id={c['id']:c for c in cards};left=set(by_id);tray=[];side=[];used=False;trace=[];peak=0;board_sequence=[]
    for step,action in enumerate(actions,1):
        before=list(tray);before_side=list(side)
        if isinstance(action,dict):kind=action['kind'];id=action.get('id')
        else:kind='move_out' if action==MOVE else 'return' if action in side else 'board';id=None if action==MOVE else action
        if kind=='move_out':
            if used or len(tray)<3 or side:raise ValueError('移出必须只有一次且槽内至少三张')
            side=tray[:3];tray=tray[3:];used=True
        elif kind in ('board','return'):
            if id not in by_id:raise ValueError('未知牌 ID')
            if kind=='return':
                if id not in side:raise ValueError('点回的牌不在移出区')
                side.remove(id)
            else:
                if id not in left:raise ValueError('重复点棋盘牌')
                c=by_id[id]
                if any(by_id[p]['layer']>c['layer'] and abs(by_id[p]['rolNum']-c['rolNum'])<8 and abs(by_id[p]['rowNum']-c['rowNum'])<8 for p in left):raise ValueError('点击被遮挡的牌')
                left.remove(id);board_sequence.append(id)
            t=by_id[id]['type'];same=[j for j,x in enumerate(tray) if by_id[x]['type']==t]
            tray.insert(same[-1]+1 if same else len(tray),id)
            if len(same)==2:tray=[x for x in tray if by_id[x]['type']!=t]
            if len(tray)>=7:raise ValueError('消除后牌槽满七张')
        else:raise ValueError('不支持的道具操作')
        peak=max(peak,len(tray));trace.append({'operation':step,'kind':kind,'id':id,'type':by_id[id]['type'] if id else None,
            'tray_before':before,'tray_after':list(tray),'side_before':before_side,'side_after':list(side)})
    if left or tray or side or not used:raise ValueError('道具路线未完整清空棋盘、牌槽和移出区')
    if board_sequence[:len(prefix)]!=list(prefix) or any(t['kind']!='board' for t in trace[:len(prefix)]):raise ValueError('道具路线与已确认点击不一致')
    return trace,peak

def publish(map_path,data,actions,prefix,folder,origin):
    trace,peak=verify(data,actions,prefix);types,covers=board(data)
    legacy=[MOVE if t['kind']=='move_out' else t['id'] for t in trace]
    check=replay(types,covers,legacy)
    if not check.won() or check.peak!=peak:raise ValueError('两套道具回放结果不一致')
    move=next(t for t in trace if t['kind']=='move_out');by_id={c['id']:c for c in initial_cards(data)[0]}
    payload={'execution_kind':KIND,'engine':'one_move_out_v1','map_sha256':hashlib.sha256(map_path.read_bytes()).hexdigest(),
        'confirmed_prefix':list(prefix),'actions':[{'kind':t['kind'],**({'id':t['id']} if t['id'] else {})} for t in trace],
        'prop_validated':True,'validated':False,'executable':False,'desktop_clicks_enabled':False,
        'move_out_uses':1,'board_clicks':sum(t['kind']=='board' for t in trace),'return_clicks':sum(t['kind']=='return' for t in trace),
        'operation_count':len(trace),'peak_tray_after_elimination':peak,'move_after_board_clicks':move['operation']-1,
        'tray_types_before_move':[by_id[x]['type'] for x in move['tray_before']],
        'moved_types':[by_id[x]['type'] for x in move['side_after']],
        'return_operations':[{'operation':t['operation'],'id':t['id'],'type':t['type']} for t in trace if t['kind']=='return'],
        'normal_solvability':'unknown','rule_assumption':'同型后插入，否则最右；移出最左三张，之后逐张点回；消除后少于七张',
        'prop_desktop_execution_available':True,
        'requires':['人工移出并确认广告结束','核对移出前槽内顺序','移出区自动识别成功后续接'],
        'source':origin}
    folder.mkdir(parents=True,exist_ok=True)
    atomic_json(folder/'solution.json',payload);atomic_json(folder/'replay_trace.json',trace)
    lines=['operation\tkind\tcard_id\ttype\tlayer\tx\ty\ttray_after\tside_after']
    for t in trace:
        c=by_id.get(t['id'],{});lines.append('\t'.join(str(x) for x in (t['operation'],t['kind'],t['id'] or '',t['type'] or '',c.get('layer',''),c.get('rolNum',''),c.get('rowNum',''),','.join(t['tray_after']),','.join(t['side_after']))))
    (folder/'route_with_move_out.tsv').write_text('\n'.join(lines)+'\n',encoding='utf-8-sig')
    (folder/'使用移出路线说明.md').write_text(f"# 一次移出路线已通过两套离线回放\n\n共 {len(trace)} 个操作。正常规则可解性仍为未知。\n\n在助手主界面点击 **执行到手动移出**，程序自动执行前 {payload['move_after_board_clicks']} 次棋盘点击后暂停。\n\n请到游戏里手动使用一次“移出”并完成广告；回到助手点击 **已完成移出，核对并继续**。程序仅在棋盘、牌槽和移出区匹配后继续；剩余棋盘点击和点回操作自动执行。\n\n移出前槽内牌型：{payload['tray_types_before_move']}；移出牌型：{payload['moved_types']}。\n\n点回操作：{payload['return_operations']}。\n\n移出区无法识别或广告未关闭时保持等待，不发送下一次点击。\n",encoding='utf-8')
    return folder/'solution.json',payload

def cached(map_path,data,prefix,folder,legacy=None):
    digest=hashlib.sha256(map_path.read_bytes()).hexdigest()
    for path in (folder/'solution.json',legacy):
        if path is None:continue
        answer=read(path)
        if not answer or ('map_sha256' in answer and answer['map_sha256']!=digest):continue
        try:return publish(map_path,data,answer['actions'],prefix,folder,'verified_cache' if path==folder/'solution.json' else 'verified_legacy_import')
        except (ValueError,KeyError,TypeError):continue
    return None

def search(map_path,data,prefix,folder,routes,seconds=20,cancel=lambda:False,notify=lambda **kw:None):
    """Two workers, hard wall-clock deadline, memory stop and finally reaping."""
    types,covers=board(data);deadline=time.monotonic()+max(0,seconds);exe=ROOT/'PropFastSolver.exe'
    points=list(candidates(types,covers,routes,60,3));points=[p for p in points if p[:len(prefix)]==list(prefix) and len(p)>=len(prefix)]
    active=[];owned=[];serial=0;found=None
    from search_resources import ResourceSampler,available_memory
    sampler=ResourceSampler();memory_each=max(64*1024**2,min(512*1024**2,(available_memory()-2*1024**3)//2))
    states=max(50000,min(3000000,int((memory_each-32*1024**2)/160)))
    try:
        while (points or active) and time.monotonic()<deadline and exe.exists():
            if cancel():raise ValueError('已暂停移出搜索')
            while points and len(active)<2 and time.monotonic()<deadline:
                p=points.pop(0);built=synthetic(data,types,covers,p)
                if not built:continue
                mp,checkpoint,moved=built;serial+=1;job=folder/f'job_{time.time_ns()}_{serial}';job.mkdir(parents=True,exist_ok=True)
                atomic_json(job/'map.json',mp);atomic_json(job/'prefix.json',{'sequence':checkpoint})
                log=(job/'process.log').open('w',encoding='utf-8')
                try:process=subprocess.Popen([str(exe),str(job/'map.json'),str(job/'answer.json'),'tight-top',str((.8,.7,0)[serial%3]),str(min(3,max(.01,deadline-time.monotonic()))),str(serial*7919+1),str(states),str(job/'progress.json'),str(job/'prefix.json'),'512','6'],stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS)
                except Exception:log.close();raise
                item=(process,job,log,p,checkpoint);active.append(item);owned.append(item)
            for item in active[:]:
                process,job,log,p,checkpoint=item
                if process.poll() is None:
                    if sampler.process_memory(process)[1]>memory_each:process.terminate()
                    continue
                active.remove(item)
                native=read(job/'answer.json').get('sequence',[])
                if process.returncode==0 and native[:len(checkpoint)]==checkpoint:
                    rest=native[len(checkpoint):]
                    if any(id.startswith('P~') for id in rest):continue
                    actions=p+[MOVE]+[id[2:] if id.startswith('B~') else id for id in rest]
                    try:found=publish(map_path,data,actions,prefix,folder,'native_one_move_out_search')
                    except (ValueError,KeyError,TypeError):continue
                    break
            notify(phase='prop_search',workers=len(active),prop_attempts=serial)
            if found:break
            time.sleep(.05)
    finally:
        for process,_,_,_,_ in owned:
            if process.poll() is None:process.terminate()
        for process,_,log,_,_ in owned:
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)
            log.close()
    if cancel():raise ValueError('已暂停移出搜索')
    return found
