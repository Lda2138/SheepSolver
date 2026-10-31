"""Independent tiny-board oracle for exact pruning and colored memo states."""
import copy
import json
from pathlib import Path
import random
import subprocess
import tempfile
import unittest

from color_rules import initial_cards,ColorState
from solve_original_batch import export

ROOT=Path(__file__).resolve().parent.parent
EXE=ROOT/'FastOriginalSolver.exe'


def sample_map(seed):
    r=random.Random(seed);levels={};types=([1,1,2,2,3,3,4,4,5] if seed%3==0 else [1,2,3]*3);r.shuffle(types)
    for i,t in enumerate(types):
        layer=1+i//3;x=r.choice((0,8,16,32));y=r.choice((0,8,16))
        # IDs need to be unique even if coordinates overlap.
        levels.setdefault(str(layer),[]).append({'id':f'{layer}-{i}-card','rolNum':x,'rowNum':y,
            'type':t,'metaType':2 if i in (0,3,6) else 0,'metaData':0,'moldType':1})
    return {'levelData':levels,'blockTypeData':{'1':1,'2':1,'3':1},
            'map_seed':[697634228,2420934636,717715288,3972332771]}


def graph(cards):
    return {c['id']:{p['id'] for p in cards if p['layer']>c['layer'] and
            abs(p['rolNum']-c['rolNum'])<8 and abs(p['rowNum']-c['rowNum'])<8} for c in cards}


def move(state,tray,id):
    s=copy.deepcopy(state);t=s.types[id];new=tray+[t];crush=new.count(t)==3
    if crush:new=[v for v in new if v!=t]
    s.remove(id,crush)
    return s,new,crush


def oracle(state,tray,parents,first_crush=False,seen=None):
    if not state.remaining:return not tray and not first_crush
    if seen is None:seen=set()
    key=(tuple(sorted(state.remaining)),tuple(sorted(tray)),
         tuple(state.types[i] for i in state.special if i in state.remaining),
         state.rng._seed_1 if state.rng else 0,state.rng._seed_2 if state.rng else 0)
    if key in seen:return False
    seen.add(key)
    for id in sorted(state.remaining):
        if parents[id]&state.remaining:continue
        s,t,crushed=move(state,tray,id)
        if len(t)>=7:continue
        if first_crush and crushed:return True
        if oracle(s,t,parents,first_crush,seen):return True
    return False


@unittest.skipUnless(EXE.exists(),'native experiment absent')
class TightSearchTests(unittest.TestCase):
    def run_native(self,folder,data,mode,prefix=None):
        mp=folder/'map.json';mp.write_text(json.dumps(data));answer=folder/'answer.json'
        answer.unlink(missing_ok=True)
        cmd=[str(EXE),str(mp),str(answer),mode,'0','10','31','2000000',str(folder/'progress.json')]
        if prefix is not None:
            pp=folder/'prefix.json';pp.write_text(json.dumps({'sequence':prefix}));cmd.append(str(pp))
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=15,creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertIn(p.returncode,(0,2),p.stderr)
        return p,mp,answer

    def test_short_horizon_matches_independent_exhaustion(self):
        checked=0;dead=0
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            for seed in range(30):
                data=sample_map(seed);cards,rules=initial_cards(data);state=ColorState(cards,rules)
                parents=graph(cards);r=random.Random(seed+99);prefix=[];tray=[]
                while state.remaining:
                    options=[id for id in sorted(state.remaining) if not parents[id]&state.remaining]
                    if not options:break
                    id=r.choice(options);s,t,_=move(state,tray,id)
                    if len(t)>=7:break
                    prefix.append(id);state,tray=s,t
                    if len(tray) in (4,5):
                        expected=oracle(state,tray,parents,True)
                        p,_,_=self.run_native(folder,data,'probe-tight',prefix)
                        actual=json.loads(p.stdout)['possible'];self.assertEqual(actual,expected,(seed,prefix,tray))
                        checked+=1;dead+=not expected
        self.assertGreater(checked,30);self.assertGreater(dead,0)

    def test_pruning_and_symmetry_preserve_satisfiability_and_verified_answers(self):
        solved=dead=0
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            for seed in range(18):
                data=sample_map(seed);cards,rules=initial_cards(data)
                expected=oracle(ColorState(cards,rules),[],graph(cards))
                solved+=expected;dead+=not expected
                for mode in ('color-top','tight-top','two-top','symmetry-top'):
                    p,mp,answer=self.run_native(folder,data,mode)
                    self.assertEqual(p.returncode==0,expected,(seed,mode,p.stdout))
                    if expected:self.assertTrue(export(mp,json.loads(answer.read_text()),folder)['validated'])
        self.assertGreater(solved,0);self.assertGreater(dead,0)

    def test_compact_memo_matches_independent_color_state_and_draw_count(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            for seed in range(10):
                data=sample_map(seed);cards,rules=initial_cards(data);state=ColorState(cards,rules)
                parents=graph(cards);prefix=[];tray=[];draws=1+rules['pool_draws']+len(state.special)
                for _ in range(6):
                    options=[id for id in sorted(state.remaining) if not parents[id]&state.remaining]
                    if not options:break
                    id=options[seed%len(options)];s,t,crushed=move(state,tray,id)
                    if len(t)>=7:break
                    prefix.append(id);state,tray=s,t
                    count=sum(i in state.remaining for i in state.special)
                    if crushed and count>1:draws+=count
                    p,_,_=self.run_native(folder,data,'probe-memo',prefix);memo=json.loads(p.stdout)
                    words=[0,0,0,0]
                    for j,id in enumerate(state.special):
                        if id in state.remaining:words[j//10]|=state.types[id]<<((j%10)*6)
                    self.assertEqual([memo[f'C{i}'] for i in range(4)],words)
                    uniform=len({state.types[id] for id in state.special if id in state.remaining})<=1
                    self.assertEqual(memo['Draws'],0 if uniform else draws)
                    # Global type conservation independently derives the tray.
                    for t in range(1,64):
                        total=sum(c['type']==t for c in cards)
                        remaining=sum(state.types[id]==t for id in state.remaining)
                        self.assertEqual((total-remaining)%3,tray.count(t))


if __name__=='__main__':unittest.main()
