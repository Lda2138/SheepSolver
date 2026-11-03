import json
from pathlib import Path
import random
import tempfile
import unittest

from color_rules import initial_cards
from static_group_schedule import StaticGroups,partition_order
from solve_original_batch import export
from test_color_rules import fixture


def partition_oracle(data,order,ordered=False):
    """Plain click enumeration for the same fixed partition; no propagation."""
    cards,_=initial_cards(data);by_id={c['id']:c for c in cards};groups={}
    for t in sorted({c['type'] for c in cards}):
        ids=[id for id in order if by_id[id]['type']==t]
        groups[t]=[set(ids[i:i+3]) for i in range(0,len(ids),3)]
    parents={c['id']:{p['id'] for p in cards if p['layer']>c['layer'] and
        abs(p['rolNum']-c['rolNum'])<8 and abs(p['rowNum']-c['rowNum'])<8} for c in cards}
    seen=set()
    def dfs(left,tray):
        if not left:return not tray
        key=(frozenset(left),tuple(sorted(tray)))
        if key in seen:return False
        seen.add(key)
        allowed=set()
        for typed in groups.values():
            pending=[group&left for group in typed if group&left]
            partial=[group for group in pending if len(group)<3]
            for group in (pending[:1] if ordered else (partial or pending)):allowed.update(group)
        for id in sorted(allowed):
            if parents[id]&left:continue
            t=by_id[id]['type'];new=tray+[t]
            if new.count(t)==3:new=[v for v in new if v!=t]
            if len(new)>=7:continue
            if dfs(left-{id},new):return True
        return False
    return dfs(set(by_id),[])


class StaticGroupTests(unittest.TestCase):
    def validate(self,data,answer):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);mp=out/'map.json';mp.write_text(json.dumps(data),encoding='utf-8')
            self.assertTrue(export(mp,answer,out)['validated'])

    def test_safe_propagation_matches_click_oracle_with_capacity_traps(self):
        for seed in range(24):
            rng=random.Random(seed);types=[1,2,3,4]*3;rng.shuffle(types);levels={}
            for i,t in enumerate(types):
                levels.setdefault(str(i//4+1),[]).append({'id':str(i),'type':t,
                    'rolNum':rng.choice((0,8,16)),'rowNum':rng.choice((0,8,16))})
            data={'levelData':levels};order=partition_order(data,seed)
            expected=partition_oracle(data,order)
            for propagate in (False,True):
                info,answer=StaticGroups(data,order).solve(seconds=3,propagate=propagate)
                self.assertNotEqual(info['state'],'unknown',(seed,info))
                self.assertEqual(bool(answer),expected,(seed,propagate))
                if answer:self.validate(data,answer)

    def test_multiple_groups_per_type_and_nonempty_intermediate_tray(self):
        for seed in range(6):
            rng=random.Random(seed+51);types=[1,2,3]*6;rng.shuffle(types);levels={}
            for i,t in enumerate(types):
                levels.setdefault(str(i//6+1),[]).append({'id':str(i),'type':t,
                    'rolNum':rng.choice((0,8,16,24)),'rowNum':rng.choice((0,8,16))})
            data={'levelData':levels};order=partition_order(data,seed)
            for ordered in (False,True):
                expected=partition_oracle(data,order,ordered)
                info,answer=StaticGroups(data,order,ordered=ordered).solve(seconds=3)
                self.assertNotEqual(info['state'],'unknown');self.assertEqual(bool(answer),expected,(seed,ordered))
                if answer:self.validate(data,answer)

    def test_budget_expiration_never_proves_partition_infeasibility(self):
        data={'levelData':{'1':[{'id':str(i),'type':i//3,'rolNum':i*8,'rowNum':0} for i in range(21)]}}
        info,answer=StaticGroups(data,[str(i) for i in range(21)]).solve(seconds=0)
        self.assertEqual(info['state'],'unknown');self.assertIsNone(answer)
        self.assertEqual(info['dead_states'],0)

    def test_changing_colors_and_invalid_partitions_are_rejected(self):
        data=fixture()
        with self.assertRaisesRegex(ValueError,'changing colors'):
            StaticGroups(data,[c['id'] for c in data['levelData']['1']])
        for c in data['levelData']['1']:c['metaType']=0
        with self.assertRaisesRegex(ValueError,'every card once'):StaticGroups(data,[])

    def test_capacity_proof_excludes_a_subset_across_other_group_changes(self):
        # The original chain is solved by 1,1,1 / 2,2,2 / 3,3,3, twice.
        # Deliberately pairing distant cards creates an impossible partition,
        # whose proof involves only the B/C groups, not the A grouping.
        data={'levelData':{str(18-i):[{'id':str(i),'type':1+(i//3)%3,
                                     'rolNum':0,'rowNum':0}] for i in range(18)}}
        complete=[str(i) for i in range(18)]
        self.validate(data,{'sequence':complete})
        bad=list(map(str,[0,9,10,1,2,11,3,12,13,4,5,14,6,15,16,7,8,17]))
        proof=StaticGroups(data,bad).capacity_obstruction()
        self.assertIsNotNone(proof)
        self.assertEqual(len(proof['groups']),4)
        self.assertTrue(all(c['minimum_cards']>7 for c in proof['first_completion_requirements']))
        self.assertFalse(partition_oracle(data,bad))
        # Change all A triples. The exact same B/C proof remains valid.
        other=list(map(str,[0,1,2,9,10,11,3,12,13,4,5,14,6,15,16,7,8,17]))
        changed=StaticGroups(data,other)
        actual={frozenset(changed.ids[i] for i in changed.members(g)) for typed in changed.groups for g in typed}
        self.assertTrue(all(frozenset(g) in actual for g in proof['groups']))
        self.assertFalse(partition_oracle(data,other))
        self.assertIsNone(StaticGroups(data,complete).capacity_obstruction())
        info,answer=StaticGroups(data,complete).solve(seconds=3)
        self.assertEqual(info['state'],'solved');self.validate(data,answer)


if __name__=='__main__':unittest.main()
