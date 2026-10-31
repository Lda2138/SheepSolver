import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from color_rules import compatibility,initial_cards,solution_state
from desktop_assistant import validate_solution
from energy_rules import EnergyBoard,replay
from map_protocol import decode_map
from solve_original_batch import export
from test_auto_capture import blob,integer,map_bytes,SEED

ROOT=Path(__file__).resolve().parent.parent


def fixture(threshold=2):
    cards=[{'id':f'1-{i*8}-0','type':1 if i<3 else 2,'rolNum':i*8,'rowNum':0,
            'metaType':0,'metaData':0} for i in range(9)]
    return {'levelData':{'1':cards},'blockTypeData':{'1':1,'2':2},'map_seed':SEED,
            '_has_gold_rules':True,'goldBlockData':{'collectCount':threshold,'blockList':[{'val':1,'count':0}]}}


class EnergyTests(unittest.TestCase):
    def test_native_cache_keeps_tray_order_before_seeded_alien(self):
        types=[1,2]*6+[3]*3
        data={'levelData':{'1':[{'id':f'1-{i*8}-0','type':t,'rolNum':i*8,'rowNum':0} for i,t in enumerate(types)],
              '2':[{'id':f'2-{i*8}-0','type':4,'rolNum':i*8,'rowNum':0} for i in range(12,15)]},
              'blockTypeData':{'1':2,'2':2,'3':1,'4':1},'map_seed':SEED,
              'goldBlockData':{'collectCount':1,'blockList':[{'val':3}]}}
        cards,rules=initial_cards(data)
        source=r'''
using System;using System.Collections.Generic;using System.IO;using System.Linq;using System.Web.Script.Serialization;
class CacheCheck{static int Main(string[] args){
var input=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(File.ReadAllText(args[0]));
var cards=((System.Collections.IEnumerable)input["cards"]).Cast<Dictionary<string,object>>().ToArray();
var a=new EnergyRules(cards,(Dictionary<string,object>)input["rules"]);var b=a.Copy();
foreach(int i in new[]{0,1,2,3})a.Step("1-"+(i*8)+"-0");
foreach(int i in new[]{1,0,3,2})b.Step("1-"+(i*8)+"-0");
if(!a.Remaining.SequenceEqual(b.Remaining)||!a.Tray.OrderBy(t=>t).SequenceEqual(b.Tray.OrderBy(t=>t)))return 2;
for(int i=0;i<cards.Length;i++)if(a.Charge(i)!=b.Charge(i))return 3;
if(a.Key()==b.Key())return 1;
a.Step("2-96-0");b.Step("2-96-0");
var x=a.Step("1-96-0");var y=b.Step("1-96-0");
if(!x.Alien||!y.Alien||x.AutoRemoved.SequenceEqual(y.AutoRemoved))return 4;
return 0;}}
'''
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);cs=folder/'CacheCheck.cs';cs.write_text(source,encoding='utf-8')
            mp=folder/'input.json';mp.write_text(json.dumps({'cards':cards,'rules':rules}),encoding='utf-8')
            exe=folder/'CacheCheck.exe';compiler=Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
            result=subprocess.run([str(compiler),'/nologo','/reference:System.Web.Extensions.dll',f'/out:{exe}',
                str(cs),str(ROOT/'native/rules/EnergyRules.cs'),str(ROOT/'native/rules/ColorRules.cs')],capture_output=True,text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,timeout=15)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            result=subprocess.run([str(exe),str(mp)],capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW,timeout=5)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_preview_battery_and_alien_states_match_independent_replay(self):
        # Run the exported browser script with inert DOM/canvas stubs. Compare
        # every frame with the source rules, including reset and new exposure.
        data=fixture();data['levelData']['2']=[{'id':f'2-{i*8}-0','type':2,'rolNum':i*8,'rowNum':0} for i in range(3)]
        data['blockTypeData']['2']=3
        cards,rules=initial_cards(data);board=EnergyBoard(cards,rules)
        prefix=['2-0-0','1-0-0','2-8-0','1-8-0']
        # The extra cover is already in the tray when the alien activates;
        # consume the actual remaining cards after its automatic removals.
        for id in prefix:board.move(id)
        route=prefix+[id for id in board.ids if id in board.remaining]
        board,peak=replay(cards,rules,route)
        payload={'cards':cards,'rules':rules,'sequence':route,'sequence_types':[e['type'] for e in board.events],
                 'energy_events':board.events,'source_label':'test','card_count':len(cards)}
        script=(ROOT/'assistant_preview.html').read_text(encoding='utf-8').split('<script>',1)[1].split('</script>',1)[0]
        stub=r'''
const elements=new Map(),context=new Proxy({}, {get:(o,k)=>o[k]||(()=>{}),set:(o,k,v)=>(o[k]=v,true)});
function element(){return {textContent:'',hidden:false,getContext:()=>context,replaceChildren(){},append(){},scrollIntoView(){}}}
global.document={querySelector:key=>{if(!elements.has(key))elements.set(key,element());return elements.get(key)},createElement:element};
'''
        probe=r'''
const frames=[];function frame(){return {charges:Object.fromEntries(charges),energy:energyCount,alien:alienCount,remaining:[...remaining].sort(),tray:tray.slice()}}
frames.push(frame());while(index<data.sequence.length){step();frames.push(frame())}reset();process.stdout.write(JSON.stringify({frames,reset:frame()}));
'''
        result=subprocess.run(['node','-e',stub+script.replace('__PAYLOAD__',json.dumps(payload))+probe],
            capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        actual=json.loads(result.stdout);board=EnergyBoard(cards,rules);expected=[];aliens=0
        def frame():return {'charges':dict(board.charges),'energy':board.collected,'alien':aliens,
                            'remaining':sorted(board.remaining),'tray':list(board.tray)}
        expected.append(frame())
        for id in route:
            event=board.move(id);aliens+=event['alien'];expected.append(frame())
        self.assertEqual(actual['frames'],expected);self.assertEqual(actual['reset'],expected[0])

    def test_decoder_keeps_energy_threshold_and_type(self):
        raw=map_bytes()+blob(6,integer(1,10)+blob(2,integer(1,17)+integer(2,0)))
        data=decode_map(raw)
        self.assertEqual(data['goldBlockData'],{'collectCount':10,'blockList':[{'val':17,'count':0}]})
        self.assertTrue(data['_has_gold_rules'])

    def test_energy_expires_and_newly_uncovered_energy_is_not_drained(self):
        data=fixture();cards,rules=initial_cards(data);board=EnergyBoard(cards,rules)
        for c in cards[3:7]:board.move(c['id'])
        self.assertTrue(all(board.charges[c['id']]==0 for c in cards[:3]))
        self.assertFalse(board.move(cards[0]['id'])['collected'])
        data=fixture();gold=data['levelData']['1'][0]
        data['levelData']['2']=[{'id':'2-0-0','type':3,'rolNum':0,'rowNum':0}]
        cards,rules=initial_cards(data);board=EnergyBoard(cards,rules)
        board.move('2-0-0');self.assertEqual(board.charges[gold['id']],4)
        board.move('1-24-0');self.assertEqual(board.charges[gold['id']],3)

    def test_alien_removes_hidden_matches_and_clears_tray(self):
        data=fixture();cards,rules=initial_cards(data);board=EnergyBoard(cards,rules)
        board.move(cards[0]['id']);ev=board.move(cards[1]['id'])
        self.assertTrue(ev['alien']);self.assertEqual(ev['auto_removed'],[cards[2]['id']])
        self.assertFalse(board.tray);self.assertEqual(board.collected,0)
        self.assertTrue(all(v==0 for v in board.charges.values()))
        # Auto removal is not a click: it can remove a covered target.
        data=fixture();data['levelData']['2']=[{'id':'2-16-0','type':2,'rolNum':16,'rowNum':0}]
        cards,rules=initial_cards(data);board=EnergyBoard(cards,rules)
        self.assertFalse(board.exposed('1-16-0'))
        board.move('1-0-0');self.assertEqual(board.move('1-8-0')['auto_removed'],['1-16-0'])

    def test_complete_export_validates_alien_trace_against_source(self):
        data=fixture();cards,rules=initial_cards(data)
        route=[cards[0]['id'],cards[1]['id']]+[c['id'] for c in cards[3:]]
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);mp=out/'map.json';mp.write_text(json.dumps(data),encoding='utf-8')
            answer=export(mp,{'sequence':route},out);validate_solution(answer)
            self.assertEqual(len(answer['sequence']),8);self.assertEqual(answer['card_count'],9)
            state,tray=solution_state(answer,2);self.assertEqual(len(state.remaining),6);self.assertFalse(tray)
            bad=copy.deepcopy(answer);bad['energy_events'][1]['auto_removed']=[]
            with self.assertRaisesRegex(ValueError,'event trace'):validate_solution(bad)
            with self.assertRaises(ValueError):export(mp,{'sequence':[c['id'] for c in cards]},out)

    def test_native_energy_solver_and_clicker_replay_agree_without_input(self):
        from types import SimpleNamespace
        from solve_verified_map import run
        for threshold in (2,3):
            data=fixture(threshold)
            with tempfile.TemporaryDirectory() as d:
                out=Path(d);mp=out/'map.json';mp.write_text(json.dumps(data),encoding='utf-8')
                a=SimpleNamespace(map=mp,output=out/'answer',solve_first=.8,time_limit=5,performance='fast')
                self.assertEqual(run(a),0)
                solution=json.loads((a.output/'solution.json').read_text(encoding='utf-8'));validate_solution(solution)
                self.assertTrue(any(e['alien'] for e in solution['energy_events']))
                plan={'solution_path':str(a.output/'solution.json'),'map_sha256':solution['map_sha256'],
                      'positions':{c['id']:[30+45*(i%6),180+45*(i//6)] for i,c in enumerate(solution['cards'])},'anchors':[]}
                pp=out/'plan.json';pp.write_text(json.dumps(plan),encoding='utf-8')
                result=subprocess.run([str(ROOT/'SolverPlayback.exe'),str(pp),'--validate-only'],
                    capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW,timeout=8)
                self.assertEqual(result.returncode,0,result.stderr)

    def test_empty_tray_alien_and_seeded_client_methods_match(self):
        game=ROOT/'output/game_protocol/_subpackages_script-bundle__9.js'
        if not game.exists():self.skipTest('Client source unavailable')
        source=game.read_text(encoding='utf-8')
        def method(start,end):
            a=source.index(start);b=source.index(end,a+len(start));return source[a:b]
        klass=method('class i{constructor(){this._randTimes=0}','n.XorShift=i').rstrip(';')
        # Execute original client methods with visual/audio stubs. This tests
        # partial shuffle, numeric traversal and battery drain independently.
        originals='\n'.join(method(a,b) for a,b in [
            ('findAlienBlockRandomly(e,t,n){','clearGoldMeta(){'),
            ('onAfterBlockRemoved(e){','updateColorChangeBlockType(){'),
            ('useAlienItemCallbackA(){','useAlienItemCallbackB(){')])
        alien=method('useAlienItem(){','setAlienPlayingState(){')
        harness=r'''
const s={EBlockMetaType:{Normal:0,ColorFixed:1,ColorChangedWhenCrush:2,Gold:3,Cake:4},EBlockConst:{AlienItemRemovedCardCount:3},isAlienCollectBlock:c=>[0,1,3].includes(c.metaType),PropType:{alienItem:10}},a=s;
const u={CollectOneGoldBlock:{emit(){}},CollectOneCakeBlock:{emit(){}}};
const util={shuffleArrayIndex:(n,r)=>Math.floor(r.random()*n)};
const rng=new i();rng.setSeed(input.map_seed);rng.random();for(let k=0;k<9;k++)rng.random();
let pending=false,removed=[],rows=[];
const all=input.levelData['1'].map(c=>({cardDataObj:{...c,metaType:c.type===1?3:0,metaData:c.type===1?4:0},isShowLight:true,getType(){return this.cardDataObj.type},setMetaType(t,d){this.cardDataObj.metaType=t;this.cardDataObj.metaData=d},clearGoldMeta(){this.setMetaType(0,0)},isCanCrushPieces(){return this.isShowLight&&this.cardDataObj.metaType===3&&this.cardDataObj.metaData>0},crushGoldPieces(){if(--this.cardDataObj.metaData===0)this.clearGoldMeta()},isCanCrushCakePieces(){return false}}));
class Board{
constructor(){this.goldCollectCount=0;this.nowLevelData=input;this.mapXorShift=rng;this.left=new Set(all);this.parentComp={onRecoveryCriticalOperation(){}};this.moveOutArea={getComponent(){throw Error('Unexpected move-out')}};}
foreachBlockComponent(fn){Array.from(this.left).sort((x,y)=>x.cardDataObj.rowNum-y.cardDataObj.rowNum||x.cardDataObj.rolNum-y.cardDataObj.rolNum).forEach(fn)}
scheduleOnce(){pending=true}clearGoldMeta(){this.foreachBlockComponent(c=>c.clearGoldMeta())}
mapShuffle(arr,n){for(let k=arr.length-1;k>=0;k--){if(n--<=0)return arr;let j=Math.floor(rng.random()*(k+1));[arr[k],arr[j]]=[arr[j],arr[k]];}return arr}
getBlockCount(){return this.left.size}
METHODS
}
class Area{
constructor(board){this.chessboard=board;this.crushArray=[];this.parentComp={addOp(){}}}
setAlienPlayingState(){} firstThreeBlockNonStopCount(){return 0}isThreeBlockCanCrush(){return false}
playAlienEffect(arr){removed=arr.filter(c=>this.chessboard.left.has(c)).map(c=>c.cardDataObj.id);arr.forEach(c=>this.chessboard.left.delete(c));this.crushArray=[]}
ALIEN
}
// The original method refers to its imported utility as a.default.
util.default=util;s.default=util;a.default=util;
const board=new Board(),area=new Area(board);board.crushArea=area;
for(const id of route){
 const card=all.find(c=>c.cardDataObj.id===id);board.left.delete(card);area.crushArray.push(card);
 if(area.crushArray.filter(c=>c.getType()===card.getType()).length===3)area.crushArray=area.crushArray.filter(c=>c.getType()!==card.getType());
 removed=[];board.onAfterBlockRemoved(card);
 if(pending){pending=false;board.useAlienItemCallbackA()}
 rows.push({count:board.goldCollectCount,charges:Object.fromEntries(all.map(c=>[c.cardDataObj.id,c.cardDataObj.metaData])),auto:removed,tray:area.crushArray.map(c=>c.getType()),draws:rng.randTimes});
}
process.stdout.write(JSON.stringify(rows));
'''.replace('METHODS',originals).replace('ALIEN',alien)
        for threshold in (2,3):
            data=fixture(threshold);cards,rules=initial_cards(data);route=[c['id'] for c in cards[:threshold]]
            js=klass+';const input='+json.dumps(data)+';const route='+json.dumps(route)+';'+harness
            result=subprocess.run(['node','-e',js],capture_output=True,text=True,check=True,creationflags=subprocess.CREATE_NO_WINDOW)
            expected=json.loads(result.stdout);board=EnergyBoard(cards,rules)
            for id,row in zip(route,expected):
                ev=board.move(id)
                self.assertEqual(ev['auto_removed'],row['auto']);self.assertEqual(board.tray,row['tray'])
                self.assertEqual(board.collected,row['count']);self.assertEqual(board.draws+1,row['draws'])
                self.assertEqual({c['id']:board.charges.get(c['id'],0) for c in cards},row['charges'])


if __name__=='__main__':unittest.main()
