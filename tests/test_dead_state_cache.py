import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import struct
import unittest
from unittest.mock import patch

from dead_state_cache import DeadStateCache,legacy_hash

ROOT=Path(__file__).resolve().parent.parent
EXE=ROOT/'FastOriginalSolver.exe'


class DeadCacheTests(unittest.TestCase):
    def test_reader_lock_defers_optional_write_and_keeps_the_previous_cache(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'dead.bin';p=DeadStateCache(path,'a'*64)
            p.records.add(bytes(80));p.dirty=True;p.save(True);before=path.read_bytes()
            p.records.add(bytes([1])*80);p.dirty=True
            with patch('dead_state_cache.os.replace',side_effect=PermissionError('reader lock')),patch('dead_state_cache.time.sleep'):
                self.assertFalse(p.save(True))
            self.assertTrue(p.dirty);self.assertTrue(p.write_deferred);self.assertEqual(path.read_bytes(),before)
            self.assertTrue(p.save(True));self.assertFalse(p.dirty);self.assertEqual(len(DeadStateCache(path,'a'*64).records),2)

    def test_exact_records_and_map_binding(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'dead.bin';p=DeadStateCache(path,'a'*64)
            record=bytes(range(80));path.write_bytes(p.header+record)
            p.merge(path);p.save(True);self.assertIn(record,DeadStateCache(path,'a'*64).records)
            self.assertFalse(DeadStateCache(path,'b'*64).records)
            path.write_bytes(p.header+record[:-1]);self.assertFalse(DeadStateCache(path,'a'*64).records)

    def native(self,folder,mp,prefix,cache,allowed=None,budget=1000000):
        pp=folder/'prefix.json';pp.write_text(json.dumps({'sequence':prefix,'allowed':allowed,'dead_cache':str(cache)}))
        progress=folder/'progress.json';output=folder/'answer.json'
        p=subprocess.run([str(EXE),str(mp),str(output),'color-top','0','10','1997',str(budget),str(progress),str(pp)],
                         capture_output=True,timeout=15,creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(p.returncode,2,p.stderr)
        return json.loads(progress.read_text()),Path(str(progress)+'.dead.bin')

    @unittest.skipUnless(EXE.exists(),'native cache experiment absent')
    def test_only_unrestricted_exhaustion_proves_the_parent(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);cards=[{'id':str(i),'type':i+1,'rolNum':i*8,'rowNum':0} for i in range(6)]
            mp=folder/'map.json';mp.write_text(json.dumps({'levelData':{'1':cards}}))
            cache=DeadStateCache(folder/'dead.bin',hashlib.sha256(mp.read_bytes()).hexdigest())
            status,proof=self.native(folder,mp,[],cache.path,budget=2)
            self.assertEqual(status['state'],'limited');self.assertEqual(proof.stat().st_size,72)
            # A restricted sibling batch refutes its children, never its parent.
            status,proof=self.native(folder,mp,['0'],cache.path,allowed=['1','2'])
            cache.merge(proof);cache.save(True)
            status,proof=self.native(folder,mp,['0'],cache.snapshot or cache.path)
            self.assertGreater(status['nodes'],0)
            cache.merge(proof);cache.save(True)
            status,_=self.native(folder,mp,['0'],cache.snapshot or cache.path)
            self.assertEqual(status['nodes'],0);self.assertEqual(status['dead_cache_hits'],1)

    @unittest.skipUnless(EXE.exists(),'native cache experiment absent')
    def test_seeded_colored_proof_and_matching_hash_still_compare_every_field(self):
        from test_tight_search import sample_map
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);mp=folder/'map.json';mp.write_text(json.dumps(sample_map(0)))
            cache=DeadStateCache(folder/'dead.bin',hashlib.sha256(mp.read_bytes()).hexdigest())
            first,proof=self.native(folder,mp,[],cache.path);self.assertEqual(first['state'],'exhausted')
            cache.merge(proof);cache.save(True)
            second,_=self.native(folder,mp,[],cache.snapshot);self.assertEqual(second['nodes'],0)
            # Preserve the stored hash and bucket, but alter a complete-state
            # field. This simulates a hash collision rather than a valid proof.
            parent=next(r for r in cache.records if r[:40]==bytes(40))
            wrong=bytearray(parent);wrong[8]=1
            cache.records.remove(parent);cache.records.add(bytes(wrong));cache.dirty=True;cache.save(True)
            third,_=self.native(folder,mp,[],cache.snapshot)
            self.assertGreater(third['nodes'],0)

    @unittest.skipUnless(EXE.exists(),'native cache experiment absent')
    def test_real_refuted_tail_is_reused_without_expansion(self):
        mp=ROOT/'output/auto_capture/sessions/20261009T023904Z-2f3084/map_1.json'
        pp=ROOT/'output/workflow_test_fixtures/tail_prefix_219.json'
        if not mp.exists() or not pp.exists():self.skipTest('real map absent')
        prefix=json.loads(pp.read_text())['sequence']
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);cache=DeadStateCache(folder/'dead.bin',hashlib.sha256(mp.read_bytes()).hexdigest())
            first,proof=self.native(folder,mp,prefix,cache.path);cache.merge(proof);cache.save(True)
            data=proof.read_bytes();legacy=folder/'old.bin'
            records=[data[i:i+80] for i in range(72,len(data),80)]
            for record in records:self.assertEqual(legacy_hash(record[:76]),struct.unpack_from('<I',record,76)[0])
            legacy.write_bytes(b'SSDEAD01'+cache.header[8:]+b''.join(r[:76] for r in records))
            migrated=DeadStateCache(legacy,cache.header[8:].decode());migrated.save(True)
            migrated_status,_=self.native(folder,mp,prefix,migrated.snapshot)
            self.assertEqual(migrated_status['dead_cache_hits'],1);self.assertEqual(migrated_status['nodes'],0)
            second,_=self.native(folder,mp,prefix,cache.snapshot or cache.path)
            self.assertGreater(first['nodes'],0);self.assertEqual(first['state'],'exhausted')
            self.assertEqual(second['nodes'],0);self.assertEqual(second['dead_cache_hits'],1)


if __name__=='__main__':unittest.main()
