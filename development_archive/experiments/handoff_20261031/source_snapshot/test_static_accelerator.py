import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from color_rules import initial_cards
from desktop_assistant import validate
from solve_constraints import solve_board
from static_accelerator import prepare,boundary_cut
from constraint_conflicts import ConflictStore


def flat(count=21):
    return {'levelData':{'1':[{'id':str(i),'type':i//3+1,'rolNum':i*8,'rowNum':0} for i in range(count)]}}


class StaticAcceleratorTests(unittest.TestCase):
    def write_map(self,folder,data):
        mp=folder/'map.json';mp.write_text(json.dumps(data),encoding='utf-8');return mp

    def test_fast_full_route_and_cached_route_are_independently_legal(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);data=flat(12);mp=self.write_map(folder,data)
            info,answer,_,_=prepare(mp,data,[],folder/'out',4,1,lambda:False,lambda **kw:None)
            self.assertEqual(info['state'],'solved',info);self.assertIsNotNone(answer)
            validate(initial_cards(data)[0],answer['sequence'])
            with patch('static_accelerator.subprocess.Popen',side_effect=AssertionError('cache must be used')):
                info,answer,_,_=prepare(mp,data,[],folder/'out',4,1,lambda:False,lambda **kw:None)
            self.assertEqual(info['method'],'static_candidate_cache')

    def test_large_board_fast_path_is_published_without_calling_global_cp_sat(self):
        from static_plan import plan_current
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);data=flat(66);mp=self.write_map(folder,data)
            with patch('static_plan.solve_board',side_effect=AssertionError('fast complete answer must skip global search')):
                path,answer=plan_current(mp,[],folder/'out',seconds=20,performance='max')
            self.assertTrue(path.exists());self.assertTrue(answer['validated'])
            self.assertEqual(len(answer['sequence']),66)
            self.assertIn(answer['search_method'],('native_fast_path','exact_tail_repair'))
            self.assertEqual(json.loads((folder/'out/search_status.json').read_text(encoding='utf-8'))['state'],'solved')

    def test_partial_hints_do_not_skip_native_search(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);data=flat(12);mp=self.write_map(folder,data);out=folder/'out';out.mkdir()
            scope={'map_sha256':hashlib.sha256(mp.read_bytes()).hexdigest(),'confirmed_prefix':[],'candidate_version':2}
            (out/'hints.json').write_text(json.dumps({'scope':scope,'routes':[['0','1']]}))
            with patch('static_accelerator.solve_board',return_value=({'state':'unknown'},None)),patch('static_accelerator.boundary_cut',return_value=([],{'state':'limited'})):
                info,answer,hint,store=prepare(mp,data,[],out,1,1,lambda:False,lambda **kw:None)
            self.assertIsNotNone(answer);self.assertEqual(info['method'],'native_fast_path')
            validate(initial_cards(data)[0],answer['sequence'])
            self.assertFalse(store.load());self.assertFalse(store.boundary_proven())
            self.assertTrue(json.loads((out/'hints.json').read_text())['trials'])

    def test_unknown_retries_change_seeds_and_never_become_proofs(self):
        commands=[]
        class Process:
            def __init__(self,command,**kw):commands.append(command);self._handle=-1;self.pid=os.getpid()
            def poll(self):return 2
            def wait(self,timeout):return 2
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);data=flat(12);mp=self.write_map(folder,data)
            with patch('static_accelerator.subprocess.Popen',side_effect=Process):
                boundaries=[]
                for _ in range(2):
                    boundaries.append(len(commands))
                    info,answer,_,store=prepare(mp,data,[],folder/'out',1,1,lambda:False,lambda **kw:None)
                    self.assertIsNone(answer);self.assertEqual(info['state'],'unknown');self.assertFalse(store.load())
            self.assertGreater(boundaries[1],1,'finished workers must be refilled within one run')
            self.assertNotEqual(commands[boundaries[0]][6],commands[boundaries[1]][6])
            status=json.loads((folder/'out/hints.json').read_text())
            self.assertEqual(status['native_serial'],len(commands))

    def test_cp_sat_cannot_take_unused_native_budget_or_report_native_worker_count(self):
        from static_plan import plan_current
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);data=flat(66);mp=self.write_map(folder,data)
            notices=[]
            def cp(*args,**kw):
                self.assertLessEqual(kw['seconds'],60)
                self.assertEqual(kw['workers'],2)
                kw['notify'](phase='search');notices.append(json.loads((folder/'out/search_status.json').read_text(encoding='utf-8')))
                return {'state':'unknown'},None
            with patch('static_accelerator.prepare',return_value=({'state':'unknown'},None,[],None)),patch('static_plan.solve_board',side_effect=cp),patch('prop_plan.search',return_value=None):
                with self.assertRaisesRegex(ValueError,'未知'):plan_current(mp,[],folder/'out',300)
            self.assertEqual(notices[0]['workers'],2)
            from static_plan import SCHEDULER_VERSION
            self.assertEqual(notices[0]['scheduler_version'],SCHEDULER_VERSION)

    def test_cancel_reaps_every_owned_native_process(self):
        processes=[]
        class Process:
            def __init__(self,*args,**kw):self.alive=True;self.waited=False;self.command=args[0];processes.append(self)
            def poll(self):return None if self.alive else -15
            def terminate(self):self.alive=False
            def wait(self,timeout):self.waited=True;return -15
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);data=flat();mp=self.write_map(folder,data)
            with patch('static_accelerator.subprocess.Popen',side_effect=Process):
                with self.assertRaisesRegex(ValueError,'暂停'):
                    prepare(mp,data,[],folder/'out',4,4,lambda:True,lambda **kw:None)
            self.assertEqual(len(processes),4)
            self.assertGreater(float(processes[0].command[5]),3.9)
            self.assertLessEqual(float(processes[0].command[5]),4)
            self.assertTrue(all(not p.alive and p.waited for p in processes))

    def test_proved_nogoods_remain_sound_with_dominance_symmetry(self):
        data=flat();bad={i:str(i*3) for i in range(6)}
        info,answer=solve_board(data,seconds=4,encoding='cumulative',assignments=bad)
        self.assertEqual(info['state'],'infeasible_assumptions');self.assertIsNone(answer)
        self.assertTrue(info['learned_conflicts'])
        info,answer=solve_board(data,seconds=4,encoding='cumulative',known_conflicts=info['learned_conflicts'],
            proved_conflicts_symmetry=True)
        self.assertEqual(info['state'],'solved',info);validate(initial_cards(data)[0],answer['sequence'])
        self.assertGreater(info['dominance_constraints'],0)

    def test_dead_set_cut_compresses_ancestors_and_preserves_a_complete_witness(self):
        levels={};bad=[];good=[]
        for t in range(1,7):
            id=f'{7-t}-0-0';bad.append(id)
            levels[str(7-t)]=[{'id':id,'type':t,'rolNum':0,'rowNum':0}]
            pair=[{'id':f'0-{t}-{j}','type':t,'rolNum':100+t*20+j*8,'rowNum':0} for j in (0,1)]
            levels.setdefault('0',[]).extend(pair);good.extend([id]+[c['id'] for c in pair])
        data={'levelData':levels};validate(initial_cards(data)[0],good)
        facts,audit=boundary_cut(data,[],bad,backtrack=False)
        self.assertEqual(audit['state'],'dead_prefix');self.assertEqual(len(facts),1)
        self.assertEqual(facts[0]['card'],bad[-1]);self.assertEqual(facts[0]['step'],6)
        info,answer=solve_board(data,seconds=4,encoding='cumulative',known_conflicts=[facts],
            proved_conflicts_symmetry=True,fixed_route=good)
        self.assertEqual(info['state'],'solved');validate(initial_cards(data)[0],answer['sequence'])
        info,answer=solve_board(data,seconds=4,encoding='cumulative',known_conflicts=[facts],
            assignments=dict(enumerate(bad)))
        self.assertEqual(info['state'],'infeasible_assumptions');self.assertIsNone(answer)
        facts,audit=boundary_cut(data,[],bad,seconds=2,backtrack=True)
        self.assertEqual(facts,[]);self.assertEqual(audit['state'],'complete')
        validate(initial_cards(data)[0],audit['witness'])

    def test_truncated_boundary_audit_never_learns_a_cut(self):
        facts,audit=boundary_cut(flat(),[],['0'],seconds=0)
        self.assertEqual(audit['state'],'limited');self.assertEqual(facts,[])

    def test_dead_boundary_cut_uses_times_relative_to_confirmed_prefix(self):
        data=flat();bad=['0','3','6','9','12','15']
        facts,audit=boundary_cut(data,['0'],bad,seconds=1,backtrack=False)
        self.assertEqual(audit['state'],'dead_prefix')
        self.assertEqual({f['step'] for f in facts},{5})
        good=[str(i) for i in range(21)]
        info,answer=solve_board(data,prefix=['0'],known_conflicts=[facts],fixed_route=good,
            encoding='cumulative',seconds=4)
        self.assertEqual(info['state'],'solved');validate(initial_cards(data)[0],answer['sequence'])


if __name__=='__main__':unittest.main()
