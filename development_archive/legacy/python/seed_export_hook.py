"""Prepare an explicitly opt-in, reversible local seed export patch.

Preparation only writes project output. Installation alters one local game
package; it requires restarting the mini-game and may be rejected by WeChat's
package integrity check. No integrity checks are disabled or changed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import uuid

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from inspect_game_package import APPID, PACKAGES, decode, files

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'output/seed_export_hook'
TARGET = PACKAGES / '_subpackages_script-bundle_.wxapkg'
NEEDLE = b'if(s.code===C.Server2Client.SS2C_GetSeedAck.AckCode.Success)return s;'
REPLACEMENT = (
    b'if(s.code===C.Server2Client.SS2C_GetSeedAck.AckCode.Success){'
    b'try{wx.getFileSystemManager().writeFileSync(wx.env.USER_DATA_PATH+"/sheepsolver_seed.json",'
    b'JSON.stringify({capture_format:1,request_seed:t,map_seed:s.mapSeed,map_seed_2:s.mapSeed2,'
    b'exported_at:Date.now()}),"utf8")}catch(__sheep_export_error){}return s;}'
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def assemble(original, items):
    index_size = 4 + sum(12 + len(name.encode('utf-8')) for name, _ in items)
    offset = 14 + index_size
    index = struct.pack('>I', len(items))
    body = bytearray()
    for name, content in items:
        encoded = name.encode('utf-8')
        index += struct.pack('>I', len(encoded)) + encoded + struct.pack('>II', offset, len(content))
        offset += len(content)
        body.extend(content)
    header = b'\xbe' + original[1:5] + struct.pack('>II', index_size, len(body)) + b'\xed'
    return header + index + body


def encrypt(data):
    if len(data) < 1023:
        raise ValueError('Package is too small for this encryption format')
    key = hashlib.pbkdf2_hmac('sha1', APPID.encode(), b'saltiest', 1000, 32)
    enc = Cipher(algorithms.AES(key), modes.CBC(b'the iv: 16 bytes')).encryptor()
    head = enc.update(data[:1023] + b'\x01') + enc.finalize()
    tail = bytes(x ^ ord(APPID[-2]) for x in data[1023:])
    return b'V1MMWX' + head + tail


def prepare():
    original = TARGET.read_bytes()
    decoded = decode(original)
    entries = list(files(decoded))
    count, changed = 0, None
    modified = []
    for name, content in entries:
        hits = content.count(NEEDLE) if name.endswith('.js') else 0
        if hits:
            count += hits
            changed = name
            content = content.replace(NEEDLE, REPLACEMENT)
        modified.append((name, content))
    if count != 1:
        raise ValueError('当前包的种子成功返回位置不唯一，未准备或安装补丁。')
    rebuilt = assemble(decoded, modified)
    packaged = encrypt(rebuilt) if original.startswith(b'V1MMWX') else rebuilt
    # Re-open the actual encrypted output and compare every embedded file.
    if list(files(decode(packaged))) != modified:
        raise ValueError('补丁包回读与文件边界校验失败。')
    if any(a != b for a, b in zip(entries, modified) if a[0] != changed):
        raise ValueError('意外改动了其他资源。')
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'patched.wxapkg').write_bytes(packaged)
    (OUT / 'patched_game.js').write_bytes(next(c for n, c in modified if n == changed))
    manifest = {'target': str(TARGET), 'changed_file': changed, 'file_count': len(entries),
                'original_sha256': sha(original), 'patched_sha256': sha(packaged),
                'installed': False, 'exported_file': 'wx.env.USER_DATA_PATH/sheepsolver_seed.json',
                'purpose': '在原生种子解密成功后导出游戏种子及其请求标识',
                'requires_game_restart': True, 'package_integrity_acceptance': 'unverified'}
    (OUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


def install():
    manifest = json.loads((OUT / 'manifest.json').read_text(encoding='utf-8'))
    if Path(manifest['target']).resolve() != TARGET.resolve():
        raise ValueError('补丁路径不匹配。')
    data = TARGET.read_bytes()
    patch = (OUT / 'patched.wxapkg').read_bytes()
    if sha(data) != manifest['original_sha256'] or sha(patch) != manifest['patched_sha256']:
        raise ValueError('本地包已变化，未安装补丁。')
    backup = OUT / ('original-' + sha(data) + '.wxapkg')
    if backup.exists() and sha(backup.read_bytes()) != sha(data):
        raise ValueError('备份校验失败，未安装补丁。')
    backup.write_bytes(data)
    # Save recovery details before replacing the package.
    manifest.update(installed=True, backup=str(backup), installation_id=uuid.uuid4().hex)
    (OUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    temp = TARGET.with_name(TARGET.name + '.sheepsolver.tmp')
    temp.write_bytes(patch)
    temp.replace(TARGET)
    return manifest


def restore(expected_installation_id=None):
    manifest = json.loads((OUT / 'manifest.json').read_text(encoding='utf-8'))
    if not manifest.get('installed'):
        return manifest
    if expected_installation_id and manifest.get('installation_id') != expected_installation_id:
        return manifest
    if Path(manifest['target']).resolve() != TARGET.resolve():
        raise ValueError('恢复路径不匹配。')
    current = sha(TARGET.read_bytes())
    if current == manifest['patched_sha256']:
        backup = Path(manifest['backup'])
        if backup.parent.resolve() != OUT.resolve() or sha(backup.read_bytes()) != manifest['original_sha256']:
            raise ValueError('原始包备份校验失败，未写入。')
        temp = TARGET.with_name(TARGET.name + '.sheepsolver.tmp')
        temp.write_bytes(backup.read_bytes())
        temp.replace(TARGET)
    elif current != manifest['original_sha256']:
        # A legitimate update must not be overwritten with an old package.
        manifest['restore_note'] = '微信已更新本地包，保留更新版本。'
    manifest['installed'] = False
    (OUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'install', 'restore'])
    args = parser.parse_args()
    result = globals()[args.action]()
    print(json.dumps(result, ensure_ascii=True, indent=2))
