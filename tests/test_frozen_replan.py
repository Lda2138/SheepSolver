import json
from pathlib import Path
import tempfile
import unittest

from color_rules import initial_cards
from frozen_replan import LiveBoard,trial,check_static_route
from solve_original_batch import export
from test_color_rules import fixture as colored
from test_energy_rules import fixture as energy


def simple_static_planner(snapshot,prefix,folder,**kwargs):
    prefix=snapshot['_planning_prefix'];cards,_=initial_cards(snapshot)
    route=prefix+[c['id'] for c in sorted(cards,key=lambda c:(c['type'],c['id'])) if c['id'] not in prefix]
    return {'state':'frozen_solved','planned_cards':len(route)-len(prefix)},check_static_route(snapshot,prefix,route,True)


class FrozenReplanTests(unittest.TestCase):
    def test_only_segment_before_seeded_refresh_is_used(self):
        data=colored()
        def ordinary_first(snapshot,prefix,folder,**kwargs):
            prefix=snapshot['_planning_prefix'];cards,_=initial_cards(snapshot)
            # Complete the exposed ordinary triple first, leaving changing
            # cards on the board so the seeded refresh is actually exercised.
            route=prefix+[c['id'] for c in sorted(cards,key=lambda c:(c['type'],-c['rolNum'])) if c['id'] not in prefix]
            return {'state':'frozen_solved'},check_static_route(snapshot,prefix,route,True)
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);info,answer=trial(data,out,seconds=5,planner=ordinary_first)
            self.assertEqual(info['state'],'solved',info)
            changes=sum(bool(e['changed']) for r in info['rounds'] for e in r['executed_events'])
            self.assertGreater(changes,0);self.assertEqual(info['replans'],changes+1)
            mp=out/'map.json';mp.write_text(json.dumps(data),encoding='utf-8')
            self.assertTrue(export(mp,answer,out)['validated'])

    def test_alien_removal_discards_old_tail_and_replans_remaining_board(self):
        data=energy();snapshots=[]
        def planner(snapshot,*args,**kw):
            snapshots.append(snapshot);return simple_static_planner(snapshot,*args,**kw)
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);info,answer=trial(data,out,seconds=5,planner=planner)
            self.assertEqual(info['state'],'solved');self.assertEqual(info['replans'],2)
            self.assertEqual(len(answer['sequence']),8)
            remaining={c['id'] for v in snapshots[1]['levelData'].values() for c in v}
            self.assertNotIn('1-16-0',remaining)
            mp=out/'map.json';mp.write_text(json.dumps(data),encoding='utf-8')
            self.assertTrue(export(mp,answer,out)['validated'])

    def test_snapshot_carries_exact_nonempty_tray_with_virtual_cards(self):
        board=LiveBoard(colored());id=board.cards[0]['id'];board.play_to_refresh([id])
        snapshot=board.snapshot();runtime=LiveBoard(snapshot)
        for held in snapshot['_planning_prefix']:runtime.play_to_refresh([held])
        self.assertEqual(runtime.tray,board.tray)
        self.assertNotIn(id,runtime.state.remaining)
        self.assertEqual({c['id']:c['type'] for c in runtime.cards if c['id'] in runtime.state.remaining},
                         {id:board.state.types[id] for id in board.state.remaining})

    def test_frozen_snapshot_cannot_be_exported_as_a_real_solution(self):
        snapshot=LiveBoard(colored()).snapshot()
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);mp=out/'map.json';mp.write_text(json.dumps(snapshot),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'可执行答案'):export(mp,{'sequence':[]},out)


if __name__=='__main__':unittest.main()
