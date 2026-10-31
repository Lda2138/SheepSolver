"""Passive OFB stream recovery from the game's own redundant settlement body.

Installed gameOver() encrypts encodeURIComponent(JSON.stringify(r)), then adds
encrypt_data and encrypt_key_version to that same r. No requests are changed.
Stream bytes remain in RAM. Validate against each seed request's known prefix.
"""
import base64
import json
import subprocess
import urllib.parse

from map_protocol import decode_seed


def xor(a, b):
    return bytes(x ^ y for x, y in zip(a, b))


def js_plaintext(value):
    # Use the real JS serializer for floating point values and property order.
    # The body travels through stdin only; it is never a command-line argument.
    result = subprocess.run(['node', '-e',
        'let b="";process.stdin.setEncoding("utf8");process.stdin.on("data",c=>b+=c);process.stdin.on("end",()=>process.stdout.write(encodeURIComponent(JSON.stringify(JSON.parse(b)))));'],
        input=json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8'),
        capture_output=True, timeout=3, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode != 0:
        raise ValueError('JS 结算序列化失败')
    return result.stdout


def request_body(raw, settlement=False):
    if not raw or len(raw) > 2*1024**2:
        raise ValueError('游戏请求正文长度异常')
    text = raw.decode('utf-8')
    if text.lstrip().startswith('{'):
        result = json.loads(text)
        if not isinstance(result, dict):
            raise ValueError('游戏请求不是对象')
        return result
    pairs = urllib.parse.parse_qsl(text, keep_blank_values=True, strict_parsing=True)
    result = dict(pairs)
    if len(result) != len(pairs) or not pairs:
        raise ValueError('游戏表单字段重复或为空')
    if settlement:
        # Verified fields populated by reportData() and gameOver(). The auth
        # lease is removed before encryption; common auth params are in URL.
        numeric = {'rank_state','rank_time','removed','rand_times','choose',
                   'rank_score','rank_role','skin','watch_ad_count','encrypt_key_version'}
        for key in numeric & result.keys():
            try:
                result[key] = json.loads(result[key])
            except ValueError:
                raise ValueError('结算数字表单字段无效') from None
            # Daily mode's choose is the Boolean returned by hasContent().
            if key == 'choose' and isinstance(result[key], bool):
                continue
            if not isinstance(result[key], (int,float)) or isinstance(result[key],bool):
                raise ValueError('结算数字表单字段无效')
    return result


def settlement_stream(body):
    if not isinstance(body, dict) or len(body) > 100:
        raise ValueError('结算正文无效')
    cipher = base64.b64decode(body['encrypt_data'], validate=True)
    version = body['encrypt_key_version']
    if not isinstance(version, (int, str)) or isinstance(version, bool) or not str(version).isdigit() or int(version) <= 0:
        raise ValueError('加密版本无效')
    # The HTTP form encoder sorts keys. Rebuild the insertion order used by
    # reportData()/gameOver() before encryption (verified installed source).
    names = ('rank_state', 'rank_time', 'map_seed_2', 'version', 'play_info',
             'removed', 'rand_times', 'choose', 'rank_score', 'rank_role', 'skin')
    ordered = {k: body[k] for k in names if k in body}
    ignored = ('encrypt_data', 'encrypt_key_version', 'watch_ad_count')
    for k, v in body.items():
        if k not in ordered and k not in ignored:
            ordered[k] = v
    if 'watch_ad_count' in body:
        ordered['watch_ad_count'] = body['watch_ad_count']
    plain = js_plaintext(ordered)
    if not 32 <= len(cipher) <= 1024**2 or len(cipher) != len(plain):
        raise ValueError('结算明密文长度不匹配')
    return str(version), xor(plain, cipher)


def seed_request_prefix(body, seed_identifier):
    frame = base64.b64decode(body['info'], validate=True)
    seed = str(seed_identifier).encode('utf-8')
    if not 1 <= len(seed) < 128 or len(frame) != 3 + 2 + len(seed):
        raise ValueError('种子请求长度不匹配')
    return str(body['encryptKeyVersion']), xor(frame[3:], bytes([10, len(seed)]) + seed)


def decode_with_stream(raw, stream, prefix):
    if len(raw) > len(stream) or len(prefix) < 8 or stream[:len(prefix)] != prefix:
        raise ValueError('当前种子请求与结算加密流未匹配')
    return decode_seed(xor(raw, stream))
