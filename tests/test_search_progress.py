"""Search completion, real progress, cancellation and resource boundaries."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from solve_verified_map import policies, atomic_json
from search_resources import worker_target, state_cap, GIB

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT/'output/auto_capture/sessions/20261008T141544Z-a50904'


class SearchTests(unittest.TestCase):
    def test_cpu_memory_and_small_map_bound_workers(self):
        self.assertEqual(len(policies('max', 'level-top', .8, 255, 16, 16*1024**3)), 14)
        self.assertEqual(len(policies('fast', 'level-top', .8, 255, 16, 16*1024**3)), 6)
        self.assertEqual(len(policies('max', 'level-top', .8, 255, 1, 16*1024**3)), 1)
        self.assertEqual(len(policies('max', 'level-top', .8, 255, 16, 4*1024**3)), 2)
        self.assertEqual(len(policies('max', 'level-top', .8, 18, 16, 16*1024**3)), 1)

    def test_idle_cpu_and_memory_budget_are_shared(self):
        self.assertEqual(worker_target('max',258,22,40,8*GIB,True),11)
        self.assertEqual(worker_target('max',258,22,90,8*GIB,True),1)
        self.assertEqual(worker_target('max',258,22,0,512*1024**2,True),1)
        self.assertEqual(worker_target('fast',258,22,0,8*GIB,True),6)
        self.assertEqual(worker_target('eco',258,22,0,8*GIB,True),1)
        for color in (True,False):
            for workers in (1,6,20):
                budget=8*GIB
                caps=[state_cap(budget,workers,i==0,color) for i in range(workers)]
                estimated=sum(c*(256 if color else 160)+48*1024**2 for c in caps)
                self.assertLessEqual(estimated,budget*.75)

    def start(self, output, level, seconds):
        return subprocess.Popen([sys.executable, str(ROOT/'solve_verified_map.py'), '--map',
            str(FIXTURE/f'map_{level}.json'), '--output', str(output), '--time-limit', str(seconds),
            '--performance', 'max'], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW)

    @unittest.skipUnless(FIXTURE.exists(), 'real map fixture absent')
    def test_complete_answer_and_final_status_are_verified(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder); p = self.start(output, 0, 10)
            stdout, stderr = p.communicate(timeout=20)
            self.assertEqual(p.returncode, 0, stderr.decode())
            status = json.loads((output/'search_status.json').read_text(encoding='utf-8'))
            solution = json.loads((output/'solution.json').read_text(encoding='utf-8'))
            self.assertEqual(status['state'], 'solved')
            self.assertTrue(status['validated'] and solution['validated'])
            self.assertEqual(status['deepest'], solution['card_count'])
            self.assertEqual(status['map_sha256'], solution['map_sha256'])

    @unittest.skipUnless(FIXTURE.exists(), 'real map fixture absent')
    def test_live_progress_cancel_and_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder); p = self.start(output, 1, 30)
            try:
                deadline = time.monotonic()+12; states = []
                while time.monotonic() < deadline:
                    path = output/'search_status.json'
                    if path.exists():
                        status = json.loads(path.read_text(encoding='utf-8'))
                        if status['nodes'] > 0: states.append(status)
                        if len(states)>1 and states[-1]['nodes']>states[0]['nodes']: break
                    time.sleep(.35)
                self.assertGreater(len(states), 1)
                self.assertGreater(states[-1]['nodes_per_second'], 0)
                self.assertEqual(states[-1]['state'], 'searching')
                atomic_json(output/'search_control.json', {'action':'stop'})
                stdout, stderr=p.communicate(timeout=10)
                self.assertEqual(p.returncode, 2, stderr.decode(errors='replace'))
                terminal=json.loads((output/'search_status.json').read_text(encoding='utf-8'))
                self.assertEqual(terminal['state'], 'cancelled')
                self.assertEqual(terminal['workers'],0)
                self.assertEqual(terminal['resources']['solver_memory_mb'],0)
                self.assertFalse((output/'solution.json').exists())
                # The wrapper waits for every native worker before returning.
                result = subprocess.run(['powershell', '-NoProfile', '-Command',
                    "Get-CimInstance Win32_Process -Filter \"Name = 'FastOriginalSolver.exe'\" | Select-Object -ExpandProperty CommandLine"],
                    capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
                self.assertNotIn(str(output), result.stdout)
            finally:
                if p.poll() is None: p.terminate(); p.communicate(timeout=10)

    @unittest.skipUnless(FIXTURE.exists(), 'real map fixture absent')
    def test_live_performance_change_retires_auxiliary_workers(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder);p=self.start(output,1,30)
            try:
                deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    path=output/'search_status.json'
                    if path.exists():
                        status=json.loads(path.read_text(encoding='utf-8'))
                        if status.get('nodes',0)>0:break
                    time.sleep(.2)
                atomic_json(output/'search_control.json',{'action':'settings','performance':'eco'})
                deadline=time.monotonic()+8
                while time.monotonic()<deadline:
                    status=json.loads((output/'search_status.json').read_text(encoding='utf-8'))
                    if status['performance']=='eco' and status['workers']==1 and status['target_workers']==1:break
                    time.sleep(.2)
                self.assertEqual(status['performance'],'eco')
                self.assertEqual(status['workers'],1)
                self.assertEqual(status['target_workers'],1)
                self.assertIn('solver_memory_mb',status['resources'])
                atomic_json(output/'search_control.json',{'action':'stop'})
                p.communicate(timeout=10);self.assertEqual(p.returncode,2)
            finally:
                if p.poll() is None:
                    atomic_json(output/'search_control.json',{'action':'stop'})
                    p.communicate(timeout=10)

    @unittest.skipUnless(FIXTURE.exists(), 'real map fixture absent')
    def test_limit_does_not_report_complete_or_export_partial(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder); p=self.start(output, 1, 1)
            p.communicate(timeout=15)
            self.assertEqual(p.returncode, 2)
            status=json.loads((output/'search_status.json').read_text(encoding='utf-8'))
            self.assertEqual(status['state'], 'limited')
            self.assertNotIn('validated', status)
            self.assertFalse((output/'solution.json').exists())


if __name__ == '__main__': unittest.main()
