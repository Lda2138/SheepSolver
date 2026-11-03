"""Inspect a local game package to identify the current map protocol."""
import hashlib
import os
from pathlib import Path
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

ROOT = Path(__file__).resolve().parent
APPID = 'wx141bfb9b73c970a9'
PACKAGES = Path(os.environ.get('WECHAT_APPLET_PACKAGES', ''))
OUT = ROOT / 'output/game_protocol'


def decode(raw):
    if raw[:6] != b'V1MMWX':
        return raw
    key = hashlib.pbkdf2_hmac('sha1', APPID.encode(), b'saltiest', 1000, 32)
    dec = Cipher(algorithms.AES(key), modes.CBC(b'the iv: 16 bytes')).decryptor()
    head = dec.update(raw[6:1030]) + dec.finalize()
    tail = bytes(x ^ ord(APPID[-2]) for x in raw[1030:])
    data = head[:-1] + tail
    if data[0] != 0xBE or data[13] != 0xED:
        raise ValueError('Package format validation failed')
    return data


def files(data):
    count = struct.unpack_from('>I', data, 14)[0]
    at = 18
    if count > 10000:
        raise ValueError('Unexpected package file count')
    for _ in range(count):
        length = struct.unpack_from('>I', data, at)[0]
        at += 4
        name = data[at:at+length].decode('utf-8')
        at += length
        offset, size = struct.unpack_from('>II', data, at)
        at += 8
        if offset+size > len(data):
            raise ValueError('Package bounds invalid')
        yield name, data[offset:offset+size]


if __name__ == '__main__':
    if not os.environ.get('WECHAT_APPLET_PACKAGES'):
        raise SystemExit('请设置 WECHAT_APPLET_PACKAGES 指向目标游戏包目录')
    OUT.mkdir(parents=True, exist_ok=True)
    for package in PACKAGES.glob('*.wxapkg'):
        decoded = decode(package.read_bytes())
        for index, (name, content) in enumerate(files(decoded)):
            if name.endswith('.js'):
                target = OUT / (package.stem + '_' + str(index) + '.js')
                target.write_bytes(content)
                print(package.name, name, len(content), '->', target.name)
