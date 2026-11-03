"""Isolated, bounded real-map experiments; never launches game playback."""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parent


class Memory(ctypes.Structure):
    _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD)]+[(name,ctypes.c_size_t) for name in
        ('peak_rss','rss','peak_pool','pool','peak_nonpaged','nonpaged','pagefile','peak_pagefile')]


class ProcessEntry(ctypes.Structure):
    _fields_=[('size',wintypes.DWORD),('usage',wintypes.DWORD),('pid',wintypes.DWORD),
              ('heap',ctypes.c_size_t),('module',wintypes.DWORD),('threads',wintypes.DWORD),
              ('parent',wintypes.DWORD),('priority',wintypes.LONG),('flags',wintypes.DWORD),
              ('exe',wintypes.WCHAR*260)]


def tree_memory(root_pid):
    # Windows venv launchers spawn the actual interpreter. Measuring only
    # Popen.pid would miss almost all memory and leave a solver after timeout.
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
    kernel.Process32FirstW.argtypes=[wintypes.HANDLE,ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.argtypes=kernel.Process32FirstW.argtypes
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    snapshot=kernel.CreateToolhelp32Snapshot(2,0);parents={}
    try:
        entry=ProcessEntry();entry.size=ctypes.sizeof(entry)
        ok=kernel.Process32FirstW(snapshot,ctypes.byref(entry))
        while ok:
            parents[entry.pid]=entry.parent
            ok=kernel.Process32NextW(snapshot,ctypes.byref(entry))
    finally:kernel.CloseHandle(snapshot)
    pids={root_pid}
    while True:
        updated=pids|{pid for pid,parent in parents.items() if parent in pids}
        if updated==pids:break
        pids=updated
    getmem=ctypes.WinDLL('psapi').GetProcessMemoryInfo
    getmem.argtypes=[wintypes.HANDLE,ctypes.POINTER(Memory),wintypes.DWORD]
    total=0
    for pid in pids:
        handle=kernel.OpenProcess(0x410,False,pid)
        if not handle:continue
        try:
            mem=Memory();mem.cb=ctypes.sizeof(mem)
            if getmem(handle,ctypes.byref(mem),mem.cb):total+=mem.rss
        finally:kernel.CloseHandle(handle)
    return total


def experiment(mp,out,seconds,workers,prefix=None,hint=None,assignments=None,repair=False,memory_mb=2048,encoding='auto',segment_assumptions=None,prefer_early_triples=False,no_lp=False,base_witness=None,fixed_route=None,learn_conflicts=True,triple_assumptions=None,fixed_triples=None):
    out.mkdir(parents=True,exist_ok=True)
    cmd=[sys.executable,str(ROOT/'solve_constraints.py'),'--map',str(mp),'--output',str(out),
         '--seconds',str(seconds),'--workers',str(workers),'--encoding',encoding]
    for option,value in (('--prefix',prefix),('--hint',hint),('--assumptions',assignments),
                         ('--segment-assumptions',segment_assumptions),('--base-witness',base_witness),
                         ('--fixed-route',fixed_route),('--triple-assumptions',triple_assumptions),
                         ('--fixed-triples',fixed_triples)):
        if value is not None:cmd.extend([option,str(value)])
    if repair:cmd.append('--repair')
    if prefer_early_triples:cmd.append('--prefer-early-triples')
    if no_lp:cmd.append('--no-lp')
    if not learn_conflicts:cmd.append('--no-conflict-learning')
    started=time.monotonic();started_wall=time.time();peak=0;reason=None
    with (out/'process.log').open('w',encoding='utf-8') as log:
        child=subprocess.Popen(cmd,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
        while child.poll() is None:
            peak=max(peak,tree_memory(child.pid))
            if peak>memory_mb*1024*1024:reason='memory_limit'
            if time.monotonic()-started>seconds+10:reason='wall_limit'
            if reason:
                subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW,timeout=10)
                child.wait(timeout=10);break
            time.sleep(.25)
    status_path=out/'constraint_status.json'
    try:
        if status_path.stat().st_mtime<started_wall:raise ValueError('Stale status')
        result=json.loads(status_path.read_text(encoding='utf-8'))
    except (OSError,ValueError):result={'state':'error','reason':'missing_current_status'}
    if reason:result.update(state='unknown',reason=reason)
    elif child.returncode not in (0,2):result.update(state='error',reason='process_failed')
    result.update(wall_seconds=time.monotonic()-started,peak_rss_mb=round(peak/1024**2,1),
                  process_exit=child.returncode,map_sha256=hashlib.sha256(mp.read_bytes()).hexdigest())
    (out/'benchmark_result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(out.name,json.dumps(result,ensure_ascii=True),flush=True)
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--map',type=Path,required=True)
    p.add_argument('--candidate',type=Path);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--boundaries',default='237,225,207,183,0')
    p.add_argument('--seconds',type=float,default=30);p.add_argument('--workers',type=int,default=4)
    p.add_argument('--encoding',choices=['auto','slots','occurrences','events','schedule'],default='auto')
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    route=json.loads(a.candidate.read_text(encoding='utf-8-sig'))['sequence'] if a.candidate else []
    for size in map(int,a.boundaries.split(',')):
        if size>len(route):raise ValueError('Boundary exceeds candidate length')
        prefix=a.output/f'prefix_{size}.json'
        prefix.write_text(json.dumps({'sequence':route[:size]}),encoding='utf-8')
        experiment(a.map,a.output/f'boundary_{size}',a.seconds,a.workers,prefix=prefix,encoding=a.encoding)


if __name__=='__main__':main()
