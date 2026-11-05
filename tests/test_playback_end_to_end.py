"""Runs the real player against a fake game window; touches no real game."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parent.parent
CSC = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
UI = ['/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll', '/reference:System.Web.Extensions.dll']
PLAYER_SOURCES = ('native/clicker/SheepClicker.cs', 'native/clicker/SolverPlayback.cs',
                  'native/planner/RollingPlanner.cs', 'native/rules/ColorRules.cs', 'native/rules/EnergyRules.cs')
TYPES = 14


def compile_exe(output, main, sources):
    # PLAYBACK_TEST makes the player look for the fake window's title only.
    command = [str(CSC), '/nologo', '/optimize+', '/target:exe', '/define:PLAYBACK_TEST', '/main:' + main, *UI, '/out:' + str(output)]
    command += [str(source) for source in sources]
    build = subprocess.run(command, capture_output=True, timeout=60, creationflags=NO_WINDOW)
    if build.returncode:raise AssertionError(build.stdout.decode(errors='replace'))


def board():
    """Every card is exposed. The route holds pairs until the tray has six cards
    and clears from full, like real routes, so a click arriving during a clear
    overflows the fake game."""
    tiles = []
    for index in range(TYPES * 3):
        row, col = divmod(index, 6)
        tiles.append({'id': f'c{index}', 'type': index % TYPES, 'x': 40 + 48 * col, 'y': 150 + 44 * row,
                      'row': row, 'col': col})
    by_type = {t: [tile for tile in tiles if tile['type'] == t] for t in range(TYPES)}
    sequence, held = [], []
    for type_id in range(TYPES):
        if len(held) == 3:
            sequence.append(by_type[held.pop(0)][2]['id'])
        sequence += [by_type[type_id][0]['id'], by_type[type_id][1]['id']]
        held.append(type_id)
    sequence += [by_type[type_id][2]['id'] for type_id in held]
    return tiles, sequence


def write_plan(folder, tiles, sequence, full_speed):
    by_id = {tile['id']: tile for tile in tiles}
    cards = [{'id': t['id'], 'type': t['type'], 'layer': 1, 'rolNum': t['col'] * 8, 'rowNum': t['row'] * 8,
              'metaType': 0, 'metaData': 0} for t in tiles]
    solution = {'map_sha256': 'fake', 'validated': True, 'cards': cards, 'sequence': sequence,
                'sequence_types': [by_id[i]['type'] for i in sequence]}
    (folder / 'solution.json').write_text(json.dumps(solution), encoding='utf-8')
    first = {}
    for tile in tiles:first.setdefault(tile['type'], tile)
    plan = {'solution_path': str(folder / 'solution.json'), 'reference_path': str(folder / 'reference.png'),
            'map_sha256': 'fake', 'positions': {t['id']: [t['x'], t['y']] for t in tiles},
            'anchors': [{'id': t['id'], 'type': t['type'], 'center': [t['x'], t['y']]} for t in first.values()],
            'mark_milliseconds': 0, 'verify_milliseconds': 20, 'verification_mode': 'ultra', 'full_speed': full_speed,
            'adaptive_verify': True, 'dynamic_anchors': True, 'full_check_interval': 30,
            'retry_milliseconds': 500, 'clear_gap_milliseconds': 150, 'click_gap_milliseconds': 135}
    (folder / 'plan.json').write_text(json.dumps(plan), encoding='utf-8')
    return folder / 'plan.json'


def run_game(folder, player, fake, drop_every=0, full_speed=False):
    tiles, sequence = board()
    (folder / 'tiles.json').write_text(json.dumps(tiles), encoding='utf-8')
    ready = folder / 'ready'
    game = subprocess.Popen([str(fake), str(folder / 'tiles.json'), str(folder / 'reference.png'),
                             str(folder / 'clicks.jsonl'), str(drop_every), str(ready)])
    try:
        deadline = time.monotonic() + 10
        while not ready.exists():
            if time.monotonic() > deadline:raise AssertionError('fake game did not start')
            time.sleep(.02)
        plan = write_plan(folder, tiles, sequence, full_speed)
        result = subprocess.run([str(player), str(plan)], capture_output=True, timeout=60, creationflags=NO_WINDOW)
    finally:
        game.kill();game.wait(timeout=5)
    clicks = [json.loads(line) for line in (folder / 'clicks.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    actions = [json.loads(line) for line in (folder / 'actions.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    status = json.loads((folder / 'playback_status.json').read_text(encoding='utf-8-sig'))
    return result, clicks, actions, status, sequence


@unittest.skipUnless(CSC.exists(), 'Windows C# compiler required')
class PlaybackEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        base = Path(cls.temp.name)
        cls.player, cls.fake = base / 'Player.exe', base / 'FakeGame.exe'
        compile_exe(cls.player, 'SolverPlayback', [ROOT / path for path in PLAYER_SOURCES])
        compile_exe(cls.fake, 'FakeGameWindow', [ROOT / 'tests/native/FakeGameWindow.cs'])

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def play(self, name, **options):
        folder = Path(self.temp.name) / name
        folder.mkdir()
        return folder, run_game(folder, self.player, self.fake, **options)

    def assert_exact_route(self, folder, result, clicks, actions, status, sequence):
        self.assertEqual(result.returncode, 0, result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace'))
        # A resent card lands later than the rest of its group, but every card
        # must be taken exactly once.
        self.assertEqual(sorted(c['id'] for c in clicks if c['kind'] == 'accepted'), sorted(sequence))
        self.assertEqual([c for c in clicks if c['kind'] in ('miss', 'overflow')], [])
        self.assertEqual([a['card_id'] for a in actions], sequence)
        self.assertEqual((status['state'], status['next_step'], status['input_pending']), ('sequence_finished', len(sequence), False))
        self.assertTrue((folder / 'last_verified.png').exists())

    def test_route_runs_exactly_at_the_measured_click_spacing(self):
        folder, (result, clicks, actions, status, sequence) = self.play('clean')
        self.assert_exact_route(folder, result, clicks, actions, status, sequence)
        self.assertEqual([c['id'] for c in clicks if c['kind'] == 'accepted'], sequence)
        self.assertEqual(sum(a['input_retries'] for a in actions), 0)
        types = {tile['id']: tile['type'] for tile in board()[0]}
        accepted = [c for c in clicks if c['kind'] == 'accepted']
        counts = {}
        gaps = []
        for click, following in zip(accepted, accepted[1:]):
            counts[types[click['id']]] = counts.get(types[click['id']], 0) + 1
            if counts[types[click['id']]] == 3:gaps.append(following['ms'] - click['ms'])
        self.assertGreaterEqual(len(gaps), 10)
        # A small allowance covers the two processes' clock reads.
        self.assertGreaterEqual(min(gaps), 149)
        self.assertGreaterEqual(min(b['ms'] - a['ms'] for a, b in zip(accepted, accepted[1:])), 133)

    def test_dropped_clicks_are_resent_without_double_clicks(self):
        folder, (result, clicks, actions, status, sequence) = self.play('drops', drop_every=2)
        self.assert_exact_route(folder, result, clicks, actions, status, sequence)
        dropped = sum(c['kind'] == 'dropped' for c in clicks)
        self.assertGreaterEqual(dropped, 2)
        self.assertEqual(sum(a['input_retries'] for a in actions), dropped)
        recovery = [json.loads(line) for line in (folder / 'input_recovery.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()]
        self.assertEqual([item['result'] for item in recovery], ['resent_in_process'] * dropped)

    def test_lost_triple_click_at_full_tray_is_never_trusted(self):
        for full_speed in (False, True):
            with self.subTest(full_speed=full_speed):
                folder, (result, clicks, actions, status, sequence) = self.play(f'full_tray_{full_speed}', drop_every=-1, full_speed=full_speed)
                self.assert_exact_route(folder, result, clicks, actions, status, sequence)
                self.assertGreaterEqual(sum(c['kind'] == 'dropped' for c in clicks), 5)

    def test_full_speed_setting_keeps_the_same_route(self):
        folder, outcome = self.play('full_speed', full_speed=True, drop_every=3)
        self.assert_exact_route(folder, *outcome)

    def test_full_speed_clicks_groups_back_to_back(self):
        folder, (result, clicks, actions, status, sequence) = self.play('burst', full_speed=True)
        self.assert_exact_route(folder, result, clicks, actions, status, sequence)
        accepted = [c['ms'] for c in clicks if c['kind'] == 'accepted']
        gaps = [b - a for a, b in zip(accepted, accepted[1:])]
        self.assertGreater(sum(gap < 60 for gap in gaps), len(gaps) // 4)
        types = {tile['id']: tile['type'] for tile in board()[0]}
        counts, clears = {}, []
        for click in (c for c in clicks if c['kind'] == 'accepted'):
            counts[types[click['id']]] = counts.get(types[click['id']], 0) + 1
            if counts[types[click['id']]] == 3:clears.append(click['ms'])
        self.assertGreaterEqual(min(b - a for a, b in zip(clears[1:-3], clears[2:-2])), 459)
        self.assertGreater(sum(a['verification'] == 'burst_group' for a in actions), len(actions) // 2)


if __name__ == '__main__':
    unittest.main()
