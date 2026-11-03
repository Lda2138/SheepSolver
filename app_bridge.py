"""Application commands; only explicit play launches the independent clicker."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from board_align import fit,mark,exposed,face_offsets,energy_face
from desktop_assistant import validate, validate_solution, validate_execution
from color_rules import compatibility, initial_cards, solution_state

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'output/auto_capture'
NO_WINDOW=subprocess.CREATE_NO_WINDOW


class AwaitBoard(ValueError):
    def __init__(self,state,message):
        super().__init__(message);self.state=state


def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))


def current(mode,level,session):
    from capture_service import alive
    status=read(OUT/'status.json');lease=read(OUT/'service_lease.json')
    if not lease.get('running') or not alive(lease['pid']):raise ValueError('请先启用抓取，再进入新一局')
    if not status.get('complete') or status.get('session')!=session:raise ValueError('当前局完整地图尚未就绪，或已经重新开局')
    actual='world' if '/world/' in status.get('source_path','') else 'daily' if status.get('source_path')=='/sheep/v1/game/map_info_ex' else 'other'
    if actual!=mode:raise ValueError('当前抓取模式与界面选择不一致，请切换模式')
    folder=OUT/'sessions'/session
    mp=folder/f'map_{level}.json'
    data=read(mp)
    if data.get('_acquisition',{}).get('session')!=session:raise ValueError('地图局号不一致')
    if compatibility(data) not in ('standard','color_shuffle','energy_alien') or data.get('_resumed_state'):raise ValueError('该关含特殊规则或已进行的状态，旧求解器暂不支持')
    return folder,mp,data


def solution_for(folder,level):
    return folder/f'level_{level}_solution'


def validate_map_solution(map_path, solution):
    if solution.get('execution_kind')=='manual_move_out_v1':raise ValueError('移出路线需人工道具操作，不能作为普通点击计划执行')
    data=read(map_path);cards,rules=initial_cards(data)
    if solution['cards']!=cards or solution.get('rules')!=rules:
        raise ValueError('答案牌型或变色规则与当前原始地图不一致')
    return validate_solution(solution)


def capture_snapshot(destination=None):
    args=['--capture-to',str(destination)] if destination is not None else ['--capture']
    result=subprocess.run([str(ROOT/'SheepClicker.Diagnostics.exe'),*args],cwd=ROOT,capture_output=True,creationflags=NO_WINDOW,timeout=10)
    if result.returncode:raise ValueError(result.stderr.decode('utf-8-sig',errors='replace').strip() or '暂时无法取得游戏截图，请保持小程序窗口可见')
    return Path(destination) if destination is not None else ROOT/'output/程序标准化截图.png'


def tray_count(image):
    count=0;gap=False
    for k in range(7):
        light=sum(r>185 and g>180 and b>115 for y in range(492,527,3) for dx in range(-15,16,3)
                  for r,g,b in [image.getpixel((40+42*k+dx,y))])
        if light>22:
            if gap:raise ValueError('牌槽动画尚未结束')
            count+=1
        else:gap=True
    return count


def pattern_score(image,point,templates):
    import numpy as np
    x,y=point;best=1000.0
    for template in templates:
        sample=template[7:30:2,6:29:2]
        for dy in range(-4,5):
            for dx in range(-3,4):
                patch=image[y+dy-11:y+dy+12:2,x+dx-11:x+dx+12:2]
                if patch.shape==sample.shape:best=min(best,float(np.abs(patch-sample).sum(axis=2).mean()))
    return best


def visual_tray(solution,steps):
    """Tray types as drawn: a tile goes after the last of its type, triples
    vanish, an alien empties the tray. solution_state keeps pick order."""
    cards={c['id']:c for c in solution['cards']};types=solution.get('sequence_types') or [cards[i]['type'] for i in solution['sequence']]
    events=solution.get('energy_events') or [];tray=[]
    for k,t in enumerate(types[:steps]):
        same=[j for j,v in enumerate(tray) if v==t]
        tray.insert(same[-1]+1 if same else len(tray),t)
        if len(same)==2:tray=[v for v in tray if v!=t]
        if k<len(events) and events[k].get('alien'):tray=[]
    return tray


def tray_pattern_score(image,point,previous,old_point):
    """Compare slot icons with a 3x3 low-pass for subpixel redraw differences.

    Keep the strict score gate; only soften antialiased edges, not whole icons.
    Board geometry, slot count and surviving slot order are checked separately.
    """
    import numpy as np
    def smooth(patch):
        patch=patch.astype(np.float64)
        return sum(patch[dy:dy+36,dx:dx+34] for dy in range(3) for dx in range(3))/9
    x,y=point;old_x,old_y=old_point
    # The search in pattern_score needs a margin around the slot patch.
    region=image[y-23:y+24,x-21:x+22]
    filtered=sum(region[dy:dy+45,dx:dx+41].astype(np.float64) for dy in range(3) for dx in range(3))/9
    template=smooth(previous[old_y-19:old_y+19,old_x-18:old_x+18])
    return pattern_score(filtered,(20,22),[template])


def type_templates(plan,play):
    import numpy as np
    from PIL import Image
    reference=np.asarray(Image.open(plan['reference_path']).convert('RGB'),dtype=np.int16)
    templates={}
    for anchor in plan['anchors']:
        x,y=anchor['center'];templates.setdefault(anchor['type'],[]).append(reference[y-18:y+18,x-17:x+17])
    directory=play/'templates';context=read(directory/'context.json') if (directory/'context.json').exists() else {}
    if context.get('map_sha256')==plan['map_sha256']:
        for file in directory.glob('type_*.png'):
            templates.setdefault(int(file.stem[5:]),[]).append(np.asarray(Image.open(file).convert('RGB'),dtype=np.int16))
    return templates


def check_resume_visuals(now,last,solution,plan,n,expected,play,tray_indices=None,allow_new_types=False):
    """Temporary toasts may vanish: verify every exposed tile and tray icon."""
    import numpy as np
    image=np.asarray(now,dtype=np.int16);previous=np.asarray(last,dtype=np.int16);templates=type_templates(plan,play)
    runtime,_=solution_state(solution,n)
    ids=set(runtime.remaining);remaining=[{**c,'type':runtime.types[c['id']]} for c in solution['cards'] if c['id'] in ids]
    def face(array,point):
        x,y=point;return sum(array[y+dy,x+dx,0]>205 and array[y+dy,x+dx,1]>205 and array[y+dy,x+dx,2]>145
            for dx,dy in face_offsets(plan['calibration']))>=5
    for card in exposed(remaining):
        point=plan['positions'][card['id']];t=card['type']
        battery=(solution.get('rules') or {}).get('kind')=='energy_alien_v1' and t==solution['rules']['gold_type']
        if not face(image,point) and not (battery and energy_face(image,point)):raise ValueError('临时提示或动画遮住了牌面，等待画面恢复')
        if not templates.get(t) and face(previous,point):
            x,y=point;templates.setdefault(t,[]).append(previous[y-18:y+18,x-17:x+17])
        # A tile uncovered by the click being reconciled may show a type no
        # template knows yet; its face was already required above.
        if allow_new_types and not templates.get(t):continue
        score=pattern_score(image,point,templates.get(t,[]))
        if score>60:
            x,y=point;unchanged=face(previous,point) and pattern_score(image,point,[previous[y-18:y+18,x-17:x+17]])<=8
            alternatives=min((pattern_score(image,point,values) for other,values in templates.items() if other!=t),default=1000)
            if not unchanged and not (score<=90 and alternatives>=score+25) and not (battery and energy_face(image,point)):
                raise ValueError('当前棋盘的牌型与确认记录不一致，无法自动续接')
    for k,t in enumerate(expected):
        # Tray icons use a different scale from board icons. Compare each
        # slot with the already verified tray, allowing animation subpixels.
        x,y=43+42*k,511
        if tray_indices is not None and tray_indices[k] is None:continue
        old_x=43+42*(tray_indices[k] if tray_indices is not None else k)
        if tray_pattern_score(image,(x,y),previous,(old_x,y))>8:raise ValueError('当前牌槽图案与确认记录不一致，无法自动续接')
    from board_align import check_silhouette
    check_silhouette(play/'resume_check.png',remaining,plan['calibration'])


def verify_tray_image(now,expected,templates):
    from PIL import Image
    import numpy as np
    if tray_count(now)!=len(expected):raise ValueError('牌槽数量尚未匹配，等待飞行和三消完成')
    scaled={}
    for t,patches in templates.items():
        scaled[t]=[]
        for patch in patches:
            for scale in (1,1.1,1.2,1.3,1.4):
                w,h=round(34*scale),round(36*scale)
                im=Image.fromarray(patch.astype(np.uint8)).resize((w,h),Image.Resampling.BICUBIC)
                scaled[t].append(np.asarray(im.crop(((w-34)//2,(h-36)//2,(w-34)//2+34,(h-36)//2+36)),dtype=np.int16))
    image=np.asarray(now,dtype=np.int16)
    for k,t in enumerate(expected):
        point=(43+42*k,511);score=pattern_score(image,point,scaled.get(t,[]))
        other=min((pattern_score(image,point,values) for typ,values in scaled.items() if typ!=t),default=1000)
        if score>75 or other<score+15:raise ValueError(f'第 {k+1} 格牌型尚未匹配')


def verify_full_board(play,image_path,n,tray_only=False):
    """Independent visible-board, silhouette, tray count and tray-type check.

    Uses map-bound templates, not the possibly animating preceding fast frame.
    No input and no confirmed-action mutation.
    """
    from PIL import Image
    import numpy as np
    play=Path(play);plan=read(play/'plan.json');solution=read(Path(plan['solution_path']))
    validate_execution(solution)
    if solution['map_sha256']!=plan['map_sha256']:raise ValueError('全盘核对：答案与地图不匹配')
    if not 0<=n<=len(solution['sequence']):raise ValueError('全盘核对步数超出答案范围')
    now=Image.open(image_path).convert('RGB')
    if now.size!=(336,622):raise ValueError('全盘核对截图尺寸不一致')
    expected=visual_tray(solution,n)
    if tray_count(now)!=len(expected):raise ValueError('全盘核对：牌槽数量尚未匹配')
    # Each exposed card must match its predicted type and geometry. Do not
    # accept an unchanged but incorrect frame as the fallback reference.
    if not tray_only:
        blank=Image.new('RGB',now.size);now.save(play/'resume_check.png')
        check_resume_visuals(now,blank,solution,plan,n,[],play,allow_new_types=True)
    verify_tray_image(now,expected,type_templates(plan,play))
    runtime,_=solution_state(solution,n)
    return {'step':n,'exposed_cards_checked':len(exposed([c for c in solution['cards'] if c['id'] in runtime.remaining])),
            'tray_count':len(expected),'verified':True}


def reconcile_pending(folder,level,image_path):
    """Confirm one already sent triple by independent board and slot evidence.

    Never sends input. Unsupported ambiguity stays blocked rather than replayed.
    """
    from PIL import Image
    import numpy as np
    from capture_service import alive
    from datetime import datetime,timezone
    from solve_verified_map import atomic_json
    play=folder/f'level_{level}_play';status=read(play/'playback_status.json')
    if not status.get('input_pending'):return False
    if status.get('pid') and alive(status['pid']):raise ValueError('点击器仍在确认结果，请等待')
    plan=read(play/'plan.json');solution=read(Path(plan['solution_path']));validate_execution(solution)
    if plan['session']!=folder.name or plan['map_sha256']!=hashlib.sha256((folder/f'map_{level}.json').read_bytes()).hexdigest():raise ValueError('未确认点击与当前局地图不一致')
    n=status['next_step'];sequence=solution['sequence'];cards={c['id']:c for c in solution['cards']}
    actions=[json.loads(line) for line in (play/'actions.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    if not 0<n<len(sequence) or len(actions)!=n or any(a['step']!=i+1 or a['card_id']!=sequence[i] for i,a in enumerate(actions)):raise ValueError('未确认点击的前缀记录不完整')
    before=solution_state(solution,n)[1]
    t=solution.get('sequence_types',[cards[v]['type'] for v in sequence])[n]
    alien=bool(solution.get('energy_events') and solution['energy_events'][n]['alien'])
    after=solution_state(solution,n+1)[1]
    if status['expected_tray']!=after:raise ValueError('未确认点击的牌槽预测不一致')
    # Map every slot after the click to its slot before it, in drawn order;
    # the newly inserted tile has no earlier slot.
    shown=visual_tray(solution,n);shown_after=visual_tray(solution,n+1)
    if alien:indices=[]
    elif shown.count(t)==2:indices=[k for k,v in enumerate(shown) if v!=t]
    else:
        same=[k for k,v in enumerate(shown) if v==t];j=same[-1]+1 if same else len(shown)
        indices=[None if k==j else k if k<j else k-1 for k in range(len(shown_after))]
    now=Image.open(image_path).convert('RGB');last=Image.open(play/'last_verified.png').convert('RGB')
    x,y=plan['positions'][sequence[n]]
    old_array=np.asarray(last,dtype=np.int16);now_array=np.asarray(now,dtype=np.int16)
    board_delta=float(np.abs(now_array[118:466,20:316]-old_array[118:466,20:316]).sum(axis=2).mean())
    tray_delta=float(np.abs(now_array[493:533,22:308]-old_array[493:533,22:308]).sum(axis=2).mean())
    target_delta=float(np.abs(now_array[y-14:y+14,x-14:x+14]-old_array[y-14:y+14,x-14:x+14]).sum(axis=2).mean())
    try:age=(datetime.now(timezone.utc)-datetime.fromisoformat(status['time'])).total_seconds()
    except (ValueError,KeyError):age=0
    fast=status.get('verification_mode') in ('ultra','safe_ultra')
    no_effect=board_delta<=2 and tray_delta<=2 and target_delta<=2
    if fast and age>=5 and len(before)!=len(after) and tray_count(now)==len(before):
        # The last fast frame may still contain tray animation, including the
        # previous card flying over this target, so no pixel delta against it
        # is required. A fresh full match of the pre-click state (target card
        # included) with the pre-click tray count proves the input was lost.
        try:verify_full_board(play,image_path,n);no_effect=True
        except ValueError:no_effect=False
    if age>=5 and tray_count(now)==len(before) and no_effect:
        # A dead sender, a timeout, and an unchanged target/board/tray prove
        # this input did not alter the game. Restore the pre-click boundary;
        # do not advance the confirmed log or count this as a second click.
        recovery_log=play/'input_recovery.jsonl'
        recovered=[json.loads(line) for line in recovery_log.read_text(encoding='utf-8-sig').splitlines() if line.strip()] if recovery_log.exists() else []
        if sum(item.get('step')==n+1 and item.get('result')=='no_effect' for item in recovered)>=3:
            raise AwaitBoard('waiting_input','同一步后台点击连续未生效，已停止重试；地图与种子已就绪')
        now.save(play/'resume_check.png');now.save(play/f'input_no_effect_{n+1}.png')
        if fast:verify_full_board(play,image_path,n)
        else:check_resume_visuals(now,last,solution,plan,n,shown,play)
        with (play/'input_recovery.jsonl').open('a',encoding='utf-8') as out:
            out.write(json.dumps({'time':datetime.now(timezone.utc).isoformat(),'step':n+1,
                'card_id':sequence[n],'result':'no_effect','board_delta':board_delta,
                'tray_delta':tray_delta,'target_delta':target_delta},ensure_ascii=False)+'\n')
        status.update(input_pending=False,expected_tray=before,state='paused_at_verified',
            message='上次点击未生效，已按未变化的棋盘和牌槽恢复，继续时重试该步')
        atomic_json(play/'playback_status.json',status);return True
    # Evidence for any click: the tray holds exactly the predicted count, the
    # target changed, every surviving slot and exposed tile matches.
    if tray_count(now)!=len(after):raise ValueError('已发送的点击尚未显示预期牌槽结果')
    x,y=plan['positions'][sequence[n]]
    difference=float(np.abs(np.asarray(now,dtype=np.int16)[y-14:y+14:2,x-14:x+14:2]-np.asarray(last,dtype=np.int16)[y-14:y+14:2,x-14:x+14:2]).sum(axis=2).mean())
    if difference<8:raise ValueError('已发送的点击没有得到牌面变化证据')
    now.save(play/'resume_check.png')
    if status.get('verification_mode') in ('ultra','safe_ultra'):verify_full_board(play,image_path,n+1)
    else:check_resume_visuals(now,last,solution,plan,n+1,shown_after,play,indices,allow_new_types=True)
    # The old tray slots have disappeared as expected, every shifted slot and
    # exposed tile matches, and the sole pending target changed. Commit once.
    last.save(play/f'pending_before_{n}.png');now.save(play/f'pending_confirmed_{n+1}.png')
    action={'step':n+1,'card_id':sequence[n],'type':t,'center':[x,y],'tray_count':len(after),
            'target_difference':round(difference,2),'time':datetime.now(timezone.utc).isoformat(),
            'mode':'background','verification':'reconciled_tray_slots_target_and_remaining_map'}
    with (play/'actions.jsonl').open('a',encoding='utf-8') as out:out.write(json.dumps(action,ensure_ascii=False)+'\n')
    now.save(play/'last_verified.png')
    status.update(next_step=n+1,input_pending=False,state='paused_at_verified',message='已发送的点击已通过牌面与牌槽复核确认，无需重放')
    atomic_json(play/'playback_status.json',status);return True


def checked_resume(folder,level,image_path):
    """Resume only a fully verified prefix whose board still matches reality."""
    from PIL import Image
    import numpy as np
    play=folder/f'level_{level}_play';plan=read(play/'plan.json');status=read(play/'playback_status.json')
    solution=read(Path(plan['solution_path']));validate_execution(solution)
    if plan['session']!=folder.name or plan['map_sha256']!=hashlib.sha256((folder/f'map_{level}.json').read_bytes()).hexdigest():
        raise ValueError('断点与当前局地图不一致')
    plan=recalibrate(play,plan,solution)
    n=status['next_step']
    rolling=solution.get('execution_kind')=='rolling_segment_v1'
    if not 0<n<=len(solution['sequence']) or n==len(solution['sequence']) and not rolling:raise ValueError('当前没有可续接的断点')
    if status.get('input_pending'):raise ValueError('上一次点击尚未确认，不能自动重放；请重新开局')
    actions=[json.loads(line) for line in (play/'actions.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    if len(actions)!=n or any(action['step']!=i+1 or action['card_id']!=solution['sequence'][i] for i,action in enumerate(actions)):
        raise ValueError('点击记录不完整，不能自动续接')
    expected=solution_state(solution,n)[1]
    if expected!=status['expected_tray']:raise ValueError('断点有未确认的点击，不能自动续接')
    now=Image.open(image_path).convert('RGB');last=Image.open(play/'last_verified.png').convert('RGB')
    if now.size!=(336,622) or last.size!=now.size:raise ValueError('棋盘截图尺寸不一致')
    difference=np.abs(np.asarray(now,dtype=np.int16)[118:466:2,25:311:2]-np.asarray(last,dtype=np.int16)[118:466:2,25:311:2]).sum(axis=2).mean()
    if tray_count(now)!=len(expected):raise ValueError('当前牌槽数量与确认记录不一致，无法自动续接')
    if status.get('verification_mode') in ('ultra','safe_ultra'):
        verify_full_board(play,image_path,n)
    elif difference>5:
        check_path=play/'resume_check.png';now.save(check_path)
        check_resume_visuals(now,last,solution,plan,n,visual_tray(solution,n),play)
    return {'level':level,'start_step':n,'total':len(solution['sequence']),'plan':plan,'play':play,
            'rolling':rolling,'segment_end':rolling and n==len(solution['sequence'])}


def prepare_resume(folder,level,image_path):
    from PIL import Image
    context=checked_resume(folder,level,image_path);play=context['play'];plan=context['plan']
    Image.open(image_path).convert('RGB').save(play/'resume_reference.png')
    plan.update(start_step=context['start_step'],initial_reference_path=str(play/'resume_reference.png'),dynamic_anchors=True,adaptive_verify=True)
    (play/'plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    solution=read(Path(plan['solution_path']));left=solution['sequence'][context['start_step']:]
    runtime,_=solution_state(solution,context['start_step'])
    remaining=[c for c in solution['cards'] if c['id'] in runtime.remaining]
    mark(image_path,remaining,plan['calibration'],play/'aligned.png',left[0])
    result={'plan_path':str(play/'plan.json'),'preview_path':str(play/'aligned.png'),'start_step':context['start_step'],
            'card_count':context['total'],'calibration':plan['calibration'],'message':f'棋盘与记录已核对，可从第 {context["start_step"]+1} 步继续'}
    (play/'prepared.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def fresh_board_candidates(folder,image_path,map_count,require_empty=True,diagnostics=None):
    """Recognize complete empty-tray boards independently of old playback."""
    from PIL import Image
    if require_empty and tray_count(Image.open(image_path).convert('RGB')):return []
    candidates=[]
    for level in range(map_count):
        data=read(folder/f'map_{level}.json')
        cards=[{**c,'layer':int(k)} for k,v in data['levelData'].items() for c in v]
        energy=(data['goldBlockData']['blockList'][0]['val'],) if compatibility(data)=='energy_alien' else ()
        try:
            from board_align import check_silhouette
            calibration,score=fit(image_path,cards,energy);check_silhouette(image_path,cards,calibration)
            candidates.append({'level':level,'calibration':calibration,'score':score,'resume':False,
                'start_step':0,'total':len(cards),'supported':compatibility(data) in ('standard','color_shuffle','energy_alien') and not data.get('_resumed_state')})
        except ValueError as error:
            if diagnostics is not None:diagnostics.append(f'第 {level+1} 关：{error}')
    return sorted(candidates,key=lambda item:item['score'],reverse=True)


def matches_fresh_reference(folder,image_path,candidate):
    """A restart also needs all exposed icons to match a known initial board."""
    from PIL import Image
    import numpy as np
    play=folder/f'level_{candidate["level"]}_play'
    if not (play/'plan.json').exists():return False
    plan=read(play/'plan.json');data=read(folder/f'map_{candidate["level"]}.json')
    if plan.get('session')!=folder.name or plan.get('map_sha256')!=hashlib.sha256((folder/f'map_{candidate["level"]}.json').read_bytes()).hexdigest():return False
    path=Path(plan.get('reference_path',''))
    if not path.is_file():return False
    old=np.asarray(Image.open(path).convert('RGB'),dtype=np.int16)
    now=np.asarray(Image.open(image_path).convert('RGB'),dtype=np.int16);templates={}
    for anchor in plan.get('anchors',[]):
        x,y=anchor['center'];templates.setdefault(anchor['type'],[]).append(old[y-18:y+18,x-17:x+17])
    cards,_=initial_cards(data);cal=candidate['calibration']
    for card in exposed(cards):
        point=(round(cal['x_origin']+card['rolNum']*cal['x_unit']),round(cal['y_origin']+card['rowNum']*cal['y_unit']))
        score=pattern_score(now,point,templates.get(card['type'],[]))
        if score>60:return False
    return True


def inspect_board(folder,image_path,map_count):
    """Choose a new board or a resume; one level's history cannot veto another."""
    from PIL import Image
    im=Image.open(image_path).convert('RGB');resume_errors=[];historical=[]
    diagnostics=[]
    candidates=fresh_board_candidates(folder,image_path,map_count,diagnostics=diagnostics)
    for level in range(map_count):
        prop_plan=folder/f'level_{level}_play/plan.json'
        if prop_plan.exists() and read(prop_plan).get('execution_kind')=='manual_move_out_live_v1':
            try:
                import prop_playback
                return prop_playback.inspect_resume(folder,level,image_path)
            except (ValueError,OSError,KeyError) as exc:
                resume_errors.append(str(exc));historical.append(level);continue
        status_path=folder/f'level_{level}_play/playback_status.json'
        if status_path.exists():
            status=read(status_path)
            if status.get('next_step',0)>0:historical.append(level)
            if 0<status['next_step']<status['total'] or status.get('state')=='segment_finished':
                try:
                    if status.get('input_pending'):reconcile_pending(folder,level,image_path)
                    return {**checked_resume(folder,level,image_path),'resume':True}
                except AwaitBoard:raise
                except (ValueError,OSError,KeyError) as e:resume_errors.append(str(e))
    if not candidates:
        if resume_errors:
            if tray_count(im):
                raise AwaitBoard('waiting_resume','地图和种子已就绪，正在核对未完成点击；'+resume_errors[0])
            raise AwaitBoard('waiting_new_map','当前棋盘与旧记录不匹配，等待本局完整地图和种子；尚未开始求解')
        if tray_count(im) and fresh_board_candidates(folder,image_path,map_count,require_empty=False):
            raise ValueError('棋盘已经点过牌，且没有可核对的点击记录；请从新一局开始')
        from board_align import visible_face_count
        if diagnostics and visible_face_count(image_path)>=4:
            raise AwaitBoard('waiting_new_map','当前棋盘与缓存地图不匹配，等待当前模式的完整开局数据；旧路线和后续关计算已停用')
        detail='；'.join(diagnostics)
        raise AwaitBoard('waiting_board','等待游戏棋盘完成对齐；地图已保留'+('；'+detail if detail else ''))
    # The loading screen has a pale tray background which resembles seven
    # occupied slots. Require a real board before interpreting the tray.
    if tray_count(im):raise ValueError('棋盘已经点过牌，且没有可核对的点击记录；请从新一局开始')
    if len(candidates)>1 and candidates[0]['score']-candidates[1]['score']<.25:raise ValueError('当前关次识别有歧义，等待棋盘稳定')
    if not candidates[0]['supported']:raise ValueError(f'已识别第 {candidates[0]["level"]+1} 关；本关含尚未实现的特殊规则，托管已停止')
    if historical and (resume_errors or candidates[0]['level'] in historical):
        if not matches_fresh_reference(folder,image_path,candidates[0]):
            raise AwaitBoard('waiting_new_map','当前棋盘牌型与缓存地图不同，等待本局完整地图和种子；旧路线已停用')
        candidates[0]['fresh_attempt']=True
    return candidates[0]


def geometry(cards,calibration):
    positions={c['id']:[round(calibration['x_origin']+c['rolNum']*calibration['x_unit']),round(calibration['y_origin']+c['rowNum']*calibration['y_unit'])] for c in cards}
    if any(not (20<=x<=315 and 125<=y<=465) for x,y in positions.values()):raise ValueError('坐标超出棋盘，请微调对齐参数')
    return positions,[{'id':c['id'],'type':c['type'],'center':positions[c['id']]} for c in exposed(cards)]


def recalibrate(play,plan,solution):
    """Plans whose calibration was never fitted on their own reference board
    (alignment_score None) are refitted once; positions and anchors follow."""
    if plan.get('alignment_score') is not None or plan.get('manual_calibration'):return plan
    gold=(solution.get('rules') or {}).get('gold_type')
    try:
        calibration,score=fit(plan['reference_path'],solution['cards'],(gold,) if gold else ())
        positions,anchors=geometry(solution['cards'],calibration)
    except (ValueError,OSError):return plan
    plan.update(calibration=calibration,alignment_score=score,positions=positions,anchors=anchors)
    from solve_verified_map import atomic_json
    atomic_json(play/'plan.json',plan);return plan


def prepare(folder,mp,data,level,mode,manual=None,image_path=None,solution_path=None,refit=False):
    solution_path=solution_path or solution_for(folder,level)/'solution.json'
    if not solution_path.exists() and level==len(read(folder/'start_map_info.json')['map_md5'])-1:
        existing=folder/'solution/solution.json'
        if existing.exists():solution_path=existing
    if not solution_path.exists():raise ValueError('请先求解此关，得到完整答案')
    s=read(solution_path)
    rolling=s.get('execution_kind')=='rolling_segment_v1'
    if not (s.get('segment_validated') if rolling else s.get('validated')) or hashlib.sha256(mp.read_bytes()).hexdigest()!=s['map_sha256']:raise ValueError('答案与当前地图不一致，请重新求解')
    if rolling:
        from rolling_play import validate_segment
        validate_segment(s,data)
        if s['start_step']!=0:raise ValueError('已有点击记录的分段必须通过断点核对')
    else:validate_map_solution(mp,s)
    from PIL import Image
    raw=image_path or capture_snapshot()
    # Board start must have an empty tray. Use the same face test as playback.
    im=Image.open(raw).convert('RGB')
    if tray_count(im):raise ValueError('牌槽非空；请在这一关刚开始、还未点击牌时对齐')
    gold=(s.get('rules') or {}).get('gold_type')
    # A calibration found on an earlier inspection frame (refit=True) may
    # come from an opening animation; the saved reference must fit itself.
    if manual and not refit:calibration,score=manual,None
    else:
        try:calibration,score=fit(raw,s['cards'],(gold,) if gold else ())
        except ValueError:
            if not manual:raise
            calibration,score=manual,None
    from board_align import check_silhouette
    check_silhouette(raw,s['cards'],calibration)
    play=folder/f'level_{level}_play';play.mkdir(exist_ok=True)
    reference=play/'reference.png';im.save(reference)
    positions,anchors=geometry(s['cards'],calibration)
    missing=sorted({c['type'] for c in s['cards']}-{a['type'] for a in anchors})
    plan={'solution_path':str(solution_path),'reference_path':str(reference),
          'map_sha256':s['map_sha256'],'positions':positions,'anchors':anchors,
          'calibration':calibration,'alignment_score':score,'manual_calibration':bool(manual and score is None),'session':folder.name,'mode':mode,
          'level':level,'capture_status_path':str(OUT/'status.json'),'service_lease_path':str(OUT/'service_lease.json'),
          'mark_milliseconds':650,'verify_milliseconds':1100,'dynamic_anchors':True,'adaptive_verify':True}
    if rolling:plan.update(allow_rolling=True,source_map_path=str(mp),execution_kind='rolling_segment_v1')
    (play/'plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    for name in ('control.json','playback_status.json'):(play/name).unlink(missing_ok=True)
    mark(reference,s['cards'],calibration,play/'aligned.png',s['sequence'][0])
    result={'plan_path':str(play/'plan.json'),'preview_path':str(play/'aligned.png'),'calibration':calibration,'rolling':rolling,
            'anchor_count':len(anchors),'missing_anchor_types':missing,'card_count':s['card_count'],
            'start_step':0,'message':'棋盘已自动核对，红框为下一步'+('；隐藏牌露出后会自动学习牌型' if missing else '')}
    (play/'prepared.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def prepare_continuation(context,folder,mp,data,level,mode,solution_path):
    """Upgrade a confirmed partial prefix to a fully verified continuation."""
    from PIL import Image
    from solve_verified_map import atomic_json
    answer=read(solution_path);validate_map_solution(mp,answer)
    raw=capture_snapshot()
    if not context.get('resume'):
        return prepare(folder,mp,data,level,mode,context.get('calibration'),raw,solution_path,refit=True)
    checked=checked_resume(folder,level,raw);n=checked['start_step'];plan=checked['plan'];play=checked['play']
    previous=read(Path(plan['solution_path']))
    if answer['sequence'][:n]!=previous['sequence'][:n] or n!=context['start_step']:
        raise ValueError('完整续解没有衔接已确认记录')
    if n>=len(answer['sequence']):raise ValueError('当前路线已经执行完毕')
    Image.open(raw).convert('RGB').save(play/'resume_reference.png')
    plan.update(solution_path=str(solution_path),start_step=n,initial_reference_path=str(play/'resume_reference.png'),
        dynamic_anchors=True,adaptive_verify=True)
    for key in ('allow_rolling','execution_kind'):plan.pop(key,None)
    atomic_json(play/'plan.json',plan)
    runtime,_=solution_state(answer,n)
    remaining=[{**c,'type':runtime.types[c['id']]} for c in answer['cards'] if c['id'] in runtime.remaining]
    mark(raw,remaining,plan['calibration'],play/'aligned.png',answer['sequence'][n])
    result={'plan_path':str(play/'plan.json'),'preview_path':str(play/'aligned.png'),'start_step':n,
        'card_count':len(answer['cards']),'calibration':plan['calibration'],
        'message':'完整续解已验证，继续时保留整条路线'}
    atomic_json(play/'prepared.json',result);return result


def prepare_segment(context,folder,mp,data,level,mode,solution_path):
    """Recheck the actual screen after short planning and bind the next segment."""
    from PIL import Image
    from solve_verified_map import atomic_json
    from rolling_play import validate_segment
    solution=read(solution_path);validate_segment(solution,data)
    if hashlib.sha256(mp.read_bytes()).hexdigest()!=solution['map_sha256']:raise ValueError('分段地图已变化')
    raw=capture_snapshot()
    if not solution['start_step']:
        return prepare(folder,mp,data,level,mode,context.get('calibration'),raw,solution_path,refit=True)
    checked=checked_resume(folder,level,raw);n=checked['start_step'];plan=checked['plan'];play=checked['play']
    previous=read(Path(plan['solution_path']))
    if n!=solution['start_step'] or solution['sequence'][:n]!=previous['sequence'][:n]:
        raise ValueError('分段未从已确认点击记录继续')
    Image.open(raw).convert('RGB').save(play/'resume_reference.png')
    plan.update(solution_path=str(solution_path),start_step=n,initial_reference_path=str(play/'resume_reference.png'),
        allow_rolling=True,source_map_path=str(mp),execution_kind='rolling_segment_v1',dynamic_anchors=True,adaptive_verify=True)
    atomic_json(play/'plan.json',plan)
    runtime,_=solution_state(solution,n)
    remaining=[{**c,'type':runtime.types[c['id']]} for c in solution['cards'] if c['id'] in runtime.remaining]
    mark(raw,remaining,plan['calibration'],play/'aligned.png',solution['sequence'][n])
    result={'plan_path':str(play/'plan.json'),'preview_path':str(play/'aligned.png'),'start_step':n,
        'card_count':len(solution['cards']),'calibration':plan['calibration'],
        'rolling':True,'message':f'第 {n+1} 步起的本段已核对，执行后继续计算'}
    atomic_json(play/'prepared.json',result);return result


def main():
    if len(sys.argv)>1 and sys.argv[1] in ('verify-playback','verify-tray'):
        parser=argparse.ArgumentParser();parser.add_argument('action');parser.add_argument('--play',type=Path,required=True)
        parser.add_argument('--image',type=Path,required=True);parser.add_argument('--step',type=int,required=True)
        args=parser.parse_args();print(json.dumps(verify_full_board(args.play,args.image,args.step,args.action=='verify-tray'),ensure_ascii=False));return
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=['solve','prepare'])
    p.add_argument('--mode',choices=['daily','world'],required=True)
    p.add_argument('--session',required=True)
    p.add_argument('--level',type=int,required=True)
    p.add_argument('--seconds',type=int,default=180)
    p.add_argument('--performance',choices=['eco','fast','max'],default='fast')
    p.add_argument('--calibration',type=Path)
    a=p.parse_args()
    folder,mp,data=current(a.mode,a.level,a.session)
    if a.action=='solve':
        proc=subprocess.run([sys.executable,str(ROOT/'solve_verified_map.py'),'--map',str(mp),
             '--output',str(solution_for(folder,a.level)),'--time-limit',str(a.seconds),
             '--performance',a.performance],cwd=ROOT,timeout=a.seconds+20,creationflags=NO_WINDOW)
        if proc.returncode:
            status=solution_for(folder,a.level)/'search_status.json'
            detail=read(status).get('message') if status.exists() else None
            raise ValueError(detail or '限时内未找到完整答案，可增加时间重试')
    else:
        print(json.dumps(prepare(folder,mp,data,a.level,a.mode,read(a.calibration) if a.calibration else None),ensure_ascii=False))


if __name__=='__main__':
    try:main()
    except Exception as e:
        print(str(e),file=sys.stderr);raise SystemExit(1)
