"""Regression for the scaled world tutorial which used to wait forever."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import numpy as np
from PIL import Image,ImageDraw
import app_bridge
from board_align import fit,exposed,face_offsets,check_silhouette
from color_rules import initial_cards

ROOT=Path(__file__).resolve().parent.parent
FIXTURE=ROOT/'output/alignment_regression_20261029'


@unittest.skipUnless((FIXTURE/'small_world_tutorial.png').exists(),'captured regression absent')
class ScaledBoardTests(unittest.TestCase):
    def setUp(self):
        self.image=FIXTURE/'small_world_tutorial.png'
        self.cards,_=initial_cards(app_bridge.read(FIXTURE/'map_0.json'))

    def test_real_small_tutorial_matches_all_seven_exposed_tiles(self):
        calibration,score=fit(self.image,self.cards)
        check_silhouette(self.image,self.cards,calibration)
        self.assertGreater(score,5.8)
        self.assertAlmostEqual(calibration['x_unit'],3.6,delta=.04)
        self.assertAlmostEqual(calibration['y_unit'],3.64,delta=.04)
        im=np.asarray(Image.open(self.image).convert('RGB'))
        anchors=[]
        for card in exposed(self.cards):
            x=round(calibration['x_origin']+card['rolNum']*calibration['x_unit'])
            y=round(calibration['y_origin']+card['rowNum']*calibration['y_unit'])
            pale=sum(all(im[y+dy,x+dx]>[205,205,145]) for dx,dy in face_offsets(calibration))
            self.assertGreaterEqual(pale,5)
            anchors.append({'center':[x,y]})
        self.assertEqual(len(anchors),7)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'plan.json'
            path.write_text(json.dumps({'calibration':calibration,'anchors':anchors}),encoding='utf-8')
            result=subprocess.run([str(ROOT/'SolverPlayback.exe'),'--check-aligned-faces',str(path),str(self.image)],
                                  capture_output=True,timeout=5,creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode,0,result.stderr.decode('utf-8-sig',errors='replace'))

    def test_identifies_first_level_and_rejects_missing_tile(self):
        result=app_bridge.inspect_board(FIXTURE,self.image,2)
        self.assertEqual(result['level'],0)
        with tempfile.TemporaryDirectory() as directory:
            im=Image.open(self.image).convert('RGB')
            ImageDraw.Draw(im).rectangle((95,231,125,266),fill=(193,255,137))
            path=Path(directory)/'missing.png';im.save(path)
            with self.assertRaises(ValueError):fit(path,self.cards)

    def test_actual_second_level_uses_same_scaled_layout_and_switches_level(self):
        image=FIXTURE/'small_world_main.png'
        if not image.exists():self.skipTest('second-level frame absent')
        cards,_=initial_cards(app_bridge.read(FIXTURE/'map_1.json'))
        calibration,score=fit(image,cards);check_silhouette(image,cards,calibration)
        self.assertEqual(len(cards),231)
        self.assertGreater(score,5.8)
        self.assertLess(calibration['face_scale_x'],.85)
        self.assertEqual(app_bridge.inspect_board(FIXTURE,image,2)['level'],1)

    def test_alignment_failure_reports_geometry_instead_of_seed_or_search(self):
        with tempfile.TemporaryDirectory() as directory:
            im=Image.new('RGB',(336,622),(193,255,137))
            ImageDraw.Draw(im).rectangle((20,480,320,545),fill=(90,150,10))
            path=Path(directory)/'loading.png';im.save(path)
            with self.assertRaises(app_bridge.AwaitBoard) as raised:
                app_bridge.inspect_board(FIXTURE,path,2)
            message=str(raised.exception)
            self.assertIn('对齐',message)
            self.assertIn('第 1 关',message)
            self.assertNotIn('尚未开始求解',message)


@unittest.skipUnless((FIXTURE/'small_world_tutorial.png').exists(),'captured regression absent')
class RecalibrationTests(unittest.TestCase):
    """A calibration taken from another frame (alignment_score None) made the
    clicker reject every lower-layer tile of the right group in a loop."""
    def plan(self,directory,**extra):
        cards,_=initial_cards(app_bridge.read(FIXTURE/'map_0.json'))
        good,_=fit(FIXTURE/'small_world_tutorial.png',cards)
        wrong={**good,'x_origin':good['x_origin']-4,'x_unit':good['x_unit']+.15}
        positions,anchors=app_bridge.geometry(cards,wrong)
        plan={'reference_path':str(FIXTURE/'small_world_tutorial.png'),'calibration':wrong,'alignment_score':None,
              'positions':positions,'anchors':anchors,**extra}
        return plan,{'cards':cards},good

    def test_unfitted_plan_is_refitted_on_its_own_reference(self):
        with tempfile.TemporaryDirectory() as d:
            plan,solution,good=self.plan(d)
            fixed=app_bridge.recalibrate(Path(d),plan,solution)
            self.assertAlmostEqual(fixed['calibration']['x_unit'],good['x_unit'],places=3);self.assertIsNotNone(fixed['alignment_score'])
            c=solution['cards'][0]
            self.assertEqual(fixed['positions'][c['id']],[round(good['x_origin']+c['rolNum']*good['x_unit']),round(good['y_origin']+c['rowNum']*good['y_unit'])])
            self.assertEqual(json.loads((Path(d)/'plan.json').read_text(encoding='utf-8'))['calibration'],fixed['calibration'])

    def test_fitted_or_user_supplied_calibration_is_left_alone(self):
        with tempfile.TemporaryDirectory() as d:
            for extra in ({'manual_calibration':True},{'alignment_score':6.1}):
                plan,solution,_=self.plan(d,**extra);before=dict(plan['calibration'])
                self.assertEqual(app_bridge.recalibrate(Path(d),plan,solution)['calibration'],before)
            self.assertFalse((Path(d)/'plan.json').exists())


class VisualTrayTests(unittest.TestCase):
    def test_tray_is_drawn_in_type_groups_and_alien_clears_it(self):
        cards=[{'id':str(i),'type':t} for i,t in enumerate([6,12,6,8,6,9])]
        solution={'cards':cards,'sequence':[c['id'] for c in cards]}
        self.assertEqual(app_bridge.visual_tray(solution,4),[6,6,12,8])
        self.assertEqual(app_bridge.visual_tray(solution,5),[12,8])
        solution['energy_events']=[{'alien':False}]*3+[{'alien':True}]
        self.assertEqual(app_bridge.visual_tray(solution,5),[6])


PENDING=ROOT/'output/alignment_regression_20261031/pending_step41'


@unittest.skipUnless((PENDING/'now.png').exists(),'captured pending click absent')
class PendingClickTests(unittest.TestCase):
    """Esc stopped the player right after it sent a non-triple click; recovery
    used to refuse it for lack of a triple and left the run stuck."""
    def copy(self,directory):
        folder=Path(directory)/'20261030T175157Z-73339d';shutil.copytree(PENDING/folder.name,folder)
        play=folder/'level_1_play';plan=json.loads((play/'plan.json').read_text(encoding='utf-8-sig'))
        plan['solution_path']=str(folder/'level_1_static/complete/solution.json')
        for key in ('reference_path','initial_reference_path'):
            if key in plan:plan[key]=str(play/Path(plan[key]).name)
        (play/'plan.json').write_text(json.dumps(plan,ensure_ascii=False),encoding='utf-8');return folder,play

    def test_landed_non_triple_click_is_confirmed_once(self):
        with tempfile.TemporaryDirectory() as d:
            folder,play=self.copy(d)
            self.assertTrue(app_bridge.reconcile_pending(folder,1,PENDING/'now.png'))
            status=json.loads((play/'playback_status.json').read_text(encoding='utf-8-sig'))
            self.assertEqual((status['next_step'],status['input_pending']),(41,False))
            last=json.loads((play/'actions.jsonl').read_text(encoding='utf-8-sig').splitlines()[-1])
            self.assertEqual((last['step'],last['card_id']),(41,'23-20-24'))

    def test_unchanged_screen_is_not_counted_as_a_click(self):
        with tempfile.TemporaryDirectory() as d:
            folder,play=self.copy(d)
            self.assertTrue(app_bridge.reconcile_pending(folder,1,play/'last_verified.png'))
            status=json.loads((play/'playback_status.json').read_text(encoding='utf-8-sig'))
            self.assertEqual((status['next_step'],status['input_pending']),(40,False))
            self.assertEqual(len((play/'actions.jsonl').read_text(encoding='utf-8-sig').splitlines()),40)


ENERGY=ROOT/'output/alignment_regression_20261031'


@unittest.skipUnless((ENERGY/'daily_energy_level2.png').exists(),'captured energy regression absent')
class EnergyBoardTests(unittest.TestCase):
    """Daily level 2 with two exposed battery cards used to wait for a new map forever."""
    def test_exposed_battery_cards_do_not_block_fresh_board_match(self):
        candidates=app_bridge.fresh_board_candidates(ENERGY,ENERGY/'daily_energy_level2.png',2)
        self.assertEqual([c['level'] for c in candidates],[1])
        self.assertGreater(candidates[0]['score'],5.8)

    def test_battery_position_must_show_a_battery_face(self):
        image=ENERGY/'daily_energy_level2.png'
        cards,rules=initial_cards(app_bridge.read(ENERGY/'map_1.json'))
        calibration,_=fit(image,cards)
        im=Image.open(image).convert('RGB');draw=ImageDraw.Draw(im)
        for c in exposed(cards):
            if c['metaType']==3:
                x=round(calibration['x_origin']+c['rolNum']*calibration['x_unit'])
                y=round(calibration['y_origin']+c['rowNum']*calibration['y_unit'])
                draw.rectangle((x-14,y-14,x+14,y+14),fill=(250,245,225))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'no_battery.png';im.save(path)
            with self.assertRaisesRegex(ValueError,'电池牌'):fit(path,cards)


if __name__=='__main__':unittest.main()
