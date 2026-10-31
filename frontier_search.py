"""Durable DFS work blocks: continuation plus every unexpanded stack sibling.

The compact blocks share a cutoff path, storing just each sibling's card and
prefix length. No arbitrary tail truncation is needed to resume a bounded job.
"""
import hashlib
import json
import time


class FrontierPlanner:
    def __init__(self,path,digest,total,root_cards,save):
        self.path,self.digest,self.total,self.save_json=path,digest,total,save
        self.blocks=[];self.active={};self.next_id=0;self.completed=0;self.resumed=0;self.serial=0
        self.dirty=True;self.last_save=0;self.pending=0
        try:
            data=json.loads(path.read_text(encoding='utf-8'))
            if data.get('version')==1 and data.get('map_sha256')==digest:
                self.blocks=data['blocks'];self.next_id=data['next_id'];self.completed=data['completed'];self.resumed=data['resumed']
                self.pending=sum(self.block_size(b) for b in self.blocks)
                # Jobs leased at the last save are not known to have finished.
                for task in data.get('active',{}).values():self.add_prefix(task['prefix'],task.get('allowed'))
                return
        except (OSError,ValueError,KeyError):pass
        self.blocks=[{'path':[],'leaf':False,'frames':[{'depth':0,'cards':list(root_cards)}]}]
        self.pending=len(root_cards)

    @staticmethod
    def block_size(block):
        return ((len(block.get('allowed') or []) or 1) if block['leaf'] else 0)+sum(len(f['cards']) for f in block['frames'])

    @staticmethod
    def key(prefix,allowed=None):return hashlib.sha256(json.dumps([prefix,allowed],separators=(',',':')).encode()).hexdigest()

    def add_prefix(self,prefix,allowed=None):
        self.blocks.append({'path':list(prefix),'leaf':True,'frames':[], 'allowed':allowed});self.dirty=True
        self.pending+=len(allowed or []) or 1

    def next_task(self):
        self.serial+=1
        while self.blocks:
            # Usually follow the deepest continuation. Periodically return to
            # old blocks so an unlucky early choice cannot monopolize all work.
            index=0 if self.serial%9==0 else len(self.blocks)-1;block=self.blocks[index]
            allowed=None
            if block['leaf']:
                prefix=block['path'];allowed=block.get('allowed');block['leaf']=False
                self.pending-=len(allowed or []) or 1
            else:
                frames=block['frames']
                while frames and not frames[0]['cards']:frames.pop(0)
                if not frames:self.blocks.pop(index);self.dirty=True;continue
                frame=frames[0]
                if frame['depth']>0 and len(frame['cards'])>=4:
                    # Search sibling alternatives in one native invocation.
                    # They share the replayed parent and transposition table.
                    # Keep root alternatives separate for initial parallelism.
                    allowed=frame['cards'][:8];del frame['cards'][:8]
                    self.pending-=len(allowed)
                    prefix=block['path'][:frame['depth']]
                else:
                    prefix=block['path'][:frame['depth']]+[frame['cards'].pop(0)];self.pending-=1
            key=self.key(prefix,allowed)
            if any(t['key']==key for t in self.active.values()):continue
            self.next_id+=1;task={'id':str(self.next_id),'key':key,'prefix':list(prefix),'fixed':len(prefix),'allowed':allowed}
            self.active[task['id']]=task;self.resumed+=1;self.dirty=True;return task
        return None

    def finish(self,task,state,frontier=None):
        if not task or task['id'] not in self.active:return
        if frontier and frontier.get('map_sha256')==self.digest:
            path=frontier.get('path',[]);base=task['prefix']
            if path[:len(base)]!=base or len(path)>self.total:raise ValueError('Search frontier does not extend leased prefix')
            for frame in frontier.get('frames',[]):
                if not len(base)<=frame['depth']<len(path):raise ValueError('Invalid frontier frame depth')
            self.active.pop(task['id']);self.dirty=True
            self.blocks.append({'path':list(path),'leaf':True,'frames':[
                                {'depth':f['depth'],'cards':list(f['cards'])} for f in frontier.get('frames',[])],
                                'allowed':task.get('allowed') if len(path)==len(base) else None})
            self.pending+=self.block_size(self.blocks[-1])
        else:
            self.active.pop(task['id']);self.dirty=True
            if state in ('exhausted','candidate','solved'):self.completed+=1
            else:self.add_prefix(task['prefix'],task.get('allowed'))

    def save(self,force=False):
        now=time.monotonic()
        if self.dirty and (force or now-self.last_save>=3):
            self.save_json(self.path,{'version':1,'map_sha256':self.digest,'total':self.total,
                                    'blocks':self.blocks,'active':self.active,'next_id':self.next_id,
                                    'completed':self.completed,'resumed':self.resumed})
            self.dirty=False;self.last_save=now

    def summary(self,tasks):
        live=[t for t in tasks if t]
        return {'workers':len(live),'completed_subtrees':self.completed,'resumed_jobs':self.resumed,
                'saved_blocks':len(self.blocks),'pending_branches':self.pending,
                'fixed_min':min((t['fixed'] for t in live),default=0),'fixed_max':max((t['fixed'] for t in live),default=0)}
