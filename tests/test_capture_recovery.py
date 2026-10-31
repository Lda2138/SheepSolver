import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from auto_capture import AutoCapture,write_json
from test_auto_capture import map_bytes,start,HASH,SEED
from map_protocol import decode_map


class CaptureRecoveryTests(unittest.TestCase):
    def fixture(self,directory):
        out=Path(directory);addon=AutoCapture(out,False,False,False)
        self.addCleanup(addon.pool.shutdown,wait=True)
        (out/'maps').mkdir();(out/'maps'/(HASH+'.map')).write_bytes(map_bytes())
        addon.maps[HASH]=decode_map(map_bytes())
        addon.accept_start(start(False),'/sheep/v1/game/world/game_start')
        folder=out/'sessions'/addon.current['id']
        write_json(folder/'seed.json',{'map_seed':SEED,'map_seed_2':'saved-seed'})
        request={'capture_pid':os.getpid(),'kind':'verified_previous_capture','session':folder.name,
                 'expires_at':time.time()+60,'source_path':'/sheep/v1/game/world/game_start',
                 'map_sha256':[hashlib.sha256((folder/'map_0.json').read_bytes()).hexdigest()]}
        return out,folder,request

    def test_verified_recovery_preserves_exact_map_identity_then_new_game_wins(self):
        with tempfile.TemporaryDirectory() as directory,patch('capture_runtime.handoff',None):
            out,folder,request=self.fixture(directory)
            original=(folder/'map_0.json').read_bytes()
            write_json(out/'resume_capture.json',request)
            addon=AutoCapture(out,False,False,False);self.addCleanup(addon.pool.shutdown,wait=True)
            self.assertEqual(addon.current['id'],request['session']);self.assertTrue(addon.state['complete'])
            self.assertEqual(addon.current['seed'],SEED);self.assertEqual((folder/'map_0.json').read_bytes(),original)
            addon.accept_start(start(False),'/sheep/v1/game/map_info_ex')
            self.assertNotEqual(addon.current['id'],request['session'])

    def test_bad_identity_expired_request_or_wrong_seed_never_becomes_current(self):
        for problem in ('identity','expired','seed','missing_map'):
            with self.subTest(problem=problem),tempfile.TemporaryDirectory() as directory,patch('capture_runtime.handoff',None):
                out,folder,request=self.fixture(directory)
                if problem=='identity':request['map_sha256']=['wrong']
                elif problem=='expired':request['expires_at']=0
                elif problem=='missing_map':request['map_sha256']=[]
                else:write_json(folder/'seed.json',{'map_seed':[1,2,3,4],'map_seed_2':'wrong'})
                write_json(out/'resume_capture.json',request)
                addon=AutoCapture(out,False,False,False);self.addCleanup(addon.pool.shutdown,wait=True)
                self.assertIsNone(addon.current);self.assertFalse(addon.state['complete'])
                self.assertTrue(addon.state.get('recovery_error'))


if __name__=='__main__':unittest.main()
