"""Reproducible bounded benchmark. No capture, screenshots or game input.

Each child receives only a clean map and an empty output directory. The parent
validates complete routes; unknown and unverified UNSAT claims stay separate.
"""
import argparse
from collections import Counter,defaultdict,deque
import hashlib
import html
import json
import os
from pathlib import Path
import random
import statistics
import subprocess
import sys
import time

from truth_dataset import clean,dump,flatten,generate,geometry_hash,read,sha,verify_route

ROOT=Path(__file__).resolve().parent
SOLVERS=('upstream_normal','tight_top','static_beam','cp_sat')
LABELS={'upstream_normal':'原版 Python normal','tight_top':'tight-top 原生策略',
        'static_beam':'静态束搜索 balance','cp_sat':'CP-SAT cumulative'}
COHORTS={'planted':'植入解（已知可解）','shuffled':'随机重排（真值未知）','real':'原始世界关（真值未知）'}


def optional(path):
    try:return read(path)
    except (OSError,ValueError):return {}


def python_command():return getattr(sys,'_base_executable',sys.executable)


def child_environment():
    return {**os.environ,'PYTHONHASHSEED':'0','PYTHONIOENCODING':'utf-8',
        'OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','NUMEXPR_NUM_THREADS':'1',
        'PYTHONPATH':os.pathsep.join([str(ROOT),str(Path(sys.prefix)/'Lib/site-packages')])}


def cp_worker(mp,out,seconds):
    # This entry point does not load a manifest or a witness file.
    from solve_constraints import solve_board
    data=read(mp);out.mkdir(parents=True,exist_ok=True)
    with (out/'solver.log').open('w',encoding='utf-8') as log:
        info,answer=solve_board(data,seconds=seconds,workers=1,encoding='cumulative',
            model_path=out/'model.pbtxt',log=lambda line:log.write(line+'\n'))
    dump(out/'solver_result.json',info)
    if answer:dump(out/'answer.json',answer)
    return 0 if answer else 2


def solver_command(solver,mp,out,seconds,seed=1997):
    if solver=='cp_sat':
        return [python_command(),str(Path(__file__).resolve()),'worker','--map',str(mp),
            '--output',str(out),'--seconds',str(seconds)]
    if solver=='upstream_normal':
        return [python_command(),str(ROOT/'original_snapshot.py'),'worker','--map',str(mp),
            '--output',str(out/'answer.json'),'--progress',str(out/'progress.json'),
            '--seconds',str(seconds),'--mode','normal','--solve-first','.8','--seed',str(seed)]
    mode='tight-top' if solver=='tight_top' else 'static-beam-balance'
    return [str(ROOT/'StaticFastSolver.exe'),str(mp),str(out/'answer.json'),mode,'.8' if solver=='tight_top' else '0',
        str(seconds),str(seed),'40000000',str(out/'progress.json'),str(out/'prefix.json'),'2048','6']


def audit_dataset(folder):
    manifest=read(folder/'manifest.json');counts=Counter({int(t):n for t,n in manifest['type_counts'].items()})
    for case in manifest['cases']:
        mp=folder/case['map'];data=read(mp)
        if sha(mp)!=case['map_sha256'] or set(data)!={'levelData'}:raise ValueError('Modified or nonblind solver input')
        if geometry_hash(data)!=manifest['geometry_sha256']:raise ValueError('Geometry mismatch')
        if Counter(c['type'] for c in flatten(data))!=counts:raise ValueError('Type counts mismatch')
        if case['truth']=='solvable':
            wp=folder/case['witness'];witness=read(wp)
            if sha(wp)!=case['witness_sha256'] or witness['map_sha256']!=sha(mp):raise ValueError('Witness changed')
            verify_route(data,witness['sequence'])
            from desktop_assistant import validate
            validate(flatten(data),witness['sequence'])
    return manifest


def protocol_for(folder,seconds,parallel,memory_mb,solvers):
    sources=['truth_dataset.py','truth_benchmark.py','solve_constraints.py','constraint_model.py',
        'StaticFastSolver.exe','original_snapshot.py','business/SheepSolver.py','core/tool/GamePoolController.py',
        'core/pool/OperationPool.py','core/pool/ResidualPool.py','core/card/CardContainer.py']
    import ortools
    protocol={'version':1,'manifest_sha256':sha(folder/'manifest.json'),'seconds_per_solver':seconds,
        'parallel_jobs':parallel,'threads_per_solver':1,'memory_limit_mb':memory_mb,'solver_seed':1997,
        'solvers':list(solvers),'python_version':sys.version,'ortools_version':ortools.__version__,
        'logical_cpus':os.cpu_count(),'source_sha256':{name:sha(ROOT/name) for name in sources},
        'native_state_limit':40000000,'beam_width':2048,'timing':'solver budget excludes Python startup; wall and CPU time are recorded separately',
        'witness_policy':'Parent only. No witness, hint, fixed route, conflict cache or manifest is passed to a solver.'}
    protocol['fingerprint']=hashlib.sha256(json.dumps(protocol,sort_keys=True).encode()).hexdigest()
    return protocol


def classify(solver,case,data,out,returncode,reason=None):
    info=optional(out/('solver_result.json' if solver=='cp_sat' else 'progress.json'))
    candidate=optional(out/'answer.json');result={'state':'unknown','raw_solver_state':info.get('state'),
        'solver_status':info.get('solver_status'),'solver_seconds':candidate.get('elapsed_seconds',info.get('elapsed_seconds')),
        'reason':reason,'unsat_claim':False,'certified_unsat':False}
    if reason:return result
    if returncode not in (0,2):return dict(result,state='error',reason='process_failed')
    if candidate:
        try:
            route=candidate['sequence'];metrics=verify_route(data,route)
            from desktop_assistant import validate
            validate(flatten(data),route)
        except (ValueError,KeyError,TypeError) as exc:return dict(result,state='error',reason='invalid_candidate',detail=str(exc))
        return dict(result,state='solved',validated=True,solution_metrics=metrics)
    if returncode==0:return dict(result,state='error',reason='missing_complete_answer')
    claim=(solver=='cp_sat' and info.get('state')=='infeasible') or (solver in ('upstream_normal','tight_top') and info.get('state')=='exhausted')
    if claim:
        result.update(state='model_unsat' if solver=='cp_sat' else 'unsat_claim',unsat_claim=True)
        if case['truth']=='solvable':result.update(state='contradiction',reason='unsat_claim_on_verified_planted_map')
    return result


def summarize(manifest,rows,protocol):
    groups=[]
    for cohort in COHORTS:
        cases=[c for c in manifest['cases'] if c['cohort']==cohort]
        if not cases:continue
        for solver in protocol['solvers']:
            selected=[r for r in rows if r['cohort']==cohort and r['solver']==solver];solved=[r for r in selected if r['state']=='solved']
            times=[r['solver_seconds'] for r in solved if isinstance(r.get('solver_seconds'),(int,float))]
            counts=Counter(r['state'] for r in selected)
            groups.append({'cohort':cohort,'solver':solver,'cases':len(cases),'completed':len(selected),'solved':len(solved),
                'pending':len(cases)-len(selected),'unknown':counts['unknown'],'model_unsat':counts['model_unsat'],
                'unsat_claim':counts['unsat_claim'],'errors':counts['error']+counts['contradiction'],
                'median_solution_seconds':statistics.median(times) if times else None,
                'cpu_seconds':sum(r.get('cpu_seconds',0) for r in selected),
                'peak_private_mb':max((r.get('peak_private_mb',0) for r in selected),default=0)})
    bounds=[]
    for cohort in ('shuffled','real'):
        ids={c['id'] for c in manifest['cases'] if c['cohort']==cohort}
        if not ids:continue
        solved={r['case_id'] for r in rows if r['case_id'] in ids and r['state']=='solved'}
        certified={r['case_id'] for r in rows if r['case_id'] in ids and r.get('certified_unsat')}
        bounds.append({'cohort':cohort,'cases':len(ids),'verified_solvable_cases':len(solved),
            'certified_unsat_cases':len(certified),'unknown_cases':len(ids-solved-certified),
            'solvability_lower_bound':len(solved)/len(ids),'solvability_upper_bound':1-len(certified)/len(ids)})
    return {'total_runs':len(manifest['cases'])*len(protocol['solvers']),'completed_runs':len(rows),
        'groups':groups,'sample_solvability_bounds':bounds,'errors':sum(r['state'] in ('error','contradiction') for r in rows)}


def write_report(folder,manifest,rows,protocol,state='running'):
    summary=summarize(manifest,rows,protocol);summary['state']=state;dump(folder/'summary.json',summary)
    lines=['# 世界关同布局真值基准','',f"状态：{state}；完成 {summary['completed_runs']} / {summary['total_runs']} 次。",'',
        f"每张每算法 {protocol['seconds_per_solver']} 秒，单路，最多 {protocol['parallel_jobs']} 个独立任务并行；每任务内存上限 {protocol['memory_limit_mb']} MB。",'',
        '所有算法使用清洁地图、固定设置和新进程。通关见证仅由主进程用于生成验收，未传给求解器。', '',
        '| 样本组 | 算法 | 已测/总数 | 完整解 | 未知 | 模型无解声明 | 错误 | 出解耗时中位数 |',
        '|---|---|---:|---:|---:|---:|---:|---:|']
    for g in summary['groups']:
        timing=f"{g['median_solution_seconds']:.3f} s" if g['median_solution_seconds'] is not None else '—'
        lines.append(f"| {COHORTS[g['cohort']]} | {LABELS[g['solver']]} | {g['completed']}/{g['cases']} | {g['solved']} | {g['unknown']} | {g['model_unsat']+g['unsat_claim']} | {g['errors']} | {timing} |")
    lines+=['','耗时中位数只覆盖已找到完整解的样本，不能忽略其解出率来排名。内存/硬时间上限导致的停止记为未知。', '',
        '## 随机样本的真值边界','']
    for b in summary['sample_solvability_bounds']:
        lines.append(f"- {COHORTS[b['cohort']]}：至少 {b['verified_solvable_cases']}/{b['cases']} 张已找到合法完整解；独立无解证书 {b['certified_unsat_cases']} 张。这批样本可解比例范围为 {100*b['solvability_lower_bound']:.1f}%～{100*b['solvability_upper_bound']:.1f}%。")
    lines+=['','CP-SAT 的 INFEASIBLE 和原生搜索的 exhausted 单独列为模型/算法声明；未附独立证书时，不用于收紧上述上界。束搜索截断不表示无解。', '',
        '## 分层植入样本','', '| 槽上限档位 | 算法 | 完整解/已测 |','|---|---|---:|']
    capacities=sorted({c['profile']['capacity'] for c in manifest['cases'] if c['cohort']=='planted'})
    for cap in capacities:
        ids={c['id'] for c in manifest['cases'] if c['cohort']=='planted' and c['profile']['capacity']==cap}
        for solver in protocol['solvers']:
            sub=[r for r in rows if r['case_id'] in ids and r['solver']==solver]
            lines.append(f"| {cap} | {LABELS[solver]} | {sum(r['state']=='solved' for r in sub)}/{len(sub)} |")
    lines+=['','## 解释限制','',
        '- 植入图全部已知可解，但植入分布有偏，不能代表所有可解图；也不是真实解出率的严格上界。',
        '- 随机图只在既定布局、既定牌型数量下均匀重排，不代表游戏服务器的真实出题分布。',
        '- 大量未知只能说明预算内未判定，不能据此声称大部分地图无解。',
        '- 这是每种生成组20张的小规模试验；并行负载、CPU调度和进程启动会影响测时，CPU时间与墙钟时间均保留。',
        '- 两张原始世界关单独列出，不混入随机抽样分母；既有每日关布局不同，不作为匹配样本。',
        '- tight-top 是现有编译加速策略；原版Python normal另列，不能混称为同一个实现。','',
        '数据索引：manifest.json；完整配置：protocol.json；逐次结果和日志：runs/；完整结果列表：results.json；隐藏见证：private/witnesses/。']
    (folder/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    table=[]
    for g in summary['groups']:
        rate=g['solved']/max(1,g['completed']);table.append('<tr><td>'+html.escape(COHORTS[g['cohort']])+'</td><td>'+html.escape(LABELS[g['solver']])+f"</td><td>{g['completed']}/{g['cases']}</td><td><div class='bar' style='width:{rate*100:.1f}%'>&nbsp;</div>{g['solved']} 完整解</td><td>{g['unknown']}</td><td>{g['model_unsat']+g['unsat_claim']}</td><td>{g['errors']}</td></tr>")
    refresh='<meta http-equiv="refresh" content="5">' if state=='running' else ''
    page='<!doctype html><html lang="zh-CN"><meta charset="utf-8">'+refresh+'<title>世界关真值基准</title><style>body{font:16px system-ui;margin:40px;background:#f7f8f3;color:#20392b}table{border-collapse:collapse;width:100%;background:white}td,th{padding:12px;border-bottom:1px solid #ddd;text-align:left}.bar{background:#96c7a5;min-width:0;max-width:180px;height:6px}pre{white-space:pre-wrap;background:white;padding:20px}h1{font-size:30px}</style><h1>世界关同布局真值基准</h1>'+f"<p>{html.escape(state)} · {summary['completed_runs']} / {summary['total_runs']} · 每次 {protocol['seconds_per_solver']} 秒 / 单路</p>"+'<p>见证不传给求解器。完整解均独立回放；未知不能当无解。</p><table><tr><th>组别</th><th>算法</th><th>已测</th><th>完整解</th><th>未知</th><th>模型无解声明</th><th>错误</th></tr>'+''.join(table)+'</table><h2>完整说明与结果</h2><pre>'+html.escape('\n'.join(lines))+'</pre></html>'
    (folder/'report.html').write_text(page,encoding='utf-8');dump(folder/'results.json',rows)
    return summary


def run(folder,seconds=10,parallel=12,memory_mb=1536,solvers=SOLVERS):
    folder=folder.resolve();manifest=audit_dataset(folder);protocol=protocol_for(folder,seconds,parallel,memory_mb,solvers)
    old=optional(folder/'protocol.json')
    if old and old!=protocol:raise ValueError('Existing run uses another protocol; use a fresh dataset directory')
    dump(folder/'protocol.json',protocol);by_id={c['id']:c for c in manifest['cases']}
    tasks=[(c,solver) for c in manifest['cases'] for solver in solvers];random.Random(71297).shuffle(tasks)
    pending=deque();rows=[]
    for case,solver in tasks:
        result_path=folder/'runs'/case['id']/solver/'result.json';existing=optional(result_path)
        if existing:
            if existing.get('protocol_fingerprint')!=protocol['fingerprint'] or existing.get('map_sha256')!=case['map_sha256']:raise ValueError('Stale result')
            if existing['state']=='solved':verify_route(read(folder/case['map']),read(result_path.parent/'answer.json')['sequence'])
            rows.append(existing)
        else:pending.append((case,solver))
    from search_resources import ResourceSampler,available_memory
    sampler=ResourceSampler();active=[];state='running';total=len(tasks)
    try:
        while pending or active:
            user_stopped=(folder/'STOP').exists()
            stopped=user_stopped or state=='contradiction'
            if user_stopped:state='stopped';pending.clear()
            while pending and len(active)<parallel and available_memory()>2*1024**3 and state=='running':
                case,solver=pending.popleft();out=folder/'runs'/case['id']/solver;out.mkdir(parents=True,exist_ok=True)
                # An incomplete prior job must not supply an old answer/status.
                for name in ('answer.json','progress.json','solver_result.json'):(out/name).unlink(missing_ok=True)
                dump(out/'prefix.json',{'sequence':[]});mp=folder/case['map']
                command=solver_command(solver,mp,out,seconds);log=(out/'process.log').open('w',encoding='utf-8')
                dump(out/'invocation.json',{'argv':command,'cwd':str(out),'threads':1,'map_sha256':case['map_sha256'],
                    'witness_passed':False,'protocol_fingerprint':protocol['fingerprint']})
                process=subprocess.Popen(command,cwd=out,env=child_environment(),stdout=log,stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                active.append({'case':case,'solver':solver,'out':out,'process':process,'log':log,'start':time.monotonic(),
                    'peak_ws':0,'peak_private':0,'reason':None})
            changed=False
            for job in list(active):
                p=job['process'];ws,private=sampler.process_memory(p)
                job['peak_ws']=max(job['peak_ws'],ws);job['peak_private']=max(job['peak_private'],private)
                elapsed=time.monotonic()-job['start']
                if p.poll() is None:
                    if stopped:job['reason']='user_stop' if user_stopped else 'stopped_after_contradiction'
                    elif private>memory_mb*1024**2:job['reason']='memory_limit'
                    elif elapsed>seconds+8:job['reason']='hard_wall_limit'
                    elif available_memory()<1024**3:job['reason']='system_memory_pressure'
                    if job['reason']:p.terminate()
                    else:continue
                p.wait(timeout=5);job['log'].close();active.remove(job)
                case=job['case'];result=classify(job['solver'],case,read(folder/case['map']),job['out'],p.returncode,job['reason'])
                result.update(case_id=case['id'],cohort=case['cohort'],solver=job['solver'],dataset_truth=case['truth'],
                    map_sha256=case['map_sha256'],protocol_fingerprint=protocol['fingerprint'],process_exit=p.returncode,
                    wall_seconds=round(time.monotonic()-job['start'],4),cpu_seconds=round(sampler.process_times(p),4),
                    peak_working_set_mb=round(job['peak_ws']/1024**2,1),peak_private_mb=round(job['peak_private']/1024**2,1))
                dump(job['out']/'result.json',result);rows.append(result);changed=True
                if result['state']=='contradiction':state='contradiction';pending.clear();stopped=True
            if changed:
                summary=write_report(folder,manifest,rows,protocol,state)
                print(json.dumps({'completed':len(rows),'total':total,'state':state,'solved':sum(r['state']=='solved' for r in rows)},ensure_ascii=True),flush=True)
            dump(folder/'progress.json',{'state':state,'completed':len(rows),'total':total,
                'active':[{'case_id':j['case']['id'],'solver':j['solver'],'elapsed_seconds':round(time.monotonic()-j['start'],1)} for j in active]})
            if not active and pending and available_memory()<=2*1024**3:raise RuntimeError('Insufficient free memory to start one bounded job')
            time.sleep(.1)
        if state=='running':state='complete'
    except BaseException:
        state='interrupted';raise
    finally:
        for job in active:
            if job['process'].poll() is None:job['process'].terminate()
        for job in active:job['process'].wait(timeout=5);job['log'].close()
        summary=write_report(folder,manifest,rows,protocol,state)
        dump(folder/'progress.json',{'state':state,'completed':len(rows),'total':total,'active':[]})
    return summary


def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='action',required=True)
    g=sub.add_parser('generate');g.add_argument('--source',type=Path,required=True);g.add_argument('--output',type=Path,required=True)
    g.add_argument('--count',type=int,default=20);g.add_argument('--seed',type=int,default=20261029);g.add_argument('--original',type=Path,action='append',default=[])
    r=sub.add_parser('run');r.add_argument('--dataset',type=Path,required=True);r.add_argument('--seconds',type=float,default=10)
    r.add_argument('--parallel',type=int,default=12);r.add_argument('--memory-mb',type=int,default=1536);r.add_argument('--solvers',nargs='+',choices=SOLVERS,default=SOLVERS)
    w=sub.add_parser('worker');w.add_argument('--map',type=Path,required=True);w.add_argument('--output',type=Path,required=True);w.add_argument('--seconds',type=float,required=True)
    a=p.parse_args()
    if a.action=='generate':
        m=generate(a.source,a.output,a.count,a.seed,a.original);print(json.dumps({'cases':len(m['cases']),'geometry_sha256':m['geometry_sha256']}));return 0
    if a.action=='worker':return cp_worker(a.map,a.output,a.seconds)
    print(json.dumps(run(a.dataset,a.seconds,a.parallel,a.memory_mb,a.solvers),ensure_ascii=True));return 0


if __name__=='__main__':raise SystemExit(main())
