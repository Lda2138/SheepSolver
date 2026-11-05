"""Numeric equivalence and stale-frame protection; never starts a player."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
CSC = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'


@unittest.skipUnless(CSC.exists(), 'Windows C# compiler required')
class PlaybackPerformanceTests(unittest.TestCase):
    def test_optimized_pixels_preserve_legacy_scores_and_frame_age_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            exe = Path(directory) / 'PerformanceCheck.exe'
            command = [str(CSC), '/nologo', '/optimize+', '/target:exe', '/main:PlaybackPerformanceCheck',
                       '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
                       '/reference:System.Web.Extensions.dll', '/out:' + str(exe)]
            command += [str(ROOT / path) for path in (
                'native/clicker/SheepClicker.cs', 'native/clicker/SolverPlayback.cs',
                'native/planner/RollingPlanner.cs', 'native/rules/ColorRules.cs',
                'native/rules/EnergyRules.cs', 'tests/native/PlaybackPerformanceCheck.cs')]
            build = subprocess.run(command, capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(build.returncode, 0, build.stdout.decode(errors='replace') + build.stderr.decode(errors='replace'))
            result = subprocess.run([str(exe)], capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
            report = json.loads(result.stdout.decode('utf-8-sig'))
            self.assertGreaterEqual(report['parity_checks'], 20)
            self.assertTrue(report['no_input'])


if __name__ == '__main__':
    unittest.main()
