import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from frontier_search import FrontierPlanner
from solve_verified_map import atomic_json
from solve_original_batch import export
from test_color_rules import fixture

ROOT=Path(__file__).resolve().parent.parent
EXE=ROOT/'FastOriginalSolver.exe'


class FrontierTests(unittest.TestCase):
    def test_compact_stack_returns_leaf_then_siblings_and_recovers_leases(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'cache.json';p=FrontierPlanner(path,'map',10,['a','b'],atomic_json)
            task=p.next_task();self.assertEqual(task['prefix'],['a'])
            p.finish(task,'limited',{'map_sha256':'map','path':['a','c','d'],
                     'frames':[{'depth':2,'cards':['e','f']},{'depth':1,'cards':['g']} ]})
            leaf=p.next_task();self.assertEqual(leaf['prefix'],['a','c','d'])
            p.finish(leaf,'exhausted');sibling=p.next_task();self.assertEqual(sibling['prefix'],['a','c','e'])
            p.save(True);q=FrontierPlanner(path,'map',10,['a','b'],atomic_json)
            recovered=q.next_task();self.assertEqual(recovered['prefix'],sibling['prefix'])
            q.finish(recovered,'exhausted');self.assertEqual(q.next_task()['prefix'],['a','c','f'])
            self.assertEqual(FrontierPlanner(path,'other-map',10,['x'],atomic_json).next_task()['prefix'],['x'])

    def test_malformed_frontier_cannot_replace_a_leased_prefix(self):
        with tempfile.TemporaryDirectory() as d:
            p=FrontierPlanner(Path(d)/'cache.json','map',10,['a'],atomic_json);task=p.next_task()
            with self.assertRaises(ValueError):p.finish(task,'limited',{'map_sha256':'map','path':['b'],'frames':[]})

    def test_sibling_batch_keeps_restrictions_across_cutoff_and_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'cache.json';p=FrontierPlanner(path,'map',20,['a'],atomic_json)
            root=p.next_task();siblings=[str(i) for i in range(12)]
            p.finish(root,'limited',{'map_sha256':'map','path':['a','x'],
                     'frames':[{'depth':1,'cards':siblings}]})
            leaf=p.next_task();p.finish(leaf,'exhausted')
            batch=p.next_task();self.assertEqual(batch['prefix'],['a']);self.assertEqual(batch['allowed'],siblings[:8])
            p.finish(batch,'limited',{'map_sha256':'map','path':['a'],'frames':[]})
            retry=p.next_task();self.assertEqual(retry['allowed'],siblings[:8])
            p.save(True);q=FrontierPlanner(path,'map',20,['a'],atomic_json)
            recovered=q.next_task();self.assertEqual(recovered['allowed'],siblings[:8])
            q.finish(recovered,'exhausted');last=q.next_task();self.assertEqual(last['allowed'],siblings[8:])

    @unittest.skipUnless(EXE.exists(),'frontier native build absent')
    def test_tiny_state_budgets_resume_to_verified_solution_and_exhaustion(self):
        for solvable,colored in ((True,False),(False,False),(True,True)):
            with self.subTest(solvable=solvable,colored=colored),tempfile.TemporaryDirectory() as d:
                folder=Path(d);n=6 if solvable else 4
                cards=[{'id':f'1-{i*8}-0','rolNum':i*8,'rowNum':0,'type':1 if i<(3 if solvable else 2) else 2,
                        'metaType':0,'metaData':0,'moldType':1} for i in range(n)]
                data={'levelData':{'1':cards}}
                if colored:data=fixture();cards=data['levelData']['1'];n=len(cards)
                mp=folder/'map.json';mp.write_text(json.dumps(data))
                digest=hashlib.sha256(mp.read_bytes()).hexdigest()
                planner=FrontierPlanner(folder/'cache.json',digest,n,[c['id'] for c in cards],atomic_json)
                prefixes=set();solved=False;jobs=0
                while jobs<100:
                    task=planner.next_task()
                    if task is None:break
                    jobs+=1;key=(tuple(task['prefix']),tuple(task.get('allowed') or []));self.assertNotIn(key,prefixes);prefixes.add(key)
                    pp=folder/'prefix.json';pp.write_text(json.dumps({'map_sha256':digest,'sequence':task['prefix'],'allowed':task.get('allowed')}))
                    answer=folder/'answer.json';progress=folder/'progress.json';frontier=Path(str(progress)+'.frontier.json');frontier.unlink(missing_ok=True)
                    p=subprocess.run([str(EXE),str(mp),str(answer),'color-top','0','5','1997','2',str(progress),str(pp)],
                         capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW,timeout=10)
                    self.assertIn(p.returncode,(0,2),p.stderr)
                    if p.returncode==0:
                        solved=True;self.assertTrue(export(mp,json.loads(answer.read_text()),folder)['validated']);break
                    status=json.loads(progress.read_text());saved=json.loads(frontier.read_text()) if frontier.exists() else None
                    planner.finish(task,status['state'],saved)
                self.assertEqual(solved,solvable);self.assertGreater(jobs,1)
                if not solvable:self.assertFalse(planner.blocks or planner.active)


if __name__=='__main__':unittest.main()
