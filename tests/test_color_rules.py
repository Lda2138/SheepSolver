import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from color_rules import initial_cards, ColorState, compatibility, solution_state
from desktop_assistant import validate_solution
from solve_original_batch import export

ROOT = Path(__file__).resolve().parent.parent


def fixture():
    cards = []
    for i in range(12):
        cards.append({'id':f'1-{i*8}-0','rolNum':i*8,'rowNum':0,'type':1+i%2,
                      'metaType':2 if i<6 else 0,'metaData':0,'moldType':1})
    return {'levelData':{'1':cards},'blockTypeData':{'1':1,'2':1},
            'map_seed':[697634228,2420934636,717715288,3972332771]}


class ColorRuleTests(unittest.TestCase):
    def test_pool_order_initial_shuffle_and_fixed_tray(self):
        data=fixture();cards,rules=initial_cards(data);state=ColorState(cards,rules)
        self.assertEqual(compatibility(data),'color_shuffle')
        before=dict(state.types);id=state.special[0];picked_type=before[id]
        state.remove(id,False)
        self.assertEqual(state.types,before)  # Individual moves do not shuffle.
        fixed_rng=(state.rng._seed_1,state.rng._seed_2)
        state.remove(state.special[1],True)
        self.assertEqual(state.types[id],picked_type)  # Removed colors freeze.
        self.assertNotEqual(fixed_rng,(state.rng._seed_1,state.rng._seed_2))
        self.assertEqual(sorted(state.types[i] for i in state.special if i in state.remaining),
                         sorted(before[i] for i in state.special if i in state.remaining))

    def test_unknown_rules_and_unseeded_colors_are_rejected(self):
        data=fixture();data['levelData']['1'][0]['metaType']=3
        self.assertEqual(compatibility(data),'contains_special_card_rules')
        with self.assertRaises(ValueError):initial_cards(data)
        data=fixture();del data['map_seed']
        self.assertEqual(compatibility(data),'contains_special_card_rules')

    def test_search_python_and_native_replays_agree_without_clicks(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);mp=folder/'map.json';mp.write_text(json.dumps(fixture()))
            answer=folder/'answer.json'
            p=subprocess.run([str(ROOT/'FastOriginalSolver.exe'),str(mp),str(answer),
                'level-top','0','5','1','100000'],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(p.returncode,0,p.stderr)
            solution=export(mp,json.loads(answer.read_text()),folder)
            self.assertEqual(solution['rules']['kind'],'color_shuffle_v1')
            validate_solution(solution)
            state,tray=solution_state(solution,len(solution['sequence']))
            self.assertFalse(state.remaining);self.assertFalse(tray)
            positions={c['id']:[30+(i%6)*45,180+(i//6)*45] for i,c in enumerate(solution['cards'])}
            plan=folder/'plan.json';plan.write_text(json.dumps({'solution_path':str(folder/'solution.json'),
                'map_sha256':solution['map_sha256'],'positions':positions,'anchors':[]}))
            p=subprocess.run([str(ROOT/'SolverPlayback.exe'),str(plan),'--validate-only'],
                capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(p.returncode,0,p.stderr)
            solution['sequence_types'][0]=63
            with self.assertRaisesRegex(ValueError,'预测牌型'):validate_solution(solution)

    def test_original_client_rng_and_iteration_order(self):
        game=ROOT/'output/game_protocol/_subpackages_script-bundle__9.js'
        if not game.exists():self.skipTest('Local client source unavailable')
        source=game.read_text(encoding='utf-8');start=source.index('class i{constructor(){this._randTimes=0}',source.index('xorshift:[function'))
        end=source.index('n.XorShift=i',start);klass=source[start:end].rstrip(';')
        # Execute the actual extracted client PRNG in Node, independently of
        # Python's 64-bit implementation and C#'s search implementation.
        data=fixture()
        script=klass+';const map='+json.dumps(data)+''';
const rng=new i();rng.setSeed(map.map_seed);rng.random();
for(let k=0;k<6;k++)rng.random();
const cs=map.levelData["1"].filter(c=>c.metaType===2).sort((a,b)=>a.rowNum-b.rowNum||a.rolNum-b.rolNum);
const frames=[];
for(let step=0;cs.length;step++){
 if(step&&step%2===0)cs.splice(step%cs.length,1);
 const values=cs.map(c=>c.type);
 if(values.length>1)for(let k=values.length-1;k>=0;k--){let j=Math.floor(rng.random()*(k+1));[values[k],values[j]]=[values[j],values[k]];}
 cs.forEach((c,k)=>c.type=values[k]);frames.push(Object.fromEntries(cs.map(c=>[c.id,c.type])));
}console.log(JSON.stringify(frames));'''
        p=subprocess.run(['node','-e',script],capture_output=True,text=True,check=True,creationflags=subprocess.CREATE_NO_WINDOW)
        reference=json.loads(p.stdout);cards,rules=initial_cards(data);state=ColorState(cards,rules);ids=state.special[:]
        self.assertEqual({id:state.types[id] for id in ids},reference[0])
        for step,frame in enumerate(reference[1:],1):
            if step%2==0:state.remaining.remove(ids.pop(step%len(ids)))
            values=[state.types[id] for id in ids]
            if len(values)>1:state.rng.shuffle(values,len(values))
            state.types.update(zip(ids,values))
            self.assertEqual({id:state.types[id] for id in ids},frame)


if __name__=='__main__':unittest.main()
