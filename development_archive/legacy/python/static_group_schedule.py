"""Exact fixed-triple scheduling for ordinary cards with safe propagation.

This is a subproblem, not a proof that the original map is infeasible. Each
type has a partition into triples. Once all unpicked members of a
next triple are exposed and fit in the tray, completing that triple is safe:
it only removes blockers and reduces held cards. Shuffle cards are rejected.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import random
import time

from color_rules import initial_cards
from solve_original_batch import export
from solve_verified_map import atomic_json


class BudgetExpired(Exception):
    pass


class StaticGroups:
    def __init__(self,data,order,*,ordered=False):
        self.cards,rules=initial_cards(data)
        self.ordered=ordered
        if rules:raise ValueError('Safe static propagation cannot be applied to changing colors')
        self.ids=[c['id'] for c in self.cards];self.index={id:i for i,id in enumerate(self.ids)}
        if len(order)!=len(self.ids) or set(order)!=set(self.ids):raise ValueError('Partition order must contain every card once')
        self.parents=[sum(1<<j for j,p in enumerate(self.cards) if p['layer']>c['layer'] and
            abs(p['rolNum']-c['rolNum'])<8 and abs(p['rowNum']-c['rowNum'])<8) for c in self.cards]
        self.children=[sum(1<<j for j,mask in enumerate(self.parents) if mask&(1<<i)) for i in range(len(self.ids))]
        typed=defaultdict(list)
        for id in order:typed[self.cards[self.index[id]]['type']].append(self.index[id])
        if any(len(v)%3 for v in typed.values()):raise ValueError('Type counts must be multiples of three')
        self.groups=[[sum(1<<i for i in items[j:j+3]) for j in range(0,len(items),3)]
                     for _,items in sorted(typed.items())]
        self.full=(1<<len(self.ids))-1

    def capacity_obstruction(self):
        """Necessary condition with a checkable proof on a subset of triples.

        Consider the first completed triple among S. All its covering
        ancestors that belong to S must have been picked, but none can have
        been eliminated yet. More than seven such cards is impossible.
        Triples outside S are ignored, so the proof applies to other
        partitions that retain precisely these triples.
        """
        ancestors=list(self.parents)
        for i in sorted(range(len(self.ids)),key=lambda i:self.cards[i]['layer'],reverse=True):
            for j in self.members(self.parents[i]):ancestors[i]|=ancestors[j]
        groups=[g for typed in self.groups for g in typed]
        needed=[]
        for group in groups:
            required=group
            for i in self.members(group):required|=ancestors[i]
            needed.append(required)
        active=list(range(len(groups)))
        def union(indices):
            mask=0
            for i in indices:mask|=groups[i]
            return mask
        def impossible(indices):
            if not indices:return False
            mask=union(indices)
            return all((needed[i]&mask).bit_count()>7 for i in indices)
        while active:
            mask=union(active)
            removable=[i for i in active if (needed[i]&mask).bit_count()<=7]
            if not removable:break
            active=[i for i in active if i not in removable]
        if not active:return None
        # Delete redundant triples while preserving the actual lower bound;
        # no solver timeout or heuristic score participates in this proof.
        for i in list(active):
            trial=[j for j in active if j!=i]
            if impossible(trial):active=trial
        mask=union(active)
        return {'proof_source':'ancestor_tray_capacity','scope':'fixed_triple_subset',
            'groups':[[self.ids[j] for j in self.members(groups[i])] for i in active],
            'first_completion_requirements':[
                {'group':[self.ids[j] for j in self.members(groups[i])],
                 'must_hold':[self.ids[j] for j in self.members(needed[i]&mask)],
                 'minimum_cards':(needed[i]&mask).bit_count()} for i in active]}

    @staticmethod
    def members(mask):
        while mask:
            bit=mask&-mask;yield bit.bit_length()-1;mask^=bit

    def current(self,remaining):
        groups=[];held=0
        for typed in self.groups:
            pending=[group&remaining for group in typed if group&remaining]
            partial=[group for group in pending if group.bit_count()<3]
            if len(partial)>1:raise ValueError('Two triples of the same type cannot be held together')
            if self.ordered:groups.extend(pending[:1])
            else:groups.extend(partial or pending)
            if partial:held+=3-partial[0].bit_count()
        return groups,held

    def solve(self,seconds=10,notify=None,cancel=None,cache_limit=200000,propagate=True,capacity_cuts=True):
        start=time.monotonic();deadline=start+seconds;last_report=start;dead=set()
        stats={'nodes':0,'branches':0,'safe_triples':0,'dead_states':0}
        notify=notify or (lambda **kw:None)
        def tick():
            nonlocal last_report
            now=time.monotonic()
            if now>=deadline or (cancel and cancel()):raise BudgetExpired
            if now-last_report>=.5:
                notify(phase='search',elapsed_seconds=now-start,**stats);last_report=now
        def normalize(remaining):
            route=[]
            while True:
                tick();groups,held=self.current(remaining);changed=False
                for pending in groups:
                    count=pending.bit_count()
                    if held+count-1>6:continue
                    if any(self.parents[i]&remaining for i in self.members(pending)):continue
                    route.extend(self.members(pending));remaining^=pending
                    stats['safe_triples']+=1;changed=True;break
                if not changed:return remaining,route
        def dfs(remaining):
            tick();stats['nodes']+=1
            remaining,forced=normalize(remaining) if propagate else (remaining,[])
            if not remaining:return forced
            if remaining in dead:return None
            groups,held=self.current(remaining);choices=[]
            for pending in groups:
                for i in self.members(pending):
                    if self.parents[i]&remaining:continue
                    if pending.bit_count()!=1 and held>=6:continue
                    choices.append((pending.bit_count(),-(self.children[i]&remaining).bit_count(),i))
            for _,_,i in sorted(choices):
                stats['branches']+=1
                tail=dfs(remaining^(1<<i))
                if tail is not None:return forced+[i]+tail
            # Only exhaustive failure is memoized. Budget expiration unwinds
            # directly and never creates an infeasibility entry.
            if len(dead)<cache_limit:dead.add(remaining)
            stats['dead_states']=len(dead)
            return None
        try:
            tick()
            obstruction=self.capacity_obstruction() if capacity_cuts else None
            if obstruction:
                stats.update(state='infeasible_partition',elapsed_seconds=time.monotonic()-start,
                    proof_scope='fixed_triple_subset',proof=obstruction,propagation=propagate,
                    ordered_groups=self.ordered,capacity_cuts=capacity_cuts)
                return stats,None
            result=dfs(self.full)
            stats.update(state='solved' if result is not None else 'infeasible_partition')
        except BudgetExpired:
            result=None;stats.update(state='cancelled' if cancel and cancel() else 'unknown')
        stats.update(elapsed_seconds=time.monotonic()-start,proof_scope='fixed_triple_partition',
                     cache_limit=cache_limit,propagation=propagate,ordered_groups=self.ordered,
                     capacity_cuts=capacity_cuts)
        answer=None if result is None else {'sequence':[self.ids[i] for i in result],
            'engine':'static_group_schedule','rules_engine':'standard','mode':'exact_fixed_groups'}
        return stats,answer


def partition_order(data,seed):
    """No solution input: topological ordering with reproducible tie breaks."""
    cards,rules=initial_cards(data)
    if rules:raise ValueError('Only ordinary-card partition experiments are supported')
    rng=random.Random(seed)
    # Higher layers precede lower layers; equal-layer cards cannot cover.
    ranked=[(-c['layer'],c['rowNum'] if seed==0 else rng.random(),c['rolNum'],c['id']) for c in cards]
    return [item[-1] for item in sorted(ranked)]


def main():
    p=argparse.ArgumentParser();p.add_argument('--map',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--order',type=Path)
    p.add_argument('--seconds',type=float,default=30);p.add_argument('--attempts',type=int,default=8)
    p.add_argument('--no-propagation',action='store_true')
    p.add_argument('--ordered-groups',action='store_true',help='Also enforce the supplied within-type triple order')
    p.add_argument('--no-capacity-cuts',action='store_true')
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    data=json.loads(a.map.read_text(encoding='utf-8-sig'));start=time.monotonic();results=[];proofs=[]
    supplied=json.loads(a.order.read_text(encoding='utf-8-sig'))['sequence'] if a.order else None
    attempts=1 if supplied is not None else a.attempts
    for attempt in range(attempts):
        left=a.seconds-(time.monotonic()-start)
        if left<=0:break
        order=supplied if supplied is not None else partition_order(data,attempt)
        board=StaticGroups(data,order,ordered=a.ordered_groups)
        def notify(**info):
            atomic_json(a.output/'static_group_status.json',dict(info,attempt=attempt+1,budget_seconds=a.seconds))
        partition={frozenset(board.ids[i] for i in board.members(group)) for typed in board.groups for group in typed}
        reused=next((proof for proof in proofs if all(frozenset(group) in partition for group in proof['groups'])),None)
        if reused:
            info={'state':'infeasible_partition','proof_scope':'fixed_triple_subset','reused_proof':True,
                  'proof':reused,'elapsed_seconds':0};answer=None
        else:
            info,answer=board.solve(seconds=left/(attempts-attempt),notify=notify,
                propagate=not a.no_propagation,capacity_cuts=not a.no_capacity_cuts)
            if info.get('proof'):proofs.append(info['proof'])
        info.update(attempt=attempt+1,partition_source='supplied' if supplied is not None else 'map_geometry')
        results.append(info)
        if answer:
            info['validated']=export(a.map,answer,a.output)['validated'];break
    summary={'state':'solved' if results and results[-1]['state']=='solved' else 'unknown',
             'elapsed_seconds':time.monotonic()-start,'attempts':results,
             'reference_input':supplied is not None,'map_sha256':hashlib.sha256(a.map.read_bytes()).hexdigest(),
             'proved_triple_subsets':len(proofs),'reused_proofs':sum(r.get('reused_proof',False) for r in results)}
    # Failed partitions, even exhaustively disproved ones, never prove that
    # other partitions of the same map are infeasible.
    atomic_json(a.output/'static_group_results.json',summary)
    atomic_json(a.output/'static_group_status.json',summary)
    print(json.dumps(summary,ensure_ascii=True))
    return 0 if summary['state']=='solved' else 2


if __name__=='__main__':raise SystemExit(main())
