"""Measures click speed on a board with no complete route.

Runs the first steps of the deepest partial route found for the current game, then
stops. The game cannot be won from there, so restart it afterwards. Clicks only
after the screen has been matched to the map.

Usage: python speed_test.py [--steps 100] [--level 1]
"""
import argparse
import glob
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

import app_bridge
from rolling_play import make_segment, replay_prefix
from solve_verified_map import atomic_json

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / 'output'
# Only these leave the board still: between levels the app looks idle for a moment
# while the next board is still animating in.
SETTLED = ('search_limited', 'prop_ready', 'blocked')
# The speeds the app uses for full-speed play.
TIMING = {'mark_milliseconds': 0, 'verify_milliseconds': 20, 'verification_mode': 'ultra', 'full_speed': True,
          'retry_milliseconds': 500, 'clear_gap_milliseconds': 150, 'click_gap_milliseconds': 135,
          'burst_gap_milliseconds': 35, 'clear_hold_milliseconds': 460, 'adaptive_verify': True,
          'dynamic_anchors': True, 'full_check_interval': 30}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def wait_until_ready(level, seconds=120, sleep=time.sleep):
    """Waits out the seed and the search, so the test can be started at any moment."""
    shown = None
    for _ in range(int(seconds)):
        workflow = read(OUTPUT / 'workflow/status.json')
        capture = read(OUTPUT / 'auto_capture/status.json')
        folder = OUTPUT / 'auto_capture/sessions' / capture.get('session', '')
        if capture.get('session') and (folder / f'map_{level}.json').exists() and workflow.get('state') in SETTLED:
            if glob.glob(str(folder / f'level_{level}_static/accelerator/*.best.json')):return folder, capture
        message = workflow.get('message', '')
        if message != shown:print('等待：' + message, flush=True);shown = message
        sleep(1)
    raise SystemExit(f'等了 {seconds} 秒仍未就绪，没有点击。助手当前状态：{shown}')


def deepest_route(folder, level):
    routes = []
    # A worker that finishes the map leaves its route in the plain result file.
    for path in glob.glob(str(folder / f'level_{level}_static/accelerator/native_*.json')):
        if path.endswith(('.progress.json', '.checkpoint.json', '.frontier.json', '.external_stop.json')):continue
        try:routes.append(read(path).get('sequence', []))
        except (OSError, ValueError):continue
    if not routes or not max(routes, key=len):raise ValueError('这局还没有搜索路线；等助手算完这一关再测速')
    return max(routes, key=len)


def cut_at_clear(data, route, steps):
    """Stop on a clear so the run ends with the tray as low as it gets."""
    board = replay_prefix(data, [])
    last = 0
    for index, card in enumerate(route[:steps], 1):
        if board.play_to_refresh([card])['elimination']:last = index
    if not last:raise ValueError('这段路线里没有三消，无法测速')
    return route[:last]


def build_segment(folder, level, steps):
    source = folder / f'map_{level}.json'
    data = read(source)
    route = cut_at_clear(data, deepest_route(folder, level), steps)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    segment = make_segment(data, digest, [], route, exact=True)
    segment['source_label'] = '测速：只跑一段，之后这局不可继续'
    path = folder / f'level_{level}_speed_test/solution.json'
    path.parent.mkdir(exist_ok=True)
    atomic_json(path, segment)
    return source, data, path, segment


def summarize(actions, recovery):
    """Group gaps come from the moments each group was confirmed."""
    moments = []
    for action in actions:
        if not moments or moments[-1] != action['time']:moments.append(action['time'])
    parse = lambda text:time.mktime(time.strptime(text[:19], '%Y-%m-%dT%H:%M:%S')) + float('0.' + text[20:26])
    stamps = [parse(moment) for moment in moments]
    gaps = sorted((b - a) * 1000 for a, b in zip(stamps, stamps[1:]))
    seconds = stamps[-1] - stamps[0] + actions[0].get('confirmation_ms', 0) / 1000
    return {'steps': len(actions), 'seconds': round(seconds, 1), 'steps_per_second': round(len(actions) / seconds, 2),
            'whole_game_seconds': round(seconds / len(actions) * 234, 1), 'groups': len(moments),
            'group_gap_p50_ms': round(gaps[len(gaps) // 2]) if gaps else None,
            'group_gap_p90_ms': round(gaps[int(len(gaps) * .9)]) if gaps else None,
            'early_steps': sum(bool(a.get('early_confirm')) for a in actions), 'resent_clicks': len(recovery)}


def lines(path):
    if not path.exists():return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--steps', type=int, default=100)
    parser.add_argument('--level', type=int, default=1)
    args = parser.parse_args()
    folder, capture = wait_until_ready(args.level)
    mode = 'world' if '/world/' in capture.get('source_path', '') else 'daily'
    try:
        source, data, solution, segment = build_segment(folder, args.level, args.steps)
        # Raises before any click if the screen is not this map's fresh board.
        result = app_bridge.prepare(folder, source, data, args.level, mode, solution_path=solution)
    except ValueError as error:
        raise SystemExit(f'没有点击：{error}')
    plan_path = Path(result['plan_path'])
    plan = read(plan_path)
    plan.update(TIMING)
    atomic_json(plan_path, plan)
    play = plan_path.parent
    for name in ('actions.jsonl', 'input_recovery.jsonl'):(play / name).unlink(missing_ok=True)
    print(f"第 {args.level + 1} 关开始测速：{len(segment['sequence'])} 步", flush=True)
    finished = subprocess.run([str(ROOT / 'SolverPlayback.exe'), str(plan_path)], cwd=ROOT, capture_output=True,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if not (play / 'playback_status.json').exists():
        raise SystemExit('没有点击，点击程序启动时退出：' + (finished.stdout + finished.stderr).decode('utf-8', 'replace').strip())
    status = read(play / 'playback_status.json')
    actions = lines(play / 'actions.jsonl')
    report = {'state': status.get('state'), 'message': status.get('message'), 'exit_code': finished.returncode}
    if actions:report.update(summarize(actions, lines(play / 'input_recovery.jsonl')))
    atomic_json(play / 'speed_test.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print('这局已不能继续，请在游戏里重新开始')
    return 0 if status.get('state') == 'segment_finished' else 1


if __name__ == '__main__':
    sys.exit(main())
