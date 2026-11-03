"""Serial, reproducible experiments using independently verified real maps."""
import argparse
import copy
import hashlib
import json
from pathlib import Path

from benchmark_constraints import experiment
from color_rules import initial_cards,ColorState
from solve_original_batch import export

ROOT=Path(__file__).resolve().parent
SAMPLES={'ordinary':'20261008T162423Z-e73fea','colored':'20261008T172128Z-3a7fdb',
         'ordinary255':'20261008T141544Z-a50904','hard':'20261009T023904Z-2f3084'}


def replay(data,sequence):
    cards,rules=initial_cards(data);state=ColorState(cards,rules);tray=[]
    parents={c['id']:{p['id'] for p in cards if p['layer']>c['layer'] and
        abs(p['rolNum']-c['rolNum'])<8 and abs(p['rowNum']-c['rowNum'])<8} for c in cards}
    for id in sequence:
        if id not in state.remaining or parents[id]&state.remaining:raise ValueError('Invalid replay')
        t=state.types[id];tray.append(t);hit=tray.count(t)==3
        if hit:tray=[v for v in tray if v!=t]
        if len(tray)>=7:raise ValueError('Replay overflows tray')
        state.remove(id,hit)
    return state,tray,parents


def find_trap(data,prefix,max_nodes=10000,max_depth=10):
    initial,tray,parents=replay(data,prefix);seen=set();visited=0
    def dfs(state,held,route):
        nonlocal visited
        visited+=1
        if visited>max_nodes:return None
        available=[id for id in sorted(state.remaining) if not parents[id]&state.remaining]
        if len(held)==6 and available and not any(held.count(state.types[id])==2 for id in available):
            return route
        if len(route)>=max_depth:return None
        rng=(state.rng._seed_1,state.rng._seed_2) if state.rng else None
        key=(frozenset(state.remaining),tuple(sorted(held)),
             tuple(state.types[id] for id in state.special if id in state.remaining),rng)
        if key in seen:return None
        seen.add(key)
        available.sort(key=lambda id:(held.count(state.types[id]),id))
        for id in available:
            t=state.types[id];new=held+[t];hit=new.count(t)==3
            if hit:new=[v for v in new if v!=t]
            if len(new)>=7:continue
            nxt=copy.deepcopy(state);nxt.remove(id,hit)
            found=dfs(nxt,new,route+[id])
            if found is not None:return found
        return None
    return dfs(initial,tray,[]),visited


def write_sequence(path,route):
    path.write_text(json.dumps({'sequence':route}),encoding='utf-8');return path


def run_case(mp,out,route,*,prefix_size=0,encoding='schedule',seconds=30,workers=1,
             fixed=False,trap=None,repair=False,learn_conflicts=True,group_cards=None,witnessed=True,hard_groups=False):
    out.mkdir(parents=True,exist_ok=True)
    prefix=write_sequence(out/'prefix.json',route[:prefix_size])
    witness=write_sequence(out/'base_witness.json',route) if witnessed else None
    groups=write_sequence(out/'triple_decisions.json',route[:group_cards]) if group_cards is not None else None
    assignments=segments=None
    if trap is not None:
        if encoding in ('events','schedule'):segments=write_sequence(out/'decisions.json',trap)
        else:
            assignments=out/'decisions.json';assignments.write_text(json.dumps(dict(enumerate(trap))),encoding='utf-8')
    return experiment(mp,out,seconds,workers,prefix=prefix,encoding=encoding,assignments=assignments,
                      segment_assumptions=segments,repair=repair,base_witness=witness,
                      fixed_route=witness if fixed else None,learn_conflicts=learn_conflicts,
                      triple_assumptions=None if hard_groups else groups,
                      fixed_triples=groups if hard_groups else None)


def main():
    p=argparse.ArgumentParser();p.add_argument('--kind',choices=list(SAMPLES),default='ordinary')
    p.add_argument('--stage',choices=['witness','scale','trap','compare','full','groups'],required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--encoding',default='schedule')
    p.add_argument('--seconds',type=float,default=30);p.add_argument('--remaining',default='30,60,90,120')
    p.add_argument('--group-cards',default='all',help='Comma-separated counts of candidate triple memberships; no immutable prefix')
    p.add_argument('--hard-groups',action='store_true',help='Isolated scheduling feasibility; permits stronger presolve but no group retraction')
    p.add_argument('--workers',type=int,default=1);a=p.parse_args()
    sample=ROOT/'output/auto_capture/sessions'/SAMPLES[a.kind];mp=sample/'map_1.json'
    if a.kind=='hard' and a.stage!='groups':raise ValueError('Hard sample has no verified complete reference; use groups')
    answer_path=sample/('solution' if a.kind=='ordinary255' else 'level_1_solution')/('search_best_prefix.json' if a.kind=='hard' else 'solution.json')
    original=json.loads(answer_path.read_text(encoding='utf-8-sig'))
    sha=hashlib.sha256(mp.read_bytes()).hexdigest()
    source_digest_match=original.get('map_sha256')==sha
    a.output.mkdir(parents=True,exist_ok=True)
    # Validate from the source map, rather than trusting cards in an old
    # solution file. This is evidence of base feasibility, not solve speed.
    data=json.loads(mp.read_text(encoding='utf-8-sig'))
    if a.kind=='hard':
        route=original['sequence'];replay(data,route);witnessed=False
    else:
        verified=export(mp,original,a.output/'verified_reference');route=verified['sequence'];witnessed=True
    manifest={'sample':SAMPLES[a.kind],'map_sha256':sha,'total':sum(map(len,data['levelData'].values())),
              'candidate_cards':len(route),'verified_reference':witnessed,
              'original_digest_match':source_digest_match,
              'stage':a.stage,'encoding':a.encoding,'workers':a.workers,'results':[]}
    def save(result):
        manifest['results'].append(result)
        (a.output/'research_results.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    if a.stage=='groups':
        if a.encoding!='schedule':raise ValueError('Triple membership requires schedule encoding')
        for entry in a.group_cards.split(','):
            count=len(route) if entry=='all' else int(entry)
            if not 0<count<=len(route):raise ValueError('Invalid candidate group size')
            result=run_case(mp,a.output/f'groups_{count}',route,encoding=a.encoding,
                seconds=a.seconds,workers=a.workers,group_cards=count,repair=not a.hard_groups,witnessed=witnessed,
                hard_groups=a.hard_groups)
            result.update(experiment_kind='fixed_group_schedule' if a.hard_groups else 'candidate_group_repair',immutable_prefix=0,
                          input_group_cards=count,complete_reference_used=witnessed)
            save(result)
    elif a.stage in ('witness','full'):
        save(run_case(mp,a.output/a.stage,route,encoding=a.encoding,seconds=a.seconds,workers=a.workers,
                      fixed=a.stage=='witness'))
    else:
        for remaining in map(int,a.remaining.split(',')):
            boundary=len(route)-remaining
            if boundary<0:raise ValueError('Sample size exceeds source map')
            if a.stage=='scale':
                save(run_case(mp,a.output/f'tail_{remaining}',route,prefix_size=boundary,encoding=a.encoding,
                              seconds=a.seconds,workers=a.workers))
            else:
                trap,visited=find_trap(data,route[:boundary])
                if trap is None:
                    save({'state':'no_trap_found','remaining':remaining,'visited':visited});continue
                out=a.output/f'trap_{remaining}'
                modes=(True,False) if a.stage=='compare' else (True,)
                for learning in modes:
                    target=out/('learning_on' if learning else 'learning_off') if a.stage=='compare' else out
                    result=run_case(mp,target,route,prefix_size=boundary,encoding=a.encoding,
                                    seconds=a.seconds,workers=a.workers,trap=trap,repair=True,learn_conflicts=learning)
                    result.update(trap=trap,visited=visited,remaining=remaining)
                    save(result)


if __name__=='__main__':main()
