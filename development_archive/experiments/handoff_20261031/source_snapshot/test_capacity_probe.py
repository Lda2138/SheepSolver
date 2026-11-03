import json
from pathlib import Path
import tempfile
import unittest

import capacity_probe

ROOT=Path(__file__).resolve().parent
KNOWN=ROOT/'output/world_handoff_20261029_aec907/known_solvable_1'


@unittest.skipUnless((KNOWN/'map.json').exists(),'known solvable world map missing')
class CapacityProbeTests(unittest.TestCase):
    def setUp(self):
        self.data=json.loads((KNOWN/'map.json').read_text(encoding='utf-8-sig'))
        self.route=json.loads((KNOWN/'solution.json').read_text(encoding='utf-8-sig'))['sequence']

    def test_relaxed_replay_checks_capacity_cover_and_permutation(self):
        self.assertTrue(capacity_probe.relaxed_clear(self.data,self.route,6))
        self.assertFalse(capacity_probe.relaxed_clear(self.data,self.route,5))
        self.assertFalse(capacity_probe.relaxed_clear(self.data,self.route[::-1],8))
        self.assertFalse(capacity_probe.relaxed_clear(self.data,self.route[:-1],8))

    def test_probe_reports_seven_slots_and_is_never_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);prefix=folder/'prefix.json'
            prefix.write_text(json.dumps({'sequence':[]}),encoding='utf-8')
            result=capacity_probe.finish(capacity_probe.start(KNOWN/'map.json',prefix,folder,10),self.data)
        self.assertEqual(result['smallest_solved_capacity'],7)
        self.assertFalse(result['executable'])
        self.assertIn('可解性仍未知',capacity_probe.describe(result))


if __name__=='__main__':
    unittest.main()
