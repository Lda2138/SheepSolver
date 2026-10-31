"""Offline complete-board constraint solving and conflict-directed repair."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import threading
import time

from ortools.sat.python import cp_model

from constraint_model import ConstraintBoard,BuildLimit,triple_decisions
from solve_original_batch import export
from solve_verified_map import atomic_json
from constraint_conflicts import ConflictStore
from color_rules import initial_cards,ColorState
from desktop_assistant import validate


def solve_board(data, *, seconds=30, workers=1, prefix=(), hint=(), assignments=None,
                repair=False, notify=None, cancel=None, log=None, encoding='auto',segment_assumptions=(),
                prefer_early_triples=False,no_lp=False,known_conflicts=(),save_conflicts=None,
                base_witness=None,boundary_proven=False,save_boundary_proof=None,fixed_route=None,
                learn_conflicts=True,triple_assumptions=(),fixed_triples=(),model_path=None,proved_conflicts_symmetry=False):
    start=time.monotonic();deadline=start+seconds;notify=notify or (lambda **kw:None)
    if not learn_conflicts:known_conflicts=()
    if fixed_route is not None:
        if assignments or segment_assumptions or triple_assumptions or fixed_triples:raise ValueError('Fixed route and structural decisions cannot be combined')
        cards,rules=initial_cards(data);validate(cards,fixed_route,rules)
        if list(fixed_route[:len(prefix)])!=list(prefix):raise ValueError('Fixed route does not extend boundary')
        if base_witness is None:base_witness=fixed_route
    if base_witness is not None:
        cards,rules=initial_cards(data);validate(cards,base_witness,rules)
        if list(base_witness[:len(prefix)])!=list(prefix):raise ValueError('Witness does not extend the fixed boundary')
        if fixed_triples:
            parents={c['id']:{p['id'] for p in cards if p['layer']>c['layer'] and
                abs(p['rolNum']-c['rolNum'])<8 and abs(p['rowNum']-c['rowNum'])<8} for c in cards}
            runtime=ColorState(cards,rules)
            actual={f['card']:f for f in triple_decisions(runtime,[],parents,base_witness)}
            for fact in triple_decisions(runtime,[],parents,list(prefix)+list(fixed_triples)):
                if actual[fact['card']]!=fact:raise ValueError('Witness does not satisfy the fixed triple groups')
    proof_scope='fixed_triples' if fixed_triples else ('fixed_boundary' if prefix else 'whole_map')
    if boundary_proven:
        if base_witness is not None:raise ValueError('Stored boundary proof contradicts verified base witness')
        return {'state':'infeasible','proof_scope':proof_scope,
                'reason':'stored_boundary_proof','elapsed_seconds':time.monotonic()-start},None
    if sum(bool(v) for v in (assignments,segment_assumptions,triple_assumptions))>1:
        raise ValueError('Use one assumption representation')
    try:board=ConstraintBoard(data,prefix,deadline=deadline,notify=notify,
                              symmetry=not (assignments or segment_assumptions or triple_assumptions or fixed_triples or (known_conflicts and not proved_conflicts_symmetry) or fixed_route),encoding=encoding,
                              cancel=cancel)
    except BuildLimit:
        return {'state':'cancelled' if cancel and cancel() else 'unknown','reason':'build_limit',
                'elapsed_seconds':time.monotonic()-start},None
    board.add_hint(hint)
    if assignments:board.assume_positions(assignments)
    if segment_assumptions:board.assume_segments(segment_assumptions)
    if triple_assumptions:board.assume_triples(triple_assumptions)
    if fixed_triples:board.fix_triples(fixed_triples)
    if fixed_route is not None:
        tail=fixed_route[len(prefix):]
        if board.encoding in ('events','schedule'):board.assume_segments(tail)
        else:board.assume_positions(dict(enumerate(tail)))
        for record in board.assumptions.values():board.model.add(record[2]==1)
        board.activate_assumptions([]);board.assumptions.clear()
    for conflict in known_conflicts:board.restore_conflict(conflict,proved=proved_conflicts_symmetry)
    if prefer_early_triples and not board.assumptions and board.encoding=='occurrences' and board.n:
        board.model.minimize(sum(board.boundaries))
    error=board.model.validate()
    if error:raise ValueError('Constraint model invalid: '+error)
    if model_path is not None:board.model.export_to_file(str(model_path))
    active=set(board.assumptions);learned=list(known_conflicts);proved_cores=[];attempts=0
    while True:
        left=deadline-time.monotonic()
        if left<=0 or (cancel and cancel()):
            return {'state':'cancelled' if cancel and cancel() else 'unknown','reason':'budget',
                    'elapsed_seconds':time.monotonic()-start,'learned_conflicts':learned},None
        solver=cp_model.CpSolver()
        solver.parameters.max_time_in_seconds=left
        solver.parameters.num_workers=1 if active else max(1,workers)
        solver.parameters.random_seed=1997
        solver.parameters.stop_after_first_solution=True
        if no_lp:solver.parameters.linearization_level=0
        solver.parameters.log_search_progress=True
        solver.parameters.log_to_stdout=False
        progress={'phase':'presolve'}
        def solver_log(message):
            if log:log(message)
            if 'Starting presolve' in message:progress['phase']='presolve'
            if 'Starting search' in message:progress['phase']='search'
            match=re.search(r'#Model\s+[\d.]+s var:([\d]+)/([\d]+)',message)
            if match:
                progress['active_variables']=int(match[1]);progress['presolved_variables']=int(match[2])
        solver.log_callback=solver_log
        # Assumption analysis is satisfiability only: no hidden objective
        # turns a failed optimization attempt into an infeasibility proof.
        attempts+=1;done=threading.Event()
        def watch():
            while not done.wait(.5):
                elapsed=time.monotonic()-start
                notify(**progress.copy(),elapsed_seconds=elapsed,budget_seconds=seconds,
                       remaining_seconds=max(0,seconds-elapsed),
                       assumptions=len(active),learned_conflicts=len(learned),attempt=attempts)
                if cancel and cancel():solver.stop_search();return
        monitor=threading.Thread(target=watch,daemon=True);monitor.start()
        notify(phase='presolve',elapsed_seconds=time.monotonic()-start,assumptions=len(active),attempt=attempts,
               variables=len(board.model.proto.variables),constraints=len(board.model.proto.constraints))
        try:status=solver.solve(board.model)
        finally:done.set();monitor.join(timeout=1)
        info={'state':'unknown','solver_status':solver.status_name(status),
              'elapsed_seconds':time.monotonic()-start,'build_seconds':board.build_seconds,
              'branches':solver.num_branches,'conflicts':solver.num_conflicts,
              'attempts':attempts,'learned_conflicts':learned,
              'proved_cores':proved_cores,'conflict_learning':learn_conflicts,
              'encoding':board.encoding,
              'base_feasibility':'witnessed' if base_witness is not None else 'unknown',
              'fixed_route':fixed_route is not None,
              'hint_cards':len(hint),
              'triple_assumption_cards':len(triple_assumptions),
              'fixed_triple_cards':len(fixed_triples),
              'fixed_prefix':len(prefix),'assumptions':len(active),
              'variables':len(board.model.proto.variables),'constraints':len(board.model.proto.constraints)}
        info.update(dominance_constraints=len(getattr(board,'dominance_edges',[])),
                    occupancy_intervals=getattr(board,'occupancy_intervals',0),
                    solver_workers=solver.parameters.num_workers,
                    deterministic_time=solver.response_proto.deterministic_time)
        if status in (cp_model.OPTIMAL,cp_model.FEASIBLE):
            info['state']='solved';answer=board.answer(solver);answer['elapsed_seconds']=info['elapsed_seconds']
            return info,answer
        if status==cp_model.MODEL_INVALID:raise ValueError(solver.response_stats())
        if status!=cp_model.INFEASIBLE:
            if cancel and cancel():info['state']='cancelled'
            return info,None
        core=list(solver.sufficient_assumptions_for_infeasibility()) if active else []
        if not core:
            if base_witness is not None:raise ValueError('Exact model contradicts a verified complete witness')
            info['state']='infeasible';info['proof_scope']=proof_scope
            info['base_feasibility']='infeasible'
            if save_boundary_proof:save_boundary_proof()
            return info,None
        if any(i not in active for i in core):raise ValueError('Unexpected assumption core')
        facts=[board.assumption_facts(i) for i in core]
        # Persist every proved core, including diagnosis-only runs. Timeouts
        # never arrive here and cannot create a pruning clause.
        proved_cores.append(facts)
        if learn_conflicts:
            board.learn_core(core);learned.append(facts)
            if save_conflicts:save_conflicts(learned)
        if not repair:
            info['state']='infeasible_assumptions';info['core']=facts;return info,None
        # The sufficient core proves these fixed decisions cannot all hold.
        # Keep that generalized clause, release all decisions it identifies,
        # and solve the full continuation again instead of widening a suffix.
        active.difference_update(core)
        board.activate_assumptions(active)
        notify(phase='repairing',core=facts,assumptions=len(active),learned_conflicts=len(learned),
               elapsed_seconds=time.monotonic()-start)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--map',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--seconds',type=float,default=30)
    p.add_argument('--workers',type=int,default=4)
    p.add_argument('--encoding',choices=['auto','slots','occurrences','cumulative','events','schedule'],default='auto')
    p.add_argument('--prefix',type=Path);p.add_argument('--hint',type=Path)
    p.add_argument('--assumptions',type=Path);p.add_argument('--repair',action='store_true')
    p.add_argument('--segment-assumptions',type=Path)
    p.add_argument('--triple-assumptions',type=Path,help='Retractable triple membership from a legal candidate route')
    p.add_argument('--fixed-triples',type=Path,help='Hard triple memberships for an isolated scheduling subproblem; click times remain free')
    p.add_argument('--prefer-early-triples',action='store_true')
    p.add_argument('--no-lp',action='store_true')
    p.add_argument('--conflicts',type=Path,help='Scoped proof store; defaults to the output folder')
    p.add_argument('--base-witness',type=Path,help='Verified complete route that extends the immutable prefix')
    p.add_argument('--fixed-route',type=Path,help='Hard-fix a complete route for model correctness checking')
    p.add_argument('--no-conflict-learning',action='store_true',help='Research ablation: retract cores without adding explicit clauses')
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    data=json.loads(a.map.read_text(encoding='utf-8-sig'))
    def sequence(path):return json.loads(path.read_text(encoding='utf-8-sig'))['sequence'] if path else []
    prefix=sequence(a.prefix)
    fixed_triples=sequence(a.fixed_triples)
    store=ConflictStore(a.conflicts or a.output/'constraint_conflicts.json',
                        hashlib.sha256(a.map.read_bytes()).hexdigest(),prefix,a.encoding,fixed_triples)
    known_conflicts=[] if a.no_conflict_learning else store.load()
    payload={'state':'building','engine':'global_constraint_cp_sat'}
    status_path=a.output/'constraint_status.json';control=a.output/'constraint_control.json'
    def cancelled():
        try:return json.loads(control.read_text(encoding='utf-8')).get('action')=='stop'
        except (OSError,ValueError):return False
    def notify(**kw):
        payload.update(kw)
        payload['state']=kw['phase'] if kw.get('phase') in ('solving','repairing','presolve','search') else 'building'
        atomic_json(status_path,payload)
    log_file=(a.output/'constraint_solver.log').open('w',encoding='utf-8',buffering=1)
    try:
        info,answer=solve_board(data,seconds=a.seconds,workers=a.workers,prefix=prefix,
            hint=sequence(a.hint),assignments=json.loads(a.assumptions.read_text(encoding='utf-8')) if a.assumptions else None,
            repair=a.repair,notify=notify,cancel=cancelled,log=lambda s:log_file.write(s+'\n'),encoding=a.encoding,
            segment_assumptions=sequence(a.segment_assumptions),prefer_early_triples=a.prefer_early_triples,
            no_lp=a.no_lp,known_conflicts=known_conflicts,save_conflicts=store.save,
            base_witness=sequence(a.base_witness) if a.base_witness else None,
            boundary_proven=store.boundary_proven(),save_boundary_proof=store.save_boundary_proof,
            fixed_route=sequence(a.fixed_route) if a.fixed_route else None,
            learn_conflicts=not a.no_conflict_learning,triple_assumptions=sequence(a.triple_assumptions),
            fixed_triples=fixed_triples)
        info['restored_conflicts']=len(known_conflicts)
        if answer:
            payload['state']='validating';atomic_json(status_path,payload)
            answer['source_label']='真实完整地图；全局约束求解，已独立回放验证'
            result=export(a.map,answer,a.output)
            info['validated']=result['validated']
        payload.update(info);atomic_json(status_path,payload)
        print(json.dumps(info,ensure_ascii=True))
        return 0 if answer else 2
    except BaseException as e:
        payload.update(state='error',message=str(e));atomic_json(status_path,payload);raise
    finally:log_file.close()


if __name__=='__main__':raise SystemExit(main())
