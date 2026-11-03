"""Bounded sequential CPU-use comparison on one fixed model; never clicks."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import statistics
from types import SimpleNamespace

from search_resources import ResourceSampler
from solve_constraints import solve_board
from solve_original_batch import export
from solve_verified_map import atomic_json


def benchmark(map_path,output,counts,seconds=30):
    output.mkdir(parents=True,exist_ok=True)
    raw=map_path.read_bytes();data=json.loads(raw.decode('utf-8-sig'))
    digest=hashlib.sha256(raw).hexdigest();results=[]
    process=SimpleNamespace(pid=os.getpid(),_handle=-1)
    affinity=ctypes.c_size_t();system=ctypes.c_size_t()
    kernel=ctypes.windll.kernel32
    kernel.GetProcessAffinityMask(ctypes.c_void_p(-1),ctypes.byref(affinity),ctypes.byref(system))
    identity={'map_sha256':digest,'logical_cpus':os.cpu_count(),
              'affinity_mask':affinity.value,'priority_class':kernel.GetPriorityClass(ctypes.c_void_p(-1)),
              'seconds_each':seconds,'trials':results,'quality_speedup_proven':False}
    for trial,workers in enumerate(counts):
        samples=[];sampler=ResourceSampler();sampler.previous_process[process.pid]=sampler.process_times(process)
        def notify(**kw):
            sample=sampler.sample([process]);sample.update(phase=kw.get('phase'),elapsed_seconds=kw.get('elapsed_seconds',0))
            samples.append(sample)
            atomic_json(output/'status.json',{'trial':trial,'workers':workers,**kw,'resources':sample})
        with (output/f'trial_{trial}_{workers}.log').open('w',encoding='utf-8') as log:
            info,answer=solve_board(data,seconds=seconds,workers=workers,encoding='cumulative',
                notify=notify,log=lambda line:log.write(line+'\n'))
        search=[s for s in samples if s['phase']=='search']
        row={'workers':workers,**info,'search_samples':len(search),
             'solver_cpu_median':round(statistics.median(s['solver_cpu_percent'] for s in search),1) if search else None,
             'system_cpu_median':round(statistics.median(s['system_cpu_percent'] for s in search),1) if search else None,
             'solver_cpu_peak':max((s['solver_cpu_percent'] for s in search),default=0),
             'peak_memory_mb':max((s['solver_memory_mb'] for s in samples),default=0),
             'deterministic_work_per_second':info.get('deterministic_time',0)/max(.001,info['elapsed_seconds'])}
        if answer:
            if hashlib.sha256(map_path.read_bytes()).hexdigest()!=digest:raise ValueError('Map changed during benchmark')
            row['validated']=export(map_path,answer,output/'complete')['validated']
        results.append(row);atomic_json(output/'results.json',identity)
        print(json.dumps(row,ensure_ascii=True),flush=True)
        if answer:break
    return results


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--map',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--seconds',type=float,default=30)
    p.add_argument('--workers',type=int,nargs='+',default=[22,32,22]);a=p.parse_args()
    benchmark(a.map,a.output,a.workers,a.seconds)
