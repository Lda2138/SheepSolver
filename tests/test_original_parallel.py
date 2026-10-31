import itertools
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from original_parallel import partitions,search,prioritize_unsearched
from original_snapshot import current_snapshot,ENGINE
from frozen_replan import LiveBoard,check_static_route


def flat(types):
    return {'levelData':{'1':[{'id':f'1-{i*16}-0','type':t,'rolNum':i*16,'rowNum':0,
        'metaType':0,'metaData':0} for i,t in enumerate(types)]}}


class OriginalParallelTests(unittest.TestCase):
    def test_unsearched_queue_is_resumed_only_for_the_same_snapshot(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);old=root/'run_1';current=root/'run_2';old.mkdir();current.mkdir()
            branches=[['a'],['b'],['c']]
            (old/'partition_manifest.json').write_text(json.dumps({'snapshot_sha256':'same','branches':branches}))
            (old/'progress_0000.json').write_text('{"state":"limited"}')
            (old/'progress_0001.json').write_text('{"state":"exhausted"}')
            tasks,info=prioritize_unsearched(current,'same',branches)
            self.assertEqual(tasks,[['c'],['a']]);self.assertEqual(info['exhausted_skipped'],1)
            self.assertEqual(prioritize_unsearched(current,'different',branches),(branches,{}))

    def test_partitions_cover_every_order_once(self):
        snapshot=current_snapshot(LiveBoard(flat([1,1,1])))
        branches=partitions(snapshot,4)
        ids=[c['id'] for c in snapshot['levelData']['1']]
        for route in itertools.permutations(ids):
            self.assertEqual(sum(list(route[:len(p)])==p for p in branches),1)

    def test_real_original_workers_handle_current_tray_and_return_complete_route(self):
        data=flat([1,2,3,1,2,3,1,2,3]);board=LiveBoard(data)
        for c in data['levelData']['1'][:2]:board.play_to_refresh([c['id']])
        snapshot=current_snapshot(board);reports=[]
        with tempfile.TemporaryDirectory() as directory:
            answer=search(snapshot,Path(directory),5,None,lambda *a,**kw:reports.append(kw),max_workers=2)
            tail=check_static_route(snapshot,snapshot['_planning_prefix'],answer['sequence'],True)
            self.assertEqual(len(tail),7);self.assertEqual(answer['engine'],ENGINE)
            self.assertGreaterEqual(answer['parallel_workers'],1)
            self.assertIn('business/SheepSolver.py',answer['upstream_sources'])

    def test_cancel_stops_every_owned_worker(self):
        snapshot=current_snapshot(LiveBoard(flat([1,2,3]*6)))
        children=[];cancelled=[False];original=subprocess.Popen
        def launch(*args,**kwargs):
            child=original(*args,**kwargs);children.append(child);return child
        def report(*args,**kwargs):cancelled[0]=True
        with tempfile.TemporaryDirectory() as directory,patch('original_parallel.subprocess.Popen',side_effect=launch):
            with self.assertRaisesRegex(ValueError,'暂停'):
                search(snapshot,Path(directory),5,lambda:cancelled[0],report,max_workers=2)
            self.assertTrue(children);self.assertTrue(all(child.poll() is not None for child in children))


if __name__=='__main__':unittest.main()
