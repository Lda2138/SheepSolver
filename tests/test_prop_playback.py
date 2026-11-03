"""Independent route replay plus screenshot-driven execution with a fake client.

The simulator renders the board/tray/side state and records requested input;
no real window or game receives clicks in these tests.
"""
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image,ImageDraw

import app_bridge as bridge
import prop_playback as player
import prop_plan
import workflow_controller as workflow
from solve_verified_map import atomic_json


def fixture():
    word=[1,2,3,4,5,6,1,1,2,2,3,3,4,4,5,5,6,6]
    data={'levelData':{str(18-i):[{'id':str(i),'type':t,'rolNum':28,'rowNum':28,'metaType':0,'moldType':1}] for i,t in enumerate(word)}}
    actions=[str(i) for i in range(6)]+['MOVE_OUT']
    for j in range(6):
        actions.extend([str(6+j*2),str(7+j*2)])
        if j<3:actions.append(str(j))
    return data,actions


COLORS={1:(20,45,230),2:(220,20,30),3:(20,160,35),4:(160,20,190),5:(20,150,190),6:(180,90,10)}


def icon(t):
    image=Image.new('RGB',(34,36),(250,245,210));draw=ImageDraw.Draw(image)
    draw.ellipse((7,7,26,29),fill=COLORS[t]);draw.line((9,t+8,24,t+8),fill=(255,255,255),width=2)
    return image


class PropPlaybackTests(unittest.TestCase):
    def make(self,directory):
        root=Path(directory)/'session';play=root/'level_1_play';play.mkdir(parents=True)
        data,actions=fixture();mp=root/'map_1.json';atomic_json(mp,data)
        source,answer=prop_plan.publish(mp,data,actions,[],root/'prop','simulation')
        cards,_=player.initial_cards(data);trace,_=prop_plan.verify(data,answer['actions'])
        plan={'execution_kind':player.KIND,'prop_solution_path':str(source),'source_map_path':str(mp),
              'map_sha256':hashlib.sha256(mp.read_bytes()).hexdigest(),'positions':{c['id']:[168,250] for c in cards},
              'anchors':[],'calibration':{'x_origin':47.04,'y_origin':131,'x_unit':4.32,'y_unit':4.25,'face_scale_x':1,'face_scale_y':1},'session':'session','mode':'world','level':1,
              'reference_path':str(play/'reference.png')}
        Image.new('RGB',(336,622),(193,255,137)).save(play/'reference.png')
        templates=play/'templates';templates.mkdir()
        atomic_json(templates/'context.json',{'map_sha256':plan['map_sha256']})
        for t in COLORS:icon(t).save(templates/f'type_{t}.png')
        atomic_json(play/'plan.json',plan);atomic_json(play/'prop_state.json',{'next_operation':0,'input_pending':False})
        return root,play,plan,cards,trace

    def render(self,plan,cards,trace,n,path):
        remaining,tray,side=player.state_at(cards,trace,n);by_id={c['id']:c for c in cards}
        image=Image.new('RGB',(336,622),(193,255,137))
        ImageDraw.Draw(image).rectangle((15,482,320,544),fill=(85,155,10))
        for c in sorted(remaining,key=lambda c:c['layer']):image.paste(icon(c['type']),(151,232))
        for k,id in enumerate(tray):image.paste(icon(by_id[id]['type']),(26+42*k,493))
        for k,id in enumerate(side):image.paste(icon(by_id[id]['type']),(83+50*k,435))
        image.save(path);return image

    def test_full_route_runs_board_and_returns_but_never_clicks_move_out_or_ad(self):
        with tempfile.TemporaryDirectory() as d:
            root,play,plan,cards,trace=self.make(d);driver={'n':0,'manual_frames':0,'captures':0};requests=[];states=[]
            def capture(destination=None):
                driver['captures']+=1
                if driver['captures']>150:raise RuntimeError('Simulator exceeded capture limit')
                status=bridge.read(play/'playback_status.json');states.append(status.get('state'))
                if driver['n']==6:
                    driver['manual_frames']+=1
                    if driver['manual_frames']>=4:driver['n']=7 # user completed move-out/ad
                self.render(plan,cards,trace,driver['n'],Path(destination));return Path(destination)
            def click(args,**kwargs):
                req=bridge.read(Path(args[-1]));n=req['operation'];self.assertEqual(n,driver['n'])
                self.assertNotEqual(trace[n]['kind'],'move_out');requests.append((n,trace[n]['kind']))
                driver['n']+=1;return SimpleNamespace(returncode=0,stderr=b'')
            with patch.object(player.bridge,'current'),patch.object(player.bridge,'capture_snapshot',side_effect=capture),patch.object(player.subprocess,'run',side_effect=click),patch.object(player.time,'sleep'):
                self.assertEqual(player.run(play/'plan.json'),0,bridge.read(play/'playback_status.json'))
            self.assertIn('waiting_manual_move',states)
            self.assertEqual(len(requests),21);self.assertEqual(sum(kind=='return' for _,kind in requests),3)
            self.assertEqual(bridge.read(play/'prop_state.json')['next_operation'],22)
            self.assertEqual(bridge.read(play/'playback_status.json')['state'],'sequence_finished')

    def test_manual_transition_requires_matching_side_cards_and_tray(self):
        with tempfile.TemporaryDirectory() as d:
            _,play,plan,cards,trace=self.make(d);image=play/'now.png'
            self.render(plan,cards,trace,6,image)
            with self.assertRaises(ValueError):player.check_state(plan,cards,trace,7,image,play)
            self.render(plan,cards,trace,7,image)
            self.assertEqual(len(player.check_state(plan,cards,trace,7,image,play)),3)
            wrong=Image.open(image).convert('RGB');wrong.paste(icon(6),(83,435));wrong.save(image)
            with self.assertRaisesRegex(ValueError,'移出区'):player.check_state(plan,cards,trace,7,image,play)

    def test_pending_return_is_reconciled_once_without_input(self):
        with tempfile.TemporaryDirectory() as d,patch('capture_service.alive',return_value=False):
            root,play,plan,cards,trace=self.make(d)
            n=next(i for i,t in enumerate(trace) if t['kind']=='return')
            atomic_json(play/'prop_state.json',{'next_operation':n,'input_pending':True})
            image=play/'now.png';self.render(plan,cards,trace,n+1,image)
            with patch.object(player.subprocess,'run') as click:
                result=player.inspect_resume(root,1,image)
                self.assertTrue(result['prop_execution']);click.assert_not_called()
            self.assertEqual(bridge.read(play/'prop_state.json')['next_operation'],n+1)
            player.inspect_resume(root,1,image)
            self.assertEqual(bridge.read(play/'prop_state.json')['next_operation'],n+1)

    def test_map_tampering_blocks_execution(self):
        with tempfile.TemporaryDirectory() as d:
            _,play,plan,_,_=self.make(d)
            Path(plan['source_map_path']).write_text('{}')
            with self.assertRaises(ValueError):player.load(play/'plan.json')

    def test_manual_move_completed_while_app_closed_is_reconciled_from_side_evidence(self):
        with tempfile.TemporaryDirectory() as d,patch('capture_service.alive',return_value=False):
            root,play,plan,cards,trace=self.make(d)
            atomic_json(play/'prop_state.json',{'next_operation':6,'input_pending':False})
            image=play/'now.png';self.render(plan,cards,trace,7,image)
            player.inspect_resume(root,1,image)
            self.assertEqual(bridge.read(play/'prop_state.json')['next_operation'],7)
            self.assertEqual(len((play/'prop_actions.jsonl').read_text().splitlines()),1)
            player.inspect_resume(root,1,image)
            self.assertEqual(len((play/'prop_actions.jsonl').read_text().splitlines()),1)

    def test_explicit_prop_entry_prepares_then_launches_dedicated_worker(self):
        from concurrent.futures import Future
        with tempfile.TemporaryDirectory() as d,patch.object(workflow,'OUT',Path(d)):
            _,play,plan,cards,trace=self.make(d);controller=workflow.Controller()
            self.addCleanup(controller.pool.shutdown,wait=True)
            controller.prop_result={'solution_path':plan['prop_solution_path']};controller.settings['rolling']=False
            with patch.object(controller,'submit') as submit:
                controller.command('start_prop');self.assertEqual(submit.call_args.args[0],'prepare')
            controller.job_kind='prepare';controller.job_epoch=controller.epoch;controller.job=Future()
            controller.job.set_result({'prop_execution':True,'plan_path':str(play/'plan.json'),'preview_path':str(play/'reference.png'),'message':'ready'})
            controller.collect_job();self.assertIsNone(controller.prop_result)
            with patch.object(workflow.subprocess,'Popen') as spawn:
                controller.start_player();self.assertIn('prop_playback.py',spawn.call_args.args[0][1])
            if controller.player_log:controller.player_log.close()
            controller.player=None


if __name__=='__main__':unittest.main()
