"""Plays real recorded routes in the fake game; manual stress run, never touches the game.

Usage: python tests/simulate_real_routes.py [--full-speed] [--chances 0.1,0.25] [session ...]
Each route keeps its real card positions, layers and stacks, so overlaps, covered
cards and near-identical stacks behave as on the real board.
"""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import test_playback_end_to_end as e2e

SESSIONS = ROOT / 'output/auto_capture/sessions'


def load(session):
    play = SESSIONS / session / 'level_1_play'
    plan = json.loads((play / 'plan.json').read_text(encoding='utf-8-sig'))
    return plan, json.loads(Path(plan['solution_path']).read_text(encoding='utf-8-sig'))


def run_route(base, player, fake, session, seed, chance, full_speed, tray_lag=0, early=True):
    plan, solution = load(session)
    folder = base / f'{session[:15]}_{seed}_{chance}_{full_speed}'
    folder.mkdir()
    positions = plan['positions']
    tiles = [{'id': c['id'], 'type': c['type'], 'x': positions[c['id']][0], 'y': positions[c['id']][1],
              'layer': c['layer'], 'rol': c['rolNum'], 'rownum': c['rowNum']} for c in solution['cards']]
    (folder / 'tiles.json').write_text(json.dumps(tiles), encoding='utf-8')
    ready = folder / 'ready'
    game = subprocess.Popen([str(fake), str(folder / 'tiles.json'), str(folder / 'reference.png'),
                             str(folder / 'clicks.jsonl'), '0', str(ready), str(seed), str(chance), str(tray_lag)])
    started = time.monotonic()
    try:
        while not ready.exists():
            if time.monotonic() - started > 10:raise RuntimeError('fake game did not start')
            time.sleep(.02)
        cards = {c['id']: c for c in solution['cards']}
        def covered(card):
            return any(o['layer'] > card['layer'] and abs(o['rolNum'] - card['rolNum']) < 8 and abs(o['rowNum'] - card['rowNum']) < 8
                       for o in solution['cards'])
        anchors = {}
        for card in solution['cards']:
            if not covered(card):anchors.setdefault(card['type'], {'id': card['id'], 'type': card['type'], 'center': positions[card['id']]})
        run_solution = {'map_sha256': 'fake', 'validated': True, 'cards': solution['cards'],
                        'sequence': solution['sequence'], 'sequence_types': solution['sequence_types']}
        (folder / 'solution.json').write_text(json.dumps(run_solution), encoding='utf-8')
        run_plan = {'solution_path': str(folder / 'solution.json'), 'reference_path': str(folder / 'reference.png'),
                    'map_sha256': 'fake', 'positions': positions, 'anchors': list(anchors.values()),
                    'mark_milliseconds': 0, 'verify_milliseconds': 20, 'verification_mode': 'ultra', 'full_speed': full_speed,
                    'adaptive_verify': True, 'dynamic_anchors': True, 'full_check_interval': 30, 'retry_milliseconds': 500,
                    'clear_gap_milliseconds': 150, 'click_gap_milliseconds': 135, 'burst_gap_milliseconds': 35,
                    'clear_hold_milliseconds': 460, 'early_confirm': early}
        (folder / 'plan.json').write_text(json.dumps(run_plan), encoding='utf-8')
        begun = time.monotonic()
        try:result = subprocess.run([str(player), str(folder / 'plan.json')], capture_output=True, timeout=200, creationflags=e2e.NO_WINDOW)
        except subprocess.TimeoutExpired:result = None
        elapsed = time.monotonic() - begun
    finally:
        game.kill();game.wait(timeout=5)
    clicks = [json.loads(line) for line in (folder / 'clicks.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    status = json.loads((folder / 'playback_status.json').read_text(encoding='utf-8-sig'))
    accepted = [c['id'] for c in clicks if c['kind'] == 'accepted']
    wanted = solution['sequence']
    extra = len(set(accepted[:]) - set(wanted)) + len(accepted) - len(set(accepted))
    order_ok = sorted(accepted) == sorted(wanted[:len(accepted)])
    actions = [json.loads(line) for line in (folder / 'actions.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    outcome = {'early': sum(bool(a.get('early_confirm')) for a in actions), 'session': session[:15], 'seed': seed, 'chance': chance, 'state': status.get('state'), 'step': status.get('next_step'),
               'total': len(wanted), 'seconds': round(elapsed, 1), 'dropped': sum(c['kind'] == 'dropped' for c in clicks),
               'overflow': sum(c['kind'] == 'overflow' for c in clicks), 'miss': sum(c['kind'] == 'miss' for c in clicks),
               'off_route': extra, 'set_ok': order_ok, 'message': (status.get('message') or '')[:70],
               'folder': str(folder)}
    print(json.dumps(outcome, ensure_ascii=False), flush=True)
    return outcome


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('sessions', nargs='*')
    parser.add_argument('--ultra', action='store_true')
    parser.add_argument('--chances', default='0.1,0.25')
    parser.add_argument('--seeds', type=int, default=1)
    parser.add_argument('--keep', type=Path)
    parser.add_argument('--no-early', action='store_true', help='wait for the tray reading on every group')
    parser.add_argument('--tray-lag', type=int, default=0, help='extra ms before a landed card is drawn; the real tray reads late')
    args = parser.parse_args()
    base = args.keep or Path(tempfile.mkdtemp());base.mkdir(parents=True, exist_ok=True)
    player, fake = base / 'Player.exe', base / 'FakeGame.exe'
    e2e.compile_exe(player, 'SolverPlayback', [ROOT / path for path in e2e.PLAYER_SOURCES])
    e2e.compile_exe(fake, 'FakeGameWindow', [ROOT / 'tests/native/FakeGameWindow.cs'])
    outcomes = []
    for session in args.sessions:
        for chance in [float(v) for v in args.chances.split(',')]:
            for seed in range(1, args.seeds + 1):
                outcomes.append(run_route(base, player, fake, session, seed, chance, not args.ultra, args.tray_lag, not args.no_early))
    won = [o for o in outcomes if o['state'] == 'sequence_finished' and not o['overflow'] and not o['off_route']]
    print(f"cleared {len(won)}/{len(outcomes)}; median seconds {statistics.median(o['seconds'] for o in won) if won else None}", flush=True)


if __name__ == '__main__':
    main()
