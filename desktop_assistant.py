"""Offline solution export and click rehearsal. Does not control the desktop."""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import time

from business.SheepSolver import SheepSolver

ROOT = Path(__file__).resolve().parent
MODES = ['normal', 'index-ascending', 'index-descending', 'level-bottom', 'level-top', 'random']


def overlap(a, b):
    return (abs(a['rolNum'] - b['rolNum']) < 8 and
            abs(a['rowNum'] - b['rowNum']) < 8)


def validate(cards, sequence, rules=None, expected_types=None):
    """Independently replay coverage and seven-slot rules before exporting."""
    if rules and rules.get('kind')=='energy_alien_v1':
        from energy_rules import replay
        return replay(cards,rules,sequence,expected_types)[1]
    remaining = {c['id']: c for c in cards}
    if len(remaining) != len(cards):
        raise ValueError('地图存在重复牌编号')
    from color_rules import ColorState
    runtime = ColorState(cards, rules)
    tray, peak = [], 0
    for step, card_id in enumerate(sequence, 1):
        if card_id not in remaining:
            raise ValueError(f'第 {step} 步牌编号不存在或重复')
        card = remaining[card_id]
        if any(b['layer'] > card['layer'] and overlap(card, b)
               for b in remaining.values()):
            raise ValueError(f'第 {step} 步目标牌仍被遮挡')
        t = runtime.types[card_id]
        if expected_types is not None and expected_types[step-1] != t:
            raise ValueError(f'第 {step} 步预测牌型与变色规则不一致')
        tray.append(t)
        crushed = tray.count(t) == 3
        if crushed:
            tray = [v for v in tray if v != t]
        runtime.remove(card_id, crushed)
        peak = max(peak, len(tray))
        if len(tray) >= 7:
            raise ValueError(f'第 {step} 步牌槽已满')
        del remaining[card_id]
    if remaining or tray:
        raise ValueError('答案未清空棋盘与牌槽')
    return peak


def validate_solution(solution):
    if solution.get('execution_kind')=='manual_move_out_v1':
        raise ValueError('道具路线尚不支持自动执行，需要人工移出和移出区坐标校准')
    expected = solution.get('sequence_types')
    if expected is not None and len(expected) != len(solution['sequence']):
        raise ValueError('逐步牌型记录不完整')
    if (solution.get('rules') or {}).get('kind')=='energy_alien_v1':
        from energy_rules import replay
        return replay(solution['cards'],solution['rules'],solution['sequence'],expected,
                      solution.get('energy_events'))[1]
    return validate(solution['cards'], solution['sequence'], solution.get('rules'), expected)


def validate_execution(solution):
    """Full answers and explicitly identified rolling segments have distinct gates."""
    if solution.get('execution_kind')=='rolling_segment_v1':
        from rolling_play import validate_segment
        return validate_segment(solution)
    return validate_solution(solution)


def main():
    parser = argparse.ArgumentParser(description='求解并生成离线点击预演，不操作微信或鼠标')
    parser.add_argument('--map', type=Path, default=ROOT / 'online_data.json')
    parser.add_argument('--mode', choices=MODES, default='normal')
    parser.add_argument('--time-limit', type=float, default=30)
    parser.add_argument('--solve-first', type=float, default=0.8,
                        help='原项目内置的同类牌优先策略开始进度，0 表示从第一步开始')
    parser.add_argument('--source-label', default='仓库示例地图；未核对当前微信棋盘')
    parser.add_argument('--output', type=Path, default=ROOT / 'output')
    args = parser.parse_args()
    if args.time_limit <= 0:
        parser.error('--time-limit 必须大于 0')
    raw = args.map.read_bytes()
    data = json.loads(raw.decode('utf-8-sig'))
    cards = []
    for level, items in sorted(data['levelData'].items(), key=lambda item: int(item[0])):
        for c in items:
            if not isinstance(c['type'], int) or c['type'] <= 0:
                raise ValueError('地图含未知牌类型，不能生成可靠答案')
            cards.append({**c, 'layer': int(level)})
    if any(c.get('metaType',0) not in (0,1) for c in cards):
        raise ValueError('变色关卡请使用 solve_verified_map.py 的新版搜索')
    if not cards:
        raise ValueError('地图为空')
    if any(count % 3 for count in collections.Counter(c['type'] for c in cards).values()):
        raise ValueError('牌类型数量不是 3 的倍数；此工具要求从完整的空牌槽开局地图求解')
    if not 0 <= args.solve_first <= 1:
        parser.error('--solve-first 必须在 0 到 1 之间')
    solver = SheepSolver({'show_progress': False, 'solve_first': args.solve_first,
                          'time_limit': args.time_limit,
                          'expect_progress': {'time': -1, 'percentage': 0.8}}, args.mode)
    solver.load_map_data(data)
    start = time.monotonic()
    solver.solve()
    sequence = solver.generate_card_id_result()
    if not sequence:
        raise RuntimeError('限时内未找到解；不会输出部分答案作为点击计划')
    peak = validate(cards, sequence)
    payload = {'source_label': args.source_label,
               'map_sha256': hashlib.sha256(raw).hexdigest(),
               'mode': args.mode, 'card_count': len(cards),
               'solve_first': args.solve_first,
               'elapsed_seconds': round(time.monotonic() - start, 3),
               'validated': True, 'peak_tray_after_elimination': peak,
               'cards': cards, 'sequence': sequence,
               'desktop_clicks_enabled': False}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'solution.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    template = (ROOT / 'assistant_preview.html').read_text(encoding='utf-8')
    embedded = json.dumps(payload, ensure_ascii=False).replace('<', '\\u003c')
    (args.output / '点击预演.html').write_text(template.replace('__PAYLOAD__', embedded), encoding='utf-8')
    print(json.dumps({k: v for k, v in payload.items() if k not in ('cards', 'sequence')}, ensure_ascii=False, indent=2))
    print(f'预演文件: {args.output / "点击预演.html"}')


if __name__ == '__main__':
    main()
