"""Verified resumption, UI handoff and non-blocking workflow state."""
from concurrent.futures import Future
import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

import app_bridge as bridge
import capture_service
import workflow_controller as workflow

ROOT=Path(__file__).resolve().parent.parent
FIXTURE=ROOT/'output/auto_capture/sessions/20261008T162423Z-e73fea'


class WorkflowTests(unittest.TestCase):
    def test_long_lived_controller_reloads_changed_scheduler_before_planning(self):
        import hashlib
        import importlib
        import static_plan
        with tempfile.TemporaryDirectory() as directory,patch.object(workflow,'OUT',Path(directory)):
            controller=workflow.Controller();controller.settings['solver_engine']='static_constraint_v1'
            try:
                stale={**static_plan.SCHEDULER_SOURCES,'static_accelerator.py':'obsolete'}
                with patch.object(static_plan,'SCHEDULER_SOURCES',stale),patch.object(bridge,'compatibility',return_value='standard'),patch('importlib.reload',wraps=importlib.reload) as reload:
                    planner,_=controller.fixed_planner(Path(directory),1,{})
                    self.assertGreaterEqual(reload.call_count,3)
                    self.assertEqual(controller.scheduler_runtime['sources']['static_accelerator.py'],hashlib.sha256((ROOT/'static_accelerator.py').read_bytes()).hexdigest())
                    self.assertEqual(planner.__module__,'static_plan')
            finally:controller.pool.shutdown(wait=True)

    def test_static_engine_selects_global_model_and_leaves_special_on_original(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(workflow,'OUT',Path(directory)):
            controller=workflow.Controller();controller.settings['solver_engine']='static_constraint_v1'
            try:
                with patch.object(bridge,'compatibility',return_value='standard'):
                    planner,folder=controller.fixed_planner(Path(directory),1,{})
                    self.assertEqual(planner.__module__,'static_plan');self.assertEqual(folder.name,'level_1_static')
                with patch.object(bridge,'compatibility',return_value='color_shuffle'):
                    planner,folder=controller.fixed_planner(Path(directory),1,{})
                    self.assertEqual(planner.__module__,'original_snapshot');self.assertEqual(folder.name,'level_1_original')
            finally:controller.pool.shutdown(wait=True)

    def test_static_timeout_does_not_repeat_same_search_or_claim_infeasible(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(workflow,'OUT',Path(directory)):
            controller=workflow.Controller();controller.auto=True;controller.settings['continue_search']=True
            controller.search_folder=Path(directory)
            (Path(directory)/'search_status.json').write_text(json.dumps({'state':'limited','engine':'static_constraint_v1'}))
            controller.job_kind='original';controller.job=Future()
            controller.job.set_exception(ValueError('budget unknown'))
            try:
                controller.collect_job();self.assertEqual(controller.state,'search_limited')
                self.assertFalse(controller.auto);self.assertEqual(controller.waiting_new_game,'')
            finally:controller.pool.shutdown(wait=True)


    def setUp(self):
        # User preferences must not change the legacy regression fixtures.
        original_read=workflow.read
        def isolated(path,*args,**kwargs):
            if path==ROOT/'output/app_preferences.json':return {}
            return original_read(path,*args,**kwargs)
        self.addCleanup(patch.stopall)
        patch.object(workflow,'read',side_effect=isolated).start()

    def test_pending_input_stays_in_resume_state_with_seed_already_ready(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(workflow,'OUT',Path(directory)):
            folder=Path(directory);controller=workflow.Controller();controller.auto=True
            controller.play_folder=folder;controller.player_epoch=controller.epoch;controller.player_level=0
            (folder/'playback_status.json').write_text(json.dumps({'state':'stopped','input_pending':True,
                'message':'点击后牌面未稳定','next_step':8,'total':18}))
            from unittest.mock import Mock
            controller.player=Mock();controller.player.poll.return_value=1;controller.player.returncode=1
            controller.player_log=Mock()
            try:
                controller.collect_player()
                self.assertEqual(controller.state,'waiting_resume');self.assertTrue(controller.auto)
                self.assertEqual(controller.blocked,'');self.assertIsNone(controller.player)
            finally:controller.pool.shutdown(wait=True)

    def test_background_poll_and_healthy_transport_do_not_change_wait_reason(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(workflow,'OUT',Path(directory)):
            controller=workflow.Controller();controller.stage('waiting_resume','地图已齐，点击待复核')
            try:
                controller.submit('inspect',lambda: (_ for _ in ()).throw(bridge.AwaitBoard('waiting_resume','地图已齐，点击待复核')))
                self.assertEqual(controller.state,'waiting_resume')
                while not controller.job.done():time.sleep(.001)
                controller.collect_job();self.assertEqual(controller.state,'waiting_resume')
                controller.submit('transport',lambda:'ok')
                while not controller.job.done():time.sleep(.001)
                controller.collect_job()
                self.assertEqual((controller.state,controller.message),('waiting_resume','地图已齐，点击待复核'))
            finally:controller.pool.shutdown(wait=True)

    def test_stop_then_start_does_not_accept_an_old_search_result(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(workflow,'OUT',Path(directory)):
            controller=workflow.Controller();controller.job=Future();controller.job_kind='original'
            controller.job_epoch=controller.epoch;old=controller.epoch
            try:
                controller.command('stop');controller.command('start')
                self.assertGreater(controller.epoch,old)
                controller.job.set_result({'plan_path':'obsolete','preview_path':'obsolete'})
                controller.collect_job();self.assertIsNone(controller.ready);self.assertTrue(controller.auto)
            finally:controller.pool.shutdown(wait=True)



    def test_authorized_continuous_search_retries_budget_but_not_errors(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(workflow,'OUT',Path(directory)):
            controller=workflow.Controller();controller.auto=True;controller.settings['continue_search']=True
            controller.search_folder=Path(directory)
            try:
                for search_state,expected in [('limited','waiting_retry'),('error','blocked')]:
                    controller.auto=True;(Path(directory)/'search_status.json').write_text(json.dumps({'state':search_state}))
                    controller.job_kind='original';controller.job=Future();controller.job.set_exception(ValueError('budget or error'))
                    controller.collect_job();self.assertEqual(controller.state,expected)
            finally:controller.pool.shutdown(wait=True)

    def test_ordinary_board_routes_to_complete_solver_even_with_rolling_enabled(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(workflow,'OUT',Path(directory)):
            folder=Path(directory);controller=workflow.Controller();controller.folder=folder;controller.count=2
            controller.settings['rolling']=True
            (folder/'map_1.json').write_text('{"levelData": {"1": []}}')
            context={'level':1,'resume':True,'rolling':True,'start_step':3}
            controller.job=Future();controller.job.set_result(context);controller.job_kind='inspect'
            try:
                with patch.object(workflow,'valid_solution',return_value=None),patch.object(controller,'submit') as submit:
                    controller.collect_job();self.assertEqual(submit.call_args.args[0],'standard')
                    self.assertEqual(submit.call_args.args[2],context)
            finally:controller.pool.shutdown(wait=True)

    def test_dead_ordinary_prefix_waits_for_next_game_without_repeating_search(self):
        with tempfile.TemporaryDirectory() as directory:
            output,capture,status=self.fake(directory)
            with patch.object(workflow,'OUT',output),patch.object(workflow,'CAPTURE',capture),patch.object(workflow,'alive',return_value=True):
                controller=workflow.Controller();controller.session='s1';controller.folder=Path(directory);controller.count=2;controller.auto=True
                controller.search_folder=Path(directory);(Path(directory)/'search_status.json').write_text('{"state":"no_continuation"}')
                controller.job=Future();controller.job.set_exception(ValueError('dead prefix'));controller.job_kind='standard'
                try:
                    controller.collect_job();self.assertEqual(controller.state,'waiting_new_game');self.assertTrue(controller.auto)
                    with patch.object(controller,'submit') as submit:controller.tick();submit.assert_not_called()
                    status.update(session='s2',complete=False);(capture/'status.json').write_text(json.dumps(status))
                    controller.tick();self.assertEqual(controller.waiting_new_game,'');self.assertTrue(controller.auto)
                    self.assertEqual(controller.state,'waiting_start')
                finally:controller.pool.shutdown(wait=True)

    def test_rolling_limit_retries_but_exhaustion_and_pause_do_not(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(workflow,'OUT',Path(directory)):
            controller=workflow.Controller();controller.search_folder=Path(directory)
            try:
                for search_state,paused,attempt,expected in (
                    ('limited',False,1,'waiting_retry'),('limited',False,3,'blocked'),
                    ('lookahead_exhausted',False,1,'blocked'),('no_next_elimination',False,1,'blocked'),
                    ('limited',True,1,'paused')):
                    controller.auto=True;controller.paused=paused;controller.rolling_attempt=attempt
                    (Path(directory)/'search_status.json').write_text(json.dumps({'state':search_state}))
                    controller.job=Future();controller.job.set_exception(ValueError('route unavailable'))
                    controller.job_kind='rolling';controller.collect_job();self.assertEqual(controller.state,expected)
            finally:controller.pool.shutdown(wait=True)

    def test_resuming_legacy_ready_segment_forces_new_capacity_plan(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(workflow,'OUT',Path(directory)):
            folder=Path(directory);controller=workflow.Controller()
            answer=folder/'answer.json';answer.write_text(json.dumps({'execution_kind':'rolling_segment_v1'}))
            plan=folder/'plan.json';plan.write_text(json.dumps({'allow_rolling':True,'solution_path':str(answer)}))
            controller.ready={'plan_path':str(plan)}
            try:
                with patch.object(workflow.subprocess,'Popen') as spawn:
                    controller.start_player();spawn.assert_not_called();self.assertIsNone(controller.ready)
                    self.assertEqual(controller.state,'identifying')
            finally:controller.pool.shutdown(wait=True)

    def test_energy_whole_route_segment_starts_player_instead_of_replanning_forever(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(workflow,'OUT',Path(directory)):
            folder=Path(directory);controller=workflow.Controller()
            answer=folder/'answer.json';answer.write_text(json.dumps({'execution_kind':'rolling_segment_v1','planning_engine':'energy_static_v1'}))
            plan=folder/'plan.json';plan.write_text(json.dumps({'allow_rolling':True,'solution_path':str(answer)}))
            controller.ready={'plan_path':str(plan)}
            try:
                with patch.object(workflow.subprocess,'Popen') as spawn:
                    controller.start_player();spawn.assert_called_once();self.assertEqual(controller.state,'playing')
            finally:
                if controller.player_log:controller.player_log.close()
                controller.player=None;controller.pool.shutdown(wait=True)

    def test_same_step_failing_the_same_check_three_times_stops_instead_of_looping(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(workflow,'OUT',Path(directory)):
            folder=Path(directory);controller=workflow.Controller();controller.auto=True
            status={'pid':5,'next_step':7,'total':18,'input_pending':False,'state':'stopped','message':'目标牌图案未通过核对：1-44-16 score=141.6'}
            (folder/'playback_status.json').write_text(json.dumps(status,ensure_ascii=False),encoding='utf-8')
            class Done:
                returncode=1;pid=5
                def poll(self):return 1
            try:
                for attempt in range(3):
                    controller.play_folder=folder;controller.player=Done();controller.player_log=open(folder/'log.txt','a')
                    controller.player_epoch=controller.epoch;controller.player_level=0;controller.collect_player()
                    self.assertEqual(controller.state,'waiting_board' if attempt<2 else 'blocked')
                self.assertFalse(controller.auto);self.assertIn('第 8 步连续 3 次',controller.blocked)
                controller.command('start');self.assertIsNone(controller.player_retry_key)
            finally:controller.pool.shutdown(wait=True)

    def test_speed_run_stops_after_its_steps_and_reports_time(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(workflow,'OUT',Path(directory)):
            folder=Path(directory);controller=workflow.Controller();controller.auto=True
            (folder/'playback_status.json').write_text(json.dumps({'pid':5,'next_step':2,'state':'segment_finished'}),encoding='utf-8')
            (folder/'plan.json').write_text(json.dumps({'partial_run':True}),encoding='utf-8')
            rows=[{'time':f'2026-11-05T10:00:0{n}.0000000+09:00','confirmation_ms':500.0} for n in (0,1)]
            (folder/'actions.jsonl').write_text('\n'.join(json.dumps(row) for row in rows),encoding='utf-8')
            class Done:
                returncode=0;pid=5
                def poll(self):return 0
            try:
                controller.play_folder=folder;controller.player=Done();controller.player_log=open(folder/'log.txt','a')
                controller.player_epoch=controller.epoch;controller.player_level=1;controller.collect_player()
                self.assertEqual(controller.state,'search_limited');self.assertFalse(controller.auto)
                self.assertIn('2 步用时 1.5 秒',controller.blocked);self.assertTrue((folder/'speed_test.json').exists())
            finally:controller.pool.shutdown(wait=True)

    def test_speed_run_plan_starts_the_player_instead_of_replanning(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(workflow,'OUT',Path(directory)):
            folder=Path(directory);controller=workflow.Controller();controller.auto=True
            (folder/'solution.json').write_text(json.dumps({'sequence':['a']}),encoding='utf-8')
            (folder/'plan.json').write_text(json.dumps({'allow_rolling':True,'partial_run':True,'solution_path':str(folder/'solution.json')}),encoding='utf-8')
            controller.ready={'plan_path':str(folder/'plan.json')};controller.level=1
            try:
                with patch.object(workflow.subprocess,'Popen') as popen:
                    controller.start_player()
                    self.assertTrue(popen.called);self.assertEqual(controller.state,'playing')
                controller.player=None;controller.player_log.close();controller.player_log=None
            finally:controller.pool.shutdown(wait=True)

    def test_speed_run_skips_the_complete_solve_on_large_boards_only(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(workflow,'OUT',Path(directory)):
            controller=workflow.Controller();folder=Path(directory);full=lambda *args,**kw:(folder/'s.json',{})
            self.assertEqual(controller.settings['partial_steps'],0)
            controller.settings['partial_steps']=100
            try:
                with patch.object(bridge,'current',return_value=(folder,folder/'m.json',{})),patch.object(bridge,'compatibility',return_value='standard'),\
                     patch.object(bridge,'prepare_continuation',return_value='complete'),patch.object(controller,'fixed_planner',return_value=(full,folder)),\
                     patch.object(controller,'plan_partial',return_value='partial'):
                    with patch.object(workflow,'initial_cards',return_value=([{}]*249,None)):
                        self.assertEqual(controller.plan_original({'level':1}),'partial')
                        controller.settings['partial_steps']=0
                        self.assertEqual(controller.plan_original({'level':1}),'complete')
                        controller.settings['partial_steps']=100
                    with patch.object(workflow,'initial_cards',return_value=([{}]*18,None)):
                        self.assertEqual(controller.plan_original({'level':0}),'complete')
            finally:controller.pool.shutdown(wait=True)

    def test_player_failing_before_its_first_status_reports_its_own_error(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(workflow,'OUT',Path(directory)):
            folder=Path(directory);controller=workflow.Controller();controller.auto=True
            # The previous segment's success status is still on disk.
            (folder/'playback_status.json').write_text(json.dumps({'pid':111,'next_step':40,'state':'segment_finished','message':'本段已确认，继续计算下一段'},ensure_ascii=False),encoding='utf-8')
            (folder/'player_process.log').write_text('本段已确认，继续计算下一段\n棋盘与坐标校准截图不一致\n',encoding='utf-8')
            class Failed:
                returncode=1;pid=222
                def poll(self):return 1
            try:
                controller.play_folder=folder;controller.player=Failed();controller.player_log=open(folder/'log.txt','a')
                controller.player_epoch=controller.epoch;controller.player_level=1;controller.collect_player()
                self.assertEqual(controller.state,'waiting_board');self.assertIn('棋盘与坐标校准截图不一致',controller.message)
                self.assertTrue(controller.auto)
            finally:controller.pool.shutdown(wait=True)

    def test_performance_setting_reaches_running_search(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(workflow,'OUT',Path(directory)):
            controller=workflow.Controller();controller.search_folder=Path(directory);controller.job_kind='solve'
            try:
                with patch.object(controller,'save_settings'):
                    controller.command('settings',{'performance':0})
                command=json.loads((Path(directory)/'search_control.json').read_text(encoding='utf-8'))
                self.assertEqual(command,{'action':'settings','performance':'eco'})
            finally:controller.pool.shutdown(wait=True)


    def test_color_search_limit_retries_but_user_stop_does_not(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(workflow,'OUT',Path(directory)):
            controller=workflow.Controller();controller.auto=True;controller.search_folder=Path(directory)
            (Path(directory)/'search_status.json').write_text(json.dumps({'state':'limited','rules':'color_shuffle_v1'}))
            try:
                controller.job=Future();controller.job.set_exception(ValueError('本轮搜索到时'))
                controller.job_kind='solve';controller.collect_job()
                self.assertTrue(controller.auto);self.assertEqual(controller.state,'waiting_retry')
                self.assertGreater(controller.next_inspect,time.monotonic())
                controller.auto=False;controller.stopping=True
                controller.job=Future();controller.job.set_exception(ValueError('本轮搜索到时'))
                controller.job_kind='solve';controller.collect_job()
                self.assertEqual(controller.state,'stopped');self.assertFalse(controller.auto)
            finally:controller.pool.shutdown(wait=True)
    @unittest.skipUnless(FIXTURE.exists(),'verified daily fixture absent')
    def test_verified_prefix_resume_and_pending_input_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)/FIXTURE.name;play=folder/'level_1_play';play.mkdir(parents=True)
            shutil.copyfile(FIXTURE/'map_1.json',folder/'map_1.json')
            for name in ('plan.json','actions.jsonl','last_verified.png','playback_status.json'):
                shutil.copyfile(FIXTURE/'level_1_play'/name,play/name)
            # Stable fixture is the 43-step prefix, independent of future play.
            actions=[json.loads(line) for line in (play/'actions.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()][:43]
            (play/'actions.jsonl').write_text('\n'.join(json.dumps(a) for a in actions),encoding='utf-8')
            status={'next_step':43,'total':234,'expected_tray':[10,4,14,12],'state':'stopped'}
            (play/'playback_status.json').write_text(json.dumps(status))
            # resume_reference is the preserved frame taken before the resumed run.
            reference=ROOT/'output/workflow_test_fixtures/step_43.png'
            if not reference.exists():reference=FIXTURE/'level_1_play/last_verified.png'
            shutil.copyfile(reference,play/'last_verified.png')
            context=bridge.checked_resume(folder,1,play/'last_verified.png')
            self.assertEqual(context['start_step'],43)
            status['input_pending']=True;(play/'playback_status.json').write_text(json.dumps(status))
            with self.assertRaisesRegex(ValueError,'尚未确认'):bridge.checked_resume(folder,1,play/'last_verified.png')
            status['input_pending']=False;status['expected_tray']=[];(play/'playback_status.json').write_text(json.dumps(status))
            with self.assertRaisesRegex(ValueError,'未确认'):bridge.checked_resume(folder,1,play/'last_verified.png')

    @unittest.skipUnless((ROOT/'output/workflow_test_fixtures/halfway_toast.png').exists(),'halfway toast fixture absent')
    def test_toast_disappearance_allowed_but_changed_tile_or_tray_blocked(self):
        from PIL import Image,ImageDraw
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)/FIXTURE.name;play=folder/'level_1_play';play.mkdir(parents=True)
            shutil.copyfile(FIXTURE/'map_1.json',folder/'map_1.json')
            shutil.copyfile(FIXTURE/'level_1_play/plan.json',play/'plan.json')
            shutil.copytree(FIXTURE/'level_1_play/templates',play/'templates')
            actions=[json.loads(line) for line in (FIXTURE/'level_1_play/actions.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()][:117]
            (play/'actions.jsonl').write_text('\n'.join(json.dumps(a) for a in actions),encoding='utf-8')
            (play/'playback_status.json').write_text(json.dumps({'next_step':117,'total':234,'expected_tray':[9,9,4,1,13,1],'state':'stopped'}))
            source=ROOT/'output/workflow_test_fixtures';shutil.copyfile(source/'halfway_toast.png',play/'last_verified.png')
            self.assertEqual(bridge.checked_resume(folder,1,source/'halfway_clear.png')['start_step'],117)
            altered=Image.open(source/'halfway_clear.png').convert('RGB');ImageDraw.Draw(altered).rectangle((158,160,178,180),fill='red');altered.save(play/'changed.png')
            with self.assertRaisesRegex(ValueError,'牌型'):bridge.checked_resume(folder,1,play/'changed.png')
            altered=Image.open(source/'halfway_clear.png').convert('RGB');ImageDraw.Draw(altered).rectangle((33,501,53,521),fill='purple');altered.save(play/'changed.png')
            with self.assertRaisesRegex(ValueError,'牌槽'):bridge.checked_resume(folder,1,play/'changed.png')

    def test_upgrade_preserves_only_matching_owner_and_instance(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(capture_service,'ROOT',Path(directory)):
            marker=Path(directory)/'output/app_upgrade.json';marker.parent.mkdir()
            marker.write_text(json.dumps({'old_pid':42,'instance':'current','expires_at':100}))
            self.assertTrue(capture_service.preserve_upgrade({'instance':'current'},[12,42],99))
            self.assertFalse(capture_service.preserve_upgrade({'instance':'different'},[42],99))
            self.assertFalse(capture_service.preserve_upgrade({'instance':'current'},[12],99))
            self.assertFalse(capture_service.preserve_upgrade({'instance':'current'},[42],101))

    @unittest.skipUnless((ROOT/'output/workflow_test_fixtures/pending_current.png').exists(),'pending click fixture absent')
    def test_pending_triple_is_confirmed_once_without_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)/FIXTURE.name;play=folder/'level_1_play';play.mkdir(parents=True)
            shutil.copyfile(FIXTURE/'map_1.json',folder/'map_1.json');shutil.copyfile(FIXTURE/'level_1_play/plan.json',play/'plan.json')
            shutil.copytree(FIXTURE/'level_1_play/templates',play/'templates')
            shutil.copyfile(ROOT/'output/workflow_test_fixtures/pending_before.png',play/'last_verified.png')
            actions=[json.loads(line) for line in (FIXTURE/'level_1_play/actions.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()][:150]
            (play/'actions.jsonl').write_text('\n'.join(json.dumps(a) for a in actions)+'\n',encoding='utf-8')
            pending={'pid':1,'next_step':150,'total':234,'expected_tray':[4,6,14,6],'input_pending':True,'state':'stopped'}
            (play/'playback_status.json').write_text(json.dumps(pending))
            with patch('capture_service.alive',return_value=False):
                with self.assertRaisesRegex(ValueError,'预期牌槽结果'):bridge.reconcile_pending(folder,1,play/'last_verified.png')
                image=ROOT/'output/workflow_test_fixtures/pending_current.png'
                self.assertTrue(bridge.reconcile_pending(folder,1,image))
                self.assertFalse(bridge.reconcile_pending(folder,1,image))
                self.assertEqual(bridge.read(play/'playback_status.json')['next_step'],151)
                self.assertEqual(len((play/'actions.jsonl').read_text(encoding='utf-8-sig').splitlines()),151)

    def fake(self,directory):
        output=Path(directory)/'workflow';capture=Path(directory)/'capture';capture.mkdir()
        status={'session':'s1','complete':True,'source_path':'/sheep/v1/game/map_info_ex','map_count':2,'seed_received':True,'maps_received':2}
        (capture/'status.json').write_text(json.dumps(status));(capture/'service_lease.json').write_text(json.dumps({'running':True,'pid':1}))
        return output,capture,status

    def test_heartbeat_continues_during_long_identification(self):
        with tempfile.TemporaryDirectory() as directory:
            output,capture,_=self.fake(directory)
            with patch.object(workflow,'OUT',output),patch.object(workflow,'CAPTURE',capture),patch.object(workflow,'alive',return_value=True):
                controller=workflow.Controller()
                def slow(*args):time.sleep(.6);raise ValueError('等待棋盘稳定')
                controller.inspect=slow
                try:
                    controller.tick();first=workflow.read(output/'status.json');time.sleep(.12);controller.tick();second=workflow.read(output/'status.json')
                    self.assertNotEqual(first['heartbeat'],second['heartbeat'])
                    self.assertTrue(second['busy']);self.assertTrue(second['seed_received'])
                    self.assertEqual(second['state'],'identifying')
                finally:controller.pool.shutdown(wait=True)

    def test_blocked_reason_survives_network_status_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            output,capture,status=self.fake(directory)
            with patch.object(workflow,'OUT',output),patch.object(workflow,'CAPTURE',capture),patch.object(workflow,'alive',return_value=True):
                controller=workflow.Controller();controller.session='s1';controller.blocked='牌面状态改变，需要重新核对'
                try:
                    controller.tick();status['game_requests']=100;(capture/'status.json').write_text(json.dumps(status));controller.tick()
                    snapshot=workflow.read(output/'status.json');self.assertEqual(snapshot['state'],'blocked');self.assertEqual(snapshot['message'],controller.blocked)
                finally:controller.pool.shutdown(wait=True)

    def test_old_session_result_cannot_start_new_session_player(self):
        with tempfile.TemporaryDirectory() as directory:
            output,capture,status=self.fake(directory)
            with patch.object(workflow,'OUT',output),patch.object(workflow,'CAPTURE',capture),patch.object(workflow,'alive',return_value=True):
                controller=workflow.Controller();controller.session='old';controller.auto=True
                controller.job=Future();controller.job_kind='prepare';controller.job_epoch=0
                controller.job.set_result({'plan_path':'old_plan','preview_path':'old_board','start_step':43})
                controller.next_inspect=time.monotonic()+100
                status['complete']=False;(capture/'status.json').write_text(json.dumps(status))
                try:
                    with patch.object(controller,'start_player') as player:
                        controller.tick();player.assert_not_called();self.assertIsNone(controller.ready)
                        self.assertEqual(controller.session,'s1')
                finally:controller.pool.shutdown(wait=True)


if __name__=='__main__':unittest.main()
