"""Sound ordinary-card relaxation for diagnosing early decision conflicts.

Merge the entire changing-color palette into one type. Every exact legal
route remains legal: (sum of residues) % 3 <= sum of residues. Colors outside
that palette never change. An infeasible assumption core is therefore valid
for the original board; a feasible relaxed route is NOT an executable answer.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path

from color_rules import initial_cards
from solve_constraints import solve_board
from solve_verified_map import atomic_json


def project(data):
    cards,rules=initial_cards(data)
    palette={c['type'] for c in cards if c.get('metaType')==2}
    result=copy.deepcopy(data)
    representative=min(palette) if palette else None
    for items in result['levelData'].values():
        for c in items:
            if c['type'] in palette:c['type']=representative
            c['metaType']=0
    result['_constraint_relaxation']={'kind':'merge_changing_palette_v1','palette':sorted(palette),
                                     'executable':False}
    return result


def diagnose(data,sequence,*,fixed=None,seconds=30,repair=False,notify=None,log=None):
    decisions=dict(enumerate(sequence[:fixed])) if fixed is not None else dict(enumerate(sequence))
    info,answer=solve_board(project(data),seconds=seconds,assignments=decisions,
                            repair=repair,notify=notify,log=log)
    info['projection']='merge_changing_palette_v1';info['executable']=False
    if info['state']=='solved':info['state']='relaxation_feasible'
    if info.get('proof_scope')=='whole_map':info['proof_scope']='whole_original_map_via_relaxation'
    return info,answer


def main():
    p=argparse.ArgumentParser();p.add_argument('--map',type=Path,required=True)
    p.add_argument('--candidate',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--fixed',type=int);p.add_argument('--seconds',type=float,default=30)
    p.add_argument('--repair',action='store_true');a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    data=json.loads(a.map.read_text(encoding='utf-8-sig'))
    sequence=json.loads(a.candidate.read_text(encoding='utf-8-sig'))['sequence']
    payload={'state':'building','executable':False};status=a.output/'diagnosis_status.json'
    def notify(**kw):
        payload.update(kw);payload['state']=kw['phase'];atomic_json(status,payload)
    with (a.output/'diagnosis_solver.log').open('w',encoding='utf-8',buffering=1) as log:
        info,answer=diagnose(data,sequence,fixed=a.fixed,seconds=a.seconds,repair=a.repair,
                            notify=notify,log=lambda s:log.write(s+'\n'))
    info['map_sha256']=hashlib.sha256(a.map.read_bytes()).hexdigest();payload.update(info)
    atomic_json(status,payload)
    if answer:
        atomic_json(a.output/'relaxation_candidate.json',{'sequence':answer['sequence'],
            'executable':False,'projection':info['projection'],'map_sha256':info['map_sha256']})
    print(json.dumps(info,ensure_ascii=True))


if __name__=='__main__':main()
