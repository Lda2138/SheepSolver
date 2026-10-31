import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from standard_plan import solve_continuation,check_answer,audit_static_boundary
from rolling_play import replay_prefix
from test_rolling_capacity import flat,six_needed

ROOT=Path(__file__).resolve().parent.parent
DAILY=ROOT/'output/auto_capture/sessions/20261010T160409Z-ee084a'


class StandardPlanTests(unittest.TestCase):
    def test_complete_route_and_cache_extend_prefix_without_research(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);data=flat([1]*3+[2]*3+[3]*3);mp=folder/'map.json'
            mp.write_text(json.dumps(data),encoding='utf-8');out=folder/'full'
            path,answer=solve_continuation(mp,[],out,seconds=3)
            self.assertTrue(answer['validated']);self.assertEqual(answer['planning_capacity'],5)
            self.assertEqual(len(answer['sequence']),9)
            prefix=answer['sequence'][:4]
            with patch('standard_plan.subprocess.Popen',side_effect=AssertionError('must reuse')):
                path2,answer2=solve_continuation(mp,prefix,out,seconds=3)
            self.assertEqual(path,path2);self.assertEqual(answer,answer2)
            self.assertTrue(json.loads((out/'search_status.json').read_text(encoding='utf-8'))['cache_reused'])
            bad=copy.deepcopy(answer);bad['sequence']=bad['sequence'][:-1]
            with self.assertRaises(ValueError):check_answer(bad,data,hashlib.sha256(mp.read_bytes()).hexdigest(),prefix)

    def test_five_to_six_searches_full_continuation(self):
        data,prefix=six_needed()
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);mp=folder/'map.json';mp.write_text(json.dumps(data),encoding='utf-8')
            _,answer=solve_continuation(mp,prefix,folder/'full',seconds=3)
            self.assertEqual(answer['planning_capacity'],6);self.assertEqual(answer['sequence'][:len(prefix)],prefix)
            board=replay_prefix(data,answer['sequence']);self.assertFalse(board.state.remaining);self.assertEqual(board.tray,[])
            status=json.loads((folder/'full/search_status.json').read_text(encoding='utf-8'))
            self.assertEqual([a['capacity'] for a in status['capacity_attempts']],[5,6])

    @unittest.skipUnless(DAILY.exists(),'Local daily failure map absent')
    def test_failed_daily_map_has_a_complete_verified_five_slot_answer(self):
        with tempfile.TemporaryDirectory() as d:
            _,answer=solve_continuation(DAILY/'map_1.json',[],Path(d),seconds=5)
            self.assertTrue(answer['validated']);self.assertEqual(len(answer['sequence']),231)
            self.assertLessEqual(answer['peak_tray_after_elimination'],5)
            # Verify the actual playback gate with real board coordinates,
            # source-map hash and complete original-map replay. No input.
            from subprocess import run,CREATE_NO_WINDOW
            plan=json.loads((DAILY/'level_1_play/plan.json').read_text(encoding='utf-8-sig'))
            plan.update(solution_path=str(Path(d)/'solution.json'),start_step=0)
            for key in ('allow_rolling','execution_kind','capture_status_path','service_lease_path'):plan.pop(key,None)
            path=Path(d)/'plan.json';path.write_text(json.dumps(plan),encoding='utf-8')
            p=run([str(ROOT/'SolverPlayback.exe'),str(path),'--validate-only'],capture_output=True,
                creationflags=CREATE_NO_WINDOW,timeout=8)
            self.assertEqual(p.returncode,0,p.stderr.decode('utf-8',errors='replace'))

    @unittest.skipUnless(DAILY.exists(),'Local daily failure prefix absent')
    def test_failed_daily_prefix_is_independently_proved_dead(self):
        data=json.loads((DAILY/'map_1.json').read_text(encoding='utf-8-sig'))
        actions=[json.loads(s) for s in (DAILY/'level_1_play/actions.jsonl').read_text(encoding='utf-8-sig').splitlines() if s.strip()]
        prefix=[a['card_id'] for a in actions[:115]]
        audit=audit_static_boundary(data,prefix,seconds=5)
        self.assertEqual(audit['state'],'dead_prefix')
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)
            with self.assertRaisesRegex(ValueError,'死局'):solve_continuation(DAILY/'map_1.json',prefix,out,seconds=5)
            status=json.loads((out/'search_status.json').read_text(encoding='utf-8'));self.assertEqual(status['state'],'no_continuation')
            self.assertEqual(status['workers'],0);self.assertFalse((out/'solution.json').exists())

    def test_cancellation_does_not_publish_complete_route(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);mp=folder/'map.json';mp.write_text(json.dumps(flat([1]*3)),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'停止'):solve_continuation(mp,[],folder/'full',cancel=lambda:True)
            self.assertFalse((folder/'full/solution.json').exists())
            self.assertEqual(json.loads((folder/'full/search_status.json').read_text(encoding='utf-8'))['state'],'cancelled')
