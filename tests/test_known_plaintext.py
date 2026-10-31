import base64
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
import urllib.parse

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
from cryptography.hazmat.decrepit.ciphers.modes import OFB
from auto_capture import AutoCapture
from game_known_plaintext import js_plaintext, settlement_stream, seed_request_prefix, decode_with_stream, request_body
from test_auto_capture import ack, start, map_bytes, HASH, SEED


def encrypt(data):
    encryptor = Cipher(algorithms.AES(b'0123456789abcdef'), OFB(b'fedcba9876543210')).encryptor()
    return encryptor.update(data) + encryptor.finalize()


class KnownPlainTests(unittest.TestCase):
    def test_real_js_serialization_and_uri_encoding(self):
        value = {'rank_state': 2, 'map_seed_2': 'marker', 'play_info': '中文/ "\n', 'counts': [1, True, None]}
        process = subprocess.run(['node', '-e', 'process.stdout.write(encodeURIComponent(JSON.stringify(JSON.parse(process.argv[1]))))', json.dumps(value)], capture_output=True, check=True)
        self.assertEqual(js_plaintext(value), process.stdout)

    def test_stream_validation_and_seed_decode(self):
        body = {'rank_state': 2, 'map_seed_2': 'marker', 'play_info': 'fixture_data'*10}
        body['encrypt_data'] = base64.b64encode(encrypt(js_plaintext(body))).decode()
        body['encrypt_key_version'] = 123
        version, stream = settlement_stream(body)
        identifier = 'initial-seed-id'
        request = {'info': base64.b64encode(b'\x00\x75\xbf' + encrypt(bytes([10,len(identifier)]) + identifier.encode())).decode(), 'encryptKeyVersion': 123}
        req_version, prefix = seed_request_prefix(request, identifier)
        self.assertEqual(req_version, version)
        self.assertEqual(decode_with_stream(encrypt(ack()), stream, prefix)['map_seed'], SEED)
        with self.assertRaises(ValueError):
            decode_with_stream(encrypt(ack()), stream, b'wrong_prefix')
        with self.assertRaises(ValueError):
            decode_with_stream(encrypt(ack()), stream[:10], prefix)
        body['play_info'] = 'changed'
        with self.assertRaises(ValueError):
            settlement_stream(body)

    def test_capture_next_game_and_pending_response(self):
        with tempfile.TemporaryDirectory() as folder:
            capture = AutoCapture(Path(folder), auto_download=False, auto_solve=False, crypto_probe=False)
            try:
                capture.accept_start(start())
                capture.accept_map(HASH, map_bytes())
                identifier=capture.current['seed2']
                req={'info':base64.b64encode(b'\x00\x75\xbf'+encrypt(bytes([10,len(identifier)])+identifier.encode())).decode(),'encryptKeyVersion':321}
                flow=SimpleNamespace(metadata={},request=SimpleNamespace(pretty_url='https://cat-match.easygame2021.com/sheep/v1/game/map_info_ex_seed',content=json.dumps(req).encode()))
                capture.request(flow)
                capture.accept_seed(encrypt(ack()),True,capture.current['id'])
                self.assertFalse(capture.state['complete'])
                body={'rank_state':2,'map_seed_2':'marker','play_info':'fixture_data'*10}
                body['encrypt_data']=base64.b64encode(encrypt(js_plaintext(body))).decode();body['encrypt_key_version']=321
                flow=SimpleNamespace(metadata={},request=SimpleNamespace(pretty_url='https://cat-match.easygame2021.com/sheep/v1/game/world/game_over',content=json.dumps(body).encode()))
                capture.request(flow)
                self.assertTrue(capture.state['complete'])
                self.assertEqual(capture.state['seed_source'],'validated_settlement_stream')
                for path in Path(folder).rglob('*.json'):
                    self.assertNotIn('encrypt_data', path.read_text(encoding='utf-8'))
            finally:
                capture.done()

    def test_real_form_transport_and_float_countdown(self):
        with tempfile.TemporaryDirectory() as folder:
            capture=AutoCapture(Path(folder),auto_download=False,auto_solve=False,crypto_probe=False)
            try:
                body={'rank_state':2,'rank_time':23.5,'map_seed_2':'marker','version':'500',
                      'play_info':'fixture+/='*20,'removed':30,'rand_times':1,'watch_ad_count':0}
                body['encrypt_data']=base64.b64encode(encrypt(js_plaintext(body))).decode()
                body['encrypt_key_version']=777
                encoded=urllib.parse.urlencode(body).encode()
                self.assertEqual(request_body(encoded,settlement=True),body)
                flow=SimpleNamespace(metadata={},request=SimpleNamespace(pretty_url='https://cat-match.easygame2021.com/sheep/v1/game/world/game_over',content=encoded))
                capture.request(flow)
                self.assertTrue(capture.state['settlement_stream_received'])
                capture.accept_start(start());capture.accept_map(HASH,map_bytes())
                identifier=capture.current['seed2']
                req={'info':base64.b64encode(b'\x00\x75\xbf'+encrypt(bytes([10,len(identifier)])+identifier.encode())).decode(),'encryptKeyVersion':777}
                flow=SimpleNamespace(metadata={},request=SimpleNamespace(pretty_url='https://cat-match.easygame2021.com/sheep/v1/game/map_info_ex_seed',content=urllib.parse.urlencode(req).encode()),response=SimpleNamespace(status_code=200,content=encrypt(ack())))
                capture.request(flow);capture.response(flow)
                self.assertTrue(capture.state['complete'])
                self.assertEqual(capture.current['seed'],SEED)
            finally:
                capture.done()

    def test_alphabetically_sorted_http_form_restores_encryption_order(self):
        body = {'rank_state': 2, 'rank_time': 23.5, 'map_seed_2': 'marker',
                'version': '500', 'play_info': 'fixture+/='*20,
                'removed': 30, 'rand_times': 1, 'watch_ad_count': 0}
        plain = js_plaintext(body)
        cipher = encrypt(plain)
        body.update(encrypt_data=base64.b64encode(cipher).decode(), encrypt_key_version=11)
        encoded = urllib.parse.urlencode(sorted(body.items())).encode()
        parsed = request_body(encoded, settlement=True)
        version, stream = settlement_stream(parsed)
        self.assertEqual(version, '11')
        self.assertEqual(stream, bytes(a ^ b for a, b in zip(plain, cipher)))
        identifier = 'initial-seed-id'
        request = {'info': base64.b64encode(b'\x00\x75\xbf' +
                    encrypt(bytes([10,len(identifier)]) + identifier.encode())).decode(),
                   'encryptKeyVersion': 11}
        _, prefix = seed_request_prefix(request, identifier)
        self.assertEqual(decode_with_stream(encrypt(ack()), stream, prefix)['map_seed'], SEED)

    def test_daily_boolean_choose_and_sorted_form(self):
        for choose in (False, True):
            with self.subTest(choose=choose), tempfile.TemporaryDirectory() as folder:
                body={'rank_state':2,'rank_time':0,'map_seed_2':'marker','version':'500',
                      'play_info':'fixture+/='*20,'removed':255,'rand_times':1,
                      'choose':choose,'rank_score':1,'rank_role':2,'skin':1,'watch_ad_count':0}
                plain=js_plaintext(body)
                body.update(encrypt_data=base64.b64encode(encrypt(plain)).decode(),encrypt_key_version=11)
                transport={k:('true' if v else 'false') if isinstance(v,bool) else v for k,v in body.items()}
                encoded=urllib.parse.urlencode(sorted(transport.items())).encode()
                parsed=request_body(encoded,settlement=True)
                self.assertEqual(parsed['choose'],choose)
                self.assertIsInstance(parsed['choose'],bool)
                _,stream=settlement_stream(parsed)
                self.assertEqual(stream,bytes(a^b for a,b in zip(plain,encrypt(plain))))
                capture=AutoCapture(Path(folder),auto_download=False,auto_solve=False,crypto_probe=False)
                try:
                    capture.request(SimpleNamespace(metadata={},request=SimpleNamespace(pretty_url='https://cat-match.easygame2021.com/sheep/v1/game/game_over_ex',content=encoded)))
                    capture.accept_start(start(),path='/sheep/v1/game/map_info_ex')
                    capture.accept_map(HASH,map_bytes())
                    identifier=capture.current['seed2']
                    req={'info':base64.b64encode(b'\x00\x75\xbf'+encrypt(bytes([10,len(identifier)])+identifier.encode())).decode(),'encryptKeyVersion':11}
                    flow=SimpleNamespace(metadata={},request=SimpleNamespace(pretty_url='https://cat-match.easygame2021.com/sheep/v1/game/map_info_ex_seed',content=urllib.parse.urlencode(req).encode()),response=SimpleNamespace(status_code=200,content=encrypt(ack())))
                    capture.request(flow);capture.response(flow)
                    self.assertTrue(capture.state['complete'])
                    self.assertEqual(capture.current['seed'],SEED)
                finally:capture.done()


if __name__ == '__main__':
    unittest.main(verbosity=2)
