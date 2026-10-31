"""Energy/alien rules transcribed from the installed client's chessboard.

Serialized clicks only: wait for normal elimination and alien animation to
settle before the next click. One move-out (移出) is modeled from the client's
crushArea.moveOutBlock / moveOutArea: the leftmost min(3, tray) tiles go to
three columns; a click back runs the same onAfterBlockRemoved as a board pick
(draining exposed batteries) and an alien short of board cards takes the rest
from the move-out area, columns 1..3, without drawing randomness. Undo and
resumed boards are not modeled.
"""
from collections import Counter
import copy
import math

from core.data.ShuffleHelper import ShuffleHelper


class EnergyBoard:
    def __init__(self,cards,rules):
        if not rules or rules.get('kind')!='energy_alien_v1':raise ValueError('Missing energy rules')
        if any(c.get('metaType',0) not in (0,1,3) for c in cards):raise ValueError('Mixed energy/color rules are not supported')
        self.cards={c['id']:c for c in cards}
        if len(self.cards)!=len(cards):raise ValueError('Duplicate card IDs')
        self.ids=[c['id'] for c in sorted(cards,key=lambda c:(c['layer'],c['rowNum'],c['rolNum']))]
        self.types={c['id']:c['type'] for c in cards};self.remaining=set(self.ids)
        self.parents={c['id']:{p['id'] for p in cards if p['layer']>c['layer'] and
            abs(p['rolNum']-c['rolNum'])<8 and abs(p['rowNum']-c['rowNum'])<8} for c in cards}
        self.charges={c['id']:int(c.get('metaData',0)) for c in cards if c.get('metaType')==3}
        self.rules=copy.deepcopy(rules);self.rng=ShuffleHelper(rules['seed']);self.draws=0
        for _ in range(rules['pool_draws']):self.random()
        self.tray=[];self.tray_ids=[];self.collected=0;self.sequence=[];self.events=[]
        # tray/tray_ids keep pick order (checked by the C# executor); slot is
        # the visual order (insert after the last same type) used by move-out.
        self.slot=[];self.side=[];self.moved_out=False

    def random(self):
        self.draws+=1;return self.rng.get_random()

    def exposed(self,id):return id in self.remaining and not self.parents[id]&self.remaining

    def alien_targets(self):
        selected=[]
        def find(count,t=None):
            available=[id for id in self.ids if id in self.remaining]
            if t is None:
                if not available:return
                t=self.types[available[math.floor(self.random()*len(available))]]
            candidates=[id for id in available if self.types[id]==t]
            if len(candidates)<count:
                # getAlienRemoveBlock: all board cards, then move-out columns.
                extra=[id for id in self.side if self.types[id]==t][:count-len(candidates)]
                if len(candidates)+len(extra)<count:raise ValueError('Alien type counts are inconsistent')
                selected.extend(candidates+extra);return
            # The client shuffles only the last count positions, including
            # k=0 if that lies in the partial shuffle. Uses the board PRNG.
            for k in range(len(candidates)-1,len(candidates)-count-1,-1):
                j=math.floor(self.random()*(k+1));candidates[k],candidates[j]=candidates[j],candidates[k]
            selected.extend(candidates[-count:])
        if not self.tray:find(3)
        else:
            counts=Counter(self.tray)
            # useAlienItem walks crushArray (visual order). Without a move-out
            # that equals first-seen pick order; after one it may not.
            for t in dict.fromkeys(self.types[i] for i in self.slot):find(3-counts[t],t)
        return selected

    def move_out(self):
        if self.moved_out or not self.slot:raise ValueError('Move-out unavailable')
        n=min(3,len(self.slot));moved=self.slot[:n];self.slot=self.slot[n:];self.side=moved;self.moved_out=True
        self.tray_ids=[i for i in self.tray_ids if i not in moved];self.tray=[self.types[i] for i in self.tray_ids]
        event={'move_out':moved,'tray':list(self.tray),'remaining':len(self.remaining),'rng_draws':self.draws}
        self.events.append(event);return event

    def return_card(self,id):
        if id not in self.side:raise ValueError('Card is not in the move-out area')
        return self.move(id,from_side=True)

    def move(self,id,from_side=False):
        if not from_side and not self.exposed(id):raise ValueError('Energy target is covered, unknown or already removed')
        t=self.types[id];old_exposed=[other for other in self.charges if other!=id and self.exposed(other)]
        tray=self.tray+[t];tray_ids=self.tray_ids+[id];crush=tray.count(t)==3
        if crush:
            tray_ids=[i for i in tray_ids if self.types[i]!=t];tray=[v for v in tray if v!=t]
        # Alien runs after the click settles; it does not authorize a click
        # that first fills seven stable slots without a triple.
        if len(tray)>=7:raise ValueError('Energy click overflows the tray before alien activation')
        same=[k for k,x in enumerate(self.slot) if self.types[x]==t]
        slot=self.slot[:same[-1]+1]+[id]+self.slot[same[-1]+1:] if same else self.slot+[id]
        if crush:slot=[x for x in slot if self.types[x]!=t]
        if from_side:self.side.remove(id)
        else:self.remaining.remove(id)
        self.sequence.append(id);self.tray=tray;self.tray_ids=tray_ids;self.slot=slot
        collected=self.charges.get(id,0)>0
        if collected:self.collected+=1
        self.charges[id]=0
        # onAfterBlockRemoved sees the old isShowLight flags: a battery just
        # uncovered by this pick keeps all four charges until the next pick.
        for other in old_exposed:
            if self.charges[other]>0:self.charges[other]-=1
        auto=[];tray_removed=[]
        triggered=self.collected>=self.rules['collect_count']
        if triggered:
            self.collected-=self.rules['collect_count']
            for other in self.charges:self.charges[other]=0
            auto=self.alien_targets();tray_removed=list(self.tray_ids)
            self.remaining.difference_update(auto);self.side=[i for i in self.side if i not in auto]
            self.tray=[];self.tray_ids=[];self.slot=[]
        event={'card':id,**({'returned':True} if from_side else {}),'type':t,'crush':crush,'collected':collected,
               'energy_count':self.collected,'alien':triggered,'auto_removed':auto,
               'tray_removed':tray_removed,'tray':list(self.tray),'remaining':len(self.remaining),
               'rng_draws':self.draws}
        self.events.append(event);return event


def replay(cards,rules,sequence,expected_types=None,expected_events=None,complete=True):
    board=EnergyBoard(cards,rules);peak=0
    for i,id in enumerate(sequence):
        event=board.move(id);peak=max(peak,len(board.tray))
        if expected_types is not None and event['type']!=expected_types[i]:raise ValueError('Energy picked type mismatch')
    if expected_events is not None and board.events!=expected_events:raise ValueError('Energy/alien event trace mismatch')
    if complete and (board.remaining or board.tray):raise ValueError('Energy answer did not clear board and tray')
    return board,peak


def replay_actions(cards,rules,actions,complete=True):
    """Board picks, one move-out and returns: {'kind':'board'|'move_out'|'return','id':...}."""
    board=EnergyBoard(cards,rules);peak=0
    for action in actions:
        kind=action['kind']
        if kind=='board':board.move(action['id'])
        elif kind=='move_out':board.move_out()
        elif kind=='return':board.return_card(action['id'])
        else:raise ValueError('Unknown energy action')
        peak=max(peak,len(board.tray))
    if complete and (board.remaining or board.tray or board.side):raise ValueError('Energy prop route did not clear board, tray and move-out area')
    return board,peak
