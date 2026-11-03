"""A solved board is executable work, never permission to skip to the next."""
from concurrent.futures import Future
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

import workflow_controller as workflow
from solve_original_batch import export


class ExecutionPriorityTests(unittest.TestCase):
    def test_ui_restart_preserves_matching_unknown_result_without_restarting_work(self):
        with tempfile.TemporaryDirectory() as directory:
            first,folder=self.make(directory,'static_constraint_v1');first.pool.shutdown(wait=True)
            output=folder/'level_1_static';output.mkdir()
            digest=hashlib.sha256((folder/'map_1.json').read_bytes()).hexdigest()
            (output/'search_status.json').write_text(json.dumps({'engine':'static_constraint_v1','state':'limited','map_sha256':digest,'message':'unknown'}))
            (workflow.OUT/'status.json').write_text(json.dumps({'session':'session','level':1,'state':'search_limited','blocked_reason':'unknown'}))
            controller=workflow.Controller();self.addCleanup(controller.pool.shutdown,wait=True)
            controller.settings['solver_engine']='static_constraint_v1'
            with patch.object(controller,'submit') as submit,patch.object(controller,'start_player') as player:
                controller.tick();controller.tick()
                submit.assert_not_called();player.assert_not_called()
            self.assertEqual(controller.state,'search_limited');self.assertEqual(controller.level,1)
            self.assertFalse(controller.auto)

    def make(self,directory,engine):
        root=Path(directory);capture=root/'capture';folder=capture/'sessions/session';folder.mkdir(parents=True)
        output=root/'workflow'
        for level in range(2):
            data={'levelData':{'1':[{'id':f'1-{x}-0','rolNum':x,'rowNum':0,'type':1}
                                    for x in (0,8,16)]}}
            (folder/f'map_{level}.json').write_text(json.dumps(data),encoding='utf-8')
        status={'session':'session','complete':True,'map_count':2,'maps_received':2,'seed_received':True,
                'source_path':'/sheep/v1/game/map_info_ex'}
        (capture/'status.json').write_text(json.dumps(status));(capture/'service_lease.json').write_text('{"running":true,"pid":1}')
        self.enterContext(patch.object(workflow,'OUT',output));self.enterContext(patch.object(workflow,'CAPTURE',capture))
        self.enterContext(patch.object(workflow,'alive',return_value=True))
        controller=workflow.Controller();self.addCleanup(controller.pool.shutdown,wait=True)
        controller.session='session';controller.folder=folder;controller.count=2;controller.auto=True
        controller.settings.update(solver_engine=engine,rolling=False)
        return controller,folder

    def cache(self,controller,folder,level=0):
        mp=folder/f'map_{level}.json'
        if controller.settings['solver_engine'] in ('upstream_normal_snapshot_v1','static_constraint_v1'):
            _,output=controller.fixed_planner(folder,level,workflow.read(mp));output=output/'complete'
        else:output=folder/f'level_{level}_solution'
        return export(mp,{'sequence':['1-0-0','1-8-0','1-16-0']},output)

    def test_every_engine_inspects_current_board_before_any_planning(self):
        for engine in ('static_constraint_v1','upstream_normal_snapshot_v1','legacy'):
            with self.subTest(engine=engine),tempfile.TemporaryDirectory() as directory:
                controller,folder=self.make(directory,engine);self.cache(controller,folder)
                with patch.object(controller,'submit') as submit:
                    controller.tick();self.assertEqual(submit.call_args.args[0],'inspect')
                self.assertNotIn(0,controller.finished)
                self.assertIsNone(controller.level)
                controller.pool.shutdown(wait=True)

    def test_partial_pending_or_missing_first_map_never_skips_ahead(self):
        for state in ('actions','pending','missing'):
            with self.subTest(state=state),tempfile.TemporaryDirectory() as directory:
                controller,folder=self.make(directory,'static_constraint_v1')
                play=folder/'level_0_play';play.mkdir()
                if state=='actions':(play/'actions.jsonl').write_text('confirmed action')
                elif state=='pending':(play/'playback_status.json').write_text('{"input_pending":true}')
                else:(folder/'map_0.json').unlink()
                with patch.object(controller,'submit') as submit:
                    controller.tick();self.assertEqual(submit.call_args.args[0],'inspect')
                controller.pool.shutdown(wait=True)

    def test_historical_completion_cannot_choose_the_next_board(self):
        with tempfile.TemporaryDirectory() as directory:
            controller,folder=self.make(directory,'static_constraint_v1');self.cache(controller,folder)
            controller.finished={0}
            with patch.object(controller,'submit') as submit:
                controller.tick()
                self.assertEqual(submit.call_args.args[0],'inspect')
                self.assertIsNone(controller.level)

    def test_tick_plays_cached_first_board_before_starting_second_search(self):
        with tempfile.TemporaryDirectory() as directory:
            controller,folder=self.make(directory,'static_constraint_v1');self.cache(controller,folder)
            events=[]
            def inspect(*args):events.append('inspect_first');return {'level':0,'resume':False}
            def prepare(context):
                self.assertEqual(context['level'],0);events.append('prepare_first')
                return {'preview_path':'cached','message':'ready','start_step':0}
            def start():
                events.append('play_first');controller.ready=None
                controller.player=Mock();controller.player.poll.return_value=None
                controller.player_log=Mock();controller.player_epoch=controller.epoch
                controller.player_level=0;controller.play_folder=folder/'level_0_play'
                controller.play_folder.mkdir(exist_ok=True)
            with patch.object(controller,'inspect',side_effect=inspect),patch.object(controller,'plan_original',side_effect=prepare),patch.object(controller,'start_player',side_effect=start):
                # Only the actual screenshot may choose the first board.
                controller.tick();self.assertEqual(controller.job_kind,'inspect')
                controller.job.result(timeout=2);controller.tick()
                controller.job.result(timeout=2);controller.tick()
                self.assertEqual(events,['inspect_first','prepare_first','play_first'])
                self.assertEqual(controller.player_level,0);self.assertFalse(controller.finished)
                controller.tick();self.assertIsNone(controller.job)
                # Completion is reported by the player, not inferred from cache.
                (controller.play_folder/'playback_status.json').write_text('{"state":"sequence_finished","next_step":3,"total":3}')
                controller.player.poll.return_value=0;controller.player.returncode=0
                controller.collect_player();self.assertIn(0,controller.finished)
                controller.next_inspect=0
                with patch.object(controller,'submit') as submit:
                    controller.tick()
                    self.assertEqual(submit.call_args.args[0],'inspect')

    def test_next_level_does_not_display_previous_level_playback(self):
        with tempfile.TemporaryDirectory() as directory:
            controller,folder=self.make(directory,'static_constraint_v1')
            old=folder/'level_0_play';old.mkdir()
            (old/'playback_status.json').write_text('{"state":"sequence_finished","next_step":18,"total":18}')
            controller.play_folder=old;controller.finished={0}
            controller.job_kind='inspect';controller.job=Future();controller.job.set_result({'level':1,'resume':False})
            with patch.object(controller,'submit') as submit:
                controller.collect_job();self.assertEqual(submit.call_args.args[0],'original')
            self.assertEqual(controller.level,1);self.assertEqual(controller.play_folder.name,'level_1_play')
            controller.snapshot(workflow.read(folder.parent.parent/'status.json'),True)
            self.assertEqual(workflow.read(workflow.OUT/'status.json')['playback'],{})


if __name__=='__main__':unittest.main()
