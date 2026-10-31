"""Native beam legality, safe pruning and width backfill versus an oracle."""
import json
from pathlib import Path
import random
import subprocess
import tempfile
import unittest

from color_rules import initial_cards,ColorState
from desktop_assistant import validate
from test_tight_search import graph,oracle
from test_static_plan import chain

ROOT=Path(__file__).resolve().parent.parent
EXE=ROOT/'StaticFastSolver.exe'


@unittest.skipUnless(EXE.exists(),'static beam is not built')
class StaticBeamTests(unittest.TestCase):
    def run_native(self,data,prefix=(),mode='safe',width=4096):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);mp=folder/'map.json';out=folder/'answer.json';pp=folder/'prefix.json';progress=folder/'progress.json'
            mp.write_text(json.dumps(data),encoding='utf-8');pp.write_text(json.dumps({'sequence':list(prefix)}))
            result=subprocess.run([str(EXE),str(mp),str(out),'static-beam-'+mode,'0','5','1997',
                '2000000',str(progress),str(pp),str(width)],capture_output=True,text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,timeout=10)
            self.assertIn(result.returncode,(0,2),result.stderr)
            answer=json.loads(out.read_text(encoding='utf-8-sig')) if out.exists() else None
            return answer,json.loads(progress.read_text(encoding='utf-8-sig'))

    def test_large_enough_beam_matches_exhaustive_static_oracle(self):
        for seed in range(20):
            rng=random.Random(seed);types=[1,2,3,4]*3;rng.shuffle(types);levels={}
            for i,t in enumerate(types):
                levels.setdefault(str(i//3+1),[]).append({'id':str(i),'type':t,
                    'rolNum':rng.choice((0,8,16)),'rowNum':rng.choice((0,8,16))})
            data={'levelData':levels};cards,rules=initial_cards(data)
            expected=oracle(ColorState(cards,rules),[],graph(cards))
            for mode in ('safe','dig','balance'):
                answer,p=self.run_native(data,mode=mode)
                self.assertEqual(bool(answer),expected,(seed,mode,p))
                if answer:validate(cards,answer['sequence'])

    def test_every_nonempty_tray_boundary_replays_legally(self):
        data=chain([1,2,3]*3);cards,_=initial_cards(data)
        route=[f'{9-i}-0-0' for i in range(9)]
        for step in range(10):
            answer,p=self.run_native(data,prefix=route[:step])
            self.assertIsNotNone(answer,(step,p));self.assertEqual(answer['sequence'][:step],route[:step])
            validate(cards,answer['sequence'])

    def test_empty_diversity_buckets_do_not_waste_global_beam_width(self):
        data={'levelData':{'1':[{'id':str(i),'type':i//3+1,'rolNum':i*8,'rowNum':0} for i in range(21)]}}
        answer,p=self.run_native(data,width=128)
        self.assertIsNotNone(answer,p)
        self.assertEqual(p['static_beam_max_frontier'],128)

    def test_failure_remains_limited_instead_of_proving_whole_map_unsat(self):
        answer,p=self.run_native(chain([1,2,3,4]*3))
        self.assertIsNone(answer);self.assertEqual(p['state'],'limited')


if __name__=='__main__':unittest.main()
