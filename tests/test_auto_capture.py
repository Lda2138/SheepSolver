"""Protocol fixtures and session isolation tests; no live game/proxy changes."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
from cryptography.hazmat.decrepit.ciphers.modes import OFB
from auto_capture import AutoCapture,write_json
from map_protocol import decode_map, decode_seed, materialize, decode_match_maps, decompress_match_data
from core.map.StaticDataGenerator import StaticDataGenerator

SEED = [589667220, 4047304004, 62666966, 3333988324]
HASH = 'a' * 32


def var(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    return bytes(result + bytes([value]))


def integer(n, value):
    return var(n << 3) + var(value)


def blob(n, value):
    if isinstance(value, str):
        value = value.encode()
    return var(n << 3 | 2) + var(len(value)) + value


def structure():
    return {'widthNum': 8, 'heightNum': 10, 'levelKey': 1,
            'blockTypeData': {'2': 1, '1': 1},
            'levelData': {'1': [{'id': f'1-{i*8}-0', 'type': 0, 'rolNum': i*8,
                                 'rowNum': 0, 'layerNum': 1, 'moldType': 0,
                                 'metaType': 0, 'metaData': 0} for i in range(6)]}}


def map_bytes():
    data = integer(1, 8) + integer(2, 10) + integer(3, 1)
    for key, count in structure()['blockTypeData'].items():
        data += blob(4, blob(1, key) + integer(2, count))
    cards = b''
    for c in structure()['levelData']['1']:
        node = blob(1, c['id']) + integer(3, c['rolNum']) + integer(4, 0) + integer(5, 1)
        cards += blob(1, node)
    return b'fixture header only!!'[:21].ljust(21, b'!') + data + blob(5, blob(1, '1') + blob(2, cards))


def ack(packed=True, seed2='updated-seed-id'):
    data = integer(1, 1)
    data += blob(2, b''.join(var(x) for x in SEED)) if packed else b''.join(integer(2, x) for x in SEED)
    return data + blob(3, seed2)


def start(need=True):
    return {'map_md5': [HASH], 'map_seed': [0]*4 if need else SEED,
            'need_seed': need, 'map_seed_2': 'initial-seed-id',
            'token': 'DO_NOT_RETAIN', 'account': 'DO_NOT_RETAIN'}


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.out = Path(self.temp.name)
        self.capture = AutoCapture(self.out, auto_download=False, auto_solve=False, crypto_probe=False)

    def tearDown(self):
        self.capture.done()
        self.temp.cleanup()

    def test_binary_map_and_original_shuffle(self):
        decoded = decode_map(map_bytes())
        self.assertEqual(decoded, structure())
        expected = copy.deepcopy(decoded)
        original = StaticDataGenerator.__new__(StaticDataGenerator)
        original.fill_seed_data_into_cache(expected, SEED)
        result = materialize(decoded, SEED)
        self.assertEqual(result['levelData'], expected['levelData'])
        self.assertTrue(all(c['type'] > 0 for c in result['levelData']['1']))
        for packed in (False, True):
            self.assertEqual(decode_seed(ack(packed))['map_seed'], SEED)
        with self.assertRaises(ValueError):
            materialize(decoded, [0]*4)
        with self.assertRaises(ValueError):
            decode_map(map_bytes()[:-1])

    def test_waits_for_seed_and_does_not_retain_auth(self):
        self.capture.accept_map(HASH, map_bytes())
        self.capture.accept_start(start())
        self.assertFalse(self.capture.state['complete'])
        self.assertFalse((self.out / 'current_game_map.json').exists())
        self.capture.accept_seed(ack(), session=self.capture.current['id'])
        self.assertTrue(self.capture.state['complete'])
        for file in self.out.rglob('*.json'):
            self.assertNotIn('DO_NOT_RETAIN', file.read_text(encoding='utf-8'))

    def test_finalizing_same_map_preserves_answer_identity(self):
        self.capture.accept_start(start(False));self.capture.accept_map(HASH,map_bytes())
        path=self.out/'sessions'/self.capture.current['id']/'map_0.json'
        before=path.read_bytes();self.capture.finalize();self.assertEqual(path.read_bytes(),before)
        self.assertTrue(self.capture.state['complete'])

    def test_snapshot_retries_transient_windows_reader_lock(self):
        path=self.out/'atomic.json';real=Path.replace;calls=[]
        def replace(source,destination):
            calls.append(1)
            if len(calls)<3:raise PermissionError('temporary reader handle')
            return real(source,destination)
        with patch.object(Path,'replace',replace):write_json(path,{'complete':True})
        self.assertEqual(len(calls),3);self.assertTrue(json.loads(path.read_text())['complete'])

    def test_encrypted_ack_missing_wrong_then_correct_key(self):
        self.capture.accept_start(start())
        session = self.capture.current['id']
        self.capture.accept_map(HASH, map_bytes())
        key, iv = b'1234567890abcdef', b'fedcba0987654321'
        enc = Cipher(algorithms.AES(key), OFB(iv)).encryptor()
        cipher = enc.update(ack()) + enc.finalize()
        self.capture.accept_seed(cipher, True, session)
        self.assertEqual(self.capture.state['phase'], 'waiting_for_seed_decryption')
        self.capture.try_decrypt([(b'0'*16, b'0'*16)], session)
        self.assertFalse(self.capture.state['complete'])
        self.capture.try_decrypt([(key, iv)], session)
        self.assertTrue(self.capture.state['complete'])
        self.assertEqual(self.capture.current['seed2'], 'updated-seed-id')
        for file in self.out.rglob('*'):
            if file.is_file():
                self.assertNotIn(key, file.read_bytes())

    def test_new_session_invalidates_alias_and_late_seed(self):
        self.capture.accept_start(start(False))
        self.capture.accept_map(HASH, map_bytes())
        old = self.capture.current['id']
        self.assertTrue((self.out / 'current_game_map.json').exists())
        self.capture.accept_start(start())
        self.assertFalse((self.out / 'current_game_map.json').exists())
        self.capture.accept_seed(ack(), session=old)
        self.assertFalse(self.capture.state['complete'])
        self.assertIsNone(self.capture.current['seed'])
        self.capture.try_decrypt([], old)

    def test_multiple_maps_arrive_in_reverse_order(self):
        data = start(False)
        data['map_md5'].append('b'*32)
        self.capture.accept_start(data)
        self.capture.accept_map('b'*32, map_bytes())
        self.assertFalse(self.capture.state['complete'])
        self.capture.accept_map(HASH, map_bytes())
        self.assertTrue(self.capture.state['complete'])

    def test_out_of_order_start_response(self):
        def flow():
            return SimpleNamespace(metadata={}, request=SimpleNamespace(
                pretty_url='https://cat-match.easygame2021.com/sheep/v1/game/map_info_ex'),
                response=SimpleNamespace(status_code=200, content=json.dumps({'data': start()}).encode()))
        a, b = flow(), flow()
        self.capture.request(a)
        self.capture.request(b)
        self.capture.response(b)
        session = self.capture.current['id']
        self.capture.response(a)
        self.assertEqual(session, self.capture.current['id'])

    def test_modern_match_data_compression(self):
        # Independent LZ-string implementation produces the installed UTF16 format.
        from lzstring import LZString
        runtime = [{'type': 1, 'colNum': i*8, 'rowNum': 0, 'layerNum': 1,
                    'AreaType': 0, 'moldType': 0, 'metaType': 0, 'metaData': 0} for i in range(3)]
        plain = json.dumps([{'gameState': {'allBlockRuntimeData': runtime,
                                         'crushAreaBlocks': [], 'moveOutAreaBlocks': []}}])
        compressed = LZString().compressToUTF16(plain)
        self.assertEqual(decompress_match_data(compressed), plain)
        self.assertEqual(len(decode_match_maps(compressed)[0]['levelData']['1']), 3)
        data = start()
        data['match_data'] = compressed
        self.capture.accept_start(data)
        self.assertTrue(self.capture.state['complete'])
        self.assertFalse(self.capture.state['seed_received'])
        with self.assertRaises(ValueError):
            decompress_match_data(compressed, limit=10)

    def test_original_solver_independent_replay(self):
        result = materialize(decode_map(map_bytes()), SEED)
        file = self.out / 'input.json'
        file.write_text(json.dumps(result), encoding='utf-8')
        process = subprocess.run([sys.executable, 'desktop_assistant.py', '--map', str(file),
                                  '--time-limit', '3', '--output', str(self.out / 'solution')],
                                 capture_output=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        answer = json.loads((self.out / 'solution/solution.json').read_text(encoding='utf-8'))
        self.assertTrue(answer['validated'])
        self.assertEqual(len(answer['sequence']), 6)


if __name__ == '__main__':
    unittest.main(verbosity=2)
