import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import speed_test


class Board:
    def __init__(self, clears):self.clears = clears
    def play_to_refresh(self, picked):return {'elimination': picked[0] in self.clears}


class SpeedTestTests(unittest.TestCase):
    def test_route_stops_on_the_last_clear_within_the_limit(self):
        with patch('speed_test.replay_prefix', return_value=Board({'c', 'f', 'i'})):
            self.assertEqual(speed_test.cut_at_clear({}, list('abcdefghi'), 8), list('abcdef'))

    def test_route_without_a_clear_is_refused(self):
        with patch('speed_test.replay_prefix', return_value=Board(set())):
            with self.assertRaisesRegex(ValueError, '没有三消'):speed_test.cut_at_clear({}, list('abc'), 3)

    def write_state(self, root, state, session='s', route=True):
        (root / 'workflow').mkdir(exist_ok=True);(root / 'auto_capture/sessions/s/level_1_static/accelerator').mkdir(parents=True, exist_ok=True)
        (root / 'workflow/status.json').write_text(json.dumps({'state': state, 'message': state}), encoding='utf-8')
        (root / 'auto_capture/status.json').write_text(json.dumps({'session': session}), encoding='utf-8')
        (root / 'auto_capture/sessions/s/map_1.json').write_text('{}', encoding='utf-8')
        if route:(root / 'auto_capture/sessions/s/level_1_static/accelerator/a.best.json').write_text('{}', encoding='utf-8')

    def test_waits_for_the_search_to_stop_before_clicking(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);self.write_state(root, 'solving')
            with patch('speed_test.OUTPUT', root):
                folder, _ = speed_test.wait_until_ready(1, sleep=lambda _:self.write_state(root, 'search_limited'))
            self.assertEqual(folder.name, 's')

    def test_a_board_between_levels_is_not_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);self.write_state(root, 'waiting_board')
            with patch('speed_test.OUTPUT', root):
                with self.assertRaisesRegex(SystemExit, 'waiting_board'):speed_test.wait_until_ready(1, seconds=2, sleep=lambda _:None)

    def test_gives_up_without_clicking_when_the_seed_never_arrives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);self.write_state(root, 'waiting_seed', route=False)
            with patch('speed_test.OUTPUT', root):
                with self.assertRaisesRegex(SystemExit, 'waiting_seed'):speed_test.wait_until_ready(1, seconds=2, sleep=lambda _:None)

    def test_summary_counts_groups_and_scales_to_a_whole_game(self):
        def action(stamp, early=False):
            return {'time': f'2026-11-05T10:00:{stamp}+09:00', 'confirmation_ms': 500.0, 'early_confirm': early}
        actions = [action('00.0000000', True), action('00.0000000', True), action('00.5000000'), action('01.0000000')]
        report = speed_test.summarize(actions, [{}])
        self.assertEqual((report['steps'], report['groups'], report['seconds']), (4, 3, 1.5))
        self.assertEqual((report['group_gap_p50_ms'], report['early_steps'], report['resent_clicks']), (500, 2, 1))
        self.assertEqual(report['whole_game_seconds'], 87.8)


if __name__ == '__main__':
    unittest.main()
