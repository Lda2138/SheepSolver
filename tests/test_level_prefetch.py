"""The next level is solved during this one, with one planner per output folder."""
from concurrent.futures import Future
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import app_bridge as bridge
import workflow_controller as workflow


class LevelPrefetchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        patcher = patch.object(workflow, 'OUT', self.folder / 'workflow')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.controller = workflow.Controller()
        self.addCleanup(self.controller.pool.shutdown, wait=True)
        self.addCleanup(self.controller.prefetch_pool.shutdown, wait=True)
        self.controller.settings.update(solver_engine='static_constraint_v1', seconds=60, performance=2, partial_steps=0)
        self.controller.folder = self.folder
        self.controller.count = 2
        (self.folder / 'map_1.json').write_text(json.dumps({'level': []}), encoding='utf-8')
        self.calls = []
        self.output = self.folder / 'level_1_static'

    def planner(self, map_path, prefix, output, seconds, cancel, performance):
        self.calls.append((Path(map_path).name, prefix, output, seconds, performance))
        return output, {}

    def start(self, level=0, compatibility='standard'):
        with patch.object(bridge, 'compatibility', return_value=compatibility), \
                patch.object(self.controller, 'fixed_planner', return_value=(self.planner, self.output)):
            self.controller.start_prefetch(level)
        if self.controller.prefetch:self.controller.prefetch.result(timeout=5)

    def test_next_level_is_planned_with_the_normal_budget(self):
        self.start()
        self.assertEqual(self.calls, [('map_1.json', [], self.output, 60, 'fast')])

    def test_each_level_is_prefetched_once(self):
        self.start()
        self.start()
        self.assertEqual(len(self.calls), 1)

    def test_skips_last_level_special_rules_other_engines_and_finished_routes(self):
        self.start(level=1)
        self.start(compatibility='energy_alien')
        self.controller.settings['solver_engine'] = 'upstream_normal_snapshot_v1'
        self.start()
        self.controller.settings['solver_engine'] = 'static_constraint_v1'
        (self.output / 'complete').mkdir(parents=True)
        (self.output / 'complete/solution.json').write_text('{}', encoding='utf-8')
        self.start()
        self.assertEqual(self.calls, [])

    def test_cancellation_follows_pause_stop_close_and_new_epoch(self):
        seen = []
        def planner(map_path, prefix, output, seconds, cancel, performance):seen.append(cancel)
        with patch.object(bridge, 'compatibility', return_value='standard'), \
                patch.object(self.controller, 'fixed_planner', return_value=(planner, self.output)):
            self.controller.start_prefetch(0)
        self.controller.prefetch.result(timeout=5)
        cancel = seen[0]
        self.assertFalse(cancel())
        for name, value in (('paused', True), ('stopping', True), ('closing', True)):
            setattr(self.controller, name, value)
            self.assertTrue(cancel())
            setattr(self.controller, name, False)
        self.controller.epoch += 1
        self.assertTrue(cancel())

    def test_failed_prefetch_leaves_the_normal_plan_free_to_search(self):
        job = Future()
        job.set_exception(ValueError('本轮约束求解未得到完整答案'))
        self.controller.prefetch, self.controller.prefetch_output = job, self.output
        self.controller.join_prefetch(self.output, lambda:False)

    def test_normal_plan_waits_for_the_folder_owner_and_can_be_cancelled(self):
        job = Future()
        self.controller.prefetch, self.controller.prefetch_output = job, self.output
        with self.assertRaises(ValueError):self.controller.join_prefetch(self.output, lambda:True)
        released = threading.Event()
        worker = threading.Thread(target=lambda:(self.controller.join_prefetch(self.output, lambda:False), released.set()))
        worker.start()
        self.assertFalse(released.wait(.15))
        job.set_result(None)
        self.assertTrue(released.wait(2))
        worker.join(timeout=2)

    def test_other_folders_do_not_wait(self):
        self.controller.prefetch, self.controller.prefetch_output = Future(), self.output
        self.controller.join_prefetch(self.folder / 'level_0_static', lambda:False)


if __name__ == '__main__':
    unittest.main()
