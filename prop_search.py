"""One-use "move out" (移出) fallback for ordinary boards.

Real rule model (to confirm on device): a new tile is inserted right after the
last tile of the same type, else appended; three of a type vanish; seven tiles
after settling loses. Move-out takes the leftmost three tray tiles into a side
row; each may later be clicked back individually.

Reduction to the native solver: after move-out at step k, the rest equals a
board with each moved tile as an isolated, uncovered tile, plus phantom picks
so tray residues match (removing one tile of type x == adding two, mod 3).
Every native result is converted back and replayed under the real rules.
"""
import argparse
import glob
import json
from pathlib import Path
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE=Path(__file__).resolve().parent
EXE=HERE/'PropFastSolver.exe'
MOVE='MOVE_OUT'


def board(data):
    cards=[{**c,'layer':int(k)} for k,layer in data['levelData'].items() for c in layer]
    covers={a['id']:{b['id'] for b in cards if b['layer']>a['layer']
        and abs(b['rolNum']-a['rolNum'])<8 and abs(b['rowNum']-a['rowNum'])<8} for a in cards}
    return {c['id']:c['type'] for c in cards},covers


class Game:
    def __init__(self,types,covers):
        self.types,self.covers=types,covers
        self.taken=set();self.tray=[];self.buffer=[];self.used=False;self.peak=0

    def pick(self,id):
        if id in self.buffer:self.buffer.remove(id)
        elif id in self.taken or id not in self.types:raise ValueError(f'bad tile {id}')
        elif not self.covers[id]<=self.taken:raise ValueError(f'covered {id}')
        else:self.taken.add(id)
        t=self.types[id];same=[i for i,x in enumerate(self.tray) if self.types[x]==t]
        self.tray.insert(same[-1]+1 if same else len(self.tray),id)
        if len(same)==2:self.tray=[x for x in self.tray if self.types[x]!=t]
        if len(self.tray)>=7:raise ValueError('tray full')
        self.peak=max(self.peak,len(self.tray))

    def move_out(self):
        if self.used or not self.tray:raise ValueError('move-out unavailable')
        self.buffer=self.tray[:3];self.tray=self.tray[3:];self.used=True

    def won(self):
        return len(self.taken)==len(self.types) and not self.tray and not self.buffer


def replay(types,covers,actions):
    g=Game(types,covers)
    for a in actions:
        g.move_out() if a==MOVE else g.pick(a)
    return g


def legal_prefix(types,covers,route):
    """Longest prefix of route legal under real rules without props."""
    g=Game(types,covers)
    for k,id in enumerate(route):
        try:g.pick(id)
        except ValueError:return k
    return len(route)


def synthetic(data,types,covers,prefix):
    """Map plus checkpoint for 'prefix, then move-out'. None if move-out useless."""
    g=replay(types,covers,prefix)
    if len(g.tray)<3:return None
    moved=g.tray[:3];residue={}
    for id in g.tray:residue[types[id]]=residue.get(types[id],0)+1
    count={}
    for id in moved:count[types[id]]=count.get(types[id],0)+1
    top=max(int(k) for k in data['levelData'])+1;extra=[];phantoms=[];slot=0
    def tile(id,t):
        nonlocal slot
        extra.append({'id':id,'type':t,'rolNum':4000+slot*20,'rowNum':4000,'layerNum':top,
            'moldType':1,'metaType':0,'metaData':0});slot+=1
    for id in moved:tile('B~'+id,types[id])
    # Phantoms for types that clear first keep the replayed tray small.
    for t in sorted(count,key=lambda t:-residue[t]):
        for j in range(3-count[t]):
            pid=f'P~{t}~{j}';tile(pid,t);phantoms.append(pid)
    out=json.loads(json.dumps(data));out['levelData'][str(top)]=extra
    out['_constraint_relaxation']=True
    out['_prop_move_out_reduction']='one_move_out_v1'
    return out,list(prefix)+phantoms,moved


def candidates(types,covers,routes,window,stride):
    seen=set()
    for route in routes:
        stop=legal_prefix(types,covers,route)
        for k in range(stop,max(0,stop-window),-stride):
            key=tuple(sorted(route[:k]))
            if key in seen:continue
            seen.add(key);yield route[:k]


