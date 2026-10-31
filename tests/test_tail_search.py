import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from color_rules import initial_cards, ColorState
from solve_original_batch import export
from solve_verified_map import atomic_json
from tail_search import TailPlanner
from test_color_rules import fixture

ROOT = Path(__file__).resolve().parent.parent
REAL = ROOT/'output/auto_capture/sessions/20261009T023904Z-2f3084'
PREFIX = ROOT/'output/workflow_test_fixtures/tail_prefix_219.json'


class TailTests(unittest.TestCase):
    def planner(self, directory, digest='map'):
        return TailPlanner(Path(directory)/'cache.json', digest, 258, atomic_json)

    def test_exhausted_windows_expand_and_survive_restart(self):
        with tempfile.TemporaryDirectory() as d:
            p=self.planner(d);p.offer([str(i) for i in range(231)])
            first=p.next_task();self.assertEqual((first['fixed'],first['rewind']),(219,12))
            self.assertIsNone(p.next_task()) # Avoid duplicate jobs while in flight.
            p.finish(first,'exhausted');second=p.next_task()
            self.assertEqual((second['fixed'],second['rewind']),(207,24))
            p.save(True);q=self.planner(d)
            resumed=q.next_task();self.assertEqual(resumed['fixed'],207)
            self.assertTrue(q.disproved(first['prefix']+['later']))
            self.assertFalse(q.disproved(second['prefix']))
            self.assertFalse(self.planner(d,'different-map').anchors)

    def test_budget_limit_never_proves_a_prefix_impossible(self):
        with tempfile.TemporaryDirectory() as d:
            p=self.planner(d);p.offer([str(i) for i in range(231)])
            first=p.next_task();p.finish(first,'limited','time_limit')
            self.assertFalse(p.disproved(first['prefix']))
            retry=p.next_task();self.assertEqual(retry['key'],first['key'])
            self.assertGreater(retry['seconds'],first['seconds'])
            p.finish(retry,'limited','state_limit')
            wider=p.next_task();self.assertEqual(wider['rewind'],24)
            self.assertFalse(p.disproved(first['prefix']))

    def native(self, mp, folder, prefix, seconds=5):
        pp=folder/'prefix.json';pp.write_text(json.dumps(prefix),encoding='utf-8')
        answer=folder/'answer.json';progress=folder/'progress.json'
        p=subprocess.run([str(ROOT/'FastOriginalSolver.exe'),str(mp),str(answer),
                          'color-top','0',str(seconds),'1997','1000000',str(progress),str(pp)],
                         capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW,timeout=seconds+8)
        return p,answer,progress

    def test_direct_checkpoint_replay_and_full_answer_validation(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);mp=folder/'map.json';mp.write_text(json.dumps(fixture()))
            first=folder/'first.json'
            p=subprocess.run([str(ROOT/'FastOriginalSolver.exe'),str(mp),str(first),'color-top','0','5','1997','100000'],
                             capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW,timeout=10)
            self.assertEqual(p.returncode,0,p.stderr)
            original=json.loads(first.read_text());prefix=original['sequence'][:4]
            p,answer,progress=self.native(mp,folder,{'sequence':prefix,'map_sha256':hashlib.sha256(mp.read_bytes()).hexdigest()})
            self.assertEqual(p.returncode,0,p.stderr)
            result=json.loads(answer.read_text());self.assertEqual(result['sequence'][:4],prefix)
            payload=export(mp,result,folder);self.assertTrue(payload['validated'])
            self.assertEqual(json.loads(progress.read_text())['fixed_prefix'],4)
            # Wrong map identities and repeated cards must be rejected.
            p,_,_=self.native(mp,folder,{'sequence':prefix,'map_sha256':'wrong'})
            self.assertEqual(p.returncode,1);self.assertIn(b'map mismatch',p.stderr)
            p,_,_=self.native(mp,folder,{'sequence':[prefix[0],prefix[0]]})
            self.assertEqual(p.returncode,1);self.assertIn(b'duplicate',p.stderr)

    @unittest.skipUnless((REAL/'map_1.json').exists() and PREFIX.exists(),'real color map/prefix absent')
    def test_real_checkpoint_state_and_exhaustion_match_independent_replay(self):
        mp=REAL/'map_1.json';data=json.loads(mp.read_text(encoding='utf-8'))
        saved=json.loads(PREFIX.read_text(encoding='utf-8'))
        if saved['map_sha256']!=hashlib.sha256(mp.read_bytes()).hexdigest():self.skipTest('map fixture replaced')
        prefix=saved['sequence']
        with tempfile.TemporaryDirectory() as d:
            p,_,progress=self.native(mp,Path(d),{'sequence':prefix})
            self.assertEqual(p.returncode,2,p.stderr)
            checkpoint=json.loads(Path(str(progress)+'.checkpoint.json').read_text())
            cards,rules=initial_cards(data);state=ColorState(cards,rules);tray=[]
            coverers={c['id']:{o['id'] for o in cards if o['layer']>c['layer'] and
                       abs(o['rolNum']-c['rolNum'])<8 and abs(o['rowNum']-c['rowNum'])<8} for c in cards}
            for id in prefix:
                self.assertFalse(coverers[id]&state.remaining)
                t=state.types[id];tray.append(t);crush=tray.count(t)==3
                if crush:tray=[v for v in tray if v!=t]
                self.assertLess(len(tray),7);state.remove(id,crush)
            self.assertEqual(checkpoint['remaining_types'],[state.types[c['id']] for c in cards])
            self.assertEqual(checkpoint['picked'],[c['id'] not in state.remaining for c in cards])
            self.assertEqual(checkpoint['tray_counts'],[tray.count(t) for t in range(64)])
            self.assertEqual(int(checkpoint['rng_a'],16),state.rng._seed_1)
            self.assertEqual(int(checkpoint['rng_b'],16),state.rng._seed_2)
            # Exact, independent Python traversal verifies the small tail's
            # unsatisfiability before native 'exhausted' is used as a cache proof.
            seen=set();visits=0
            def oracle(state,tray):
                nonlocal visits
                visits+=1
                if not state.remaining:return not tray
                key=(tuple(sorted((i,state.types[i]) for i in state.remaining)),
                     state.rng._seed_1,state.rng._seed_2,tuple(sorted(tray)))
                if key in seen:return False
                seen.add(key)
                for id in state.remaining:
                    if coverers[id]&state.remaining:continue
                    t=state.types[id];next_tray=tray+[t];crush=next_tray.count(t)==3
                    if crush:next_tray=[v for v in next_tray if v!=t]
                    if len(next_tray)>=7:continue
                    child=copy.deepcopy(state);child.remove(id,crush)
                    if oracle(child,next_tray):return True
                return False
            possible=oracle(state,tray)
            self.assertEqual(json.loads(progress.read_text())['state'],'exhausted')
            self.assertFalse(possible);self.assertGreater(visits,1)


if __name__=='__main__':unittest.main()
