"""Map original solver IDs to calibrated screenshot coordinates; no input."""
import argparse
import hashlib
import json
from pathlib import Path
from desktop_assistant import validate, overlap


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--solution', type=Path, required=True)
    p.add_argument('--reference', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--x-origin', type=float, required=True)
    p.add_argument('--y-origin', type=float, required=True)
    p.add_argument('--x-unit', type=float, required=True)
    p.add_argument('--y-unit', type=float, required=True)
    a = p.parse_args()
    s = json.loads(a.solution.read_text(encoding='utf-8'))
    validate(s['cards'], s['sequence'])
    positions = {c['id']: [round(a.x_origin+c['rolNum']*a.x_unit),
                           round(a.y_origin+c['rowNum']*a.y_unit)] for c in s['cards']}
    if any(not (20 <= x <= 315 and 125 <= y <= 465) for x,y in positions.values()):
        raise ValueError('Calibrated position outside board')
    anchors = [{'id': c['id'], 'type': c['type'], 'center': positions[c['id']]}
               for c in s['cards'] if not any(b['layer'] > c['layer'] and overlap(c,b) for b in s['cards'])]
    plan = {'solution_path': str(a.solution.resolve()), 'reference_path': str(a.reference.resolve()),
            'map_sha256': s['map_sha256'], 'reference_sha256': hashlib.sha256(a.reference.read_bytes()).hexdigest(),
            'positions': positions, 'anchors': anchors,
            'calibration': {'x_origin':a.x_origin,'y_origin':a.y_origin,'x_unit':a.x_unit,'y_unit':a.y_unit}}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'cards':len(s['cards']),'anchors':len(anchors),'anchor_types':sorted({c['type'] for c in anchors})}))


if __name__ == '__main__':
    main()
