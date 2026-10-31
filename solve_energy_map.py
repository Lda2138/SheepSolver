"""Bounded offline exact energy/alien search and independent source replay."""
import json
import hashlib
from pathlib import Path
import subprocess
import time

from color_rules import initial_cards
from solve_original_batch import export

ROOT=Path(__file__).resolve().parent


def run_energy(a,data):
    from solve_verified_map import atomic_json
    cards,rules=initial_cards(data);start=time.monotonic();digest=hashlib.sha256(a.map.read_bytes()).hexdigest()
    identity={'rules':'energy_alien_v1','engine':'energy_search','map_sha256':digest,
        'session':data.get('_acquisition',{}).get('session',''),'total':len(cards),'performance':a.performance}
    prepared=a.output/'energy_input.json';atomic_json(prepared,{'cards':cards,'rules':rules})
    status=a.output/'search_status.json';control=a.output/'search_control.json'
    previous=a.output/'energy_generation.json'
    try:old=json.loads(previous.read_text(encoding='utf-8'))
    except (OSError,ValueError):old={}
    generation=old.get('generation',0)+1 if old.get('map_sha256')==digest else 1
    atomic_json(previous,dict(map_sha256=digest,generation=generation))
    def cancelled():
        try:return json.loads(control.read_text(encoding='utf-8')).get('action')=='stop'
        except (OSError,ValueError):return False
    attempts=3 if a.time_limit>=6 else 1;best=0;total_nodes=0
    for attempt in range(attempts):
        left=a.time_limit-(time.monotonic()-start)
        if left<=0 or cancelled():break
        raw=a.output/f'energy_candidate_{generation}_{attempt}.json'
        progress=a.output/f'energy_native_{generation}_{attempt}.json'
        budget=left/(attempts-attempt)
        cmd=[str(ROOT/'EnergySearch.exe'),str(prepared),str(raw),str(progress),str(budget),str(1997+generation*7919+attempt*17)]
        with (a.output/f'energy_process_{generation}_{attempt}.log').open('w',encoding='utf-8') as log:
            child=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                while child.poll() is None:
                    if cancelled():child.terminate();break
                    if time.monotonic()-start>a.time_limit+3:child.terminate();break
                    try:native=json.loads(progress.read_text(encoding='utf-8'))
                    except (OSError,ValueError):native={}
                    best=max(best,native.get('deepest',0))
                    atomic_json(status,dict(native,**identity,state='searching',deepest=best,
                        elapsed_seconds=time.monotonic()-start,nodes=total_nodes+native.get('nodes',0),
                        nodes_per_second=round((total_nodes+native.get('nodes',0))/max(.01,time.monotonic()-start)),target_workers=1,
                        attempt=attempt+1,round=generation,workers=1,
                        message='正在按电量消耗、集满阈值与真实种子飞碟规则搜索完整答案'))
                    time.sleep(.2)
                child.wait(timeout=3)
            finally:
                if child.poll() is None:child.kill();child.wait(timeout=3)
        try:native=json.loads(progress.read_text(encoding='utf-8'))
        except (OSError,ValueError):native={}
        total_nodes+=native.get('nodes',0);best=max(best,native.get('deepest',0))
        if child.returncode==0 and raw.exists():
            answer=json.loads(raw.read_text(encoding='utf-8-sig'))
            payload=export(a.map,answer,a.output)
            atomic_json(status,dict(identity,state='solved',elapsed_seconds=time.monotonic()-start,
                deepest=len(cards),nodes=total_nodes,workers=0,validated=True,
                nodes_per_second=0,target_workers=1,
                alien_activations=sum(e['alien'] for e in payload['energy_events']),
                message='电量牌完整答案已通过源地图、飞碟目标与牌槽回放验证'))
            return 0
        if not cancelled() and child.returncode not in (0,2):
            atomic_json(status,dict(identity,state='error',message='电量规则求解进程异常；请查看 energy_process 日志'))
            raise RuntimeError('Energy search process failed')
    atomic_json(status,dict(identity,state='cancelled' if cancelled() else 'limited',elapsed_seconds=time.monotonic()-start,
        deepest=best,nodes=total_nodes,workers=0,round=generation,
        nodes_per_second=0,target_workers=1,
        message='已停止电量牌求解' if cancelled() else '本轮电量牌搜索尚未找到完整答案；规则已支持，可继续搜索'))
    return 2
