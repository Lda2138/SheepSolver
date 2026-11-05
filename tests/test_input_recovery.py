"""Real captured pending-input boundaries; these tests never send input."""
import json
from datetime import datetime,timezone,timedelta
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import app_bridge as bridge
from color_rules import solution_state

ROOT=Path(__file__).resolve().parent.parent
FIXTURE=ROOT/'output/auto_capture/sessions/20261008T162423Z-e73fea'
IMAGE=ROOT/'output/workflow_test_fixtures/halfway_clear.png'


@unittest.skipUnless(IMAGE.exists() and FIXTURE.exists(),'local captured boundary absent')
class InputRecoveryTests(unittest.TestCase):
    def boundary(self,directory):
        folder=Path(directory)/FIXTURE.name;play=folder/'level_1_play';play.mkdir(parents=True)
        shutil.copyfile(FIXTURE/'map_1.json',folder/'map_1.json')
        plan=bridge.read(FIXTURE/'level_1_play/plan.json')
        (play/'plan.json').write_text(json.dumps(plan),encoding='utf-8')
        answer=bridge.read(Path(plan['solution_path']));n=117
        actions=[json.loads(line) for line in (FIXTURE/'level_1_play/actions.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()][:n]
        (play/'actions.jsonl').write_text('\n'.join(json.dumps(a) for a in actions)+'\n',encoding='utf-8')
        status={'pid':1,'state':'stopped','input_pending':True,'next_step':n,'total':len(answer['sequence']),
            'expected_tray':solution_state(answer,n+1)[1],
            'time':(datetime.now(timezone.utc)-timedelta(seconds=10)).isoformat()}
        (play/'playback_status.json').write_text(json.dumps(status),encoding='utf-8')
        shutil.copyfile(IMAGE,play/'last_verified.png')
        return folder,play,answer,n

    def test_no_effect_restores_boundary_once_without_advancing_confirmed_actions(self):
        with tempfile.TemporaryDirectory() as directory,patch('capture_service.alive',return_value=False):
            folder,play,answer,n=self.boundary(directory)
            self.assertTrue(bridge.reconcile_pending(folder,1,IMAGE))
            status=bridge.read(play/'playback_status.json')
            self.assertEqual(status['next_step'],n);self.assertFalse(status['input_pending'])
            self.assertEqual(status['expected_tray'],solution_state(answer,n)[1])
            self.assertEqual(len((play/'actions.jsonl').read_text(encoding='utf-8-sig').splitlines()),n)
            self.assertFalse(bridge.reconcile_pending(folder,1,IMAGE))
            self.assertEqual(len((play/'input_recovery.jsonl').read_text(encoding='utf-8').splitlines()),1)

    def test_live_sender_is_never_retried(self):
        with tempfile.TemporaryDirectory() as directory,patch('capture_service.alive',return_value=True):
            folder,play,_,_=self.boundary(directory)
            with self.assertRaisesRegex(ValueError,'仍在确认'):bridge.reconcile_pending(folder,1,IMAGE)
            self.assertTrue(bridge.read(play/'playback_status.json')['input_pending'])

    def test_pending_resume_error_is_not_reported_as_a_missing_seed(self):
        with tempfile.TemporaryDirectory() as directory:
            folder,_,_,_=self.boundary(directory)
            with patch.object(bridge,'fresh_board_candidates',return_value=[]),patch.object(bridge,'reconcile_pending',side_effect=ValueError('点击仍待核对')):
                with self.assertRaises(bridge.AwaitBoard) as caught:bridge.inspect_board(folder,IMAGE,2)
            self.assertEqual(caught.exception.state,'waiting_resume')
            self.assertIn('地图和种子已就绪',str(caught.exception))


FIRST = ROOT/'output/auto_capture/sessions/20261103T112117Z-6a7f6b'


@unittest.skipUnless((FIRST/'level_0_play/plan.json').exists(), 'captured first-step frame absent')
class FirstInputRecoveryTests(unittest.TestCase):
    def boundary(self, directory):
        folder=Path(directory)/FIRST.name;play=folder/'level_0_play';play.mkdir(parents=True)
        shutil.copyfile(FIRST/'map_0.json',folder/'map_0.json')
        plan=bridge.read(FIRST/'level_0_play/plan.json')
        answer=bridge.read(Path(plan['solution_path']))
        (folder/'solution.json').write_text(json.dumps(answer),encoding='utf-8')
        plan['solution_path']=str(folder/'solution.json')
        source=Path(plan.get('initial_reference_path',plan['reference_path']))
        shutil.copyfile(source,play/'reference.png')
        plan['reference_path']=plan['initial_reference_path']=str(play/'reference.png')
        (play/'plan.json').write_text(json.dumps(plan),encoding='utf-8')
        status={'pid':1,'state':'stopped','input_pending':True,'next_step':0,'total':len(answer['sequence']),
                'expected_tray':solution_state(answer,1)[1],'verification_mode':'ultra',
                'time':(datetime.now(timezone.utc)-timedelta(seconds=10)).isoformat()}
        (play/'playback_status.json').write_text(json.dumps(status),encoding='utf-8')
        return folder,play

    def test_first_lost_input_can_resume_without_an_action_log_or_confirmed_frame(self):
        with tempfile.TemporaryDirectory() as directory,patch('capture_service.alive',return_value=False):
            folder,play=self.boundary(directory)
            self.assertFalse((play/'actions.jsonl').exists())
            self.assertFalse((play/'last_verified.png').exists())
            self.assertTrue(bridge.reconcile_pending(folder,0,play/'reference.png'))
            status=bridge.read(play/'playback_status.json')
            self.assertEqual((status['next_step'],status['input_pending'],status['expected_tray']),(0,False,[]))
            self.assertEqual((play/'actions.jsonl').read_text(encoding='utf-8'),'')
            self.assertEqual(bridge.checked_resume(folder,0,play/'reference.png')['start_step'],0)
            self.assertFalse(bridge.reconcile_pending(folder,0,play/'reference.png'))

    def test_changed_first_frame_is_not_treated_as_a_lost_click(self):
        from PIL import Image,ImageDraw
        with tempfile.TemporaryDirectory() as directory,patch('capture_service.alive',return_value=False):
            folder,play=self.boundary(directory)
            image=Image.open(play/'reference.png').convert('RGB')
            ImageDraw.Draw(image).rectangle((20,118,316,467),fill=(193,255,137))
            changed=play/'changed.png';image.save(changed)
            with self.assertRaises(ValueError):bridge.reconcile_pending(folder,0,changed)
            self.assertTrue(bridge.read(play/'playback_status.json')['input_pending'])


if __name__=='__main__':unittest.main()
