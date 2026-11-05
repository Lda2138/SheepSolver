"""A stopped full-speed group resumes from the furthest step the board proves."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import app_bridge as bridge


class GroupRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name) / 'session'
        self.play = self.folder / 'level_1_play'
        self.play.mkdir(parents=True)
        (self.folder / 'map_1.json').write_text('{}', encoding='utf-8')
        digest = hashlib.sha256((self.folder / 'map_1.json').read_bytes()).hexdigest()
        self.sequence = [f'c{i}' for i in range(6)]
        solution = {'sequence': self.sequence, 'cards': [{'id': c, 'type': 1} for c in self.sequence]}
        (self.play / 'solution.json').write_text(json.dumps(solution), encoding='utf-8')
        plan = {'solution_path': str(self.play / 'solution.json'), 'session': 'session', 'map_sha256': digest}
        (self.play / 'plan.json').write_text(json.dumps(plan), encoding='utf-8')
        (self.play / 'actions.jsonl').write_text(''.join(
            json.dumps({'step': i + 1, 'card_id': self.sequence[i]}) + '\n' for i in range(2)), encoding='utf-8')
        self.status = {'input_pending': True, 'next_step': 2, 'total': 6, 'expected_tray': [9], 'pending_group': {'start': 2, 'count': 3}}
        (self.play / 'playback_status.json').write_text(json.dumps(self.status), encoding='utf-8')
        self.image = self.play / 'now.png'
        Image.new('RGB', (336, 622)).save(self.image)

    def reconcile(self, proven):
        def verify(play, image, step, tray_only=False):
            if step != proven:raise ValueError('mismatch')
        with patch.object(bridge, 'validate_execution'), patch.object(bridge, 'verify_full_board', side_effect=verify), \
                patch.object(bridge, 'solution_state', side_effect=lambda solution, k:(None, list(range(k)))):
            return bridge.reconcile_pending(self.folder, 1, self.image)

    def result(self):
        status = json.loads((self.play / 'playback_status.json').read_text(encoding='utf-8-sig'))
        actions = [json.loads(line) for line in (self.play / 'actions.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()]
        return status, actions

    def test_resumes_after_the_clicks_that_landed(self):
        self.assertTrue(self.reconcile(4))
        status, actions = self.result()
        self.assertEqual((status['next_step'], status['input_pending'], status['expected_tray']), (4, False, [0, 1, 2, 3]))
        self.assertNotIn('pending_group', status)
        self.assertEqual([a['card_id'] for a in actions], self.sequence[:4])
        self.assertTrue((self.play / 'last_verified.png').exists())

    def test_nothing_landed_resumes_at_the_group_start(self):
        self.assertTrue(self.reconcile(2))
        status, actions = self.result()
        self.assertEqual((status['next_step'], len(actions)), (2, 2))

    def test_whole_group_landed_is_committed(self):
        self.assertTrue(self.reconcile(5))
        self.assertEqual(self.result()[0]['next_step'], 5)

    def test_board_matching_no_step_changes_nothing(self):
        with self.assertRaisesRegex(ValueError, '对不上'):self.reconcile(99)
        status, actions = self.result()
        self.assertEqual((status['next_step'], status['input_pending'], len(actions)), (2, True, 2))


if __name__ == '__main__':
    unittest.main()
