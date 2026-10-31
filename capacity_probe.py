"""Relaxed tray-capacity probe for ordinary boards left UNKNOWN.

Diagnostic only. Routes found with more than six settled slots break the real
rule: they are never hints, answers or proof sources. Solving at seven slots
only says the board is about one slot short; it is not a prop plan.
"""
import json
from pathlib import Path
import subprocess
import time

ROOT=Path(__file__).resolve().parent
# (capacity, seed, solve_first) in priority order. On c7e824, seven slots
# needed about 5.5M states (~145 bytes each); eight slots under 1M.
TRIALS=((7,4,.8),(8,1997,0),(7,2,.8),(7,1997,.7),(8,2,.8))
BYTES_PER_STATE=160
TARGET_STATES=6500000


def relaxed_clear(data,route,capacity):
    """Independent replay: cover rule, settled tray <= capacity, all cleared."""
    cards=[{**c,'layer':int(k)} for k,layer in data['levelData'].items() for c in layer]
    if len(route)!=len(cards) or set(route)!={c['id'] for c in cards}:return False
    covers={a['id']:{b['id'] for b in cards if b['layer']>a['layer']
        and abs(b['rolNum']-a['rolNum'])<8 and abs(b['rowNum']-a['rowNum'])<8} for a in cards}
    types={c['id']:c['type'] for c in cards};taken=set();tray={}
    for id in route:
        if not covers[id]<=taken:return False
        taken.add(id);tray[types[id]]=(tray.get(types[id],0)+1)%3
        if sum(tray.values())>capacity:return False
    return not any(tray.values())


def start(map_path,prefix_path,folder,seconds):
    from search_resources import available_memory
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    # Fewer well-funded trials beat many starved ones: run only as many as
    # memory allows at the target state count, always at least one.
    total=max(0,available_memory()-3*1024**3)*.75/BYTES_PER_STATE
    count=max(1,min(len(TRIALS),int(total//TARGET_STATES)))
    states=max(50000,min(10000000,int(total/count)))
    handles=[]
    for capacity,seed,first in TRIALS[:count]:
        out=folder/f'cap{capacity}_seed{seed}.json'
        for stale in (out,Path(str(out)+'.best.json')):stale.unlink(missing_ok=True)
        log=Path(str(out)+'.log').open('w',encoding='utf-8')
        process=subprocess.Popen([str(ROOT/'StaticFastSolver.exe'),str(map_path),str(out),'tight-top',str(first),
            str(seconds),str(seed),str(states),str(out)+'.progress.json',str(prefix_path),'512',str(capacity)],
            cwd=ROOT,stdout=log,stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS)
        handles.append({'capacity':capacity,'seed':seed,'solve_first':first,'state_limit':states,
            'process':process,'out':out,'log':log})
    return {'handles':handles,'deadline':time.monotonic()+seconds+5}


def finish(probe,data,wait=True):
    """Collect results; stop early once seven slots succeed. Never raises on solver errors."""
    handles=probe['handles'];solved=set()
    try:
        while True:
            for h in handles:
                if 'result' in h or h['process'].poll() is None:continue
                route=[]
                try:route=json.loads(h['out'].read_text(encoding='utf-8-sig')).get('sequence',[]) if h['out'].exists() else []
                except (OSError,ValueError):pass
                h['result']='complete' if route and relaxed_clear(data,route,h['capacity']) else 'unknown'
                if h['result']=='complete':solved.add(h['capacity'])
            running=[h for h in handles if 'result' not in h]
            if not running or 7 in solved or not wait or time.monotonic()>probe['deadline']:break
            # Seven-slot routes also satisfy eight; stop redundant eight-slot runs.
            if 8 in solved:
                for h in running:
                    if h['capacity']==8:h['process'].terminate()
            time.sleep(.2)
    finally:
        for h in handles:
            if h['process'].poll() is None:h['process'].terminate()
        for h in handles:
            try:h['process'].wait(timeout=5)
            except subprocess.TimeoutExpired:pass
            h['log'].close();h.setdefault('result','stopped')
    smallest=min(solved,default=None)
    return {'kind':'relaxed_capacity_diagnostic','executable':False,'smallest_solved_capacity':smallest,
        'trials':[{k:h[k] for k in ('capacity','seed','solve_first','state_limit','result')} for h in handles]}


def describe(result):
    smallest=(result or {}).get('smallest_solved_capacity')
    if smallest==7:return '消除后允许留7张时找到路线，可尝试移出；正常七格牌槽下可解性仍未知（仅诊断）'
    if smallest==8:return '消除后允许留8张时找到路线，不能据此判断正常七格牌槽无解（仅诊断）'
    if result:
        tried='/'.join(str(c) for c in sorted({t['capacity'] for t in result['trials']}))
        return f'放宽到{tried}格也未在探测预算内解出（仅诊断）'
    return ''
