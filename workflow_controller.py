"""One background owner of automatic preparation, playback and live status."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

import app_bridge as bridge
from capture_service import alive
from solve_verified_map import atomic_json

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'output/workflow'
CAPTURE=ROOT/'output/auto_capture'
NO_WINDOW=subprocess.CREATE_NO_WINDOW
CONTROLLER_SOURCE_SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def read(path,default=None):
    try:return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):return {} if default is None else default


def valid_solution(folder,level):
    mp=folder/f'map_{level}.json';digest=hashlib.sha256(mp.read_bytes()).hexdigest()
    for output in (folder/f'level_{level}_solution',folder/'solution'):
        solution=read(output/'solution.json')
        if solution.get('validated') and solution.get('map_sha256')==digest:
            bridge.validate_map_solution(mp,solution);return output
    return None


def friendly(message):
    if 'Traceback (most recent call last):' in message:
        lines=message.strip().splitlines()
        detail=next((line.split(':',1)[1].strip() for line in reversed(lines)
                     if line.startswith(('RuntimeError:','ValueError:','PermissionError:','OSError:'))),None)
        message=detail or '后台操作失败，详细原因已保存在日志中'
    if 'score=1000' in message:return '遇到开局被遮住的新牌型；新版可自动学习后续接'
    if '后台截图' in message:return '微信暂时没有提供后台截图；请保持小程序窗口可见'
    return message


def recovered_finished(folder,count):
    finished=set()
    for level in range(count):
        play=folder/f'level_{level}_play';status=read(play/'playback_status.json');plan=read(play/'plan.json')
        if status.get('state')!='sequence_finished' or status.get('input_pending') or status.get('next_step')!=status.get('total'):continue
        try:
            if plan['session']!=folder.name or plan['map_sha256']!=hashlib.sha256((folder/f'map_{level}.json').read_bytes()).hexdigest():continue
            solution=read(Path(plan['solution_path']));bridge.validate_execution(solution)
            if solution.get('execution_kind')=='rolling_segment_v1' and not solution.get('complete'):continue
            actions=[json.loads(line) for line in (play/'actions.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()]
            if len(actions)==status['total']==len(solution['sequence']) and all(a['step']==i+1 and a['card_id']==solution['sequence'][i] for i,a in enumerate(actions)):finished.add(level)
        except (OSError,KeyError,ValueError):continue
    return finished


class Controller:
    def __init__(self):
        OUT.mkdir(parents=True,exist_ok=True);(OUT/'commands').mkdir(exist_ok=True)
        self.pool=ThreadPoolExecutor(max_workers=1)
        self.job=None;self.job_kind='';self.job_epoch=0;self.epoch=0;self.command_process=None
        self.auto=False;self.paused=False;self.stopping=False;self.owns_capture=False;self.quit=False
        self.session='';self.mode='daily';self.level=None;self.folder=None;self.count=0
        self.ready=None;self.player=None;self.player_log=None;self.play_folder=None
        self.finished=set();self.blocked='';self.next_inspect=0;self.search_folder=None
        self.want_restore=False;self.player_epoch=0;self.player_level=None
        self.settle_attempted=False;self.settled=False
        self.rolling_key=None;self.rolling_attempt=0
        self.waiting_new_game=''
        self.requested=False;self.transport_next=0;self.transport_error=''
        self.inspect_only=False
        self.wait_reason=None;self.wait_count=0;self.inspect_wait_key=None;self.inspect_wait_error=None
        self.state='connecting';self.message='正在连接抓取服务';self.started=time.monotonic()
        self.settings={'performance':2,'seconds':60,'click_speed':1,'rolling':True}
        self.settings.update(read(ROOT/'output/app_preferences.json'))
        import static_plan
        self.scheduler_runtime={'version':static_plan.SCHEDULER_VERSION,'sources':static_plan.SCHEDULER_SOURCES}
        self.restore_view=read(OUT/'reopen_view.json') or read(OUT/'status.json')
        (OUT/'reopen_view.json').unlink(missing_ok=True)
        self.last_result=None;self.active_since=time.monotonic();self.last_board=str(OUT/'board.png') if (OUT/'board.png').exists() else ''
        self.board_watch=None;self.next_board_watch=0

    def stage(self,state,message):
        if (state,message)!=(self.state,self.message):
            try:
                log=OUT/'events.jsonl'
                if log.exists() and log.stat().st_size>2*1024*1024:
                    os.replace(log,OUT/'events.previous.jsonl')
                with log.open('a',encoding='utf-8') as stream:
                    stream.write(json.dumps({'time':datetime.now(timezone.utc).isoformat(),
                        'from':self.state,'to':state,'message':message,'job':self.job_kind,
                        'session':self.session,'level':self.level},ensure_ascii=False)+'\n')
            except OSError:pass
        if self.state!=state:self.active_since=time.monotonic()
        self.state=state;self.message=message

    def await_board(self,state,message):
        reason=(state,message)
        self.wait_count=self.wait_count+1 if reason==self.wait_reason else 0
        self.wait_reason=reason
        self.next_inspect=time.monotonic()+min(5,.75*2**min(3,self.wait_count))
        self.stage(state,message)

    def command(self,action,settings=None):
        if action in ('start','retry','refresh'):self.prop_result=None
        if settings:self.settings.update(settings);self.save_settings()
        if action=='settings':
            if self.search_folder and self.job_kind=='solve':atomic_json(self.search_folder/'search_control.json',{'action':'settings','performance':self.performance()})
            if self.player and self.player.poll() is None:self.play_command('speed',**self.timing())
            return
        if action in ('start','retry'):
            if not self.player:
                if self.job and self.job_kind in ('original','standard','rolling','solve','prepare','inspect'):self.epoch+=1
                self.ready=None;self.last_result=None;self.board_watch=None
                self.level=None;self.search_folder=None;self.play_folder=None
            self.inspect_only=False
            self.auto=True;self.paused=False;self.stopping=False;self.blocked=''
            self.requested=True;self.transport_next=0;self.transport_error=''
            self.wait_reason=None;self.wait_count=0;self.inspect_wait_key=None
            self.rolling_attempt=0;self.player_retry_key=None
            self.waiting_new_game=''
            if self.player and self.player.poll() is None:self.play_command('resume')
            self.next_inspect=0
        elif action=='pause':
            self.inspect_only=False
            if self.job and self.job_kind in ('original','standard','rolling','solve','prepare','inspect'):self.epoch+=1
            self.requested=False;self.auto=False;self.paused=True;self.play_command('pause')
        elif action in ('stop','shutdown','restore'):
            self.inspect_only=False
            if self.job and self.job_kind in ('original','standard','rolling','solve','prepare','inspect'):self.epoch+=1
            self.requested=False
            self.auto=False;self.paused=False;self.stopping=True;self.ready=None
            self.play_command('stop')
            if self.search_folder:atomic_json(self.search_folder/'search_control.json',{'action':'stop'})
            if action=='shutdown':self.quit=True
            if action=='restore':self.want_restore=True
        elif action=='refresh':
            if self.job and self.job_kind in ('original','standard','rolling','solve','prepare','inspect'):self.epoch+=1
            self.blocked='';self.ready=None;self.next_inspect=0
            if not self.player:self.last_result=None;self.board_watch=None;self.level=None;self.search_folder=None;self.play_folder=None
            self.inspect_wait_key=None;self.wait_reason=None;self.wait_count=0
            if not self.auto:
                self.inspect_only=True;self.paused=False;self.stopping=False

    def save_settings(self):
        atomic_json(ROOT/'output/app_preferences.json',self.settings)
        config=read(ROOT/'auto_capture_config.json')
        config.update(solve_time_limit=int(self.settings['seconds']),solve_performance=self.performance(),
                      auto_solve=not self.settings.get('rolling',True))
        atomic_json(ROOT/'auto_capture_config.json',config)

    def performance(self):return ['eco','fast','max'][max(0,min(2,int(self.settings['performance'])))]

    def timing(self):
        speed=max(0,min(2,int(self.settings['click_speed'])))
        # Fastest still commits only after the tray count matches on two stable frames.
        return {'mark_milliseconds':[650,300,60][speed],'verify_milliseconds':[1100,650,200][speed]}

    def play_command(self,action,**extras):
        if self.player and self.player.poll() is None:
            atomic_json(self.play_folder/'control.json',{'id':uuid.uuid4().hex,'action':action,**extras})

    def run_command(self,script,args,timeout):
        # All long operations run outside the status loop. Only owned children
        # are terminated on cancellation/timeout, never the capture supervisor.
        process=subprocess.Popen([sys.executable,str(ROOT/script),*map(str,args)],cwd=ROOT,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=NO_WINDOW,
            env={**os.environ,'PYTHONIOENCODING':'utf-8'})
        self.command_process=process
        try:
            out,error=process.communicate(timeout=timeout)
            if process.returncode:raise ValueError((error or out).decode('utf-8',errors='replace').strip())
            return out.decode('utf-8',errors='replace')
        except subprocess.TimeoutExpired:
            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,creationflags=NO_WINDOW,timeout=10)
            process.communicate(timeout=5);raise ValueError('操作超过时间上限，已停止本次操作，可重试')
        finally:
            if self.command_process is process:self.command_process=None

    def submit(self,kind,fn,*args):
        self.job_kind=kind;self.job_epoch=self.epoch;self.job=self.pool.submit(fn,*args)
        # Polling a known wait condition is background work, not a new phase.
        if kind=='inspect' and self.state in ('waiting_board','waiting_window','waiting_new_map','waiting_resume','waiting_input'):return
        if kind=='transport' and not self.transport_error:return
        self.stage({'inspect':'identifying','solve':'solving','standard':'solving','original':'solving','rolling':'planning_segment','prepare':'preparing','capture':'connecting','transport':'connecting','restore':'restoring','settle':'finished'}[kind],
                   {'inspect':'自动识别当前关次与断点','solve':'正在搜索完整答案','standard':'普通关正在验证整条通关路线','original':'正在计算当前棋盘的完整路线','rolling':'正在计算下一小段，算好即执行','prepare':'自动核对棋盘位置','capture':'正在启用自动抓取','transport':'正在检查并恢复抓取连接','restore':'停止抓取并恢复设置','settle':'全部步骤已确认，自动跳过通关动画'}[kind])

    def settle(self):
        bridge.current(self.mode,self.level,self.session);bridge.capture_snapshot()
        result=subprocess.run([str(ROOT/'CompletionCheck.exe'),str(self.play_folder/'plan.json')],cwd=ROOT,
            capture_output=True,creationflags=NO_WINDOW,timeout=8)
        if result.returncode:return {'settled':False}
        report={'session':self.session,'level':self.level,'animation_skipped':True,'preview_path':str(ROOT/'output/game_completion.png')}
        atomic_json(self.folder/'completion.json',report);return {'settled':True,'preview_path':report['preview_path']}

    def inspect(self,folder,count):
        try:raw=bridge.capture_snapshot()
        except ValueError as exc:
            message=str(exc)
            if '最小化' in message or '没有唯一' in message or '未找到唯一' in message:
                raise bridge.AwaitBoard('waiting_window','游戏窗口已最小化或关闭；恢复显示后自动继续，不需要置于前台') from exc
            if '后台截图' in message:
                raise bridge.AwaitBoard('waiting_window','游戏暂未提供后台截图；保持窗口显示后自动继续') from exc
            raise
        temporary=OUT/'board.tmp.png'
        if not (OUT/'board.png').exists() or raw.read_bytes()!=(OUT/'board.png').read_bytes():
            shutil.copyfile(raw,temporary);os.replace(temporary,OUT/'board.png')
        self.last_board=str(OUT/'board.png')
        from PIL import Image
        with Image.open(raw) as frame:frame_digest=hashlib.sha256(frame.crop((20,118,316,532)).tobytes()).hexdigest()
        key=(str(folder),frame_digest,tuple(hashlib.sha256((folder/f'map_{i}.json').read_bytes()).hexdigest() for i in range(count)))
        pending=any(read(path).get('input_pending') for path in folder.glob('level_*_play/playback_status.json'))
        if not pending and key==self.inspect_wait_key and self.inspect_wait_error:raise bridge.AwaitBoard(*self.inspect_wait_error)
        try:result=bridge.inspect_board(folder,OUT/'board.png',count)
        except bridge.AwaitBoard as exc:
            self.inspect_wait_key=key;self.inspect_wait_error=(exc.state,str(exc));raise
        self.inspect_wait_key=None;self.inspect_wait_error=None
        if result.get('fresh_attempt'):
            # Require a second matching frame before rotating old records.
            time.sleep(.25);raw=bridge.capture_snapshot()
            shutil.copyfile(raw,temporary);os.replace(temporary,OUT/'board.png')
            again=bridge.inspect_board(folder,OUT/'board.png',count)
            if not again.get('fresh_attempt') or again['level']!=result['level']:
                raise ValueError('新棋盘尚未稳定，等待画面恢复')
            result=again
        result['image']=str(OUT/'board.png');return result

    def rotate_attempt(self,result):
        """Keep old evidence, but scope all active playback to this attempt."""
        base=self.folder.resolve();archive=base/'attempt_history'/(
            datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:6])
        if not archive.resolve().is_relative_to(base):raise ValueError('历史归档路径异常')
        archive.mkdir(parents=True)
        targets=list(base.glob('level_*_play'))+list(base.glob('level_*_original'))+list(base.glob('level_*_static'))
        if (base/'completion.json').exists():targets.append(base/'completion.json')
        for target in targets:
            if target.resolve().parent!=base:raise ValueError('历史记录路径异常')
            shutil.move(str(target),str(archive/target.name))
        atomic_json(archive/'restart.json',{'session':self.session,'fresh_level':result['level'],
            'reason':'stable_empty_tray_complete_board_matches_initial_faces'})
        self.finished=set();self.ready=None;self.search_folder=None;self.play_folder=None
        self.blocked='';self.waiting_new_game='';self.settle_attempted=False;self.settled=False

    def prepare(self,context):
        level=context['level'];folder,mp,data=bridge.current(self.mode,level,self.session)
        if context['resume']:return bridge.prepare_resume(folder,level,Path(context['image']))
        return bridge.prepare(folder,mp,data,level,self.mode,context['calibration'],Path(context['image']))

    def plan_rolling(self,context):
        from rolling_play import plan_segment
        level=context['level'];folder,mp,data=bridge.current(self.mode,level,self.session);epoch=self.epoch
        prefix=[]
        if context.get('resume'):
            previous=read(Path(context['plan']['solution_path']));prefix=previous['sequence'][:context['start_step']]
        key=(str(mp),hashlib.sha256(mp.read_bytes()).hexdigest(),tuple(prefix))
        if key!=self.rolling_key:self.rolling_key=key;self.rolling_attempt=0
        attempt=self.rolling_attempt;self.rolling_attempt+=1
        base=[1.2,2,3][max(0,min(2,int(self.settings['performance'])))];factor=2**min(attempt,2)
        path,segment=plan_segment(mp,prefix,folder/f'level_{level}_rolling',seconds=base*factor,
            state_limit=32768*factor,
            cancel=lambda:self.stopping or self.paused or self.epoch!=epoch)
        if self.stopping or self.paused or self.epoch!=epoch:raise ValueError('已停止本段规划')
        bridge.current(self.mode,level,self.session)
        return bridge.prepare_segment(context,folder,mp,data,level,self.mode,path)

    def plan_standard(self,context):
        from standard_plan import solve_continuation
        level=context['level'];folder,mp,data=bridge.current(self.mode,level,self.session);epoch=self.epoch
        prefix=[]
        if context.get('resume'):
            previous=read(Path(context['plan']['solution_path']));prefix=previous['sequence'][:context['start_step']]
        path,answer=solve_continuation(mp,prefix,folder/f'level_{level}_solution',
            seconds=min(300,max(5,float(self.settings['seconds']))),
            cancel=lambda:self.stopping or self.paused or self.epoch!=epoch)
        if self.stopping or self.paused or self.epoch!=epoch:raise ValueError('已停止完整路线规划')
        bridge.current(self.mode,level,self.session)
        return bridge.prepare_continuation(context,folder,mp,data,level,self.mode,path)

    def fixed_planner(self,folder,level,data):
        if self.settings.get('solver_engine')=='static_constraint_v1' and bridge.compatibility(data)=='energy_alien':
            # Native search, CP-SAT and one move-out on the live board, each
            # result replayed under the exact energy and alien rules.
            import energy_plan
            return energy_plan.plan_current,folder/f'level_{level}_energy'
        if self.settings.get('solver_engine')=='static_constraint_v1' and bridge.compatibility(data)=='standard':
            import importlib
            import static_plan
            current={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in static_plan.SCHEDULER_SOURCES}
            if static_plan.SCHEDULER_SOURCES!=current:
                # Planning is owned by one job. Refresh dependencies before
                # starting its next solve, so a long-lived daemon cannot keep
                # silently using an obsolete scheduler after source updates.
                import search_resources,static_accelerator,capacity_probe,prop_search,prop_plan
                importlib.reload(search_resources);importlib.reload(static_accelerator);importlib.reload(capacity_probe);importlib.reload(prop_search);importlib.reload(prop_plan);importlib.reload(static_plan)
            self.scheduler_runtime={'version':static_plan.SCHEDULER_VERSION,'sources':static_plan.SCHEDULER_SOURCES}
            return static_plan.plan_current,folder/f'level_{level}_static'
        from original_snapshot import plan_current
        return plan_current,folder/f'level_{level}_original'

    def plan_original(self,context):
        level=context['level'];folder,mp,data=bridge.current(self.mode,level,self.session);epoch=self.epoch
        plan_current,output=self.fixed_planner(folder,level,data)
        prefix=[]
        if context.get('resume'):
            previous=read(Path(context['plan']['solution_path']));prefix=previous['sequence'][:context['start_step']]
        path,answer=plan_current(mp,prefix,output,
            seconds=min(300,max(30,float(self.settings['seconds']))),
            cancel=lambda:self.stopping or self.paused or self.epoch!=epoch,performance=self.performance())
        if self.stopping or self.paused or self.epoch!=epoch:raise ValueError('已暂停原版求解')
        bridge.current(self.mode,level,self.session)
        if answer.get('execution_kind')=='manual_move_out_v1':
            return {'prop_route_ready':True,'solution_path':str(path),'message':f"一次移出路线已验证，共{answer['operation_count']}个操作；请查看操作表，执行功能待人工移出和坐标校准"}
        if answer.get('execution_kind')=='rolling_segment_v1':
            return bridge.prepare_segment(context,folder,mp,data,level,self.mode,path)
        return bridge.prepare_continuation(context,folder,mp,data,level,self.mode,path)

    def collect_job(self):
        if not self.job or not self.job.done():return
        job=self.job;kind=self.job_kind;epoch=self.job_epoch;self.job=None;self.job_kind=''
        try:result=job.result()
        except Exception as e:
            if epoch!=self.epoch:return
            message=friendly(str(e))
            if self.stopping:self.stage('stopped','托管已停止；当前棋盘保留');return
            if self.paused:self.stage('paused','托管已暂停；已确认步骤保留');return
            if isinstance(e,bridge.AwaitBoard):
                if kind=='inspect':
                    self.last_result=None;self.board_watch=None;self.level=None
                    self.search_folder=None;self.play_folder=None
                self.ready=None;self.await_board(e.state,message);return
            if kind=='transport':
                self.transport_error=message;self.transport_next=time.monotonic()+15
                self.stage('waiting_capture','抓取连接尚未恢复，正在自动重试；'+message);return
            if kind=='original' and self.auto and self.settings.get('continue_search') and self.search_folder:
                search=read(self.search_folder/'search_status.json')
                if search.get('state')=='limited' and search.get('engine')!='static_constraint_v1':
                    self.ready=None;self.next_inspect=time.monotonic()+3
                    self.stage('waiting_retry','本轮预算已用完，保留分支覆盖记录，自动换原版选牌设置继续');return
            if kind=='original' and self.auto and self.search_folder:
                search=read(self.search_folder/'search_status.json')
                if search.get('engine')=='static_constraint_v1' and search.get('state')=='limited':
                    self.blocked=message;self.auto=False
                    self.stage('search_limited',message);return
                if search.get('engine')=='static_constraint_v1' and search.get('state')=='no_continuation':
                    self.waiting_new_game=message;self.blocked=''
                    self.stage('waiting_new_game',message+'；等待新棋盘或更改规则后重新计算');return
            if kind=='standard' and self.auto and self.search_folder:
                search=read(self.search_folder/'search_status.json')
                if search.get('state')=='no_continuation':
                    self.waiting_new_game=message;self.blocked=''
                    self.stage('waiting_new_game','旧点击前缀已是死局；重新开始每日一关后自动执行完整路线');return
            if kind=='rolling' and self.auto and self.search_folder:
                search=read(self.search_folder/'search_status.json')
                if search.get('state')=='limited' and self.rolling_attempt<3:
                    self.blocked='';self.next_inspect=time.monotonic()+.5
                    self.stage('waiting_retry','本轮未找到后续消除路线，自动扩大前瞻搜索；棋盘保持不动');return
            if kind=='solve' and self.auto and self.search_folder:
                search=read(self.search_folder/'search_status.json')
                if search.get('state')=='limited' and search.get('rules') in ('color_shuffle_v1','energy_alien_v1'):
                    self.blocked='';self.next_inspect=time.monotonic()+3
                    self.stage('waiting_retry','本轮未找到完整解，保留最佳路线，3 秒后自动换策略继续搜索');return
            if kind=='inspect' and '特殊牌规则' not in message and ('等待' in message or '动画' in message or '不是可识别' in message or '没有唯一的游戏窗口' in message):
                self.await_board('waiting_board','等待微信小程序窗口打开，地图和答案已保留' if '没有唯一的游戏窗口' in message else message);return
            if kind in ('inspect','original','prepare','standard','rolling') and any(token in message for token in (
                '牌槽数量与确认记录不一致','棋盘的牌型与确认记录不一致','当前牌槽图案与确认记录不一致',
                '牌槽非空','没有唯一的游戏窗口','窗口不存在或已最小化','后台截图','牌面持续被遮挡')):
                self.ready=None;self.stage('waiting_board','棋盘或窗口已变化，自动重新识别；'+message)
                self.next_inspect=time.monotonic()+1.5;return
            self.blocked=message;self.auto=False;self.stage('blocked',message);return
        if kind=='capture':self.owns_capture=True
        if epoch!=self.epoch:return
        if isinstance(result,dict) and result.get('prop_route_ready'):
            self.ready=None;self.auto=False;self.requested=False;self.blocked='';self.prop_result=result
            self.stage('prop_ready',result['message']);return
        if self.stopping:
            if kind=='restore':self.owns_capture=False;self.want_restore=False
            self.stage('stopped','托管已停止；当前棋盘保留');return
        if kind=='capture':self.owns_capture=True;self.stage('waiting_start','抓取已启用，等待你进入游戏');return
        if kind=='transport':
            self.transport_error='';self.transport_next=time.monotonic()+15
            if self.state in ('connecting','waiting_capture'):
                self.stage('waiting_board','抓取连接已恢复，自动识别当前棋盘')
            return
        if kind=='settle':
            self.settled=result['settled']
            if self.settled:self.last_board=result['preview_path']
            self.stage('finished','全部步骤已确认，已自动跳过通关动画并进入游戏结算' if self.settled else '全部步骤已确认，请查看游戏结算画面');return
        if kind=='restore':self.owns_capture=False;self.stage('stopped','抓取已停止，临时设置已恢复');return
        if kind=='inspect':
            self.wait_reason=None;self.wait_count=0
            if self.inspect_only:
                self.level=result['level'];self.last_result=result;self.inspect_only=False
                self.stage('waiting_start','当前棋盘已识别；点击开始托管后求解并执行本关');return
            if result.get('fresh_attempt'):self.rotate_attempt(result)
            level=result['level'];self.level=level;self.last_result=result;self.last_board=str(OUT/'board.png')
            from board_watch import BoardWatch
            self.board_watch=BoardWatch(result['image']) if result.get('image') else None
            self.next_board_watch=time.monotonic()+2
            self.play_folder=self.folder/f'level_{level}_play'
            if level in self.finished:
                self.stage('waiting_board','本关已执行完毕，等待游戏进入下一关');self.next_inspect=time.monotonic()+1.5;return
            if self.settings.get('solver_engine') in ('upstream_normal_snapshot_v1','static_constraint_v1'):
                _,self.search_folder=self.fixed_planner(self.folder,level,read(self.folder/f'map_{level}.json'))
                self.submit('original',self.plan_original,result);return
            output=valid_solution(self.folder,level)
            if output:self.search_folder=output
            if bridge.compatibility(read(self.folder/f'map_{level}.json'))=='standard':
                self.search_folder=self.folder/f'level_{level}_solution'
                self.submit('standard',self.plan_standard,result)
            elif self.settings.get('rolling',True) and (not output or result.get('rolling')):
                self.search_folder=self.folder/f'level_{level}_rolling'
                self.submit('rolling',self.plan_rolling,result)
            elif not output:
                self.search_folder=self.folder/f'level_{level}_solution'
                args=['solve','--mode',self.mode,'--session',self.session,'--level',level,'--seconds',self.settings['seconds'],'--performance',self.performance()]
                self.submit('solve',self.run_command,'app_bridge.py',args,int(self.settings['seconds'])+25)
            else:self.submit('prepare',self.prepare,result)
        elif kind=='solve':
            # Re-capture after a long search. Changes made while searching must
            # not become a plan for the old screen.
            self.next_inspect=0;self.stage('identifying','答案已验证，重新核对当前棋盘')
        elif kind in ('prepare','rolling','standard','original'):
            self.ready=result;self.last_board=result['preview_path'];self.next_inspect=time.monotonic()+2
            self.stage('ready',result['message'])

    def start_player(self):
        if getattr(self,'prop_result',None):raise ValueError('道具路线需要人工移出和坐标校准，不能交给普通点击器')
        result=self.ready;path=Path(result['plan_path']);plan=read(path)
        if plan.get('allow_rolling'):
            answer=read(Path(plan['solution_path']))
            if 'tray_capacity' not in answer and answer.get('planning_engine') not in ('upstream_normal_snapshot_v1','energy_static_v1'):
                self.ready=None;self.next_inspect=0;self.stage('identifying','旧短段已保留，按动态容量重新规划');return
        plan.update(self.timing());plan.update(dynamic_anchors=True,adaptive_verify=True)
        atomic_json(path,plan);self.play_folder=path.parent
        (self.play_folder/'control.json').unlink(missing_ok=True)
        if not plan.get('allow_rolling'):(self.play_folder/'playback_status.json').unlink(missing_ok=True)
        self.player_log=(self.play_folder/'player_process.log').open('a',encoding='utf-8')
        self.player=subprocess.Popen([str(ROOT/'SolverPlayback.exe'),str(path)],cwd=ROOT,
            stdout=self.player_log,stderr=self.player_log,creationflags=NO_WINDOW)
        self.player_epoch=self.epoch;self.player_level=self.level
        message='开源原版：沿当前牌型路线执行，牌型改变后重算' if self.settings.get('solver_engine')=='upstream_normal_snapshot_v1' else '边算边玩：执行已核对的小段，红框标出下一步' if plan.get('allow_rolling') else '按已验证答案后台点击，红框标出下一步'
        self.ready=None;self.stage('playing',message)

    def collect_player(self):
        if not self.player:return
        status=read(self.play_folder/'playback_status.json')
        if self.player.poll() is None:
            state=status.get('state','running')
            if state=='paused':self.stage('paused','托管已暂停；点击继续即可续接')
            elif state=='learning':self.stage('learning',status.get('message','正在学习新露出的牌型'))
            elif state=='waiting_target':self.stage('waiting_target',status.get('message','等待临时提示离开目标牌'))
            elif state=='waiting_alien':self.stage('verifying',status.get('message','等待飞碟消牌与棋盘稳定'))
            elif state=='input_sent':self.stage('verifying','点击已发送，等待牌面与牌槽稳定')
            else:self.stage('playing','开源原版：沿当前牌型路线执行' if self.settings.get('solver_engine')=='upstream_normal_snapshot_v1' else '边算边玩：执行当前小段' if read(self.play_folder/'plan.json').get('allow_rolling') else '按已验证答案后台点击，红框标出下一步')
            return
        code=self.player.returncode;pid=self.player.pid;self.player_log.close();self.player_log=None;self.player=None
        if self.player_epoch!=self.epoch:self.play_folder=None;self.next_inspect=0;return
        if code==0 and status.get('state')=='segment_finished':
            self.next_inspect=0;self.stage('identifying','更新当前牌型；未改变则沿用原版路线，改变则重新求解' if self.settings.get('solver_engine')=='upstream_normal_snapshot_v1' else '本段已确认，重新核对棋盘并计算下一段')
        elif code==0 and status.get('state')=='sequence_finished':
            self.finished.add(self.player_level);self.next_inspect=time.monotonic()+1.2
            if self.level==self.count-1:self.stage('finished','答案已全部执行，等待游戏结算')
            else:self.stage('waiting_board','本关已完成，自动衔接下一关')
        elif self.stopping:self.stage('stopped','托管已停止；当前棋盘保留')
        else:
            # A player that fails before its first status write leaves the
            # previous run's message behind; its own log has the real reason.
            lines=(self.play_folder/'player_process.log').read_text(encoding='utf-8',errors='replace').splitlines() if (self.play_folder/'player_process.log').exists() else []
            message=(status.get('message') if status.get('pid')==pid else '') or (lines[-1] if lines else '点击器异常退出')
            if self.auto and status.get('input_pending'):
                self.ready=None;self.next_inspect=time.monotonic()+1
                self.await_board('waiting_resume','地图和种子已就绪，正在复核刚才的点击是否生效');return
            if self.auto and not status.get('input_pending') and any(token in message for token in (
                '实际牌槽','棋盘与坐标校准截图不一致','目标牌图案未通过核对','窗口位置或尺寸改变','窗口不存在或已最小化','后台截图')):
                # Re-identify after a transient change, but the same step
                # failing the same way again is not transient: stop and say so.
                key=(self.player_level,status.get('next_step'),message.split(' score=')[0])
                self.player_retries=self.player_retries+1 if getattr(self,'player_retry_key',None)==key else 1;self.player_retry_key=key
                if self.player_retries>=3 and '窗口' not in message:
                    self.blocked=f"第 {status.get('next_step',0)+1} 步连续 3 次核对失败，已停止自动重试；{friendly(message)}";self.auto=False
                    self.stage('blocked',self.blocked);return
                self.ready=None;self.next_inspect=time.monotonic()+1.5
                self.stage('waiting_board','画面已变化，自动重新识别；'+message);return
            self.blocked=friendly(message);self.auto=False;self.stage('blocked',self.blocked)

    def snapshot(self,capture,service):
        search=read(self.search_folder/'search_status.json') if self.search_folder else {}
        playback=read(self.play_folder/'playback_status.json') if self.play_folder else {}
        if self.player:
            image=self.play_folder/('next_click.png' if playback.get('state') in ('marking','learning') else 'last_verified.png')
            if image.exists():self.last_board=str(image)
        display_message=self.message
        tail=search.get('tail_search',{})
        if self.state=='solving' and search.get('state')=='searching' and tail:
            display_message=f"优先搜索尾段，已排除 {tail.get('exhausted_subtrees',0)} 个候选前缀；需要时自动扩大回退范围"
            front=search.get("frontier_search",{})
            if front:display_message=f"断点续搜，已处理 {front.get('completed_subtrees',0)} 段；已排除 {tail.get('exhausted_subtrees',0)} 个尾段前缀" + (f"；缓存跳过 {search.get('dead_cache',{}).get('hits',0)} 次" if search.get('dead_cache') else '')
        payload={'pid':os.getpid(),'heartbeat':datetime.now(timezone.utc).isoformat(),'state':self.state,
            'message':display_message,'state_seconds':round(time.monotonic()-self.active_since,1),
            'auto_enabled':self.auto,'paused':self.paused,'service_running':service,
            'capture_pid':capture.get('capture_pid'),'mode':self.mode if self.last_result else 'unknown',
            'capture_mode':self.mode,'mode_confirmed':bool(self.last_result),'session':self.session,'level':self.level,
            'map_count':self.count,'maps_received':capture.get('maps_received',0),'seed_received':capture.get('seed_received',False),
            'capture_phase':capture.get('phase'),'game_requests':capture.get('game_requests',0),
            'last_packet':capture.get('updated_at'),'search':search,'playback':playback,
            'preview_path':self.last_board,'can_resume':bool(self.ready and self.ready.get('start_step')),
            'ready':bool(self.ready),'blocked_reason':self.blocked,'busy':bool(self.job),
            'settings':self.settings,'finished_levels':sorted(self.finished),
            'controller_source_sha256':CONTROLLER_SOURCE_SHA256,'scheduler_runtime':getattr(self,'scheduler_runtime',{}),
            'rules': bridge.compatibility(read(self.folder/f'map_{self.level}.json')) if self.folder and self.level is not None else ''}
        health=read(CAPTURE/'transport_health.json');lease=read(CAPTURE/'service_lease.json')
        payload['transport_ready']=bool(service and health.get('instance')==lease.get('instance') and health.get('healthy'))
        payload['transport_message']=health.get('message','抓取连接尚未检查')
        payload['retry_in_seconds']=round(max(0,self.next_inspect-time.monotonic()),1) if self.state in ('waiting_board','waiting_window','waiting_new_map','waiting_resume') else None
        atomic_json(OUT/'status.json',payload)

    def watch_search_board(self):
        if not self.auto or not self.job or self.job.done() or not self.board_watch:return
        if self.job_kind not in ('original','standard','rolling','solve') or time.monotonic()<self.next_board_watch:return
        self.next_board_watch=time.monotonic()+2
        try:changed=self.board_watch.changed(bridge.capture_snapshot(OUT/'search_watch.png'))
        except (ValueError,OSError):return  # Minimization does not cancel valid computation.
        if not changed:return
        self.epoch+=1;self.ready=None;self.last_result=None;self.board_watch=None
        if self.search_folder:atomic_json(self.search_folder/'search_control.json',{'action':'stop'})
        self.level=None;self.search_folder=None;self.play_folder=None;self.next_inspect=0
        self.stage('waiting_board','检测到棋盘已改变，已取消旧计算，先重新识别当前模式和关次')

    def tick(self):
        for path in sorted((OUT/'commands').glob('*.json')):
            cmd=read(path);path.unlink(missing_ok=True)
            if cmd.get('action'):self.command(cmd['action'],cmd.get('settings'))
        capture=read(CAPTURE/'status.json');lease=read(CAPTURE/'service_lease.json')
        service=bool(lease.get('running') and alive(lease.get('pid',0)))
        session=capture.get('session','')
        if session!=self.session:
            self.prop_result=None
            self.epoch+=1;self.session=session;self.folder=CAPTURE/'sessions'/session
            self.level=None;self.finished=set();self.ready=None;self.blocked='';self.stopping=False
            self.last_result=None;self.board_watch=None
            self.waiting_new_game=''
            self.inspect_wait_key=None;self.inspect_wait_error=None;self.wait_reason=None;self.wait_count=0
            if self.requested:self.auto=True
            self.search_folder=None;self.play_folder=None if not self.player else self.play_folder;self.next_inspect=0
            self.settle_attempted=False;self.settled=False
            if self.player:self.play_command('stop')
        source=capture.get('source_path','');self.mode='world' if '/world/' in source else 'daily'
        self.count=int(capture.get('map_count',0))
        previous=self.restore_view;self.restore_view={}
        # Reopening the UI must not silently restart a timed-out calculation
        # or erase its explanation. This restores display only, never input.
        if previous.get('state') in ('search_limited','prop_ready') and previous.get('session')==session and not self.auto and not self.paused and not self.stopping:
            level=previous.get('level')
            if isinstance(level,int) and 0<=level<self.count and self.folder:
                mp=self.folder/f'map_{level}.json'
                if mp.is_file() and self.settings.get('solver_engine')=='static_constraint_v1':
                    _,output=self.fixed_planner(self.folder,level,read(mp));search=read(output/'search_status.json')
                    if search.get('state')=='prop_ready' and search.get('map_sha256')==hashlib.sha256(mp.read_bytes()).hexdigest():
                        if bridge.compatibility(read(mp))=='energy_alien':
                            from energy_plan import cached
                            result=cached(mp,read(mp),search.get('confirmed_prefix',[]),output/'energy_prop')
                        else:
                            from prop_plan import cached
                            result=cached(mp,read(mp),search.get('confirmed_prefix',[]),output/'prop')
                        if result:
                            self.level=level;self.search_folder=output;self.prop_result={'solution_path':str(result[0]),'message':search['message']}
                    if search.get('engine')=='static_constraint_v1' and search.get('state')=='limited' and search.get('map_sha256')==hashlib.sha256(mp.read_bytes()).hexdigest():
                        self.level=level;self.search_folder=output
                        self.blocked=previous.get('blocked_reason') or search.get('message','上轮预算内未找到完整答案，可解性未知')
        self.watch_search_board();self.collect_player();self.collect_job()
        if self.folder and not self.finished and self.count and not self.player:
            self.finished=recovered_finished(self.folder,self.count)
            if self.count-1 in self.finished:
                self.level=self.count-1;self.play_folder=self.folder/f'level_{self.level}_play';self.search_folder=valid_solution(self.folder,self.level)
                report=read(self.folder/'completion.json')
                if report.get('session')==self.session and report.get('animation_skipped'):self.settle_attempted=self.settled=True;self.last_board=report.get('preview_path','')
        if self.want_restore and not self.player and not self.job:self.submit('restore',self.run_command,'capture_service.py',['stop'],30)
        elif self.player or self.job:pass
        elif self.stopping:self.stage('stopped','托管已停止；点击开始可核对并续接')
        elif self.paused:self.stage('paused','托管已暂停；准备与答案已保留')
        elif getattr(self,'prop_result',None):self.stage('prop_ready',self.prop_result['message'])
        elif self.waiting_new_game:self.stage('waiting_new_game',self.waiting_new_game+'；等待新局自动重新计算')
        elif self.blocked:
            search=read(self.search_folder/'search_status.json') if self.search_folder else {}
            self.stage('search_limited' if search.get('engine')=='static_constraint_v1' and search.get('state')=='limited' else 'blocked',self.blocked)
        elif not service:
            if self.auto:self.submit('capture',self.run_command,'capture_service.py',['start','--enable-proxy','--trust-ca'],40)
            else:self.stage('disconnected','点击开始托管，自动启用抓取并准备棋盘')
        elif self.auto and self.transport_error and time.monotonic()<self.transport_next:
            self.stage('waiting_capture','抓取连接尚未恢复，正在自动重试；'+self.transport_error)
        elif not capture.get('complete'):
            if capture.get('phase')=='waiting_for_seed_decryption':self.stage('waiting_seed','已收地图结构和种子密文，等待解密数据；首次请正常结算一局后再开局，保持小程序运行')
            elif capture.get('phase') in ('parse_error','unsupported_match_data'):self.stage('blocked',capture.get('message','地图数据暂不支持'))
            else:self.stage('waiting_start','抓取正在运行，等待你进入游戏的新一局')
            if self.auto and lease.get('proxy_applied') and time.monotonic()>=self.transport_next:
                self.submit('transport',self.run_command,'capture_service.py',['repair'],40)
        elif self.auto and lease.get('proxy_applied') and time.monotonic()>=self.transport_next:
            self.submit('transport',self.run_command,'capture_service.py',['repair'],40)
        elif self.count and self.count-1 in self.finished and self.last_result:
            if self.auto and not self.settle_attempted:self.settle_attempted=True;self.submit('settle',self.settle)
            else:self.stage('finished','全部步骤已确认，已进入游戏结算；新一局会自动准备' if self.settled else '全部步骤已确认，请查看游戏结算画面')
        elif self.ready:
            if self.auto:self.start_player()
            else:self.stage('ready',self.ready['message'])
        elif not self.auto and not self.inspect_only and self.settings.get('solver_engine') in ('upstream_normal_snapshot_v1','static_constraint_v1'):
            self.stage('waiting_start','已就绪；点击开始托管，先核对当前关卡并执行已验证路线')
        elif time.monotonic()>=self.next_inspect:
            # This is the sole entry to planning: identify the ACTUAL board.
            # Historical completion records never choose a level to calculate.
            self.submit('inspect',self.inspect,self.folder,self.count)
        self.snapshot(capture,service)

    def close(self):
        self.play_command('stop')
        if self.player:
            try:self.player.wait(timeout=6)
            except subprocess.TimeoutExpired:self.player.terminate();self.player.wait(timeout=3)
            if self.player_log:self.player_log.close()
        if self.search_folder:atomic_json(self.search_folder/'search_control.json',{'action':'stop'})
        if self.command_process and self.command_process.poll() is None:
            # A capture startup must finish so its proxy lease can be restored.
            if self.job_kind=='capture':
                try:self.job.result(timeout=42);self.owns_capture=True
                except Exception:pass
            else:
                try:self.job.result(timeout=2)
                except Exception:
                    process=self.command_process
                    if process and process.poll() is None:subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,creationflags=NO_WINDOW,timeout=10)
        self.pool.shutdown(wait=True,cancel_futures=True)
        try:
            if self.owns_capture:
                from capture_service import preserve_upgrade,process_ancestors
                lease=read(CAPTURE/'service_lease.json')
                if not preserve_upgrade(lease,[os.getpid()]+process_ancestors()):
                    self.run_command('capture_service.py',['stop'],30)
        finally:
            # A failed stop must not leave a dead controller advertised as live.
            atomic_json(OUT/'lease.json',{'running':False,'pid':os.getpid()})


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['start','run']);args=parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    if args.action=='start':
        lease=read(OUT/'lease.json')
        if lease.get('running') and alive(lease.get('pid',0)):return
        with (OUT/'errors.log').open('a',encoding='utf-8') as errors:
            subprocess.Popen([sys.executable,str(Path(__file__)),'run'],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=errors,creationflags=NO_WINDOW)
        return
    with (OUT/'controller.lock').open('a+b') as lock:
        if lock.tell()==0:lock.write(b'0');lock.flush()
        lock.seek(0)
        try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:return
        controller=Controller();atomic_json(OUT/'lease.json',{'running':True,'pid':os.getpid()})
        try:
            while not controller.quit:
                try:controller.tick()
                except Exception as e:
                    controller.blocked=friendly(str(e));controller.auto=False;controller.stage('blocked',controller.blocked)
                    controller.snapshot(read(CAPTURE/'status.json'),False)
                time.sleep(.25)
        finally:controller.close()


if __name__=='__main__':main()
