"""Read-only discovery of game map JSON retained in the running game.

Never changes process memory, injects code, clicks, or exports unrelated strings.
Only map geometry, card types, map hashes and seeds can be written to output.
"""
import argparse
import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

ROOT = Path(__file__).resolve().parent
APPLET = Path.home() / 'AppData/Roaming/Tencent/xwechat/radium/users/47e4cfd834b761448b3c00c953d8ed27/applet'
K = C.WinDLL('kernel32', use_last_error=True)
K.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
K.OpenProcess.restype = W.HANDLE
K.CloseHandle.argtypes = [W.HANDLE]
K.ReadProcessMemory.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
K.ReadProcessMemory.restype = W.BOOL


class MBI(C.Structure):
    _fields_ = [('base', C.c_void_p), ('allocation', C.c_void_p),
                ('allocation_protect', W.DWORD), ('size', C.c_size_t),
                ('state', W.DWORD), ('protect', W.DWORD), ('type', W.DWORD)]


K.VirtualQueryEx.argtypes = [W.HANDLE, C.c_void_p, C.POINTER(MBI), C.c_size_t]
K.VirtualQueryEx.restype = C.c_size_t
DECODER = json.JSONDecoder()
MARKERS = ['map_md5', 'map_seed', 'levelData', 'rolNum', 'blockTypeData']


def safe_map(levels):
    if not isinstance(levels, dict) or not levels:
        return None
    output = {}
    try:
        for level, cards in levels.items():
            int(level)
            if not isinstance(cards, list):
                return None
            output[str(level)] = []
            for c in cards:
                if not isinstance(c, dict):
                    return None
                entry = {k: c[k] for k in ('id', 'type', 'rolNum', 'rowNum')}
                if not isinstance(entry['id'], str) or not all(isinstance(entry[k], (int, float)) for k in ('type', 'rolNum', 'rowNum')):
                    return None
                entry['layerNum'] = int(level)
                output[str(level)].append(entry)
    except (ValueError, KeyError, TypeError):
        return None
    return output if sum(map(len, output.values())) >= 18 else None


def extract(text, found, source):
    for key in ('map_md5', 'map_seed', 'map_seed_2', 'blockTypeData', 'levelData'):
        for match in re.finditer(r'"' + key + r'"\s*:\s*', text):
            try:
                value, _ = DECODER.raw_decode(text[match.end():])
            except (ValueError, json.JSONDecodeError):
                continue
            if key == 'levelData':
                value = safe_map(value)
                if value is None:
                    continue
            elif key == 'blockTypeData':
                if not isinstance(value, dict) or not all(str(k).isdigit() and isinstance(v, int) for k, v in value.items()):
                    continue
            elif key == 'map_md5':
                candidates = value if isinstance(value, list) else [value]
                if not candidates or not all(isinstance(x, str) and re.fullmatch('[a-fA-F0-9]{32}', x) for x in candidates):
                    continue
            elif key == 'map_seed':
                if not isinstance(value, list) or len(value) != 4 or not all(isinstance(x, int) for x in value):
                    continue
            elif not isinstance(value, (int, str)):
                continue
            identity = key + ':' + json.dumps(value, sort_keys=True)
            if identity not in found:
                found[identity] = {'field': key, 'value': value, 'source': source}
                detail = {'field': key, 'source': source}
                if key == 'levelData':
                    detail['card_count'] = sum(map(len, value.values()))
                    detail['unknown_types'] = sum(c['type'] == 0 for cards in value.values() for c in cards)
                else:
                    detail['value'] = value
                print(json.dumps(detail, ensure_ascii=False), flush=True)


def scan(pid, found, cap_mb):
    handle = K.OpenProcess(0x0400 | 0x0010, False, pid)
    if not handle:
        print(json.dumps({'pid': pid, 'error': C.get_last_error()}), flush=True)
        return
    total = hits = 0
    address = 0
    started = time.monotonic()
    needles = [(s.encode(), 'utf-8', s) for s in MARKERS] + [(s.encode('utf-16-le'), 'utf-16-le', s) for s in MARKERS]
    try:
        while address < 0x7FFFFFFFFFFF and total < cap_mb * 1024**2:
            mbi = MBI()
            if not K.VirtualQueryEx(handle, address, C.byref(mbi), C.sizeof(mbi)):
                break
            base, size = mbi.base or 0, mbi.size
            address = base + size
            if size == 0:
                break
            if mbi.state != 0x1000 or mbi.protect & (0x100 | 0x01) or not mbi.protect & (0x02 | 0x04 | 0x08 | 0x20 | 0x40 | 0x80):
                continue
            for offset in range(0, size, 2 * 1024**2):
                if total >= cap_mb * 1024**2:
                    break
                length = min(size-offset, 2*1024**2 + 65536)
                buffer = C.create_string_buffer(length)
                read = C.c_size_t()
                K.ReadProcessMemory(handle, base+offset, buffer, length, C.byref(read))
                if not read.value:
                    continue
                raw = buffer.raw[:read.value]
                total += read.value
                seen = set()
                for needle, encoding, name in needles:
                    start = 0
                    for _ in range(100):
                        pos = raw.find(needle, start)
                        if pos < 0:
                            break
                        start = pos + len(needle)
                        hits += 1
                        region = (max(0, pos-8192), min(len(raw), pos+160000))
                        if (region[0]//4096, encoding) in seen:
                            continue
                        seen.add((region[0]//4096, encoding))
                        begin = region[0] - (region[0] % 2 if encoding == 'utf-16-le' else 0)
                        text = raw[begin:region[1]].decode(encoding, errors='ignore')
                        extract(text, found, 'pid:%d/address:%x' % (pid, base+offset+pos))
                del raw, buffer
    finally:
        K.CloseHandle(handle)
    print(json.dumps({'pid': pid, 'scanned_mb': round(total/1024**2, 1), 'marker_hits': hits, 'seconds': round(time.monotonic()-started, 2)}), flush=True)


def inspect_cache():
    package = APPLET / 'packages/wx141bfb9b73c970a9/500'
    print('package headers:', [(p.name, p.read_bytes()[:18].hex()) for p in package.glob('*.wxapkg')])
    data = APPLET / 'data/8'
    print('data entries:', [(p.name, p.stat().st_size if p.is_file() else 'directory') for p in data.iterdir()])
    try:
        con = sqlite3.connect((data / 'applet.db').as_uri()+'?mode=ro', uri=True)
        print('database table names:', con.execute("select name from sqlite_master where type='table'").fetchall())
        con.close()
    except sqlite3.Error as exc:
        print('database:', str(exc))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pids', nargs='*', type=int, default=[])
    parser.add_argument('--cap-mb', type=int, default=600)
    parser.add_argument('--inspect-cache', action='store_true')
    args = parser.parse_args()
    if args.inspect_cache:
        inspect_cache()
    found = {}
    for pid in args.pids:
        scan(pid, found, args.cap_mb)
        target = ROOT / 'output/map_discovery.json'
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(list(found.values()), ensure_ascii=False, indent=2), encoding='utf-8')
    maps = [x for x in found.values() if x['field'] == 'levelData']
    for index, item in enumerate(maps):
        value = {'levelData': item['value'], '_source': item['source']}
        digest = hashlib.sha256(json.dumps(item['value'], sort_keys=True).encode()).hexdigest()[:12]
        (ROOT / ('output/discovered_map_%s.json' % digest)).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
