"""Meaningful session/mode gates and map-to-screenshot geometry checks."""
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

import app_bridge
from board_align import fit,check_silhouette

ROOT=Path(__file__).resolve().parent.parent
FIXTURE=ROOT/'output/auto_capture/sessions/20261008T141544Z-a50904'


class ApplicationTests(unittest.TestCase):
    @unittest.skipUnless((ROOT/'output/daily_retry_loading.png').exists(),'captured loading frame absent')
    def test_loading_tray_does_not_block_a_new_game(self):
        from PIL import Image
        image=ROOT/'output/daily_retry_loading.png'
        folder=ROOT/'output/auto_capture/sessions/20261010T162421Z-266802'
        if not folder.exists():self.skipTest('daily maps absent')
        with tempfile.TemporaryDirectory() as directory:
            im=Image.open(image).convert('RGB')
            # Simulate a fade-in with no rendered cards and a pale tray.
            im.paste((193,255,137),(20,118,316,468))
            im.paste((215,235,170),(20,492,320,527))
            temporary=Path(directory)
            for level in range(2):(temporary/f'map_{level}.json').write_bytes((folder/f'map_{level}.json').read_bytes())
            image=temporary/'loading.png';im.save(image)
            self.assertEqual(app_bridge.tray_count(im),7)
            with self.assertRaisesRegex(ValueError,'等待游戏'):
                app_bridge.inspect_board(temporary,image,2)

    @unittest.skipUnless(FIXTURE.exists(),'local captured geometry fixtures absent')
    def test_matching_board_with_unrecorded_tray_still_blocks(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            (folder/'map_0.json').write_bytes((FIXTURE/'map_0.json').read_bytes())
            im=Image.open(FIXTURE/'tutorial_play/reference.png').convert('RGB')
            # Preserve all board geometry but put a real tile into slot one.
            im.paste(im.crop((100,199,134,235)),(23,492))
            image=folder/'with_tray.png';im.save(image)
            self.assertGreater(app_bridge.tray_count(im),0)
            with self.assertRaisesRegex(ValueError,'没有可核对的点击记录'):
                app_bridge.inspect_board(folder,image,1)

    def test_executable_checks_new_session_mode_and_service_without_input(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder);plan={'session':'current','mode':'daily','capture_status_path':str(p/'capture.json'),'service_lease_path':str(p/'lease.json')}
            (p/'plan.json').write_text(json.dumps(plan))
            for service,session,complete,mode,code in [(False,'current',True,'daily',1),(True,'other',True,'daily',1),(True,'current',False,'daily',1),(True,'current',True,'world',1),(True,'current',True,'daily',0)]:
                with self.subTest(service=service,session=session,complete=complete,mode=mode):
                    (p/'lease.json').write_text(json.dumps({'running':service}))
                    (p/'capture.json').write_text(json.dumps({'session':session,'complete':complete,'source_path':'/sheep/v1/game/world/game_start' if mode=='world' else '/sheep/v1/game/map_info_ex'}))
                    proc=subprocess.run([str(ROOT/'SolverPlayback.exe'),'--check-session',str(p/'plan.json')],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW,timeout=5)
                    self.assertEqual(proc.returncode,code)

    def test_stopped_service_old_session_wrong_mode_special_rules_blocked(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(app_bridge,'OUT',Path(folder)),patch('capture_service.alive',return_value=True):
            p=Path(folder);s={'session':'fixture','complete':True,'source_path':'/sheep/v1/game/map_info_ex'}
            (p/'status.json').write_text(json.dumps(s));(p/'service_lease.json').write_text(json.dumps({'running':False,'pid':1}))
            with self.assertRaisesRegex(ValueError,'启用抓取'):app_bridge.current('daily',0,'fixture')
            (p/'service_lease.json').write_text(json.dumps({'running':True,'pid':1}))
            with self.assertRaisesRegex(ValueError,'重新开局'):app_bridge.current('daily',0,'old')
            with self.assertRaisesRegex(ValueError,'模式'):app_bridge.current('world',0,'fixture')
            maps=p/'sessions/fixture';maps.mkdir(parents=True)
            m={'_acquisition':{'session':'fixture'},'_solver_compatibility':'contains_special_card_rules',
               'levelData':{'1':[{'id':'1-0-0','type':1,'metaType':3}]}}
            (maps/'map_0.json').write_text(json.dumps(m))
            with self.assertRaisesRegex(ValueError,'特殊规则'):app_bridge.current('daily',0,'fixture')
            m['_solver_compatibility']='standard';m['levelData']['1'][0]['metaType']=0;(maps/'map_0.json').write_text(json.dumps(m))
            self.assertEqual(app_bridge.current('daily',0,'fixture')[2],m)

    @unittest.skipUnless(FIXTURE.exists(),'local captured geometry fixtures absent')
    def test_both_captured_board_layouts_and_wrong_level(self):
        cards=[]
        for level,name in [(0,'tutorial_play'),(1,'main_play')]:
            data=json.loads((FIXTURE/f'map_{level}.json').read_text(encoding='utf-8'))
            cs=[{**c,'layer':int(k)} for k,v in data['levelData'].items() for c in v];cards.append(cs)
            image=FIXTURE/name/'reference.png';cal,score=fit(image,cs);check_silhouette(image,cs,cal)
            self.assertGreater(score,5.8)
            for c in cs:
                x=cal['x_origin']+c['rolNum']*cal['x_unit'];y=cal['y_origin']+c['rowNum']*cal['y_unit']
                self.assertLess(abs(x-(47.5+c['rolNum']*4.32)),4)
                self.assertLess(abs(y-((138.5 if level==0 else 137)+c['rowNum']*(4.18 if level==0 else 4.25))),6)
        with self.assertRaisesRegex(ValueError,'关次'):
            check_silhouette(FIXTURE/'main_play/reference.png',cards[0],{'x_origin':47.5,'y_origin':138.5,'x_unit':4.32,'y_unit':4.18})


if __name__=='__main__':unittest.main()
