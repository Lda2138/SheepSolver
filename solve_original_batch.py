"""Bounded restarts of original selection policies, accelerated by C#.

Only complete answers passing desktop_assistant.validate are exported.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import subprocess
import threading
import time
from desktop_assistant import validate
from color_rules import initial_cards, ColorState

ROOT = Path(__file__).resolve().parent


def export(map_path, answer, output):
    raw = map_path.read_bytes()
    data = json.loads(raw.decode('utf-8-sig'))
    if data.get('_constraint_relaxation') or data.get('_frozen_planning_snapshot'):
        raise ValueError('松弛模型的候选不能导出为可执行答案')
    cards, rules = initial_cards(data)
    energy_events=None
    if rules and rules.get('kind')=='energy_alien_v1':
        from energy_rules import replay
        energy,peak=replay(cards,rules,answer['sequence'],expected_events=answer.get('energy_events'))
        expected=[e['type'] for e in energy.events];energy_events=energy.events
    else:
        runtime = ColorState(cards, rules); tray = []; expected = []
        for id in answer['sequence']:
            t = runtime.types[id]; expected.append(t); tray.append(t)
            crushed = tray.count(t) == 3
            if crushed: tray = [v for v in tray if v != t]
            runtime.remove(id, crushed)
    if answer.get('sequence_types', expected) != expected:
        raise ValueError('求解器与独立变色回放结果不一致')
    peak = validate(cards,answer['sequence'],rules,expected)
    payload = {**answer,'source_label':answer.get('source_label','真实种子完整地图；原项目策略的加速实现'),
               'map_sha256':hashlib.sha256(raw).hexdigest(),'cards':cards,'card_count':len(cards),
               'validated':True,'peak_tray_after_elimination':peak,'desktop_clicks_enabled':False,
               'rules':rules,'sequence_types':expected}
    if energy_events is not None:payload['energy_events']=energy_events
    output.mkdir(parents=True,exist_ok=True)
    (output/'solution.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    template=(ROOT/'assistant_preview.html').read_text(encoding='utf-8')
    (output/'点击预演.html').write_text(template.replace('__PAYLOAD__',json.dumps(payload,ensure_ascii=False).replace('<','\\u003c')),encoding='utf-8')
    return payload


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--map',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--seconds',type=float,default=600)
    p.add_argument('--attempts',type=int,default=200)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    done=threading.Event();lock=threading.Lock();start=time.monotonic();best=0
    def run(i):
        nonlocal best
        if done.is_set() or time.monotonic()-start>a.seconds:return
        first=[0,0.3,0.6,0.8,1][i%5]
        mode='random'
        rawpath=a.output/f'attempt_{i}.json'
        cmd=[str(ROOT/'FastOriginalSolver.exe'),str(a.map),str(rawpath),mode,str(first),'45',str(7919+i*17),'2000000']
        proc=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
        summary=proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else 'no_output'
        import re
        match=re.search(r'deepest=(\d+)',summary)
        with lock:
            if match:best=max(best,int(match[1]))
            status={'attempt':i,'mode':mode,'solve_first':first,'best_progress':best,'summary':summary,'elapsed_seconds':round(time.monotonic()-start,1),'solved':done.is_set()}
            (a.output/'search_status.json').write_text(json.dumps(status),encoding='utf-8')
            print(json.dumps(status),flush=True)
            if proc.returncode==0 and rawpath.exists() and not done.is_set():
                answer=json.loads(rawpath.read_text());export(a.map,answer,a.output);done.set()
                print('COMPLETE_VALIDATED_SOLUTION',flush=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending=[pool.submit(run,i) for i in range(a.attempts)]
        for f in as_completed(pending):f.result()
    if not done.is_set():raise SystemExit(2)


if __name__=='__main__':main()
