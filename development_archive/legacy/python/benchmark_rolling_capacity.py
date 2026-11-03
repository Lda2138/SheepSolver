"""Offline real-rule replay. Never sends input to the game window."""
import argparse
import json
from pathlib import Path
import time

from frozen_replan import LiveBoard
from rolling_play import native_real_plan,make_segment,validate_lookahead
from solve_verified_map import atomic_json


def trial(map_path,output,seconds=90,plan_seconds=1,prefix=None,horizon=3):
    import hashlib
    raw=map_path.read_bytes();data=json.loads(raw.decode('utf-8-sig'));digest=hashlib.sha256(raw).hexdigest()
    sequence=list(prefix or []);rounds=[];start=time.monotonic();state='limited';output.mkdir(parents=True,exist_ok=True)
    while time.monotonic()-start<seconds:
        board=LiveBoard(data)
        for id in sequence:board.play_to_refresh([id])
        if not board.state.remaining and not board.tray:state='complete';break
        route=None;info={'state':'limited'}
        for attempt in range(3):
            left=seconds-(time.monotonic()-start)
            if left<=0:break
            info,route=native_real_plan(board,sequence,output/f'plan_{len(sequence):04}_{attempt}',
                seconds=min(plan_seconds*2**attempt,left),state_limit=32768*2**attempt,horizon_target=horizon)
            if route or info['state']=='no_next_elimination':break
        if not route:state=info['state'];break
        segment=make_segment(data,digest,sequence,route,info);validate_lookahead(segment)
        sequence=segment['sequence'];rounds.append({'step':len(sequence),'remaining':segment['remaining_count'],
            'tray':segment['expected_tray'],'capacity':info['tray_capacity'],'attempts':info['attempts'],
            'lookahead_events':info['lookahead_events'],'search_seconds':info['elapsed_seconds']})
        state='complete' if segment['complete'] else 'limited'
        if segment['complete']:break
    final=LiveBoard(data)
    for id in sequence:final.play_to_refresh([id])
    report={'state':state,'map_sha256':digest,'elapsed_seconds':time.monotonic()-start,
        'steps':len(sequence),'remaining':len(final.state.remaining),'tray':final.tray,
        'sequence':sequence,'rounds':rounds,'energy_count':final.state.collected if final.energy else None}
    atomic_json(output/'report.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('map',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--seconds',type=float,default=90);parser.add_argument('--plan-seconds',type=float,default=1)
    parser.add_argument('--actions',type=Path);parser.add_argument('--horizon',type=int,default=3)
    args=parser.parse_args();prefix=[]
    if args.actions:prefix=[json.loads(s)['card_id'] for s in args.actions.read_text(encoding='utf-8-sig').splitlines() if s.strip()]
    report=trial(args.map,args.output,args.seconds,args.plan_seconds,prefix,args.horizon)
    print(json.dumps({k:v for k,v in report.items() if k not in ('sequence','rounds')},ensure_ascii=False))
