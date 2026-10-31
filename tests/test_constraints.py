import json
import random
from pathlib import Path
import tempfile
import unittest

from constraint_model import ConstraintBoard
from solve_constraints import solve_board
from solve_original_batch import export
from color_rules import initial_cards,ColorState
from test_tight_search import sample_map,graph,oracle
from test_color_rules import fixture
from ortools.sat.python import cp_model


class ConstraintTests(unittest.TestCase):
    def validate_answer(self,data,answer):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);mp=folder/'map.json';mp.write_text(json.dumps(data),encoding='utf-8')
            self.assertTrue(export(mp,answer,folder)['validated'])

    def test_ordinary_and_seeded_color_match_independent_oracle(self):
        for seed in range(9):
            for colored in (False,True):
                data=sample_map(seed)
                if not colored:
                    for cards in data['levelData'].values():
                        for c in cards:c['metaType']=0
                cards,rules=initial_cards(data);expected=oracle(ColorState(cards,rules),[],graph(cards))
                with self.subTest(seed=seed,colored=colored):
                    info,answer=solve_board(data,seconds=12,workers=1)
                    self.assertNotEqual(info['state'],'unknown',info)
                    self.assertEqual(bool(answer),expected,info)
                    if answer:self.validate_answer(data,answer)

    def test_shuffle_trace_can_be_fixed_and_replayed(self):
        data=fixture();info,answer=solve_board(data,seconds=15)
        self.assertIsNotNone(answer,info);self.validate_answer(data,answer)
        for fixed in (2,5,8,12):
            info,continued=solve_board(data,seconds=10,prefix=answer['sequence'][:fixed])
            self.assertIsNotNone(continued,info);self.validate_answer(data,continued)

    def test_infeasible_decision_core_repairs_early_choices(self):
        # This is a legal six-card prefix, but with six distinct types no
        # seventh card can form a triple. The whole map is solvable.
        cards=[{'id':str(i),'rolNum':i*8,'rowNum':0,'type':1+i//3} for i in range(21)]
        data={'levelData':{'1':cards}}
        assignments={i:str(i*3) for i in range(6)}
        info,answer=solve_board(data,seconds=10,assignments=assignments)
        self.assertEqual(info['state'],'infeasible_assumptions');self.assertTrue(info['core'])
        info,answer=solve_board(data,seconds=10,assignments=assignments,repair=True)
        self.assertEqual(info['state'],'solved');self.assertEqual(len(info['learned_conflicts']),1)
        self.validate_answer(data,answer)

    def test_assumed_order_does_not_conflict_with_card_symmetry(self):
        data={'levelData':{'1':[{'id':str(i),'rolNum':i*8,'rowNum':0,'type':1} for i in range(3)]}}
        info,answer=solve_board(data,seconds=5,assignments={0:'2',1:'1',2:'0'})
        self.assertEqual(info['state'],'solved',info)
        self.assertEqual(answer['sequence'],['2','1','0'])

    def test_each_event_color_and_rng_consumption_matches_replay(self):
        for special_count in (1,2,6):
            for uniform in (False,True):
                data=fixture()
                for i,c in enumerate(data['levelData']['1']):
                    c['metaType']=2 if i<special_count else 0
                    if uniform:c['type']=1
                info,answer=solve_board(data,seconds=15)
                self.assertIsNotNone(answer,info)
                for fixed,encoding in ((0,'slots'),(2,'slots'),(5,'slots'),(0,'events'),(2,'events'),
                                       (0,'schedule'),(2,'schedule')):
                    board=ConstraintBoard(data,answer['sequence'][:fixed],encoding=encoding)
                    tail=answer['sequence'][fixed:]
                    if encoding=='slots':board.assume_positions(dict(enumerate(tail)))
                    else:board.assume_segments(tail)
                    solver=cp_model.CpSolver();solver.parameters.max_time_in_seconds=10
                    status=solver.solve(board.model)
                    self.assertIn(status,(cp_model.OPTIMAL,cp_model.FEASIBLE))
                    state=board.runtime;tray=list(board.tray);event=draws=0
                    for step,id in enumerate(tail):
                        t=state.types[id]
                        picked=board.picked_types[step if encoding=='slots' else board.index[id]]
                        self.assertEqual(solver.value(picked),t)
                        tray.append(t);hit=tray.count(t)==3
                        if hit:tray=[v for v in tray if v!=t]
                        state.remove(id,hit)
                        if hit and board.m:
                            event+=1;count=sum(id in state.remaining for id in board.special)
                            if count>1:draws+=count
                            self.assertEqual(solver.value(board.draws[event]),draws)
                            for j,id in enumerate(board.special):
                                self.assertEqual(solver.value(board.colors[event][j]),
                                                 state.types[id] if id in state.remaining else 0)
                    self.assertFalse(tray);self.assertFalse(state.remaining)

    def test_occurrence_capacity_model_with_nonempty_boundary_tray(self):
        data=fixture()
        for c in data['levelData']['1']:c['metaType']=0
        info,answer=solve_board(data,seconds=10)
        self.assertEqual(info['encoding'],'occurrences');self.validate_answer(data,answer)
        for fixed in range(1,12):
            info,continued=solve_board(data,seconds=10,prefix=answer['sequence'][:fixed])
            self.assertIsNotNone(continued,info);self.validate_answer(data,continued)

    def test_relaxation_is_marked_and_cannot_be_exported(self):
        from constraint_diagnosis import project,diagnose
        data=fixture();projected=project(data)
        self.assertTrue(projected['_constraint_relaxation'])
        info,answer=diagnose(data,[],seconds=5)
        self.assertEqual(info['state'],'relaxation_feasible');self.assertFalse(info['executable'])
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);mp=folder/'map.json';mp.write_text(json.dumps(projected),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'松弛模型'):export(mp,answer,folder)

    def test_event_segments_match_oracle_and_replay(self):
        for seed in range(18):
            for colored in (False,True):
                data=sample_map(seed)
                if not colored:
                    for items in data['levelData'].values():
                        for c in items:c['metaType']=0
                cards,rules=initial_cards(data);expected=oracle(ColorState(cards,rules),[],graph(cards))
                info,answer=solve_board(data,seconds=10,encoding='events')
                self.assertNotEqual(info['state'],'unknown',info)
                self.assertEqual(bool(answer),expected,(seed,colored,info))
                if answer:self.validate_answer(data,answer)
        data=fixture();info,answer=solve_board(data,seconds=10,encoding='events')
        for fixed in range(12):
            info,continued=solve_board(data,seconds=10,encoding='events',prefix=answer['sequence'][:fixed])
            self.assertIsNotNone(continued,info);self.validate_answer(data,continued)

    def test_four_type_layered_boards_match_exhaustive_oracle(self):
        # Four types exercise genuine capacity traps (three types can never
        # exceed six residues). Compare complete feasibility, not just routes.
        for seed in range(8):
            r=random.Random(seed+83);types=[1,2,3,4]*3;r.shuffle(types);levels={}
            for i,t in enumerate(types):
                levels.setdefault(str(i//4+1),[]).append({'id':str(i),'rolNum':r.choice((0,8,16)),
                    'rowNum':r.choice((0,8,16)),'type':t,'metaType':2 if i%2==0 else 0})
            data={'levelData':levels,'blockTypeData':dict.fromkeys(('1','2','3','4'),1),
                  'map_seed':[697634228+seed,2420934636,717715288,3972332771]}
            cards,rules=initial_cards(data);expected=oracle(ColorState(cards,rules),[],graph(cards))
            for encoding in ('slots','events','schedule'):
                info,answer=solve_board(data,seconds=15,encoding=encoding)
                self.assertNotEqual(info['state'],'unknown',(seed,encoding,info))
                self.assertEqual(bool(answer),expected,(seed,encoding))
                if answer:self.validate_answer(data,answer)

    def test_holding_intervals_match_oracle_and_real_tray_boundaries(self):
        for seed in range(18):
            data=sample_map(seed)
            for items in data['levelData'].values():
                for c in items:c['metaType']=0
            cards,rules=initial_cards(data);expected=oracle(ColorState(cards,rules),[],graph(cards))
            info,answer=solve_board(data,seconds=10,encoding='schedule')
            self.assertNotEqual(info['state'],'unknown',info)
            self.assertEqual(bool(answer),expected,(seed,info))
            if answer:
                self.validate_answer(data,answer)
                for fixed in (1,2,4,7):
                    info,tail=solve_board(data,seconds=10,prefix=answer['sequence'][:fixed],encoding='schedule')
                    self.assertIsNotNone(tail,info);self.validate_answer(data,tail)
        data={'levelData':{'1':[{'id':str(i),'rolNum':i*8,'rowNum':0,'type':1+i//3} for i in range(21)]}}
        info,answer=solve_board(data,seconds=10,encoding='schedule',
                                segment_assumptions=[str(i*3) for i in range(6)],repair=True)
        self.assertEqual(info['state'],'solved',info);self.assertTrue(info['learned_conflicts'])
        self.validate_answer(data,answer)

    def test_fixed_boundary_is_validated_and_timeout_is_unknown(self):
        data=fixture()
        with self.assertRaisesRegex(ValueError,'Invalid fixed'):
            solve_board(data,seconds=5,prefix=['missing'])
        info,answer=solve_board(data,seconds=.000001)
        self.assertIsNone(answer);self.assertEqual(info['state'],'unknown')

    def test_released_decisions_and_restored_clauses_cannot_hide_a_conflict(self):
        data={'levelData':{'1':[{'id':str(i),'rolNum':i*8,'rowNum':0,'type':1} for i in range(3)]}}
        for encoding in ('slots','occurrences','events','schedule'):
            board=ConstraintBoard(data,encoding=encoding)
            if encoding in ('events','schedule'):board.assume_segments(['0'])
            else:board.assume_positions({0:'0'})
            core=list(board.assumptions);facts=[board.assumption_facts(i) for i in core]
            # Synthetic exclusion checks encoding semantics; it is not saved
            # as a proof. After guards are retracted, actual choices still
            # must respect the learned OR-of-negations.
            board.learn_core(core);board.activate_assumptions([])
            model=board.model
            model.add(board.positions[board.index['0']]==0)
            if encoding in ('events','schedule'):model.add(board.event_last[board.index['0']]==0)
            solver=cp_model.CpSolver();solver.parameters.max_time_in_seconds=5
            self.assertEqual(solver.solve(model),cp_model.INFEASIBLE,encoding)
            restored=ConstraintBoard(data,encoding=encoding);restored.restore_conflict(facts)
            restored.model.add(restored.positions[restored.index['0']]==0)
            if encoding in ('events','schedule'):restored.model.add(restored.event_last[restored.index['0']]==0)
            self.assertEqual(solver.solve(restored.model),cp_model.INFEASIBLE,encoding)

    def test_conflict_store_rejects_other_maps_boundaries_and_encodings(self):
        from constraint_conflicts import ConflictStore
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'proofs.json';facts=[[{'step':0,'card':'a'}]]
            store=ConflictStore(path,'map-a',['executed'],'slots');store.save(facts)
            self.assertEqual(store.load(),facts)
            store.save_boundary_proof();self.assertTrue(store.boundary_proven())
            for map_id,prefix,encoding in [('map-b',['executed'],'slots'),
                    ('map-a',[],'slots'),('map-a',['other'],'slots'),('map-a',['executed'],'events')]:
                self.assertEqual(ConflictStore(path,map_id,prefix,encoding).load(),[])
                self.assertFalse(ConflictStore(path,map_id,prefix,encoding).boundary_proven())

    def test_verified_witness_and_dead_boundary_contract(self):
        data={'levelData':{'1':[{'id':str(i),'rolNum':i*8,'rowNum':0,'type':1+i//3} for i in range(21)]}}
        witness=[str(i) for i in range(21)]
        info,answer=solve_board(data,seconds=10,encoding='schedule',base_witness=witness,
                                segment_assumptions=[str(i*3) for i in range(6)],repair=True)
        self.assertEqual(info['state'],'solved');self.assertEqual(info['base_feasibility'],'witnessed')
        with self.assertRaisesRegex(ValueError,'does not extend'):
            solve_board(data,base_witness=witness,prefix=['3'])
        with self.assertRaisesRegex(ValueError,'contradicts verified'):
            solve_board(data,base_witness=witness,boundary_proven=True)
        marked=[]
        info,answer=solve_board(data,seconds=10,prefix=[str(i*3) for i in range(6)],encoding='schedule',
                                save_boundary_proof=lambda:marked.append(True))
        self.assertEqual(info['state'],'infeasible');self.assertTrue(marked)
        info,answer=solve_board(data,seconds=.000001,boundary_proven=True)
        self.assertEqual(info['reason'],'stored_boundary_proof');self.assertIsNone(answer)
        info,answer=solve_board(data,seconds=10,encoding='schedule',fixed_route=witness)
        self.assertEqual(info['state'],'solved');self.assertTrue(info['fixed_route'])
        self.validate_answer(data,answer)

    def test_repair_ablation_keeps_proofs_without_learning_clauses(self):
        data={'levelData':{'1':[{'id':str(i),'rolNum':i*8,'rowNum':0,'type':1+i//3} for i in range(21)]}}
        info,answer=solve_board(data,seconds=10,encoding='schedule',base_witness=[str(i) for i in range(21)],
                                segment_assumptions=[str(i*3) for i in range(6)],repair=True,learn_conflicts=False)
        self.assertEqual(info['state'],'solved');self.assertTrue(info['proved_cores'])
        self.assertFalse(info['learned_conflicts']);self.validate_answer(data,answer)

    def test_triple_membership_can_reorder_a_dead_click_prefix(self):
        data={'levelData':{'1':[{'id':str(i),'rolNum':i*8,'rowNum':0,'type':1+i//3} for i in range(21)]}}
        dead=[str(i*3) for i in range(6)]
        info,_=solve_board(data,seconds=10,encoding='schedule',prefix=dead)
        self.assertEqual(info['state'],'infeasible')
        info,answer=solve_board(data,seconds=10,encoding='schedule',triple_assumptions=dead,
                                base_witness=[str(i) for i in range(21)])
        self.assertEqual(info['state'],'solved',info)
        self.assertEqual(info['fixed_prefix'],0)
        self.assertNotEqual(answer['sequence'][:6],dead)
        self.validate_answer(data,answer)

    def test_triple_membership_preserves_seeded_routes_at_tray_boundaries(self):
        data=fixture();info,answer=solve_board(data,seconds=10,encoding='schedule')
        self.assertIsNotNone(answer,info)
        for fixed in (0,1,2,5):
            info,continued=solve_board(data,seconds=10,encoding='schedule',
                prefix=answer['sequence'][:fixed],triple_assumptions=answer['sequence'][fixed:],
                base_witness=answer['sequence'])
            self.assertEqual(info['state'],'solved',(fixed,info))
            self.validate_answer(data,continued)

    def test_triple_clauses_retain_equivalence_after_release_and_restore(self):
        from constraint_conflicts import ConflictStore
        data={'levelData':{'1':[{'id':str(i),'rolNum':i*8,'rowNum':0,'type':1} for i in range(6)]}}
        board=ConstraintBoard(data,encoding='schedule')
        board.assume_triples(['0'])
        core=list(board.assumptions);facts=[board.assumption_facts(i) for i in core]
        self.assertEqual(facts,[{'kind':'triple_group','card':'0','type':1,'triple':0}])
        # Synthetic exclusion tests literal equivalence only; it is never
        # saved as an infeasibility proof.
        board.learn_core(core);board.activate_assumptions([])
        board.model.add(board.group_assignments[board.index['0']]==board.type_group_indices[1][0])
        solver=cp_model.CpSolver();solver.parameters.max_time_in_seconds=5
        self.assertEqual(solver.solve(board.model),cp_model.INFEASIBLE)
        restored=ConstraintBoard(data,encoding='schedule');restored.restore_conflict(facts)
        restored.model.add(restored.group_assignments[restored.index['0']]==restored.type_group_indices[1][0])
        self.assertEqual(solver.solve(restored.model),cp_model.INFEASIBLE)
        # Four cards cannot belong to one triple. This separate clause is
        # actually proved before checking persistence and restoration.
        proved=ConstraintBoard(data,encoding='schedule')
        guards=[]
        for id in ('0','1','2','3'):
            fact={'kind':'triple_group','card':id,'type':1,'triple':0}
            b=proved.decision_literal(fact);proved.assumptions[b.index]=(0,id,b,fact);guards.append(b)
        proved.activate_assumptions(proved.assumptions)
        self.assertEqual(solver.solve(proved.model),cp_model.INFEASIBLE)
        facts=[proved.assumption_facts(i) for i in solver.sufficient_assumptions_for_infeasibility()]
        self.assertTrue(facts)
        with tempfile.TemporaryDirectory() as d:
            store=ConflictStore(Path(d)/'proofs.json','map-triples',[],'schedule');store.save([facts])
            replay=ConstraintBoard(data,encoding='schedule')
            for clause in store.load():replay.restore_conflict(clause)
            for fact in facts:replay.model.add(replay.decision_literal(fact)==1)
            self.assertEqual(solver.solve(replay.model),cp_model.INFEASIBLE)

    def test_fixed_group_subproblem_has_separate_proof_scope_and_witness(self):
        from constraint_conflicts import ConflictStore
        data={'levelData':{'1':[{'id':str(i),'rolNum':i*8,'rowNum':0,'type':1+i//3} for i in range(21)]}}
        dead=[str(i*3) for i in range(6)]
        info,answer=solve_board(data,seconds=10,encoding='schedule',fixed_triples=dead,
                                base_witness=[str(i) for i in range(21)])
        self.assertEqual(info['state'],'solved');self.assertEqual(info['fixed_prefix'],0)
        self.assertEqual(info['fixed_triple_cards'],6);self.validate_answer(data,answer)
        info,_=solve_board(data,encoding='schedule',fixed_triples=dead,boundary_proven=True)
        self.assertEqual(info['proof_scope'],'fixed_triples')
        ordinary={'levelData':{'1':[{'id':str(i),'rolNum':i*8,'rowNum':0,'type':1} for i in range(6)]}}
        with self.assertRaisesRegex(ValueError,'fixed triple groups'):
            solve_board(ordinary,encoding='schedule',fixed_triples=['3'],base_witness=[str(i) for i in range(6)])
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'proof.json';store=ConflictStore(path,'map',[],'schedule',dead)
            store.save_boundary_proof();self.assertTrue(store.boundary_proven())
            self.assertFalse(ConflictStore(path,'map',[],'schedule').boundary_proven())
            self.assertFalse(ConflictStore(path,'map',[],'schedule',dead[:-1]).boundary_proven())
        colored=fixture();info,witness=solve_board(colored,seconds=10,encoding='schedule')
        info,answer=solve_board(colored,seconds=10,encoding='schedule',fixed_triples=witness['sequence'],
                                base_witness=witness['sequence'])
        self.assertEqual(info['state'],'solved');self.validate_answer(colored,answer)

    def test_event_core_repairs_legal_tray_trap(self):
        data={'levelData':{'1':[{'id':str(i),'rolNum':i*8,'rowNum':0,'type':1+i//3} for i in range(21)]}}
        route=[str(i*3) for i in range(6)]
        saved=[]
        info,answer=solve_board(data,seconds=10,encoding='events',segment_assumptions=route,
                                repair=True,save_conflicts=lambda facts:saved.extend(facts))
        self.assertEqual(info['state'],'solved',info);self.assertTrue(saved)
        self.validate_answer(data,answer)
        info,answer=solve_board(data,seconds=10,encoding='events',known_conflicts=saved)
        self.assertEqual(info['state'],'solved',info);self.validate_answer(data,answer)


if __name__=='__main__':unittest.main()
