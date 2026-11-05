"""Movement recovery, local-only execution and independent checkpoint checks.

The captured regression is copied before touching the user's live checkpoint.
All tests are offline and never send game input.
"""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

import app_bridge as bridge
import workflow_controller as workflow

ROOT=Path(__file__).resolve().parent.parent
FIXTURE=ROOT/'output/window_movement_regression_20261103/20261103T031902Z-79e35a'


class FastSettingsTests(unittest.TestCase):
    @unittest.skipUnless((ROOT/'output/auto_capture/sessions/20261103T072648Z-c6dbe0/level_1_play/plan.json').exists(),'captured fast run absent')
    def test_native_logical_counts_match_independent_replay_for_every_confirmed_prefix(self):
        from color_rules import solution_state
        source=ROOT/'output/auto_capture/sessions/20261103T072648Z-c6dbe0/level_1_play/plan.json'
        plan=bridge.read(source);answer=bridge.read(Path(plan['solution_path']))
        # An offline dump validates the whole route without querying or clicking a window.
        plan.pop('capture_status_path',None);plan.pop('service_lease_path',None)
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'plan.json';path.write_text(json.dumps(plan),encoding='utf-8')
            result=subprocess.run([str(ROOT/'SolverPlayback.exe'),str(path),'--dump-tray-trace'],
                                  capture_output=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode,0,result.stderr.decode('utf-8-sig',errors='replace'))
            trace=json.loads(result.stdout.decode('utf-8-sig'))
        self.assertEqual(len(trace),len(answer['sequence'])+1)
        for n,tray in enumerate(trace):self.assertEqual(tray,solution_state(answer,n)[1],f'prefix {n}')
        self.assertEqual(trace[13],[10,4,2,6]);self.assertEqual(trace[-1],[])

    @unittest.skipUnless((ROOT/'output/world_effect_regression_20261103/sheep_overlay.png').exists(),'captured sheep effect absent')
    def test_real_world_sheep_overlay_is_not_confirmed_as_a_removed_card(self):
        fixture=ROOT/'output/world_effect_regression_20261103'
        result=subprocess.run([str(ROOT/'SolverPlayback.exe'),'--check-animation-occlusion',
                               str(fixture/'before.png'),str(fixture/'sheep_overlay.png'),'202','200'],
                              capture_output=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode,0,result.stderr.decode('utf-8-sig',errors='replace'))

    def test_native_local_target_and_checkpoint_gates(self):
        result=subprocess.run([str(ROOT/'SolverPlayback.exe'),'--check-ultra'],capture_output=True,
                              timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode,0,result.stderr.decode('utf-8-sig',errors='replace'))
        self.assertIn(b'no input',result.stdout)

    def test_modes_and_custom_interval_reach_plan_and_live_command(self):
        with tempfile.TemporaryDirectory() as d,patch.object(workflow,'OUT',Path(d)):
            controller=workflow.Controller()
            self.addCleanup(controller.pool.shutdown,wait=True)
            for mode in ('normal','ultra','safe_ultra'):
                for interval in (1,7,30,1000):
                    controller.settings.update(verification_mode=mode,full_check_interval=interval)
                    timing=controller.timing()
                    self.assertEqual((timing['verification_mode'],timing['full_check_interval']),(mode,interval))
                    if mode!='normal':self.assertEqual((timing['mark_milliseconds'],timing['verify_milliseconds']),(0,20))
            controller.settings.update(verification_mode='invalid',full_check_interval=0)
            self.assertEqual(controller.timing()['verification_mode'],'normal')
            self.assertEqual(controller.timing()['full_check_interval'],1)
            controller.settings['full_check_interval']=99999
            self.assertEqual(controller.timing()['full_check_interval'],1000)
            controller.settings.update(verification_mode='ultra',full_speed=True)
            self.assertTrue(controller.timing()['full_speed'])
            # Measured safe floor: real clears from a full tray ran at 140 ms or more.
            self.assertGreaterEqual(controller.timing()['clear_gap_milliseconds'],140)
            self.assertGreaterEqual(controller.timing()['click_gap_milliseconds'],130)
            self.assertGreaterEqual(controller.timing()['retry_milliseconds'],150)
            controller.settings['verification_mode']='normal'
            self.assertFalse(controller.timing()['full_speed'])
            from unittest.mock import Mock
            controller.player=Mock();controller.player.poll.return_value=None
            with patch.object(controller,'save_settings'),patch.object(controller,'play_command') as send:
                controller.command('settings',{'verification_mode':'safe_ultra','full_check_interval':7})
                self.assertEqual(send.call_args.kwargs['full_check_interval'],7)
                self.assertEqual(send.call_args.kwargs['verification_mode'],'safe_ultra')


@unittest.skipUnless((FIXTURE/'level_1_play/resume_check.png').exists(),'captured moved-window boundary absent')
class CapturedMovementTests(unittest.TestCase):
    def copy(self,d):
        folder=Path(d)/FIXTURE.name;shutil.copytree(FIXTURE,folder)
        play=folder/'level_1_play';plan=bridge.read(play/'plan.json')
        plan['solution_path']=str(folder/'solution.json')
        for key in ('reference_path','initial_reference_path'):
            if key in plan:plan[key]=str(play/Path(plan[key]).name)
        (play/'plan.json').write_text(json.dumps(plan),encoding='utf-8')
        return folder,play

    def test_redrawn_correct_slot_matches_but_other_icon_is_rejected(self):
        play=FIXTURE/'level_1_play'
        old=np.asarray(Image.open(play/'last_verified.png').convert('RGB'),dtype=np.int16)
        now=np.asarray(Image.open(play/'resume_check.png').convert('RGB'),dtype=np.int16)
        self.assertGreater(bridge.pattern_score(now,(43,511),[old[493:529,110:144]]),8)
        self.assertLess(bridge.tray_pattern_score(now,(43,511),old,(127,511)),8)
        self.assertGreater(bridge.tray_pattern_score(now,(43,511),old,(43,511)),100)

    def test_sent_step_211_is_confirmed_exactly_once_after_move(self):
        with tempfile.TemporaryDirectory() as d,patch('capture_service.alive',return_value=False):
            folder,play=self.copy(d)
            self.assertTrue(bridge.reconcile_pending(folder,1,play/'resume_check.png'))
            status=bridge.read(play/'playback_status.json')
            self.assertEqual((status['next_step'],status['input_pending']),(211,False))
            self.assertEqual(len((play/'actions.jsonl').read_text(encoding='utf-8-sig').splitlines()),211)
            self.assertFalse(bridge.reconcile_pending(folder,1,play/'last_verified.png'))
            self.assertEqual(bridge.checked_resume(folder,1,play/'last_verified.png')['start_step'],211)

    def test_full_check_accepts_real_board_and_blocks_wrong_slot_missing_or_extra_card(self):
        with tempfile.TemporaryDirectory() as d:
            _,play=self.copy(d);source=play/'resume_check.png'
            frame=Image.open(source).convert('RGB')
            result=bridge.verify_full_board(play,source,211)
            self.assertTrue(result['verified']);self.assertEqual(result['tray_count'],1)
            self.assertGreaterEqual(result['exposed_cards_checked'],9)
            wrong=frame.copy()
            old=Image.open(play/'last_verified.png').convert('RGB')
            wrong.paste(old.crop((26,493,60,529)),(26,493))
            image=play/'wrong_tray.png';wrong.save(image)
            with self.assertRaisesRegex(ValueError,'牌型'):bridge.verify_full_board(play,image,211)
            wrong=frame.copy();ImageDraw.Draw(wrong).rectangle((136,151,163,186),fill=(193,255,137))
            image=play/'missing.png';wrong.save(image)
            with self.assertRaises(ValueError):bridge.verify_full_board(play,image,211)
            wrong=frame.copy();ImageDraw.Draw(wrong).rectangle((265,395,305,435),fill=(250,245,225))
            image=play/'extra.png';wrong.save(image)
            with self.assertRaises(ValueError):bridge.verify_full_board(play,image,211)

    def test_fast_resume_uses_independent_full_check_instead_of_inflight_tray(self):
        with tempfile.TemporaryDirectory() as d,patch('capture_service.alive',return_value=False):
            folder,play=self.copy(d)
            status=bridge.read(play/'playback_status.json');status['verification_mode']='safe_ultra'
            (play/'playback_status.json').write_text(json.dumps(status),encoding='utf-8')
            self.assertTrue(bridge.reconcile_pending(folder,1,play/'resume_check.png'))
            self.assertEqual(bridge.checked_resume(folder,1,play/'last_verified.png')['start_step'],211)


WORLD=ROOT/'output/world_ultra_regression_20261103'


@unittest.skipUnless((ROOT/'output/fast_failure_regression_20261103/play/last_verified.png').exists(),'captured in-flight tray absent')
class InFlightCapacityTests(unittest.TestCase):
    def test_inflight_step_12_cannot_release_capacity_from_target_removal_alone(self):
        source=ROOT/'output/fast_failure_regression_20261103'
        with tempfile.TemporaryDirectory() as d:
            play=Path(d);shutil.copytree(source/'play',play,dirs_exist_ok=True)
            shutil.copyfile(source/'solution.json',play/'answer.json')
            plan=bridge.read(play/'plan.json');plan['solution_path']=str(play/'answer.json');plan['reference_path']=str(play/'reference.png')
            (play/'plan.json').write_text(json.dumps(plan),encoding='utf-8')
            with self.assertRaises(ValueError):bridge.verify_full_board(play,play/'last_verified.png',12,tray_only=True)
            self.assertTrue(bridge.verify_full_board(play,play/'reference.png',0,tray_only=True)['verified'])


@unittest.skipUnless((WORLD/'play/last_verified.png').exists(),'captured world boundary absent')
class WorldFastPlaybackTests(unittest.TestCase):
    def copy(self,d):
        folder=Path(d)/'20261103T034822Z-4e9d6d';play=folder/'level_1_play'
        shutil.copytree(WORLD/'play',play)
        shutil.copyfile(WORLD/'map.json',folder/'map_1.json')
        shutil.copyfile(WORLD/'solution.json',folder/'solution.json')
        plan=bridge.read(play/'plan.json');plan['solution_path']=str(folder/'solution.json')
        for key in ('reference_path','initial_reference_path'):
            if key in plan:plan[key]=str(play/Path(plan[key]).name)
        (play/'plan.json').write_text(json.dumps(plan),encoding='utf-8')
        return folder,play

    def test_world_board_and_grouped_tray_pass_independent_checkpoint(self):
        with tempfile.TemporaryDirectory() as d:
            _,play=self.copy(d)
            report=bridge.verify_full_board(play,play/'last_verified.png',223)
            self.assertTrue(report['verified']);self.assertEqual(report['tray_count'],4)

    def test_world_no_effect_boundary_can_be_automatically_recovered_without_reclick(self):
        with tempfile.TemporaryDirectory() as d,patch('capture_service.alive',return_value=False):
            folder,play=self.copy(d)
            self.assertTrue(bridge.reconcile_pending(folder,1,play/'last_verified.png'))
            status=bridge.read(play/'playback_status.json')
            self.assertEqual(status['next_step'],223);self.assertFalse(status['input_pending'])
            self.assertEqual(len((play/'actions.jsonl').read_text(encoding='utf-8-sig').splitlines()),223)


DROPPED=ROOT/'output/dropped_click_regression_20261103'


@unittest.skipUnless((DROPPED/'play/verification_timeout.png').exists(),'captured dropped click absent')
class DroppedClickTests(unittest.TestCase):
    def test_click_lost_during_triple_clear_is_restored_although_last_frame_was_in_flight(self):
        # last_verified.png still shows step 143 flying across the step 144 target.
        with tempfile.TemporaryDirectory() as d,patch('capture_service.alive',return_value=False):
            folder=Path(d)/'20261103T085326Z-475ed4';play=folder/'level_1_play'
            shutil.copytree(DROPPED/'play',play)
            shutil.copyfile(DROPPED/'map.json',folder/'map_1.json')
            shutil.copyfile(DROPPED/'solution.json',folder/'solution.json')
            plan=bridge.read(play/'plan.json');plan['solution_path']=str(folder/'solution.json');plan['reference_path']=str(play/'reference.png')
            (play/'plan.json').write_text(json.dumps(plan),encoding='utf-8')
            self.assertTrue(bridge.reconcile_pending(folder,1,play/'verification_timeout.png'))
            status=bridge.read(play/'playback_status.json')
            self.assertEqual(status['next_step'],143);self.assertFalse(status['input_pending'])
            self.assertEqual(status['expected_tray'],[4,16,6,6,1])
            self.assertEqual(bridge.checked_resume(folder,1,play/'verification_timeout.png')['start_step'],143)


if __name__=='__main__':unittest.main()
