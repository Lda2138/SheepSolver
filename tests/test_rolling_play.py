import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from color_rules import initial_cards
from desktop_assistant import validate_solution
from frozen_replan import LiveBoard
from rolling_play import make_segment,validate_segment,plan_segment
from test_color_rules import fixture as colored
from test_energy_rules import fixture as energy

ROOT=Path(__file__).resolve().parent.parent


class RollingTests(unittest.TestCase):
    def native_check(self,folder,mp,segment,code=0):
        sp=folder/'segment.json';sp.write_text(json.dumps(segment),encoding='utf-8')
        plan={'solution_path':str(sp),'source_map_path':str(mp),'map_sha256':segment['map_sha256'],
            'allow_rolling':True,'start_step':segment['start_step'],
            'positions':{c['id']:[30+(i%6)*45,180+(i//6)*45] for i,c in enumerate(segment['cards'])},'anchors':[]}
        pp=folder/'plan.json';pp.write_text(json.dumps(plan),encoding='utf-8')
        p=subprocess.run([str(ROOT/'SolverPlayback.exe'),str(pp),'--validate-only'],capture_output=True,
            text=True,encoding='utf-8',creationflags=subprocess.CREATE_NO_WINDOW,timeout=8)
        self.assertEqual(p.returncode,code,p.stderr)

    def test_native_player_accepts_long_segments_only_for_exact_routes(self):
        import hashlib
        from test_energy_move_out import board_map,ids
        data=board_map([2,2,2,3,3,3,4,4,4],gold=9)
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);mp=folder/'map.json';mp.write_text(json.dumps(data),encoding='utf-8')
            segment=make_segment(data,hashlib.sha256(mp.read_bytes()).hexdigest(),[],ids(9),exact=True)
            self.assertEqual(len(segment['sequence']),9);self.native_check(folder,mp,segment)
            segment.pop('exact_route');self.native_check(folder,mp,segment,1)

    def test_color_commits_through_first_refresh_and_native_checks_prefix(self):
        data=colored();board=LiveBoard(data)
        route=[c['id'] for c in sorted(board.cards,key=lambda c:(c['type'],-c['rolNum']))]
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);mp=folder/'map.json';mp.write_text(json.dumps(data),encoding='utf-8')
            segment=make_segment(data,hashlib.sha256(mp.read_bytes()).hexdigest(),[],route)
            self.assertEqual(len(segment['sequence']),3);self.assertFalse(segment['complete']);self.assertFalse(segment['validated'])
            validate_segment(segment,data)
            with self.assertRaises(ValueError):validate_solution(segment)
            self.native_check(folder,mp,segment)
            bad=copy.deepcopy(segment);bad['sequence_types'][0]=63
            with self.assertRaises(ValueError):validate_segment(bad,data)
            self.native_check(folder,mp,bad,1)
            # A partial final click cannot masquerade as game settlement.
            bad=copy.deepcopy(segment);bad['complete']=True;self.native_check(folder,mp,bad,1)

    def test_alien_segment_replans_real_remaining_cards_with_existing_tray(self):
        data=energy();board=LiveBoard(data);route=[c['id'] for c in sorted(board.cards,key=lambda c:(c['type'],c['id']))]
        first=make_segment(data,'digest',[],route)
        self.assertEqual(len(first['sequence']),2);self.assertTrue(first['energy_events'][-1]['alien'])
        prefix=first['sequence'];board=LiveBoard(data)
        for id in prefix:board.play_to_refresh([id])
        next_route=[c['id'] for c in board.cards if c['id'] in board.state.remaining]
        second=make_segment(data,'digest',prefix,next_route);validate_segment(second,data)
        self.assertTrue(set(first['energy_events'][-1]['auto_removed']).isdisjoint(second['sequence']));self.assertEqual(second['start_step'],2)
        self.assertEqual(second['sequence'][:2],prefix)
        third=make_segment(data,'digest',second['sequence'],next_route[3:]);self.assertTrue(third['complete'])

    def test_actual_partial_native_plan_is_not_exported_as_solution(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);mp=folder/'map.json';data=colored();mp.write_text(json.dumps(data),encoding='utf-8')
            path,segment=plan_segment(mp,[],folder/'rolling',seconds=.3)
            self.assertTrue(path.exists());validate_segment(segment,data)
            self.assertFalse((folder/'rolling/solution.json').exists());self.native_check(folder,mp,segment)
            mp.write_text('{}');self.native_check(folder,mp,segment,1)

    def test_real_energy_backgrounds_and_obscured_face_gates(self):
        image=ROOT/'output/workflow_test_fixtures/energy_faces_47.png'
        if not image.exists():self.skipTest('Local battery screenshot absent')
        from PIL import Image
        import numpy as np
        from app_bridge import energy_face
        array=np.asarray(Image.open(image).convert('RGB'))
        for p in ((83,185),(254,151),(100,271)):self.assertTrue(energy_face(array,p))
        for p in ((40,510),(100,237),(65,367)):self.assertFalse(energy_face(array,p))
        missing=array.copy();missing[167:203,66:100]=0;self.assertFalse(energy_face(missing,(83,185)))
        p=subprocess.run([str(ROOT/'SolverPlayback.exe'),'--check-energy-faces',str(image)],capture_output=True,
            text=True,encoding='utf-8',creationflags=subprocess.CREATE_NO_WINDOW,timeout=5)
        self.assertEqual(p.returncode,0,p.stderr)


if __name__=='__main__':unittest.main()
