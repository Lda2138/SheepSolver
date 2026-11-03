"""Compare exact short-horizon pruning on the same map, seeds and state cap."""
import json
from pathlib import Path
import subprocess
import time

ROOT=Path(__file__).resolve().parent
MAP=ROOT/'output/auto_capture/sessions/20261009T023904Z-2f3084/map_1.json'
OUT=ROOT/'output/algorithm_benchmark/tight'
OUT.mkdir(parents=True,exist_ok=True)
rows=[]
for seed in (31,1997,7919):
    for mode in ('color-top','two-top','tight-top','symmetry-top'):
        tag=f'{mode}_{seed}';progress=OUT/f'{tag}_progress.json'
        t=time.perf_counter()
        p=subprocess.run([str(ROOT/'FastOriginalSolver.Tight.exe'),str(MAP),str(OUT/f'{tag}.json'),
                          mode,'.8','30',str(seed),'1000000',str(progress)],
                         capture_output=True,text=True,timeout=40,
                         creationflags=subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS)
        if p.returncode not in (0,2):raise RuntimeError(p.stderr)
        row=json.loads(progress.read_text());row.update(seed=seed,wall=time.perf_counter()-t)
        rows.append(row)
        print(json.dumps({k:row[k] for k in ('mode','seed','deepest','nodes','elapsed_seconds','constraint_prunes','symmetry_prunes')}),flush=True)
        (OUT/'results.json').write_text(json.dumps(rows,indent=2))
