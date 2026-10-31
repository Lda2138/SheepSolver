"""Mitmproxy addon: game start -> maps + seed -> original SheepSolver JSON.

Only game-map fields are retained. Request headers, tokens and account data
are not written to disk. This addon never submits a game result or clicks.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import urllib.parse
import uuid
import importlib
import capture_runtime

import urllib3
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
from cryptography.hazmat.decrepit.ciphers.modes import OFB

from map_protocol import decode_map, decode_seed, materialize, valid_seed, decode_match_maps
import game_known_plaintext
if __name__.startswith('__mitmproxy_script__.'):
    importlib.reload(game_known_plaintext)
from game_known_plaintext import settlement_stream, seed_request_prefix, decode_with_stream, request_body

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'output/auto_capture'


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    for attempt in range(100):
        try:temporary.replace(path);return
        except PermissionError:
            if attempt==99:raise
            time.sleep(.01)


class AutoCapture:
    def __init__(self, output=None, auto_download=True, auto_solve=True, crypto_probe=True):
        self.out = Path(output) if output else OUT
        self.auto_download = auto_download
        self.auto_solve = auto_solve
        self.crypto_probe = crypto_probe
        self.start_request = 0
        self.start_accepted = 0
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=3)
        self.http = urllib3.PoolManager(timeout=urllib3.Timeout(connect=5, read=15), retries=False)
        self.current = None
        self.maps = {}
        self.crypto = []
        self.cipher_pending = []
        self.streams = {}
        self.seed_prefix = None
        self.state = {'phase': 'waiting_for_game_start', 'complete': False, 'capture_pid': os.getpid(),
                      'message': '等待微信进入游戏；只有完整地图才会进入求解。'}
        saved = capture_runtime.handoff
        capture_runtime.handoff = None
        if saved and saved['pid'] == os.getpid() and saved['out'] == str(self.out):
            for name, value in saved['values'].items():
                setattr(self, name, value)
            if self.current:
                folder = self.out / 'sessions' / self.current['id']
                verified = folder / 'seed_verified_memory_snapshot.json'
                if verified.exists():
                    seed = json.loads(verified.read_text(encoding='utf-8'))
                    if valid_seed(seed.get('map_seed')):
                        self.current['seed'] = seed['map_seed']
                        self.current['seed2'] = seed.get('map_seed_2', self.current['seed2'])
                        self.state['seed_source'] = seed.get('source')
        resume_path = self.out / 'resume_capture.json'
        if self.current is None and resume_path.exists():
            try:self.restore_capture_request(resume_path)
            except (OSError,ValueError,KeyError,TypeError) as error:
                self.state['recovery_error']=str(error)
                resume_path.unlink(missing_ok=True)
        self.status()
        if self.current:
            self.finalize()

    def restore_capture_request(self,path):
        """Explicit one-shot recovery, never a guess based on the newest folder."""
        resume=json.loads(path.read_text(encoding='utf-8'))
        if resume.get('capture_pid')!=os.getpid():return
        if not re.fullmatch(r'\d{8}T\d{6}Z-[a-f0-9]{6}',resume.get('session','')):
            raise ValueError('抓取恢复局号无效')
        folder=self.out/'sessions'/resume['session']
        info=json.loads((folder/'start_map_info.json').read_text(encoding='utf-8'))
        hashes=info['map_md5']
        if not hashes or any(not re.fullmatch('[a-f0-9]{32}',h) for h in hashes):raise ValueError('抓取恢复地图 ID 无效')
        previous=resume.get('kind')=='verified_previous_capture'
        if previous:
            # Caller rechecks the live board before issuing this short-lived
            # request. Re-materialize EVERY saved map with its true saved seed.
            if resume.get('expires_at',0)<=time.time():raise ValueError('抓取恢复请求已过期')
            if len(resume.get('map_sha256',[]))!=len(hashes):raise ValueError('抓取恢复地图数量不一致')
            proof=json.loads((folder/'seed.json').read_text(encoding='utf-8'))
            proof['source']='recovered_verified_capture'
        else:proof=json.loads((folder/'seed_verified_memory_snapshot.json').read_text(encoding='utf-8'))
        if not valid_seed(proof.get('map_seed')):raise ValueError('抓取恢复种子无效')
        maps={h:decode_map((self.out/'maps'/(h+'.map')).read_bytes()) for h in hashes}
        if previous:
            for index,h in enumerate(hashes):
                mp=folder/f'map_{index}.json'
                if hashlib.sha256(mp.read_bytes()).hexdigest()!=resume['map_sha256'][index]:raise ValueError('抓取恢复地图已变化')
                saved=json.loads(mp.read_text(encoding='utf-8'));acquisition=saved.get('_acquisition',{})
                if acquisition.get('session')!=resume['session'] or acquisition.get('map_md5')!=h or acquisition.get('level_index')!=index:
                    raise ValueError('抓取恢复地图身份不一致')
                actual=materialize(maps[h],proof['map_seed'])
                if actual!={k:v for k,v in saved.items() if k!='_acquisition'}:raise ValueError('抓取恢复种子与地图不一致')
        self.maps=maps
        self.current={'id':resume['session'],'hashes':hashes,'seed':proof['map_seed'],
                      'seed2':proof['map_seed_2'],'complete_hashes':[],'solve_started':True,
                      'has_match_data':False,'runtime_maps':None,'runtime_error':None,'start_info':info}
        self.state.update(session=resume['session'],source_path=resume['source_path'],
                          seed_source=proof['source'],map_ids=hashes,map_count=len(hashes),solver='running_externally')
        path.unlink()

    def status(self, **changes):
        self.state.update(changes)
        if self.current:
            folder = self.out / 'sessions' / self.current['id']
            solution_path = folder / 'solution/solution.json'
            try:
                if self.state.get('solver') == 'running_externally' and solution_path.exists():
                    solution = json.loads(solution_path.read_text(encoding='utf-8'))
                    if solution.get('validated') is True:
                        self.state.update(solver='solved', solution_path=str(solution_path))
                playback_path = folder / 'main_play/playback_status.json'
                if playback_path.exists():
                    playback = json.loads(playback_path.read_text(encoding='utf-8-sig'))
                    self.state['playback'] = {k:playback[k] for k in ('next_step','total','state','message')}
            except (ValueError, OSError, KeyError):
                pass
        self.state['updated_at'] = datetime.now(timezone.utc).isoformat()
        write_json(self.out / 'status.json', self.state)

    def trace_endpoint(self,path,phase,**facts):
        # Endpoint metadata only: no query string, headers or request bodies.
        if not path.startswith('/sheep/'):return
        try:
            log=self.out/'endpoints.jsonl'
            if log.exists() and log.stat().st_size>1024*1024:
                os.replace(log,self.out/'endpoints.previous.jsonl')
            with log.open('a',encoding='utf-8') as stream:
                stream.write(json.dumps({'time':datetime.now(timezone.utc).isoformat(),
                    'phase':phase,'path':path[:180],'session':self.current['id'] if self.current else None,
                    **facts},ensure_ascii=False)+'\n')
        except OSError:pass

    def request(self, flow):
        # Associate seed requests before their responses can race a new start.
        with self.lock:
            flow.metadata['sheep_session'] = self.current['id'] if self.current else None
            url = urllib.parse.urlsplit(flow.request.pretty_url)
            path = url.path
            host = (url.hostname or '').lower()
            if host == 'easygame2021.com' or host.endswith('.easygame2021.com'):
                self.trace_endpoint(path,'request')
                self.status(game_requests=self.state.get('game_requests', 0) + 1,
                            last_game_host=host,last_game_path=path[:180])
                if '/sheep/v1/game/' in path and ('game_over' in path or path.endswith('/game/over')):
                    try:
                        body = request_body(flow.request.content, settlement=True)
                        if body.get('encrypt_data'):
                            version, stream = settlement_stream(body)
                            # Bound key versions, never write bodies or streams.
                            self.streams[version] = stream
                            if len(self.streams) > 4:
                                del self.streams[next(iter(self.streams))]
                            self.status(settlement_stream_received=True, settlement_error=None)
                            self.try_stream_seed()
                    except (ValueError, TypeError, KeyError, subprocess.TimeoutExpired) as exc:
                        known_errors = ('结算正文无效','加密版本无效','结算明密文长度不匹配',
                                        '结算数字表单字段无效','游戏表单字段重复或为空','JS 结算序列化失败')
                        self.status(settlement_stream_received=False,
                                    settlement_error_type=type(exc).__name__,
                                    settlement_error=str(exc) if str(exc) in known_errors else '结算请求解析未通过')
                elif 'map_info_ex_seed' in path and self.current:
                    try:
                        body = request_body(flow.request.content)
                        self.seed_prefix = (self.current['id'], *seed_request_prefix(body, self.current['seed2']))
                        self.status(seed_request_prefix_received=True)
                    except (ValueError, TypeError, KeyError):
                        self.seed_prefix = None
                        self.status(seed_request_prefix_received=False)
            if '/sheep/' in path and ('map_info_ex' in path or 'game_start' in path) and 'seed' not in path:
                self.start_request += 1
                flow.metadata['sheep_start_order'] = self.start_request

    def response(self, flow):
        try:
            url = urllib.parse.urlsplit(flow.request.pretty_url)
            host = (url.hostname or '').lower()
            if not (host == 'easygame2021.com' or host.endswith('.easygame2021.com')):
                return
            with self.lock:
                self.status(game_responses=self.state.get('game_responses', 0) + 1,
                            last_game_response_code=flow.response.status_code)
            path = url.path
            self.trace_endpoint(path,'response',http_status=flow.response.status_code)
            content = flow.response.content or b''
            if len(content) > 8*1024**2 or flow.response.status_code != 200:
                return
            match = re.search(r'/maps/([a-fA-F0-9]{32})\.(map|txt)$', path)
            if match:
                self.accept_map(match[1].lower(), content)
                return
            if 'map_info_ex_seed' in path:
                if not flow.metadata.get('sheep_session'):
                    return
                encrypted = False
                try:
                    request = request_body(flow.request.content)
                    encrypted = bool(request.get('encryptKeyVersion'))
                except ValueError:
                    pass
                self.accept_seed(content, encrypted, flow.metadata.get('sheep_session'))
                return
            if not ('/sheep/' in path and ('game' in path or 'recovery' in path)):
                return
            try:
                parsed = json.loads(content)
            except (ValueError, UnicodeError):
                return
            data = parsed.get('data') if isinstance(parsed, dict) else None
            if isinstance(data, dict) and isinstance(data.get('map_md5'), list):
                if parsed.get('err_code', 0) == 0:
                    with self.lock:
                        order = flow.metadata.get('sheep_start_order', self.start_accepted + 1)
                        if order < self.start_accepted:
                            return
                        self.start_accepted = order
                        self.accept_start(data, host, path)
        except OSError as exc:
            # Publishing a snapshot is separate from decoding network data.
            # A transient Windows reader lock must not invalidate a real map.
            with self.lock:
                self.status(status_publish_error=str(exc))
        except Exception as exc:
            with self.lock:
                self.status(message='捕获到游戏数据但解析失败：' + str(exc), phase='parse_error', complete=False)

    def tls_failed_client(self, data):
        host = (data.context.client.sni or '').lower()
        if host == 'easygame2021.com' or host.endswith('.easygame2021.com'):
            with self.lock:
                self.status(tls_client_failures=self.state.get('tls_client_failures', 0) + 1,
                            last_tls_failure_host=host,
                            message='游戏客户端拒绝了 HTTPS 连接，尚未收到开局数据。' if not self.current else self.state['message'])

    def accept_start(self, data, host='cat-match.easygame2021.com', path='/sheep/v1/game/map_info_ex'):
        hashes = data.get('map_md5')
        if not hashes or not all(isinstance(x, str) and re.fullmatch('[a-fA-F0-9]{32}', x) for x in hashes):
            raise ValueError('开局地图 ID 不合法')
        with self.lock:
            runtime_maps, runtime_error = None, None
            if data.get('match_data'):
                try:
                    runtime_maps = decode_match_maps(data['match_data'])
                    if len(runtime_maps) != len(hashes):
                        raise ValueError('match_data 与地图 ID 数量不一致')
                except (ValueError, TypeError, KeyError) as exc:
                    runtime_maps, runtime_error = None, str(exc)
            seed = data.get('map_seed') if valid_seed(data.get('map_seed')) and not data.get('need_seed') else None
            self.current = {'id': datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:6],
                            'hashes': [h.lower() for h in hashes], 'seed': seed,
                            'seed2': data.get('map_seed_2'), 'complete_hashes': [], 'solve_started': False,
                            'has_match_data': bool(data.get('match_data')),
                            'runtime_maps': runtime_maps, 'runtime_error': runtime_error}
            self.cipher_pending.clear()
            self.seed_prefix = None
            # Retain only an explicit allowlist; never store the full server reply.
            info = {k: data[k] for k in ('map_md5', 'map_seed', 'map_seed_2', 'need_seed', 'need_wx_encrypt') if k in data}
            self.current['start_info'] = info
            folder = self.out / 'sessions' / self.current['id']
            write_json(folder / 'start_map_info.json', info)
            self.status(phase='capturing', complete=False, session=self.current['id'], map_ids=self.current['hashes'],
                        map_count=len(hashes), maps_received=0, seed_received=bool(seed), source_path=path,
                        solver='not_started', current_map=None,
                        message='已捕获开局地图 ID，等待地图与真实种子。')
            # No stale alias may masquerade as a newly captured game.
            for alias in (self.out / 'current_game_map.json', ROOT / 'output/current_game_map.json'):
                if alias.exists() and (self.out == OUT or alias.parent == self.out):
                    alias.unlink()
            session = self.current['id']
            for map_hash in self.current['hashes']:
                if self.auto_download and map_hash not in self.maps:
                    self.pool.submit(self.download_map, map_hash, session)
            if data.get('need_seed') and self.crypto_probe:
                self.pool.submit(self.probe_crypto, session)
            self.finalize()

    def download_map(self, map_hash, session):
        try:
            # Public resource download, without copying user authentication headers.
            url = 'https://cat-match-static.easygame2021.com/maps/' + map_hash + '.map'
            reply = self.http.request('GET', url)
            if reply.status != 200:
                raise ValueError('地图下载返回 HTTP ' + str(reply.status))
            self.accept_map(map_hash, reply.data)
        except Exception as exc:
            with self.lock:
                if self.current and self.current['id'] == session:
                    self.status(message='等待游戏自身下载地图；主动下载失败：' + str(exc))

    def accept_map(self, map_hash, raw):
        decoded = decode_map(raw)
        with self.lock:
            self.maps[map_hash] = decoded
            folder = self.out / 'maps'
            folder.mkdir(parents=True, exist_ok=True)
            (folder / (map_hash + '.map')).write_bytes(raw)
            write_json(folder / (map_hash + '.structure.json'), decoded)
            self.finalize()

    def accept_seed(self, raw, encrypted=False, session=None):
        with self.lock:
            if not self.current or (session is not None and self.current['id'] != session):
                return
            if valid_seed(self.current.get('seed')):
                # A retransmitted encrypted reply must not downgrade a verified map.
                return
            try:
                value = json.loads(raw)
                if isinstance(value, dict):
                    if value.get('err_code', 0) != 0:
                        raise ValueError('种子请求未成功')
                    value = value.get('data', value)
                seed = value.get('map_seed', value.get('mapSeed')) if isinstance(value, dict) else None
                if valid_seed(seed):
                    return self.set_seed({'map_seed': seed, 'map_seed_2': value.get('map_seed_2', value.get('mapSeed2'))})
            except (ValueError, UnicodeError):
                pass
            if not encrypted:
                try:
                    return self.set_seed(decode_seed(raw))
                except ValueError:
                    pass
            if len(self.cipher_pending) >= 8:
                self.cipher_pending.pop(0)
            self.cipher_pending.append((self.current['id'], bytes(raw)))
            if self.try_stream_seed():
                return
            self.status(phase='waiting_for_seed_decryption', complete=False,
                        message='种子响应已到达，等待游戏的解密参数；不会把密文当作种子。')
            if self.crypto_probe:
                self.pool.submit(self.probe_crypto, self.current['id'])

    def set_seed(self, value):
        if not self.current:
            return
        self.current['seed'] = value['map_seed']
        if value.get('map_seed_2'):
            self.current['seed2'] = value['map_seed_2']
        write_json(self.out / 'sessions' / self.current['id'] / 'seed.json', value)
        self.status(seed_received=True)
        self.finalize()

    def probe_crypto(self, expected_session):
        try:
            import win32gui
            import win32process
            from game_crypto_probe import find_game_crypto
            pids = []
            def visit(hwnd, unused):
                if win32gui.GetWindowText(hwnd) == '羊了个羊：星球':
                    pids.append(win32process.GetWindowThreadProcessId(hwnd)[1])
            win32gui.EnumWindows(visit, None)
            keys = []
            for pid in set(pids):
                keys.extend(find_game_crypto(pid))
            self.try_decrypt(keys, expected_session)
        except Exception:
            # Do not print potential key-containing exceptions or process strings.
            pass

    def try_decrypt(self, keys, expected_session):
        with self.lock:
            if not self.current or self.current['id'] != expected_session:
                return
            self.crypto = keys
            for session, raw in list(self.cipher_pending):
                if self.current['id'] != session:
                    continue
                for key, iv in keys:
                    dec = Cipher(algorithms.AES(key), OFB(iv)).decryptor()
                    try:
                        value = decode_seed(dec.update(raw) + dec.finalize())
                    except (ValueError, UnicodeError):
                        continue
                    self.set_seed(value)
                    self.cipher_pending.remove((session, raw))
                    break

    def try_stream_seed(self):
        if not self.current or not self.seed_prefix or self.seed_prefix[0] != self.current['id']:
            return False
        session, version, prefix = self.seed_prefix
        stream = self.streams.get(version)
        if not stream:
            return False
        for pending_session, raw in list(self.cipher_pending):
            if pending_session != session:
                continue
            try:
                value = decode_with_stream(raw, stream, prefix)
            except (ValueError, UnicodeError):
                continue
            self.set_seed(value)
            self.cipher_pending.remove((session, raw))
            self.status(seed_source='validated_settlement_stream')
            return True
        return False

    def finalize(self):
        if not self.current:
            return
        current = self.current
        folder = self.out / 'sessions' / current['id']
        if current['has_match_data'] and current['runtime_maps'] is None:
            self.status(phase='unsupported_match_data', complete=False,
                        message='新版 match_data 解析未完成：' + str(current['runtime_error']))
            return
        completed = []
        for index, map_hash in enumerate(current['hashes']):
            if map_hash not in self.maps and not current['runtime_maps']:
                continue
            try:
                result = materialize(current['runtime_maps'][index] if current['runtime_maps'] else self.maps[map_hash], current['seed'])
            except ValueError:
                continue
            result['_acquisition'] = {'session': current['id'], 'map_md5': map_hash,
                                       'level_index': index, 'captured_at': datetime.now(timezone.utc).isoformat()}
            map_path=folder / ('map_' + str(index) + '.json')
            old=json.loads(map_path.read_text(encoding='utf-8')) if map_path.exists() else None
            if old and {k:v for k,v in old.items() if k!='_acquisition'}=={k:v for k,v in result.items() if k!='_acquisition'} and old.get('_acquisition',{}).get('session')==current['id']:
                result=old  # Keep the exact map identity when an addon reloads.
            else:write_json(map_path,result)
            completed.append(map_hash)
            if index == len(current['hashes']) - 1:
                write_json(self.out / 'current_game_map.json', result)
                if self.out == OUT:
                    write_json(ROOT / 'output/current_game_map.json', result)
        current['complete_hashes'] = completed
        complete = len(completed) == len(current['hashes'])
        self.status(phase='complete' if complete else self.state['phase'], complete=complete,
                    current_map=str(folder / ('map_' + str(len(current['hashes'])-1) + '.json')) if complete else None,
                    maps_received=sum(h in self.maps for h in current['hashes']),
                    complete_maps=len(completed), seed_received=bool(current['seed']),
                    message='完整地图已生成，可以使用原版 SheepSolver。' if complete else self.state['message'])
        if complete and self.auto_solve and not current['solve_started']:
            current['solve_started'] = True
            self.pool.submit(self.solve_current, current['id'], folder / ('map_' + str(len(current['hashes'])-1) + '.json'))

    def solve_current(self, session, map_path):
        config = ROOT / 'auto_capture_config.json'
        settings = json.loads(config.read_text(encoding='utf-8')) if config.exists() else {}
        if not settings.get('auto_solve', True):
            return
        result = json.loads(map_path.read_text(encoding='utf-8'))
        from color_rules import compatibility
        if compatibility(result) not in ('standard', 'color_shuffle','energy_alien') or result.get('_resumed_state'):
            with self.lock:
                if self.current['id'] == session:
                    self.status(solver='unsupported_state', message='地图已完整，但含特殊牌或已进行的牌槽状态，原求解器暂不支持。')
            return
        limit = max(1, min(300, int(settings.get('solve_time_limit', 30))))
        python = ROOT / 'venv/Scripts/python.exe'
        mode = settings.get('solve_mode', 'level-top')
        if mode not in ('normal','index-ascending','index-descending','level-bottom','level-top','random'):
            mode = 'level-top'
        command = [str(python), str(ROOT / 'solve_verified_map.py'), '--map', str(map_path),
                   '--mode', mode, '--time-limit', str(limit),
                   '--output', str(self.out / 'sessions' / session / 'solution')]
        with self.lock:
            if self.current['id'] == session:
                self.status(solver='running')
        try:
            process = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=limit+15,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            state = 'solved' if process.returncode == 0 else 'no_solution_within_limit'
        except subprocess.TimeoutExpired:
            state = 'timed_out'
        with self.lock:
            if self.current and self.current['id'] == session:
                self.status(solver=state)

    def done(self):
        if __name__.startswith('__mitmproxy_script__.'):
            with self.lock:
                names = ('current', 'maps', 'crypto', 'cipher_pending', 'streams',
                         'seed_prefix', 'state', 'start_request', 'start_accepted')
                # Copy mutable sensitive collections before clearing this addon.
                values = {name: getattr(self, name) for name in names}
                for name in ('crypto', 'cipher_pending'):
                    values[name] = list(values[name])
                values['streams'] = dict(values['streams'])
                capture_runtime.handoff = {'pid': os.getpid(), 'out': str(self.out), 'values': values}
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.crypto.clear()
        self.cipher_pending.clear()
        self.streams.clear()
        self.seed_prefix = None


if __name__.startswith('__mitmproxy_script__.'):
    capture = AutoCapture()
    settings_path = ROOT / 'auto_capture_config.json'
    settings = json.loads(settings_path.read_text(encoding='utf-8')) if settings_path.exists() else {}
    addons = [capture]

