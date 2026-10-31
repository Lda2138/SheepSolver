from concurrent.futures import Future
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app_bridge
from board_watch import BoardWatch
import workflow_controller as workflow

ROOT=Path(__file__).resolve().parent.parent
DAILY=ROOT/'output/first_level_priority_regression_20261029/daily_first.png'
WORLD=ROOT/'output/alignment_regression_20261029/small_world_main.png'
OLD_MAPS=ROOT/'output/alignment_regression_20261029'


class BoardContextTests(unittest.TestCase):
    @unittest.skipUnless(DAILY.exists() and WORLD.exists(),'actual daily/world frames absent')
    def test_world_to_daily_requires_two_stable_frames_and_ignores_a_transient(self):
        watch=BoardWatch(WORLD)
        self.assertFalse(watch.changed(DAILY));self.assertFalse(watch.changed(WORLD))
        self.assertFalse(watch.changed(DAILY));self.assertTrue(watch.changed(DAILY))

    @unittest.skipUnless(DAILY.exists() and WORLD.exists(),'actual daily/world frames absent')
    def test_wrong_world_search_is_cancelled_and_its_late_result_cannot_play(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(workflow,'OUT',Path(directory)):
            controller=workflow.Controller();self.addCleanup(controller.pool.shutdown,wait=True)
            controller.auto=True;controller.job_kind='original';controller.job=Future()
            controller.job_epoch=controller.epoch;controller.level=1;controller.finished={0}
            controller.last_result={'level':1};controller.board_watch=BoardWatch(WORLD)
            with patch.object(app_bridge,'capture_snapshot',return_value=DAILY):
                controller.next_board_watch=0;controller.watch_search_board()
                self.assertEqual(controller.epoch,0)
                controller.next_board_watch=0;controller.watch_search_board()
            self.assertEqual(controller.epoch,1);self.assertIsNone(controller.level)
            self.assertEqual(controller.state,'waiting_board')
            controller.job.set_result({'plan_path':'obsolete','preview_path':'obsolete'})
            with patch.object(controller,'start_player') as player:
                controller.collect_job();self.assertIsNone(controller.ready);player.assert_not_called()

    @unittest.skipUnless(DAILY.exists(),'actual daily frame absent')
    def test_actual_daily_first_board_rejects_cached_world_maps(self):
        with self.assertRaises(app_bridge.AwaitBoard) as raised:
            app_bridge.inspect_board(OLD_MAPS,DAILY,2)
        self.assertEqual(raised.exception.state,'waiting_new_map')
        self.assertIn('缓存地图不匹配',str(raised.exception))

    def test_start_discards_old_ready_route_and_invalidates_old_search(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(workflow,'OUT',Path(directory)):
            controller=workflow.Controller();self.addCleanup(controller.pool.shutdown,wait=True)
            controller.job_kind='original';controller.job=Future();controller.ready={'plan_path':'old'}
            controller.last_result={'level':1};controller.level=1
            with patch.object(controller,'save_settings'):controller.command('start')
            self.assertIsNone(controller.ready);self.assertIsNone(controller.last_result)
            self.assertIsNone(controller.level);self.assertGreater(controller.epoch,0)

    def test_failed_inspection_clears_old_mode_level_and_solver_display(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(workflow,'OUT',Path(directory)):
            controller=workflow.Controller();self.addCleanup(controller.pool.shutdown,wait=True)
            controller.level=1;controller.last_result={'level':1};controller.mode='world'
            controller.job_kind='inspect';controller.job=Future()
            controller.job.set_exception(app_bridge.AwaitBoard('waiting_new_map','current map differs'))
            controller.collect_job();controller.snapshot({},True)
            status=workflow.read(Path(directory)/'status.json')
            self.assertEqual(status['mode'],'unknown');self.assertIsNone(status['level'])
            self.assertEqual(status['search'],{});self.assertEqual(status['playback'],{})


if __name__=='__main__':unittest.main()
