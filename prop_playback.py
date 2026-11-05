"""Explicitly started, checkpointed execution of a verified one-move-out route.

Board picks and returns are automatic. Move-out and ads are always manual;
the transition is accepted only from observed board, tray and side-row evidence.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

import numpy as np
from PIL import Image

import app_bridge as bridge
from board_align import exposed,face_offsets,fit,check_silhouette
from color_rules import initial_cards
from prop_plan import verify
from solve_verified_map import atomic_json

ROOT=Path(__file__).resolve().parent
KIND='manual_move_out_live_v1'


def load(plan_path):
    plan=bridge.read(Path(plan_path));mp=Path(plan['source_map_path']);data=bridge.read(mp)
    answer=bridge.read(Path(plan['prop_solution_path']))
    digest=hashlib.sha256(mp.read_bytes()).hexdigest()
    if plan.get('execution_kind')!=KIND or digest!=plan['map_sha256'] or answer.get('map_sha256')!=digest or not answer.get('prop_validated'):
        raise ValueError('移出路线与当前地图不一致')
    cards,rules=initial_cards(data)
    if rules:raise ValueError('移出执行暂不支持特殊牌')
    trace,_=verify(data,answer['actions'],answer.get('confirmed_prefix',[]))
    return plan,cards,trace


def state_at(cards,trace,n):
    if not 0<=n<=len(trace):raise ValueError('道具执行断点超出路线范围')
    picked={t['id'] for t in trace[:n] if t['kind']=='board'}
    return [c for c in cards if c['id'] not in picked],(trace[n-1]['tray_after'] if n else []),(trace[n-1]['side_after'] if n else [])


def side_centers(image,remaining,plan,count):
    """Find isolated pale card faces outside remaining map and the seven slots."""
    from collections import deque
    a=np.asarray(image,dtype=np.int16);mask=(a[:,:,0]>205)&(a[:,:,1]>205)&(a[:,:,2]>145)
    mask[:118]=False;mask[486:]=False;mask[:,:20]=False;mask[:,316:]=False
    sx=plan['calibration'].get('face_scale_x',1);sy=plan['calibration'].get('face_scale_y',1)
    for c in remaining:
        x,y=plan['positions'][c['id']];mask[max(0,y-round(22*sy)):min(622,y+round(22*sy)),max(0,x-round(21*sx)):min(336,x+round(21*sx))]=False
    centers=[]
    for yy,xx in zip(*np.where(mask)):
        if not mask[yy,xx]:continue
        q=deque([(int(xx),int(yy))]);mask[yy,xx]=False;x0=x1=int(xx);y0=y1=int(yy);pixels=0
        while q:
            x,y=q.popleft();pixels+=1;x0=min(x0,x);x1=max(x1,x);y0=min(y0,y);y1=max(y1,y)
            for nx,ny in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
                if 20<=nx<316 and 118<=ny<486 and mask[ny,nx]:mask[ny,nx]=False;q.append((nx,ny))
        if pixels>=100 and 18<=x1-x0<=48 and 20<=y1-y0<=48:centers.append([round((x0+x1)/2),round((y0+y1)/2)])
    centers.sort()
    if len(centers)!=count or count and max(p[1] for p in centers)-min(p[1] for p in centers)>12:
        raise ValueError(f'移出区尚未匹配：预期 {count} 张，识别到 {len(centers)} 张；请完成移出并关闭广告后重试')
    return centers


def check_state(plan,cards,trace,n,image_path,play,learn=False):
    image=Image.open(image_path).convert('RGB');array=np.asarray(image,dtype=np.int16)
    if image.size!=(336,622):raise ValueError('道具核对截图尺寸不一致')
    remaining,tray,side=state_at(cards,trace,n);by_id={c['id']:c for c in cards}
    templates=bridge.type_templates(plan,play);new={}
    for c in exposed(remaining):
        x,y=plan['positions'][c['id']]
        pale=sum(array[y+dy,x+dx,0]>205 and array[y+dy,x+dx,1]>205 and array[y+dy,x+dx,2]>145 for dx,dy in face_offsets(plan['calibration']))
        if pale<5:raise ValueError('棋盘尚有广告、特效或动画遮挡')
        known=templates.get(c['type'],[])
        if known and bridge.pattern_score(array,(x,y),known)>75:raise ValueError('当前可见牌型与移出路线不一致')
        if not known:
            patch=array[y-18:y+18,x-17:x+17];templates.setdefault(c['type'],[]).append(patch);new[c['type']]=patch
    centers=side_centers(image,remaining,plan,len(side))
    for id,point in zip(side,centers):
        score=bridge.pattern_score(array,point,templates.get(by_id[id]['type'],[]))
        if score>75:raise ValueError('移出区的牌型或从左到右顺序尚未匹配')
    bridge.verify_tray_image(image,[by_id[id]['type'] for id in tray],templates)
    # Remove only observed side faces from the silhouette test; unknown extra
    # board cards must still be rejected.
    cleaned=image.copy()
    from PIL import ImageDraw
    draw=ImageDraw.Draw(cleaned)
    for x,y in centers:draw.rectangle((x-23,y-24,x+23,y+24),fill=(193,255,137))
    if remaining:
        check=play/'prop_silhouette.png';cleaned.save(check);check_silhouette(check,remaining,plan['calibration'])
    elif n<len(trace) and np.count_nonzero((array[118:466,20:316,0]>205)&(array[118:466,20:316,1]>205)&(array[118:466,20:316,2]>145))>len(side)*1600+300:
        raise ValueError('清盘终点仍有未确认的牌')
    if learn:
        directory=play/'templates';directory.mkdir(exist_ok=True)
        atomic_json(directory/'context.json',{'map_sha256':plan['map_sha256']})
        for t,patch in new.items():Image.fromarray(patch.astype(np.uint8)).save(directory/f'type_{t}.png')
    return dict(zip(side,centers))


def prepare(context,folder,mp,data,level,mode,source):
    answer=bridge.read(source);trace,_=verify(data,answer['actions'],answer.get('confirmed_prefix',[]))
    if answer['map_sha256']!=hashlib.sha256(mp.read_bytes()).hexdigest():raise ValueError('移出方案不属于当前地图')
    raw=bridge.capture_snapshot();cards,_=initial_cards(data);n=context.get('start_step',0) if context.get('resume') else 0
    prefix=[t['id'] for t in trace[:n] if t['kind']=='board']
    if context.get('resume'):
        checked=bridge.checked_resume(folder,level,raw)
        old=bridge.read(Path(checked['plan']['solution_path']))
        if old['sequence'][:n]!=prefix or len(prefix)!=n:raise ValueError('移出路线没有衔接已确认前缀')
        calibration=checked['plan']['calibration'];reference=Path(checked['plan']['reference_path'])
    else:
        if bridge.tray_count(Image.open(raw).convert('RGB')):raise ValueError('新道具路线要求空牌槽或已确认的断点')
        calibration,_=fit(raw,cards);reference=raw
    play=folder/f'level_{level}_play';play.mkdir(exist_ok=True)
    if (play/'plan.json').exists():
        archive=play/f'before_prop_{time.time_ns()}';archive.mkdir()
        import shutil
        for name in ('plan.json','playback_status.json','actions.jsonl','last_verified.png'):
            if (play/name).exists():shutil.copyfile(play/name,archive/name)
    # Keep a dedicated reference; capture_snapshot's default path is overwritten.
    ref=play/'prop_reference.png';Image.open(reference).convert('RGB').save(ref)
    positions,anchors=bridge.geometry(cards,calibration)
    plan={'execution_kind':KIND,'prop_solution_path':str(source),'source_map_path':str(mp),'map_sha256':answer['map_sha256'],
          'reference_path':str(ref),'positions':positions,'anchors':anchors,'calibration':calibration,
          'session':folder.name,'mode':mode,'level':level,'start_operation':n,
          'capture_status_path':str(bridge.OUT/'status.json'),'service_lease_path':str(bridge.OUT/'service_lease.json')}
    check_state(plan,cards,trace,n,raw,play)
    atomic_json(play/'plan.json',plan);atomic_json(play/'prop_state.json',{'next_operation':n,'input_pending':False})
    Image.open(raw).convert('RGB').save(play/'last_verified.png')
    (play/'control.json').unlink(missing_ok=True)
    return {'plan_path':str(play/'plan.json'),'preview_path':str(play/'last_verified.png'),'prop_execution':True,
            'message':'已核对移出路线；自动执行到移出位置后暂停，手动使用一次道具，再核对并续接'}


def inspect_resume(folder,level,image_path):
    play=folder/f'level_{level}_play';plan,cards,trace=load(play/'plan.json');state=bridge.read(play/'prop_state.json');n=state['next_operation']
    from capture_service import alive
    status=bridge.read(play/'playback_status.json') if (play/'playback_status.json').exists() else {}
    if status.get('pid') and alive(status['pid']):raise ValueError('道具执行器仍在核对，请等待')
    if state.get('input_pending') or n<len(trace) and trace[n]['kind']=='move_out':
        try:
            check_state(plan,cards,trace,n+1,image_path,play)
            recovered={**trace[n],'verification':'reconciled_board_tray_and_side_row','time':datetime.now(timezone.utc).isoformat()}
            with (play/'prop_actions.jsonl').open('a',encoding='utf-8') as stream:stream.write(json.dumps(recovered,ensure_ascii=False)+'\n')
            n+=1
        except ValueError:check_state(plan,cards,trace,n,image_path,play)
        atomic_json(play/'prop_state.json',{'next_operation':n,'input_pending':False})
    else:check_state(plan,cards,trace,n,image_path,play)
    if n==len(trace):return {'level':level,'prop_finished':True,'prop_execution':True,'image':str(image_path)}
    return {'level':level,'resume':True,'prop_execution':True,'plan_path':str(play/'plan.json'),
            'preview_path':str(image_path),'message':f'道具路线已核对，从第 {n+1} 个操作续接','image':str(image_path)}


def run(plan_path):
    plan_path=Path(plan_path).resolve();play=plan_path.parent;plan,cards,trace=load(plan_path)
    by_id={c['id']:c for c in cards};state=bridge.read(play/'prop_state.json');n=state['next_operation'];pending=bool(state.get('input_pending'))
    paused=False;stop=False;last_command='';last_frame=None;stable=0;sent_at=0;error='';control=play/'control.json';wait_started=time.monotonic()
    def save(label,message):
        _,tray,side=state_at(cards,trace,n)
        atomic_json(play/'playback_status.json',{'pid':os.getpid(),'execution_kind':KIND,'next_step':n,'next_operation':n,'total':len(trace),
            'input_pending':pending,'expected_tray':[by_id[id]['type'] for id in tray],
            'state':label,'message':message,'time':datetime.now(timezone.utc).isoformat(),
            'next_operation_kind':trace[n]['kind'] if n<len(trace) else 'finished'})
    def commit(verification):
        nonlocal n,pending,stable,last_frame,wait_started
        t=trace[n]
        with (play/'prop_actions.jsonl').open('a',encoding='utf-8') as stream:stream.write(json.dumps({**t,'verification':verification,'time':datetime.now(timezone.utc).isoformat()},ensure_ascii=False)+'\n')
        n+=1;pending=False;stable=0;last_frame=None;wait_started=time.monotonic()
        atomic_json(play/'prop_state.json',{'next_operation':n,'input_pending':False})
        Image.open(play/'prop_now.png').convert('RGB').save(play/'last_verified.png')
    def poll_control():
        nonlocal paused,stop,last_command,stable,last_frame,wait_started
        if not control.exists():return
        command=bridge.read(control)
        if command.get('id')==last_command:return
        last_command=command.get('id');action=command.get('action')
        if action=='pause':paused=True;wait_started=time.monotonic()
        elif action in ('resume','confirm_move_out'):paused=False;stable=0;last_frame=None;wait_started=time.monotonic()
        elif action=='stop':stop=True
    try:
        if pending:raise ValueError('道具执行断点还有未确认输入，必须先重新核对')
        while n<len(trace):
            bridge.current(plan['mode'],plan['level'],plan['session'])
            poll_control()
            if stop and not pending:break
            if paused:save('paused','道具路线已暂停，进度保留');time.sleep(.15);continue
            target_n=n+1 if pending or trace[n]['kind']=='move_out' else n
            label='waiting_manual_move' if trace[n]['kind']=='move_out' and not pending else 'verifying' if pending else 'running'
            save(label,'请在游戏内使用“移出”并完成广告；完成后点击“已完成移出，核对并继续”' if label=='waiting_manual_move' else f'正在核对道具路线第 {n+1}/{len(trace)} 个操作')
            try:
                raw=bridge.capture_snapshot(play/'prop_now.png')
                array=np.asarray(Image.open(raw).convert('RGB'),dtype=np.int16)
                same=last_frame is not None and float(np.abs(array[118:535,20:316]-last_frame[118:535,20:316]).sum(axis=2).mean())<3
                stable=stable+1 if same else 0;last_frame=array
                # Only a settled frame is worth the full check, except that a
                # screen which never settles must still reach the timeouts below.
                if stable<1:
                    now=time.monotonic()
                    if pending and now-sent_at>8 or not pending and label!='waiting_manual_move' and now-wait_started>10:check_state(plan,cards,trace,target_n,raw,play)
                    time.sleep(.05);continue
                centers=check_state(plan,cards,trace,target_n,raw,play,learn=True)
            except (ValueError,OSError) as exc:
                stable=0;last_frame=None;error=str(exc)
                if pending and time.monotonic()-sent_at>8:raise ValueError('点击结果尚未确认，已停止后续输入；'+error)
                if not pending and label!='waiting_manual_move' and time.monotonic()-wait_started>10:raise ValueError('道具路线起点尚未核对通过，已停止输入；'+error)
                save(label,('等待手动移出和广告结束；' if label=='waiting_manual_move' else '等待画面稳定；')+error)
                time.sleep(.1);continue
            if pending or trace[n]['kind']=='move_out':
                commit('observed_board_tray_and_side_row')
                poll_control()
                # This stable, fully checked frame is the next operation's start
                # state, so a following board pick needs no second verification.
                if n>=len(trace) or stop or paused or trace[n]['kind']=='move_out':continue
            action=trace[n];point=plan['positions'][action['id']] if action['kind']=='board' else centers[action['id']]
            # Reserve the operation before invoking input, so a crash can only
            # leave an explicit pending checkpoint, never an unrecorded replay.
            pending=True;sent_at=time.monotonic();atomic_json(play/'prop_state.json',{'next_operation':n,'input_pending':True,'pending_point':point,'pending_kind':action['kind']})
            request={'id':uuid.uuid4().hex,'plan_path':str(plan_path),'before_path':str(raw),'point':point,'operation':n}
            atomic_json(play/'prop_click_request.json',request)
            result=subprocess.run([str(ROOT/'SheepClicker.Diagnostics.exe'),'--prop-click',str(play/'prop_click_request.json')],cwd=ROOT,
                                  capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW,timeout=10)
            if result.returncode:raise ValueError(result.stderr.decode('utf-8-sig',errors='replace').strip() or '道具点击未发送，需重新核对')
            stable=0;last_frame=None;save('input_sent',f'已发送第 {n+1} 个操作，核对棋盘、牌槽与移出区');time.sleep(.1)
        save('sequence_finished' if n==len(trace) else 'paused_at_limit','道具路线已全部核对完成' if n==len(trace) else '已停止，断点保留');return 0
    except Exception as exc:
        save('stopped',str(exc));print(str(exc),flush=True);return 1


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('plan',type=Path);args=parser.parse_args();raise SystemExit(run(args.plan))
