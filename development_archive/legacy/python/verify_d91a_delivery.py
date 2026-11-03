"""Reproduce delivery and no-input C# replay; optional multi-seed regression."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from pathlib import Path
import subprocess
from experiment_d91a import trial
from static_plan import plan_current
from truth_dataset import read,dump,verify_route

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'output/d91a_solved'
MAP=ROOT/'output/world_handoff_20261029_aec907/current_map.json'

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--regression',action='store_true');args=ap.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    path,answer=plan_current(MAP,[],OUT/'fresh_application_run',seconds=30,performance='max')
    metrics=verify_route(read(MAP),answer['sequence']);dump(OUT/'independent_replay.json',metrics)
    dump(OUT/'solution.json',answer)
    # Coordinates only exercise the execution validator's bounds; this is NOT
    # a calibrated click plan. --validate-only exits before window lookup/input.
    plan={'solution_path':str(path),'map_sha256':answer['map_sha256'],
          'positions':{c['id']:[40+c['rolNum']*3,160+c['rowNum']*3] for c in answer['cards']},'anchors':[],
          'validation_only':True,'coordinates_screen_aligned':False}
    dump(OUT/'validation_only_plan.json',plan)
    p=subprocess.run([str(ROOT/'SolverPlayback.exe'),str(OUT/'validation_only_plan.json'),'--validate-only'],capture_output=True,text=True,encoding='utf-8-sig',timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
    dump(OUT/'csharp_validation.json',{'returncode':p.returncode,'stdout':p.stdout.strip(),'stderr':p.stderr,'game_input_sent':False,'coordinates_screen_aligned':False})
    if p.returncode:raise RuntimeError(p.stderr)
    session=ROOT/'output/auto_capture/sessions/20261029T103253Z-aec907'
    # Populate only the exact map's normal solution cache. No playback starts.
    if (session/'map_1.json').read_bytes()!=MAP.read_bytes():raise ValueError('Session map changed')
    cached,_=plan_current(session/'map_1.json',[],session/'level_1_static',seconds=30,performance='max')
    dump(OUT/'delivery.json',{'state':'complete','map_sha256':answer['map_sha256'],'steps':len(answer['sequence']),
        'metrics':metrics,'solution_path':str(OUT/'solution.json'),'application_cache':str(cached),'game_input_sent':False})
    print('DELIVERED',metrics,p.stdout.strip(),flush=True)
    if args.regression:
        base=ROOT/'output/truth_benchmark_20261029';manifest=read(base/'manifest.json');rows=[]
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures={pool.submit(trial,ROOT/'StaticFastSolver.exe',base/case['map'],OUT/'regression'/case['id']/str(seed),.8,40000000,seed,5,900):case
                     for case in manifest['cases'] for seed in (1997,7)}
            for future in as_completed(futures):
                case=futures[future];row=future.result();row.update(case_id=case['id'],cohort=case['cohort'],truth=case['truth']);rows.append(row)
                dump(OUT/'regression_results.json',rows)
        groups={}
        for row in rows:
            key=f"{row['cohort']}_seed_{row['seed']}";counts=groups.setdefault(key,Counter());counts['runs']+=1;counts[row['classification']]+=1
            if row['truth']=='solvable' and row['classification']!='complete':counts['positive_timeout']+=1
        dump(OUT/'regression_summary.json',groups);print('REGRESSION',groups,flush=True)

if __name__=='__main__':main()
