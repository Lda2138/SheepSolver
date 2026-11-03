"""Offline parameter experiment, bounded memory, independent complete replay."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import itertools
import json
from pathlib import Path
import subprocess
import time
from search_resources import ResourceSampler, available_memory
from truth_dataset import read, dump, verify_route

ROOT=Path(__file__).resolve().parent

def trial(exe, mp, folder, first, cap, seed, seconds, memory_mb, mode='tight-top'):
    folder.mkdir(parents=True,exist_ok=True)
    pp=folder/'prefix.json';dump(pp,{'sequence':[], 'map_sha256':hashlib.sha256(mp.read_bytes()).hexdigest()})
    answer=folder/'answer.json';progress=folder/'progress.json'
    command=[str(exe),str(mp),str(answer),mode,str(first),str(seconds),str(seed),str(cap),str(progress),str(pp),'2048','6']
    started=time.monotonic();sampler=ResourceSampler();peak_ws=peak_private=0;reason=None
    with (folder/'process.log').open('w') as log:
        p=subprocess.Popen(command,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS)
        try:
            while p.poll() is None:
                ws,pv=sampler.process_memory(p);peak_ws=max(ws,peak_ws);peak_private=max(pv,peak_private)
                if pv>memory_mb*1024**2 or available_memory()<1536*1024**2:reason='memory_limit'
                elif time.monotonic()-started>seconds+10:reason='watchdog'
                if reason:p.terminate();break
                time.sleep(.05)
        finally:
            if p.poll() is None:p.terminate()
            p.wait()
    info=read(progress) if progress.exists() else {}
    row={**info,'first':first,'cap':cap,'seed':seed,'returncode':p.returncode,'wall_seconds':time.monotonic()-started,
         'peak_sampled_ws_mb':peak_ws/1024**2,'peak_sampled_private_mb':peak_private/1024**2,
         'external_stop':reason,'classification':'unknown','folder':str(folder)}
    if p.returncode==0 and answer.exists():
        metrics=verify_route(read(mp),read(answer)['sequence']);row.update(classification='complete',verification=metrics)
        dump(folder/'independent_replay.json',metrics)
    dump(folder/'result.json',row)
    return row

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--exe',type=Path,default=ROOT/'StaticFastSolver.exe')
    ap.add_argument('--output',type=Path,default=ROOT/'output/d91a_baseline');ap.add_argument('--seconds',type=float,default=60)
    ap.add_argument('--jobs',type=int,default=10);ap.add_argument('--memory-mb',type=int,default=900)
    ap.add_argument('--map',type=Path,default=ROOT/'output/world_handoff_20261029_aec907/current_map.json')
    ap.add_argument('--seeds',default='1997,1,2,3,4,5,6,7,8');ap.add_argument('--caps',default='2000000,40000000,200000000')
    args=ap.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    dump(args.output/'protocol.json',{'map_sha256':hashlib.sha256(args.map.read_bytes()).hexdigest(),'exe_sha256':hashlib.sha256(args.exe.read_bytes()).hexdigest(),**{k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},'memory_policy':'per-process sampled private cap and global 1.5 GiB reserve; externally stopped trials are UNKNOWN'})
    rows=[]
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures=[pool.submit(trial,args.exe,args.map,args.output/f'f{first}_c{cap}_s{seed}',first,cap,seed,args.seconds,args.memory_mb)
                 for first,cap,seed in itertools.product((0,.8),map(int,args.caps.split(',')),map(int,args.seeds.split(',')))]
        for future in as_completed(futures):
            row=future.result();rows.append(row);dump(args.output/'results.json',rows)
            print(json.dumps({k:row.get(k) for k in ('classification','first','cap','seed','stop_reason','external_stop','states','deepest','wall_seconds')}),flush=True)
    print('COMPLETE',sum(r['classification']=='complete' for r in rows),'/',len(rows),flush=True)

if __name__=='__main__':main()
