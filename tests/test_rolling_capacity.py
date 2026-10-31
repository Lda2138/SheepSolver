import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from frozen_replan import LiveBoard
from rolling_play import native_real_plan,plan_segment,make_segment,validate_segment,validate_lookahead
import test_rolling_play
from test_color_rules import fixture as colored
from test_energy_rules import fixture as energy

ROOT=Path(__file__).resolve().parent.parent


def flat(types):
    return {'levelData':{'1':[{'id':f'p{i}','type':t,'rolNum':i*8,'rowNum':0,
        'metaType':0,'metaData':0} for i,t in enumerate(types)]}}


def six_needed():
    data=flat([1,1,2,3]);data['levelData']['1']=[{**c,'rolNum':200+i*8} for i,c in enumerate(data['levelData']['1'])]
    positions=[(-4,-4),(0,-4),(4,-4),(-4,0),(4,0),(-4,4),(0,4),(4,4)]
    for i,(t,(x,y)) in enumerate(zip([2,2,3,3,4,4,5,5],positions)):
        data['levelData']['1'].append({'id':f'b{i}','type':t,'rolNum':x,'rowNum':y})
    for layer,t in ((2,1),(3,5),(4,4)):
        data['levelData'][str(layer)]=[{'id':f't{t}','type':t,'rolNum':0,'rowNum':0}]
    return data,['p0','p1','p2','p3']


class CapacityTests(unittest.TestCase):
    def plan(self,data,prefix=None,**kw):
        with tempfile.TemporaryDirectory() as d:
            return native_real_plan(LiveBoard(data),prefix or [],Path(d),seconds=2,**kw)

    def test_prefers_five_without_running_six(self):
        data=flat([1]*3+[2]*3+[3]*3)
        info,route=self.plan(data)
        self.assertTrue(route);self.assertEqual(info['tray_capacity'],5)
        self.assertEqual([a['capacity'] for a in info['attempts']],[5])
        segment=make_segment(data,'digest',[],route,info)
        self.assertTrue(validate_lookahead(segment)['complete'])

    def test_automatically_relaxes_to_six_only_when_needed(self):
        data,prefix=six_needed();info,route=self.plan(data,prefix)
        self.assertEqual(info['tray_capacity'],6);self.assertTrue(info['complete'])
        self.assertEqual(info['attempts'][0]['state'],'no_next_elimination')
        segment=make_segment(data,'digest',prefix,route,info)
        self.assertEqual(validate_lookahead(segment)['peak_slots'],6)
        bad=copy.deepcopy(segment);bad['tray_capacity']=5
        with self.assertRaises(ValueError):validate_segment(bad,data)
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);mp=folder/'map.json';mp.write_text(json.dumps(data),encoding='utf-8')
            segment['map_sha256']=hashlib.sha256(mp.read_bytes()).hexdigest();bad['map_sha256']=segment['map_sha256']
            test_rolling_play.RollingTests().native_check(folder,mp,segment)
            test_rolling_play.RollingTests().native_check(folder,mp,bad,1)
        # A later segment returns to the preferred five-slot capacity.
        info2,route2=self.plan(data,segment['sequence']);self.assertEqual(info2['tray_capacity'],5)

    def test_existing_six_can_drop_to_four_via_seventh_transient_tile(self):
        data=flat([1,1,2,2,3,3,1,2,3]);prefix=[f'p{i}' for i in range(6)]
        info,route=self.plan(data,prefix);self.assertEqual(info['tray_capacity'],5)
        segment=make_segment(data,'digest',prefix,route,info)
        self.assertEqual(validate_lookahead(segment)['peak_slots'],4)

    def test_no_stable_seven_or_fake_full_solution(self):
        data=flat([1,2,3,4,5,6,7]);info,route=self.plan(data,[f'p{i}' for i in range(6)])
        self.assertIsNone(route);self.assertEqual(info['state'],'no_next_elimination')

    def test_legal_first_triple_without_future_is_rejected(self):
        data=flat([1]*3+list(range(2,33)))
        info,route=self.plan(data)
        self.assertIsNone(route);self.assertNotEqual(info['state'],'no_next_elimination')
        fake={'tray_capacity':5,'horizon_target':3,'lookahead_events':1,'attempts':[]}
        with self.assertRaises(ValueError):make_segment(data,'digest',[],['p0','p1','p2'],fake)

    def test_native_and_python_replay_color_and_alien_future(self):
        for data in (colored(),energy()):
            with tempfile.TemporaryDirectory() as d:
                folder=Path(d);mp=folder/'map.json';mp.write_text(json.dumps(data),encoding='utf-8')
                _,segment=plan_segment(mp,[],folder/'rolling',seconds=2)
                validate_segment(segment,data)
                test_rolling_play.RollingTests().native_check(folder,mp,segment)
                bad=copy.deepcopy(segment);bad['lookahead_events']+=1
                with self.assertRaises(ValueError):validate_segment(bad,data)
                test_rolling_play.RollingTests().native_check(folder,mp,bad,1)

    def test_timeout_and_cancel_finish_status_without_exporting(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);mp=folder/'map.json';mp.write_text(json.dumps(flat([1]*3+[2]*3)),encoding='utf-8')
            with self.assertRaises(ValueError):plan_segment(mp,[],folder/'limited',seconds=.000001)
            status=json.loads((folder/'limited/search_status.json').read_text(encoding='utf-8'));self.assertEqual(status['state'],'limited');self.assertEqual(status['workers'],0)
            self.assertFalse(list((folder/'limited').glob('segment_*.json')))
            with self.assertRaises(ValueError):plan_segment(mp,[],folder/'cancelled',cancel=lambda:True)
            status=json.loads((folder/'cancelled/search_status.json').read_text(encoding='utf-8'));self.assertEqual(status['state'],'cancelled');self.assertEqual(status['workers'],0)
            self.assertFalse(list((folder/'cancelled').glob('segment_*.json')))

    def test_capacity_checked_before_alien_clears_slots(self):
        # On this click the alien empties the tray. The pre-alien stable sixth
        # slot still requires cap 6; checking just the final empty tray is wrong.
        data=flat([1,2,3,4,5]+[6]*3+[1,1,2,2,3,3,4,4,5,5])
        data.update(blockTypeData={str(i):1 for i in range(1,7)},map_seed=energy()['map_seed'],
            goldBlockData={'collectCount':1,'blockList':[{'val':6}]})
        cover=data['levelData']['1'].pop(4);cover['rolNum']=40
        data['levelData']['2']=[cover]  # Keep p5 charged until the last prefix pick.
        prefix=[f'p{i}' for i in range(5)]
        info={'tray_capacity':6,'horizon_target':3,'lookahead_events':1,'attempts':[]}
        segment=make_segment(data,'digest',prefix,['p5'],info)
        self.assertTrue(segment['energy_events'][-1]['alien'])
        self.assertEqual(segment['expected_tray'],[])
        bad=copy.deepcopy(segment);bad['tray_capacity']=5
        with self.assertRaises(ValueError):validate_segment(bad,data)

    def test_recorded_dead_prefix_is_rejected(self):
        folder=ROOT/'output/auto_capture/sessions/20261010T134551Z-7754a5'
        if not (folder/'map_1.json').exists():self.skipTest('Local historical map absent')
        data=json.loads((folder/'map_1.json').read_text(encoding='utf-8-sig'))
        actions=[json.loads(line) for line in (folder/'level_1_play/actions.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()]
        prefix=[a['card_id'] for a in actions[:145]]
        info,route=self.plan(data,prefix)
        self.assertIsNone(route);self.assertEqual(info['state'],'no_next_elimination')


if __name__=='__main__':unittest.main()
