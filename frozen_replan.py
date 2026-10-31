"""Offline experiment: freeze current colors, plan, replay until refresh.

Snapshots are planning inputs only. Only a complete route validated against
the original seeded map may be exported as an executable solution.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import time

from color_rules import initial_cards,ColorState
from solve_original_batch import export
from solve_verified_map import atomic_json

ROOT=Path(__file__).resolve().parent


class LiveBoard:
    def __init__(self,data):
        self.cards,self.rules=initial_cards(data)
        self.energy=bool(self.rules and self.rules['kind']=='energy_alien_v1')
        if self.energy:
            from energy_rules import EnergyBoard
            self.state=EnergyBoard(self.cards,self.rules)
        else:self.state=ColorState(self.cards,self.rules)
        self.tray=[];self.sequence=[]
        self.parents={c['id']:{p['id'] for p in self.cards if p['layer']>c['layer'] and
            abs(p['rolNum']-c['rolNum'])<8 and abs(p['rowNum']-c['rowNum'])<8} for c in self.cards}

    def snapshot(self):
        # Only current board cards plus virtual initial tray cards. Alien
        # removals may be covered, so replaying them as clicks is invalid.
        levels={}
        for c in self.cards:
            if c['id'] not in self.state.remaining:continue
            frozen={**c,'type':self.state.types[c['id']],'metaType':0,'metaData':0}
            levels.setdefault(str(c['layer']),[]).append(frozen)
        layer=max(c['layer'] for c in self.cards)+1;prefix=[]
        for i,t in enumerate(self.tray):
            id=f'tray-{i}';prefix.append(id)
            levels.setdefault(str(layer),[]).append({'id':id,'type':t,'layer':layer,
                'rolNum':10000+i*8,'rowNum':10000,'metaType':0,'metaData':0})
        return {'levelData':levels,'_frozen_planning_snapshot':True,'_planning_prefix':prefix}

    def play_to_refresh(self,route):
        """Commit only the unchanged segment through its first elimination."""
        before={id:self.state.types[id] for id in self.state.remaining};picked=[]
        for id in route:
            if self.energy:
                event=self.state.move(id);self.tray=list(self.state.tray)
                self.sequence.append(id);picked.append(id)
                if event['crush'] or event['alien']:
                    return {'picked':picked,'elimination':event['crush'],'changed':[],
                        'auto_removed':event['auto_removed'],'alien':event['alien'],
                        'energy_count':event['energy_count'],'remaining':len(self.state.remaining),
                        'tray':list(self.tray)}
                continue
            if id not in self.state.remaining or self.parents[id]&self.state.remaining:
                raise ValueError('Frozen plan contains an unavailable card')
            t=self.state.types[id];new=self.tray+[t];hit=new.count(t)==3
            if hit:new=[v for v in new if v!=t]
            if len(new)>=7:raise ValueError('Frozen plan overflows the real tray')
            self.tray=new;self.state.remove(id,hit);self.sequence.append(id);picked.append(id)
            if hit:
                changed=[id for id in self.state.remaining if before[id]!=self.state.types[id]]
                return {'picked':picked,'elimination':True,'changed':sorted(changed),
                        'remaining':len(self.state.remaining),'tray':list(self.tray)}
        return {'picked':picked,'elimination':False,'changed':[],
                'remaining':len(self.state.remaining),'tray':list(self.tray)}


def check_static_route(snapshot,prefix,route,complete):
    """Independent static replay, including coverage and tray, before use."""
    board=LiveBoard(snapshot)
    if list(route[:len(prefix)])!=list(prefix):raise ValueError('Planner did not extend the current state')
    for id in route:
        board.play_to_refresh([id])
    if complete and (board.state.remaining or board.tray):raise ValueError('Incomplete frozen solution')
    return list(route[len(prefix):])


def native_plan(snapshot,prefix,folder,*,seconds=1,seed=1997,mode='tight-top',
                state_limit=1500000,notify=None,cancel=None):
    folder.mkdir(parents=True,exist_ok=True)
    prefix=snapshot.get('_planning_prefix',prefix)
    mp=folder/'snapshot.json';pp=folder/'prefix.json';raw=folder/'frozen_candidate.json'
    progress=folder/'native_progress.json'
    atomic_json(mp,snapshot);atomic_json(pp,{'sequence':prefix})
    # Isolate each run's artifacts. No previous candidate is accepted after
    # a timeout, cancellation or process failure.
    for path in (raw,Path(str(raw)+'.best.json'),progress):path.unlink(missing_ok=True)
    cmd=[str(ROOT/'FastOriginalSolver.exe'),str(mp),str(raw),mode,'0',str(seconds),str(seed),
         str(state_limit),str(progress),str(pp)]
    start=time.monotonic();stopped=False;hard_limit=False
    with (folder/'process.log').open('w',encoding='utf-8') as log:
        child=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            while child.poll() is None:
                if cancel and cancel():stopped=True;child.terminate();break
                if time.monotonic()-start>seconds+3:hard_limit=True;child.terminate();break
                if notify:
                    try:info=json.loads(progress.read_text(encoding='utf-8'))
                    except (OSError,ValueError):info={}
                    notify(phase='planning',planner=info)
                time.sleep(.1)
            child.wait(timeout=3)
        finally:
            if child.poll() is None:child.kill();child.wait(timeout=3)
    info={'elapsed_seconds':time.monotonic()-start,'exit_code':child.returncode,'seed':seed,'mode':mode}
    if stopped:return dict(info,state='cancelled'),None
    if hard_limit:return dict(info,state='unknown',reason='process_deadline'),None
    if child.returncode not in (0,2):
        raise RuntimeError('Frozen planner process failed; see '+str(folder/'process.log'))
    try:native=json.loads(progress.read_text(encoding='utf-8'))
    except (OSError,ValueError):native={}
    info['native']=native
    complete=child.returncode==0
    source=raw if complete else Path(str(raw)+'.best.json')
    if not source.exists():return dict(info,state='unknown',reason='missing_candidate'),None
    route=json.loads(source.read_text(encoding='utf-8-sig'))['sequence']
    tail=check_static_route(snapshot,prefix,route,complete)
    return dict(info,state='frozen_solved' if complete else 'frozen_partial',planned_cards=len(tail)),tail


def trial(data,folder,*,seconds=30,plan_seconds=1,seed=1997,partial=False,
          mode='tight-top',notify=None,cancel=None,planner=native_plan):
    start=time.monotonic();board=LiveBoard(data);rounds=[];pending=[]
    notify=notify or (lambda **kw:None);state='unknown';reason='budget'
    while board.state.remaining:
        left=seconds-(time.monotonic()-start)
        if left<=0:break
        if cancel and cancel():state='cancelled';reason='requested';break
        if not pending:
            snapshot=board.snapshot();index=len(rounds)
            info,pending=planner(snapshot,list(board.sequence),folder/f'plan_{index:03}',
                seconds=min(plan_seconds,left),seed=seed+index*17,mode=mode,
                notify=notify,cancel=cancel)
            rounds.append(dict(info,step=len(board.sequence),remaining=len(board.state.remaining)))
            if info['state']=='cancelled':state='cancelled';reason='requested';break
            if not pending:reason='no_plan';break
            if not partial and info['state']!='frozen_solved':reason='frozen_full_plan_not_found';break
        event=board.play_to_refresh(pending);pending=pending[len(event['picked']):]
        rounds[-1].setdefault('executed_events',[]).append(event)
        notify(phase='refresh_check',elapsed_seconds=time.monotonic()-start,
            picked=len(board.sequence),remaining=len(board.state.remaining),tray=list(board.tray),
            replan_count=len(rounds),changed=len(event['changed']))
        if event['changed'] or event.get('alien'):
            # Every future pick of the old plan was based on stale colors.
            pending=[]
        if not event['elimination'] and not event.get('alien') and board.state.remaining:
            # A partial frozen route may end before the next elimination.
            # Ask for another snapshot; inability to progress stops the run.
            pending=[]
    else:
        if board.tray:raise ValueError('Real replay ended with nonempty tray')
        state='solved';reason='complete_real_replay'
    info={'state':state,'reason':reason,'elapsed_seconds':time.monotonic()-start,
        'picked':len(board.sequence),'total':len(board.cards),'replans':len(rounds),
        'refreshes':sum(len(r.get('executed_events',[])) for r in rounds),
        'partial_plans_allowed':partial,'rounds':rounds,'sequence':board.sequence}
    answer=None if state!='solved' else {'sequence':board.sequence,'engine':'frozen_replan',
        'mode':'replan_after_color_change','source_label':'当前牌型冻结规划；变色后重算；真实种子完整回放通过'}
    if answer and board.energy:
        answer.update(energy_events=board.state.events,
            source_label='电量牌与种子飞碟规则；飞碟后按实际剩余牌重算；完整回放通过')
    return info,answer


def main():
    p=argparse.ArgumentParser();p.add_argument('--map',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--seconds',type=float,default=60)
    p.add_argument('--plan-seconds',type=float,default=1);p.add_argument('--attempts',type=int,default=1)
    p.add_argument('--allow-partial',action='store_true',help='Use a legal partial frozen plan through the next refresh')
    p.add_argument('--mode',choices=['level-top','random','tight-top','crush-top','symmetry-top'],default='tight-top')
    p.add_argument('--seed',type=int,default=1997);a=p.parse_args()
    if a.seconds<=0 or a.plan_seconds<=0 or a.attempts<=0:p.error('Budgets and attempt count must be positive')
    a.output.mkdir(parents=True,exist_ok=True);data=json.loads(a.map.read_text(encoding='utf-8-sig'))
    digest=hashlib.sha256(a.map.read_bytes()).hexdigest();start=time.monotonic();runs=[];answer=None
    def cancel():
        try:return json.loads((a.output/'frozen_control.json').read_text(encoding='utf-8')).get('action')=='stop'
        except (OSError,ValueError):return False
    for attempt in range(a.attempts):
        left=a.seconds-(time.monotonic()-start)
        if left<=0 or cancel():break
        folder=a.output/f'attempt_{attempt+1:02}';folder.mkdir(parents=True,exist_ok=True)
        def notify(**info):
            atomic_json(a.output/'frozen_status.json',dict(info,attempt=attempt+1,offline=True,
                map_sha256=digest,state=info.get('phase','planning'),total_elapsed_seconds=time.monotonic()-start))
        info,answer=trial(data,folder,seconds=left/(a.attempts-attempt),plan_seconds=a.plan_seconds,
            seed=a.seed+attempt*7919,partial=a.allow_partial,mode=a.mode,notify=notify,cancel=cancel)
        info.update(attempt=attempt+1,map_sha256=digest)
        atomic_json(folder/'trial_result.json',info);runs.append(info)
        if answer:
            notify(phase='validating');info['validated']=export(a.map,answer,a.output)['validated']
            atomic_json(folder/'trial_result.json',info);break
        if info['state']=='cancelled':break
    summary={'state':'solved' if answer else 'cancelled' if cancel() else 'unknown',
        'map_sha256':digest,'elapsed_seconds':time.monotonic()-start,'offline':True,
        'partial_plans_allowed':a.allow_partial,'validated':bool(answer),
        'attempts':[dict((k,v) for k,v in r.items() if k not in ('rounds','sequence')) for r in runs]}
    atomic_json(a.output/'frozen_results.json',summary);atomic_json(a.output/'frozen_status.json',summary)
    print(json.dumps(summary,ensure_ascii=True))
    return 0 if answer else 2


if __name__=='__main__':raise SystemExit(main())
