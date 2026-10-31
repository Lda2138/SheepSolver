"""One move-out on energy/alien boards, following the client's moveOutArea."""
import json
from pathlib import Path
import tempfile
import unittest

from color_rules import initial_cards
from energy_rules import EnergyBoard,replay,replay_actions
import energy_plan
from test_auto_capture import SEED

ROOT=Path(__file__).resolve().parent.parent
REAL=ROOT/'output/alignment_regression_20261031/energy_level2_855371.json'


def board_map(types,threshold=10,gold=1):
    cards=[{'id':f'1-{i*8}-0','type':t,'rolNum':i*8,'rowNum':0,'metaType':0,'metaData':0} for i,t in enumerate(types)]
    counts={}
    for t in types:counts[str(t)]=counts.get(str(t),0)+1
    return {'levelData':{'1':cards},'blockTypeData':{k:v//3 for k,v in counts.items()},'map_seed':SEED,
            '_has_gold_rules':True,'goldBlockData':{'collectCount':threshold,'blockList':[{'val':gold,'count':0}]}}


def ids(n):return [f'1-{i*8}-0' for i in range(n)]


class EnergyMoveOutTests(unittest.TestCase):
    def test_return_drains_exposed_batteries_but_collects_nothing(self):
        cards,rules=initial_cards(board_map([1,1,1,2,2,2]));board=EnergyBoard(cards,rules);c=ids(6)
        board.move(c[3]);self.assertEqual([board.charges[i] for i in c[:3]],[3,3,3])
        board.move_out();self.assertEqual(board.side,[c[3]]);self.assertFalse(board.tray)
        event=board.return_card(c[3])
        self.assertTrue(event['returned']);self.assertFalse(event['collected']);self.assertEqual(board.collected,0)
        self.assertEqual([board.charges[i] for i in c[:3]],[2,2,2]);self.assertEqual(board.tray,[2])

    def test_alien_takes_missing_cards_from_move_out_area_without_randomness(self):
        cards,rules=initial_cards(board_map([1,1,1,2,2,2],threshold=1));board=EnergyBoard(cards,rules);c=ids(6)
        board.move(c[3]);board.move(c[4]);board.move_out();self.assertEqual(board.side,[c[3],c[4]])
        board.move(c[5]);draws=board.draws
        event=board.move(c[0])
        self.assertTrue(event['alien'])
        # Type 2 (first in the tray) comes wholly from the move-out area with
        # no randomness; only the two-card type-1 board shuffle draws.
        self.assertEqual(event['auto_removed'][:2],[c[3],c[4]]);self.assertEqual(set(event['auto_removed'][2:]),{c[1],c[2]})
        self.assertEqual(board.draws-draws,2)
        self.assertFalse(board.remaining or board.tray or board.side)

    def test_move_out_takes_leftmost_tiles_in_visual_order(self):
        cards,rules=initial_cards(board_map([2,3,2,4,2,3,3,4,4],gold=9));board=EnergyBoard(cards,rules);c=ids(9)
        for i in (0,1,2,3):board.move(c[i])
        self.assertEqual(board.tray_ids,[c[0],c[1],c[2],c[3]]);self.assertEqual(board.slot,[c[0],c[2],c[1],c[3]])
        board.move_out();self.assertEqual(board.side,[c[0],c[2],c[1]]);self.assertEqual(board.tray_ids,[c[3]])
        with self.assertRaises(ValueError):board.move_out()

    def test_static_answer_maps_back_to_real_tray_and_side_cards(self):
        cards,rules=initial_cards(board_map([2,2,2,3,3,3],gold=9));board=EnergyBoard(cards,rules);c=ids(6)
        board.move(c[0]);board.move(c[3])
        snap,alias,start=energy_plan.snapshot(cards,board)
        self.assertEqual([alias[i] for i in start],[('tray',c[0]),('tray',c[3])])
        self.assertEqual(sum(map(len,snap['levelData'].values())),6)
        answer={'execution_kind':energy_plan.KIND,'actions':[{'kind':'board','id':i} for i in start]+
            [{'kind':'move_out'},{'kind':'board','id':c[1]},{'kind':'return','id':start[0]},{'kind':'board','id':c[2]}]}
        self.assertEqual(energy_plan.tail_actions(answer,start,alias,False),
            [{'kind':'move_out'},{'kind':'board','id':c[1]},{'kind':'return','id':c[0]},{'kind':'board','id':c[2]}])
        with self.assertRaises(ValueError):energy_plan.tail_actions(answer,start,alias,True)

    def test_published_move_out_route_is_replayed_from_the_source_map(self):
        data=board_map([1,1,1,2,2,2]);c=ids(6)
        actions=[{'kind':'board','id':c[3]},{'kind':'board','id':c[4]},{'kind':'move_out'},
                 *({'kind':'board','id':i} for i in c[:3]),{'kind':'return','id':c[3]},{'kind':'return','id':c[4]},{'kind':'board','id':c[5]}]
        with tempfile.TemporaryDirectory() as d:
            mp=Path(d)/'map.json';mp.write_text(json.dumps(data),encoding='utf-8')
            path,payload=energy_plan.publish(mp,data,actions,[c[3]],Path(d)/'prop','test')
            self.assertEqual((payload['operation_count'],payload['return_clicks'],payload['move_after_board_clicks']),(9,2,2))
            self.assertEqual(energy_plan.cached(mp,data,[c[3]],Path(d)/'prop')[1]['actions'],payload['actions'])
            with self.assertRaises(ValueError):energy_plan.publish(mp,data,actions,[c[4]],Path(d)/'prop','test')
            with self.assertRaises(ValueError):energy_plan.publish(mp,data,actions[:-1],[],Path(d)/'prop','test')
        replay_actions(*initial_cards(data),actions)

    @unittest.skipUnless(REAL.exists(),'captured energy level absent')
    def test_real_energy_level_gets_an_exact_complete_route(self):
        with tempfile.TemporaryDirectory() as d:
            mp=Path(d)/'map_1.json';mp.write_bytes(REAL.read_bytes())
            path,segment=energy_plan.plan_current(mp,[],Path(d)/'out',60)
            self.assertEqual(segment['execution_kind'],'rolling_segment_v1')
            route=json.loads((Path(d)/'out/energy_route.json').read_text(encoding='utf-8'))['route']
            data=json.loads(REAL.read_text(encoding='utf-8-sig'));cards,rules=initial_cards(data)
            replay(cards,rules,route)
            # No alien fires on this map, so one exact segment carries the whole route.
            self.assertTrue(segment['exact_route']);self.assertEqual(segment['sequence'],route)
            # A resume reuses the replayed route instead of solving again.
            _,again=energy_plan.plan_current(mp,route[:40],Path(d)/'out',60)
            self.assertEqual((again['start_step'],again['sequence']),(40,route))

    def test_long_segments_need_an_exact_route(self):
        from rolling_play import make_segment,validate_segment
        data=board_map([2,2,2,3,3,3,4,4,4],gold=9);c=ids(9)
        exact=make_segment(data,'x',[],c,exact=True);self.assertEqual(exact['sequence'],c);validate_segment(exact,data)
        short=make_segment(data,'x',[],c);self.assertEqual(short['sequence'],c[:3])
        forged=dict(exact);forged.pop('exact_route')
        with self.assertRaises(ValueError):validate_segment(forged,data)


if __name__=='__main__':unittest.main()
