"""Check elimination-centered reduction against independent move-level DFS."""
import tempfile
from pathlib import Path
import unittest

from color_rules import initial_cards,ColorState
from test_tight_search import sample_map,graph,oracle,move

ROOT=Path(__file__).resolve().parent.parent
EXE=ROOT/'FastOriginalSolver.Macro2.exe'


@unittest.skipUnless(EXE.exists(),'macro experiment build absent')
class MacroTests(unittest.TestCase):
    def run_native(self,*args,**kwargs):
        import test_tight_search
        previous=test_tight_search.EXE;test_tight_search.EXE=EXE
        try:return test_tight_search.TightSearchTests.run_native(self,*args,**kwargs)
        finally:test_tight_search.EXE=previous

    def test_macro_completion_and_prefixes_match_move_level_oracle(self):
        import json
        from solve_original_batch import export
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            for seed in range(24):
                data=sample_map(seed);cards,rules=initial_cards(data);state=ColorState(cards,rules)
                parents=graph(cards);tray=[];prefix=[]
                for fixed in range(4):
                    expected=oracle(state,tray,parents)
                    p,mp,answer=self.run_native(folder,data,'macro',prefix)
                    self.assertEqual(p.returncode==0,expected,(seed,prefix,p.stdout))
                    if expected:self.assertTrue(export(mp,json.loads(answer.read_text()),folder)['validated'])
                    options=[id for id in sorted(state.remaining) if not parents[id]&state.remaining]
                    if not options:break
                    id=options[(seed+fixed)%len(options)];s,t,_=move(state,tray,id)
                    if len(t)>=7:break
                    prefix.append(id);state,tray=s,t

    def test_dependency_trap_is_refuted_without_move_permutations(self):
        import json
        data={'levelData':{}}
        for layer in range(1,4):
            data['levelData'][str(layer)]=[{'id':f'{layer}-{t}','rolNum':(t%2)*4,'rowNum':(t//2)*4,'type':t+1} for t in range(4)]
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);p,_,_=self.run_native(folder,data,'macro')
            self.assertEqual(p.returncode,2)
            status=json.loads((folder/'progress.json').read_text())
            self.assertEqual(status['state'],'exhausted');self.assertEqual(status['nodes'],1)


if __name__=='__main__':
    unittest.main()
