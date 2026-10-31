"""Persistent candidate prefixes and progressively wider suffix searches.

Only an exhaustive native return proves a prefix has no completion. A time or
memory limit merely defers it; it must never be recorded as an impossibility.
"""
import hashlib
import json
import time


class TailPlanner:
    WINDOWS = (12, 24, 48, 96, 192)
    MAX_ANCHORS = 24

    def __init__(self, path, digest, total, save):
        self.path, self.digest, self.total, self.save_json = path, digest, total, save
        self.anchors = []; self.jobs = {}; self.active = set(); self.dirty = False; self.last_save = 0
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if data.get('version') == 1 and data.get('map_sha256') == digest:
                self.anchors = data['anchors']; self.jobs = data['jobs']
                for job in self.jobs.values():
                    if job['state'] == 'running': job['state'] = 'interrupted'
        except (OSError, ValueError, KeyError): pass

    @staticmethod
    def key(sequence):
        return hashlib.sha256(json.dumps(sequence, separators=(',', ':')).encode()).hexdigest()

    def offer(self, sequence):
        if len(sequence) < max(9, self.total*.65) or len(set(sequence)) != len(sequence): return
        candidate = {'id':self.key(sequence), 'sequence':list(sequence), 'depth':len(sequence)}
        if any(a['id'] == candidate['id'] for a in self.anchors): return
        # Near-identical routes share early checkpoints. Keep a deeper route
        # within that family, and retain independent families for parallel work.
        for a in list(self.anchors):
            common = 0
            for x, y in zip(a['sequence'], sequence):
                if x != y: break
                common += 1
            if common >= min(len(sequence), a['depth'])-12:
                if a['depth'] >= len(sequence): return
                self.anchors.remove(a)
        self.anchors.append(candidate)
        self.anchors.sort(key=lambda a:(-a['depth'], a['id']))
        self.anchors = self.anchors[:self.MAX_ANCHORS]; self.dirty = True

    def disproved(self, prefix):
        for job in self.jobs.values():
            if job['state'] == 'exhausted':
                dead = job['prefix']
                if len(dead) <= len(prefix) and prefix[:len(dead)] == dead: return True
        return False

    def next_task(self):
        for anchor in self.anchors:
            # Rewind chosen steps, while also searching every still-unpicked
            # card. An unfinished 231/258 route rewound 12 fixes 219 steps,
            # leaving 39 cards in the subproblem.
            windows = list(self.WINDOWS) + [anchor['depth']]
            for rewind in sorted(set(min(w, anchor['depth']) for w in windows)):
                fixed = anchor['depth'] - rewind
                if fixed == 0: break  # Unrestricted workers cover the root.
                prefix = anchor['sequence'][:fixed]; key = self.key(prefix)
                if self.disproved(prefix): continue
                job = self.jobs.get(key)
                if key in self.active: break  # Broaden after the narrow job returns.
                if job and job['state'] == 'exhausted': continue
                if job and job['state'] == 'limited' and job.get('attempts', 0) >= 2: continue
                if not job:
                    job = {'prefix':prefix, 'fixed':fixed, 'rewind':rewind,
                           'anchor_depth':anchor['depth'], 'attempts':0, 'state':'pending'}
                    self.jobs[key] = job
                job['attempts'] += 1; job['state'] = 'running'; self.active.add(key); self.dirty = True
                return {'key':key, **job, 'seconds':min(16, 4*2**min(job['attempts']-1,2)),
                        'budget_multiplier':min(2, job['attempts'])}
        # After local windows have been tried, unrestricted exploration can
        # offer a different anchor. Limited jobs remain eligible for later
        # deeper budgets after other anchors/windows have had their turn.
        deferred = [(k,j) for k,j in self.jobs.items() if j['state']=='limited' and k not in self.active
                    and not self.disproved(j['prefix'])]
        if deferred:
            key,job = min(deferred,key=lambda item:(item[1]['attempts'], -item[1]['fixed']))
            job['attempts'] += 1;job['state']='running';self.active.add(key);self.dirty=True
            return {'key':key,**job,'seconds':16,'budget_multiplier':2}
        return None

    def finish(self, task, state, reason=''):
        if not task:return
        key = task['key'];self.active.discard(key)
        job = self.jobs[key]
        job['state'] = state if state in ('exhausted','limited') else 'interrupted'
        job['reason'] = reason; self.dirty = True

    def save(self, force=False):
        now=time.monotonic()
        if self.dirty and (force or now-self.last_save>=.8):
            self.save_json(self.path,{'version':1,'map_sha256':self.digest,'total':self.total,
                                      'anchors':self.anchors,'jobs':self.jobs})
            self.dirty=False;self.last_save=now

    def summary(self, tasks):
        tails = [t for t in tasks if t]
        fixed=[t['fixed'] for t in tails];rewind=[t['rewind'] for t in tails]
        return {'tail_workers':len(tails), 'global_workers':len(tasks)-len(tails),
                'fixed_prefix_min':min(fixed,default=0),'fixed_prefix_max':max(fixed,default=0),
                'backtrack_min':min(rewind,default=0),'backtrack_max':max(rewind,default=0),
                'exhausted_subtrees':sum(j['state']=='exhausted' for j in self.jobs.values()),
                'candidate_routes':len(self.anchors),'cached_tasks':len(self.jobs)}
