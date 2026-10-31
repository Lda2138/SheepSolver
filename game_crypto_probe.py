"""Find only an explicit wx.getLatestUserKey JSON result in the game host.

Keys remain in memory and are never printed or saved. No process is modified.
This is best-effort: if the callback result is no longer retained, return no key.
"""
import ctypes as C
import json
import re
import time
from map_protocol import valid_seed

from map_acquire import K, MBI


def find_game_crypto(pid, timeout=5, seed_identity=None, diagnostics=None):
    handle = K.OpenProcess(0x0400 | 0x0010, False, pid)
    if not handle:
        return []
    found = {}
    seed_found = []
    stats = {'read_bytes': 0, 'crypto_markers': 0, 'seed_markers': 0}
    address = total = 0
    deadline = time.monotonic() + timeout
    try:
        while address < 0x7FFFFFFFFFFF and total < 1024**3 and time.monotonic() < deadline:
            mbi = MBI()
            if not K.VirtualQueryEx(handle, address, C.byref(mbi), C.sizeof(mbi)):
                break
            base, size = mbi.base or 0, mbi.size
            address = base + size
            if not size:
                break
            if mbi.state != 0x1000 or mbi.protect & (0x100 | 0x01) or not mbi.protect & (0x02 | 0x04 | 0x08 | 0x20 | 0x40 | 0x80):
                continue
            for offset in range(0, size, 2*1024**2):
                if time.monotonic() > deadline:
                    break
                length = min(size-offset, 2*1024**2 + 2048)
                buffer = C.create_string_buffer(length)
                read = C.c_size_t()
                K.ReadProcessMemory(handle, base+offset, buffer, length, C.byref(read))
                if not read.value:
                    continue
                raw = buffer.raw[:read.value]
                total += read.value
                stats['read_bytes'] += read.value
                for needle, encoding in ((b'encryptKey', 'utf-8'), ('encryptKey'.encode('utf-16-le'), 'utf-16-le'),
                                         (b'encrypt_key', 'utf-8'), ('encrypt_key'.encode('utf-16-le'), 'utf-16-le')):
                    start = 0
                    while True:
                        pos = raw.find(needle, start)
                        if pos < 0:
                            break
                        start = pos + len(needle)
                        stats['crypto_markers'] += 1
                        begin = max(0, pos-400)
                        if encoding == 'utf-16-le':
                            begin -= begin % 2
                        text = raw[begin:pos+1800].decode(encoding, errors='ignore')
                        text = re.sub(r'\\+"', '"', text)
                        key = re.search(r'"(?:encryptKey|encrypt_key)"\s*:\s*"([A-Za-z0-9+/=_-]{16,64})"', text)
                        iv = re.search(r'"iv"\s*:\s*"([A-Za-z0-9+/=_-]{16,32})"', text)
                        expiry = re.search(r'"expireTime"\s*:\s*(\d{10,13})', text)
                        if key and iv:
                            k, v = key[1].encode(), iv[1].encode()
                            expires = int(expiry[1]) if expiry else 0
                            if expires > 10**12:
                                expires //= 1000
                            if len(k) in (16, 24, 32) and len(v) == 16 and (expires == 0 or expires > time.time()):
                                found[(k, v)] = (k, v)
                if seed_identity:
                    seed2, map_ids = seed_identity
                    for encoding in ('utf-8', 'utf-16-le'):
                        marker = '"map_seed"'.encode(encoding)
                        begin_at = 0
                        while True:
                            pos = raw.find(marker, begin_at)
                            if pos < 0:
                                break
                            begin_at = pos + len(marker)
                            stats['seed_markers'] += 1
                            begin = max(0, pos-4096)
                            if encoding == 'utf-16-le':
                                begin -= begin % 2
                            text = raw[begin:pos+8192].decode(encoding, errors='ignore')
                            # Require the current game's unique seed identifier
                            # and both resource hashes in the same small record.
                            identity = re.search(r'"map_seed_2"\s*:\s*"' + re.escape(str(seed2)) + r'"', text)
                            if not identity or not all(h in text for h in map_ids):
                                continue
                            for match in re.finditer(r'"map_seed"\s*:', text):
                                brace = text.rfind('{', 0, match.start())
                                for _ in range(16):
                                    if brace < 0:
                                        break
                                    try:
                                        record, _ = json.JSONDecoder().raw_decode(text[brace:])
                                    except ValueError:
                                        record = None
                                    if isinstance(record, dict):
                                        seed = record.get('map_seed')
                                        if (record.get('map_seed_2') == seed2 and record.get('map_md5') == map_ids
                                                and valid_seed(seed) and seed not in seed_found):
                                            seed_found.append(seed)
                                    brace = text.rfind('{', 0, brace)
                del raw, buffer
    finally:
        K.CloseHandle(handle)
    if diagnostics is not None:
        diagnostics.update(stats)
    return {'keys': list(found.values()), 'seeds': seed_found} if seed_identity else list(found.values())
