"""Exact-model checks against exhaustive legal clicks, including false UNSAT."""
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from ortools.sat.python import cp_model
from color_rules import initial_cards,ColorState
from constraint_model import ConstraintBoard
from desktop_assistant import validate
from solve_constraints import solve_board
from static_plan import plan_current,ENGINE
from test_tight_search import graph,oracle


def chain(types):
    n=len(types)
    return {'levelData':{str(n-i):[{'id':f'{n-i}-0-0','type':t,'rolNum':0,'rowNum':0}]
                         for i,t in enumerate(types)}}


class StaticPlanTests(unittest.TestCase):
    def test_global_occupancy_and_dominance_match_exhaustive_boards(self):
        outcomes=set()
        for seed in range(24):
            rng=random.Random(seed);types=[1,2,3,4]*3;rng.shuffle(types);levels={}
            for i,t in enumerate(types):
                levels.setdefault(str(i//3+1),[]).append({'id':str(i),'type':t,
                    'rolNum':rng.choice((0,8,16)),'rowNum':rng.choice((0,8,16))})
            data={'levelData':levels};cards,rules=initial_cards(data)
            expected=oracle(ColorState(cards,rules),[],graph(cards));outcomes.add(expected)
            for symmetry in (False,True):
                board=ConstraintBoard(data,encoding='cumulative',symmetry=symmetry)
                self.assertFalse(board.model.validate())
                solver=cp_model.CpSolver();solver.parameters.max_time_in_seconds=4
                status=solver.solve(board.model)
                self.assertIn(status,(cp_model.OPTIMAL,cp_model.FEASIBLE,cp_model.INFEASIBLE))
                self.assertEqual(status!=cp_model.INFEASIBLE,expected,(seed,symmetry))
                if expected:validate(cards,board.answer(solver)['sequence'])
        # Include a forced capacity contradiction; random geometry need not
        # happen to produce an infeasible member on every generator version.
        data=chain([1,2,3,4]*3)
        info,answer=solve_board(data,seconds=4,encoding='cumulative')
        self.assertEqual(info['state'],'infeasible');self.assertIsNone(answer)

    def test_all_boundary_trays_and_immediate_elimination(self):
        data=chain([1,2,3]*3);cards,_=initial_cards(data)
        route=[f'{9-i}-0-0' for i in range(9)]
        for fixed in range(10):
            info,answer=solve_board(data,prefix=route[:fixed],seconds=4,encoding='cumulative')
            self.assertEqual(info['state'],'solved',(fixed,info));validate(cards,answer['sequence'])
            self.assertEqual(answer['sequence'][:fixed],route[:fixed])

    def test_equivalent_cards_have_one_direction_only(self):
        data={'levelData':{'1':[{'id':str(i),'type':1,'rolNum':8*i,'rowNum':0} for i in range(6)]}}
        board=ConstraintBoard(data,encoding='cumulative',symmetry=True)
        edges=board.dominance_edges
        self.assertEqual(len(edges),15)
        self.assertFalse(any((b,a) in edges for a,b in edges))
        info,answer=solve_board(data,seconds=4,encoding='cumulative');self.assertEqual(info['state'],'solved')

    def test_complete_cache_extends_confirmed_prefix_and_map_change_invalidates(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);mp=root/'map.json';out=root/'out'
            mp.write_text(json.dumps(chain([1,2,3]*3)),encoding='utf-8')
            path,answer=plan_current(mp,[],out,seconds=5,performance='eco')
            self.assertTrue(answer['validated']);self.assertEqual(answer['engine'],ENGINE)
            with patch('static_plan.solve_board',side_effect=AssertionError('must reuse')):
                self.assertEqual(plan_current(mp,answer['sequence'][:2],out)[0],path)
            mp.write_text(json.dumps(chain([1,2,3,4]*3)),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'不可满足'):plan_current(mp,[],out,seconds=5)
            status=json.loads((out/'search_status.json').read_text(encoding='utf-8'))
            self.assertEqual(status['state'],'no_continuation')
            self.assertEqual(status['proof_scope'],'whole_map')
            self.assertFalse(json.loads((out/'analysis.json').read_text(encoding='utf-8'))['formal_certificate'])

    def test_timeout_cancel_and_bad_candidate_never_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);mp=root/'map.json';mp.write_text(json.dumps(chain([1]*3)))
            with self.assertRaisesRegex(ValueError,'未知'):plan_current(mp,[],root/'timeout',seconds=0)
            with self.assertRaisesRegex(ValueError,'暂停'):plan_current(mp,[],root/'cancel',cancel=lambda:True)
            with patch('static_plan.solve_board',return_value=({'state':'solved'},{'sequence':['bad']})):
                with self.assertRaises((ValueError,KeyError)):plan_current(mp,[],root/'invalid')
            for name in ('timeout','cancel','invalid'):self.assertFalse((root/name/'complete/solution.json').exists())


if __name__=='__main__':unittest.main()

