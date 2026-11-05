"""A test run must stop in the executor and never automatically finish a map."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import workflow_controller as workflow


class TestRunPauseTests(unittest.TestCase):
    def test_only_final_level_is_limited_and_controller_stays_paused(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(workflow, 'OUT', Path(directory)):
            root=Path(directory)
            controller=workflow.Controller()
            self.addCleanup(controller.pool.shutdown,wait=True)
            controller.command('start_test')
            self.assertEqual(controller.test_stop_remaining,10)
            self.assertNotIn('test_stop_remaining',controller.settings)
            answer=root/'answer.json';answer.write_text(json.dumps({'sequence':list(range(40))}),encoding='utf-8')
            plan=root/'plan.json';plan.write_text(json.dumps({'solution_path':str(answer)}),encoding='utf-8')
            controller.count=2
            for level,expected in [(0,None),(1,'30')]:
                controller.level=level;controller.ready={'plan_path':str(plan)}
                with patch.object(workflow.subprocess,'Popen') as launch:
                    controller.start_player()
                    command=launch.call_args.args[0]
                    self.assertEqual(len(command),2 if expected is None else 3)
                    if expected:self.assertEqual(command[-1],expected)
                controller.player_log.close();controller.player=None
            (root/'playback_status.json').write_text(json.dumps({'state':'paused_at_limit','next_step':30,'total':40}),encoding='utf-8')
            controller.player=Mock(pid=123,returncode=0);controller.player.poll.return_value=0
            controller.player_log=(root/'test.log').open('w');controller.player_epoch=controller.epoch
            controller.collect_player()
            self.assertFalse(controller.auto);self.assertTrue(controller.paused)
            self.assertEqual(controller.state,'paused');self.assertNotIn(1,controller.finished)
            controller.command('start')
            self.assertEqual(controller.test_stop_remaining,0)


if __name__=='__main__':unittest.main()
