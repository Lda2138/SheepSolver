"""Exact global CP-SAT model for coverage, tray capacity and seeded shuffles.

All decisions describe a complete continuation. Optional position assumptions
are retractable; an infeasibility core is not inferred from a search timeout.
No desktop or network operations are performed here.
"""
import copy
from collections import Counter
import time

from ortools.sat.python import cp_model

from color_rules import initial_cards, ColorState


class BuildLimit(Exception):
    pass


def triple_decisions(runtime,tray,parents,sequence):
    """Replay a legal route into type/triple identities, without fixing time."""
    state=copy.deepcopy(runtime);tray=list(tray);taken=Counter(tray);facts=[]
    for id in sequence:
        if id not in state.remaining or parents[id]&state.remaining:
            raise ValueError('Invalid triple assumption route')
        t=state.types[id];ordinal=taken[t]//3;taken[t]+=1
        tray.append(t);hit=tray.count(t)==3
        if hit:tray=[v for v in tray if v!=t]
        if len(tray)>=7:raise ValueError('Triple assumption route overflows tray')
        facts.append({'kind':'triple_group','card':id,'type':t,'triple':ordinal})
        state.remove(id,hit)
    return facts


class ConstraintBoard:
    def __init__(self, data, prefix=(), *, deadline=None, notify=None, symmetry=False, encoding='auto',cancel=None):
        self.started=time.monotonic();self.deadline=deadline;self.notify=notify or (lambda **kw: None)
        self.cancel=cancel
        self.cards,self.rules=initial_cards(data)
        self.by_id={c['id']:c for c in self.cards}
        if len(self.by_id)!=len(self.cards):raise ValueError('Duplicate card IDs')
        self.parents={c['id']:{p['id'] for p in self.cards if p['layer']>c['layer'] and
            abs(p['rolNum']-c['rolNum'])<8 and abs(p['rowNum']-c['rowNum'])<8} for c in self.cards}
        self.runtime=ColorState(self.cards,self.rules);self.tray=[];self.prefix=list(prefix)
        for step,id in enumerate(self.prefix):
            if id not in self.runtime.remaining or self.parents[id]&self.runtime.remaining:
                raise ValueError(f'Invalid fixed boundary at step {step+1}')
            t=self.runtime.types[id];self.tray.append(t);crushed=self.tray.count(t)==3
            if crushed:self.tray=[v for v in self.tray if v!=t]
            if len(self.tray)>=7:raise ValueError('Fixed boundary overflows the tray')
            self.runtime.remove(id,crushed)
        self.remaining=[c for c in self.cards if c['id'] in self.runtime.remaining]
        self.ids=[c['id'] for c in self.remaining];self.index={id:i for i,id in enumerate(self.ids)}
        self.n=len(self.ids)
        self.special=[id for id in self.runtime.special if id in self.runtime.remaining]
        self.m=len(self.special)
        self.encoding='occurrences' if encoding=='auto' and not self.m else ('slots' if encoding=='auto' else encoding)
        if self.encoding not in ('slots','occurrences','cumulative','events','schedule'):raise ValueError('Unknown encoding')
        if self.encoding=='occurrences' and self.m:raise ValueError('This model needs fixed colors')
        if self.encoding=='cumulative' and self.rules:raise ValueError('This model needs ordinary fixed colors')
        self.model=cp_model.CpModel();self.assumptions={};self.learned=[]
        self.positions=[];self.actions=[];self.picked_types=[];self.event_before=[];self.colors=[]
        self.phase='positions'
        self.symmetry=symmetry
        self._build()
        self.build_seconds=time.monotonic()-self.started

    def tick(self,phase=None):
        if phase:self.phase=phase
        self.notify(phase=self.phase,build_seconds=time.monotonic()-self.started,
                    total=len(self.cards),fixed_prefix=len(self.prefix),remaining=self.n,changing=self.m)
        if self.deadline and time.monotonic()>self.deadline:raise BuildLimit('Model construction budget reached')
        if self.cancel and self.cancel():raise BuildLimit('Model construction cancelled')

    def integer(self,lo,hi,name):return self.model.new_int_var(lo,hi,name)

    def equal(self,var,value,name):
        b=self.model.new_bool_var(name)
        self.model.add(var==value).only_enforce_if(b)
        self.model.add(var!=value).only_enforce_if(~b)
        return b

    def ge(self,var,value,name):
        b=self.model.new_bool_var(name)
        self.model.add(var>=value).only_enforce_if(b)
        self.model.add(var<value).only_enforce_if(~b)
        return b

    def conditional(self,b,yes,no,lo,hi,name):
        v=self.integer(lo,hi,name)
        self.model.add(v==yes).only_enforce_if(b)
        self.model.add(v==no).only_enforce_if(~b)
        return v

    def _build(self):
        model=self.model;n=self.n
        total=Counter(self.runtime.types[id] for id in self.ids)+Counter(self.tray)
        self.type_ids=sorted(total)
        if any(v%3 for v in total.values()):
            model.add(False);self.tick('invariant_conflict');return
        if n==0:
            if self.tray:model.add(False)
            self.tick('complete_boundary');return
        self.eliminations=(n+len(self.tray))//3
        if self.encoding=='schedule':
            from tray_schedule import build
            build(self);self.tick('model_ready');return
        if self.encoding in ('events','schedule'):
            self._build_events();self.tick('model_ready');return
        ancestors=[];descendants=[]
        for c in self.remaining:
            ancestors.append(self.parents[c['id']]&self.runtime.remaining)
            descendants.append({o['id'] for o in self.remaining if c['id'] in self.parents[o['id']]})
        # Geometry includes all overlapping higher layers. These edges are
        # already transitively complete for the exact precedence relation.
        # Compute closure explicitly: non-overlapping grandparents also matter.
        for i in sorted(range(n),key=lambda i:self.remaining[i]['layer'],reverse=True):
            closure=set(ancestors[i])
            for p in list(closure):closure.update(ancestors[self.index[p]])
            ancestors[i]=closure
        for i in range(n):descendants[i]={self.ids[j] for j in range(n) if self.ids[i] in ancestors[j]}
        self.positions=[self.integer(len(ancestors[i]),n-1-len(descendants[i]),f'p{i}') for i in range(n)]
        self.position_bounds=[(len(ancestors[i]),n-1-len(descendants[i])) for i in range(n)]
        if self.encoding=='slots':
            self.actions=[self.integer(0,n-1,f'a{s}') for s in range(n)]
            model.add_inverse(self.positions,self.actions)
        else:model.add_all_different(self.positions)
        self.reduced_parents=[]
        for i in range(n):
            direct=self.parents[self.ids[i]]&self.runtime.remaining
            redundant=set().union(*(ancestors[self.index[p]] for p in direct)) if direct else set()
            direct=direct-redundant;self.reduced_parents.append(direct)
            for p in direct:model.add(self.positions[self.index[p]]<self.positions[i])
        if self.encoding in ('occurrences','cumulative'):
            self._build_occurrences(ancestors,descendants)
            self.tick('model_ready');return
        domain=cp_model.Domain.from_values(self.type_ids)
        self.picked_types=[model.new_int_var_from_domain(domain,f't{s}') for s in range(n)]
        special_set=set(self.special)
        for i,c in enumerate(self.remaining):
            if c['id'] not in special_set:model.add_element(self.positions[i],self.picked_types,self.runtime.types[c['id']])
        # Safe global symmetry: same type AND exactly the same prerequisites
        # and descendants. Changing-card identity is never interchangeable.
        classes={}
        for i,c in enumerate(self.remaining):
            if c['id'] in special_set:continue
            key=(self.runtime.types[c['id']],frozenset(ancestors[i]),frozenset(descendants[i]))
            if self.symmetry and key in classes:model.add(self.positions[classes[key]]<self.positions[i])
            classes[key]=i
        self.tick('tray')
        previous={t:self.tray.count(t) for t in self.type_ids};before=[0];crush=[];events=[0]
        selected_by_type={t:[] for t in self.type_ids}
        self.tray_states=[previous]
        for s in range(n):
            next_state={}
            for t in self.type_ids:
                selected=self.equal(self.picked_types[s],t,f'y{s}_{t}')
                selected_by_type[t].append(selected)
                r=self.integer(0,2,f'r{s}_{t}')
                model.add_allowed_assignments([previous[t],selected,r],
                    [(held,chosen,(held+chosen)%3) for held in range(3) for chosen in (0,1)])
                next_state[t]=r
            tray=sum(next_state.values());model.add(tray<=6)
            hit=model.new_bool_var(f'crush{s}')
            model.add(tray==sum(previous.values())+1-3*hit)
            # The tray has exactly initial + clicks - 3 * eliminations.
            # Capacity bounds the elimination count to at most three values,
            # even on a 258-card board. Expose this fact before presolve.
            used=s+1+len(self.tray)
            e=self.integer(max(0,(used-6+2)//3),min(self.eliminations,used//3),f'event{s+1}')
            model.add(e==events[-1]+hit)
            crush.append(hit);before.append(e);events.append(e)
            previous=next_state;self.tray_states.append(previous)
        for r in previous.values():model.add(r==0)
        remaining_counts=Counter(self.runtime.types[id] for id in self.ids)
        for t,selected in selected_by_type.items():model.add(sum(selected)==remaining_counts[t])
        model.add(events[-1]==self.eliminations)
        self.event_before=events[:-1];self.events=events;self.crush=crush
        if self.m:
            self._build_colors()
        self.tick('model_ready')

    def _build_occurrences(self,ancestors,descendants):
        """Fixed colors: schedule type occurrences and global triple events.

        The e-th elimination is between clicks 3(e+1)-held and 3e+7-held.
        These five-slot event windows are equivalent to the tray capacity;
        no per-step/per-type residue or action table is needed.
        """
        model=self.model;n=self.n;q=self.eliminations
        self.boundaries=[self.integer(max(0,3*(e+1)-len(self.tray)-1),
                                     min(n-1,3*e+6-len(self.tray)),f'end{e}') for e in range(q)]
        for e in range(1,q):model.add(self.boundaries[e]>self.boundaries[e-1])
        model.add(self.boundaries[-1]==n-1)
        hit_ranks=[];intervals=[]
        self.dominance_edges=[]
        for t in self.type_ids:
            members=[i for i,id in enumerate(self.ids) if self.runtime.types[id]==t]
            count=len(members);held=self.tray.count(t)
            if not count:continue
            lower=sorted(self.position_bounds[i][0] for i in members)
            upper=sorted(self.position_bounds[i][1] for i in members)
            times=[self.integer(max(j,lower[j]),min(n-count+j,upper[j]),f'occ{t}_{j}') for j in range(count)]
            for j in range(1,count):model.add(times[j]>times[j-1])
            member_ids={self.ids[i] for i in members}
            ranks=[self.integer(len(ancestors[i]&member_ids),count-1-len(descendants[i]&member_ids),
                                f'ord{t}_{i}') for i in members]
            model.add_all_different(ranks)
            for i,rank in zip(members,ranks):model.add_element(rank,times,self.positions[i])
            for j,at in enumerate(times):
                if (j+1+held)%3==0:
                    rank=self.integer(0,q-1,f'hit{t}_{j}')
                    model.add_element(rank,self.boundaries,at);hit_ranks.append(rank)
            if self.encoding=='cumulative':
                # Occupancy after click s is the number of intervals containing
                # s. A triple's first and second cards occupy [pick, third),
                # and the third occupies no slot after immediate elimination.
                # Held boundary cards start at zero and end at the first hit.
                # Zero-length intervals correctly allow a hit at click zero.
                starts=[0]*held
                for j,at in enumerate(times):
                    if (j+1+held)%3:
                        starts.append(at)
                    else:
                        for k,begin in enumerate(starts):
                            size=self.integer(0,n,f'holdsize{t}_{j}_{k}')
                            intervals.append(model.new_interval_var(begin,size,at,f'hold{t}_{j}_{k}'))
                        starts=[]
                if starts:raise ValueError('Incomplete static triple')
            if self.symmetry:
                # Dominance permits exchanging equal fixed colors: the first
                # card needs no additional ancestor and unlocks every child
                # of the second. This is an existence symmetry, so never use
                # it while explaining arbitrary assumptions.
                for offset,i in enumerate(members):
                    for j in members[offset+1:]:
                        ij=ancestors[i]<=ancestors[j] and descendants[i]>=descendants[j]
                        ji=ancestors[j]<=ancestors[i] and descendants[j]>=descendants[i]
                        if ij:
                            model.add(self.positions[i]<self.positions[j]);model.add(ranks[offset]<ranks[members.index(j)])
                            self.dominance_edges.append((self.ids[i],self.ids[j]))
                        elif ji:
                            model.add(self.positions[j]<self.positions[i]);model.add(ranks[members.index(j)]<ranks[offset])
                            self.dominance_edges.append((self.ids[j],self.ids[i]))
            self.tick('type_occurrences')
        model.add_all_different(hit_ranks)
        if self.encoding=='cumulative':
            model.add_cumulative(intervals,[1]*len(intervals),6)
            self.occupancy_intervals=len(intervals)

    def _build_colors(self):
        model=self.model;n=self.n;m=self.m;q=self.eliminations
        self.tick('seeded_colors')
        color_values=sorted({self.runtime.types[id] for id in self.special})
        # Every shuffle permutes these colors; it never creates a new type.
        color_domain=cp_model.Domain.from_values([0]+color_values)
        self.colors=[[self.runtime.types[id] for id in self.special]]
        for e in range(1,q+1):
            self.colors.append([model.new_int_var_from_domain(color_domain,f'color{e}_{j}') for j in range(m)])
        for j,id in enumerate(self.special):
            p=self.positions[self.index[id]]
            if self.encoding in ('events','schedule'):e=p
            else:
                e=self.integer(0,q-1,f'pickevent{j}')
                model.add_element(p,self.event_before,e)
            t=model.new_int_var_from_domain(cp_model.Domain.from_values(color_values),f'specialtype{j}')
            model.add_element(e,[self.colors[k][j] for k in range(q)],t)
            if self.encoding in ('events','schedule'):model.add(self.picked_types[self.index[id]]==t)
            else:model.add_element(p,self.picked_types,t)
        if self.encoding in ('events','schedule'):self.boundaries=list(range(q))
        else:
            self.boundary_bounds=[(max(0,3*(e+1)-len(self.tray)-1),
                                   min(n-1,3*e+6-len(self.tray))) for e in range(q)]
            self.boundaries=[self.integer(lo,hi,f'end{e}') for e,(lo,hi) in enumerate(self.boundary_bounds)]
            for e,end in enumerate(self.boundaries):
                model.add_element(end,self.crush,1)
                model.add_element(end,self.events[1:],e+1)
                if e:model.add(end>self.boundaries[e-1])
        max_draws=m*q
        rng=copy.deepcopy(self.runtime.rng)
        stream=[rng.get_random() for _ in range(max_draws+m+1)]
        draws=[0];self.draws=draws;self.alive=[]
        for e,end in enumerate(self.boundaries):
            self.tick('shuffle_events')
            alive=[];ranks=[0]
            for j,id in enumerate(self.special):
                b=self.ge(self.positions[self.index[id]]-end,1,f'alive{e}_{j}')
                alive.append(b)
                rank=self.integer(0,m,f'rank{e}_{j+1}');model.add(rank==ranks[-1]+b);ranks.append(rank)
            self.alive.append(alive);remaining=ranks[-1]
            # All swaps of an event share ONE lookup index. Independent
            # draw-index variables would expand thousands of index literals
            # per swap in CP-SAT. Shifting the constant tables preserves the
            # exact RNG draws while sharing their integer encoding.
            clock_limit=min(max_draws,(e+1)*m)
            clock=self.integer(0,clock_limit,f'clock{e}')
            model.add(clock==draws[e]+remaining)
            shuffles=self.ge(remaining,2,f'shuffle{e}')
            delta=self.conditional(shuffles,remaining,0,0,m,f'delta{e}')
            next_draw=self.integer(0,max_draws,f'draw{e+1}');model.add(next_draw==draws[-1]+delta);draws.append(next_draw)
            compact=[model.new_int_var_from_domain(color_domain,f'compact{e}_{k}') for k in range(m)]+[0]
            for j in range(m):
                idx=self.conditional(alive[j],ranks[j],m,0,m,f'packidx{e}_{j}')
                value=self.conditional(alive[j],self.colors[e][j],0,0,max(color_values),f'packval{e}_{j}')
                model.add_element(idx,compact,value)
            for k in range(m):
                in_range=self.ge(remaining,k+1,f'inrange{e}_{k}')
                model.add(compact[k]>=1).only_enforce_if(in_range)
                model.add(compact[k]==0).only_enforce_if(~in_range)
            current=compact[:m]
            # k=0 swaps with itself but consumes a random draw when m>1.
            # Draw accumulation above includes that final client iteration.
            for k in range(m-1,0,-1):
                active=self.ge(remaining,max(2,k+1),f'swapactive{e}_{k}')
                table=[int(stream[c-1-k]*(k+1)) if c>k else 0 for c in range(clock_limit+1)]
                j=self.integer(0,k,f'swapj{e}_{k}');model.add_element(clock,table,j)
                picked=model.new_int_var_from_domain(color_domain,f'swapvalue{e}_{k}')
                model.add_element(j,current[:k+1],picked)
                next_state=list(current)
                next_state[k]=self.conditional(active,picked,current[k],0,max(color_values),f'swapout{e}_{k}_{k}')
                for l in range(k):
                    is_index=self.equal(j,l,f'swapindex{e}_{k}_{l}')
                    swap=model.new_bool_var(f'swap{e}_{k}_{l}')
                    model.add(swap<=active);model.add(swap<=is_index);model.add(swap>=active+is_index-1)
                    next_state[l]=self.conditional(swap,current[k],current[l],0,max(color_values),f'swapout{e}_{k}_{l}')
                current=next_state
            for j in range(m):
                idx=self.conditional(alive[j],ranks[j],m,0,m,f'unpackidx{e}_{j}')
                model.add_element(idx,current+[0],self.colors[e+1][j])
        for color in self.colors[-1]:model.add(color==0)

    def _build_events(self):
        """Partition cards into segments ending in exactly one triple.

        A segment's last card has no descendant in that segment. All other
        cards can be topologically sorted, since no type eliminates early.
        This removes every equivalent within-segment click permutation.
        """
        model=self.model;n=self.n;q=self.eliminations
        self.positions=[self.integer(0,q-1,f'bin{i}') for i in range(n)]
        self.event_last=[model.new_bool_var(f'last{i}') for i in range(n)]
        for i,id in enumerate(self.ids):
            for parent in self.parents[id]&self.runtime.remaining:
                j=self.index[parent]
                model.add(self.positions[j]+self.event_last[j]<=self.positions[i])
        domain=cp_model.Domain.from_values(self.type_ids)
        special_set=set(self.special)
        palette=sorted({self.runtime.types[id] for id in self.special})
        special_domain=cp_model.Domain.from_values(palette) if palette else domain
        self.picked_types=[model.new_int_var_from_domain(special_domain,f'cardtype{i}') if id in special_set
                           else self.runtime.types[id] for i,id in enumerate(self.ids)]
        type_bits={}
        for i,id in enumerate(self.ids):
            if id in special_set:
                for t in palette:type_bits[i,t]=self.equal(self.picked_types[i],t,f'cardis{i}_{t}')
        special_counts=Counter(self.runtime.types[id] for id in self.special)
        for t in palette:model.add(sum(type_bits[self.index[id],t] for id in self.special)==special_counts[t])
        self.elimination_types=[model.new_int_var_from_domain(domain,f'hittype{e}') for e in range(q)]
        hits_by_type={t:[] for t in self.type_ids}
        if self.symmetry:
            ancestors={id:set(self.parents[id]&self.runtime.remaining) for id in self.ids}
            for c in sorted(self.remaining,key=lambda c:c['layer'],reverse=True):
                for p in list(ancestors[c['id']]):ancestors[c['id']].update(ancestors[p])
            descendants={id:{child for child in self.ids if id in ancestors[child]} for id in self.ids}
            for i,id in enumerate(self.ids):
                if id in special_set:continue
                for j in range(i+1,n):
                    other=self.ids[j]
                    if other in special_set or self.runtime.types[id]!=self.runtime.types[other]:continue
                    if ancestors[id]<=ancestors[other] and descendants[id]>=descendants[other]:
                        model.add(self.positions[i]<=self.positions[j])
                    elif ancestors[other]<=ancestors[id] and descendants[other]>=descendants[id]:
                        model.add(self.positions[j]<=self.positions[i])
        for i in range(n):
            hit_type=model.new_int_var_from_domain(domain,f'lasthit{i}')
            model.add_element(self.positions[i],self.elimination_types,hit_type)
            model.add(hit_type==self.picked_types[i]).only_enforce_if(self.event_last[i])
        previous={t:self.tray.count(t) for t in self.type_ids};self.tray_states=[previous]
        for e in range(q):
            self.tick('event_segments')
            membership=[self.equal(self.positions[i],e,f'in{e}_{i}') for i in range(n)]
            ends=[];contributions={t:[] for t in self.type_ids}
            for i,id in enumerate(self.ids):
                last=model.new_bool_var(f'endmember{e}_{i}')
                model.add(last<=membership[i]);model.add(last<=self.event_last[i])
                model.add(last>=membership[i]+self.event_last[i]-1);ends.append(last)
                if id not in special_set:contributions[self.runtime.types[id]].append(membership[i])
                else:
                    for t in palette:
                        is_type=type_bits[i,t];picked=model.new_bool_var(f'incolor{e}_{i}_{t}')
                        model.add(picked<=membership[i]);model.add(picked<=is_type)
                        model.add(picked>=membership[i]+is_type-1);contributions[t].append(picked)
            model.add(sum(ends)==1)
            # The one triple occurs on the last pick, so all earlier picks
            # must fit in six slots. That last pick may temporarily use seven.
            model.add(sum(membership)+sum(previous.values())<=7)
            current={}
            for t in self.type_ids:
                hit=self.equal(self.elimination_types[e],t,f'eventhit{e}_{t}')
                hits_by_type[t].append(hit)
                r=self.integer(0,2,f'eventtray{e}_{t}')
                model.add(r==previous[t]+sum(contributions[t])-3*hit)
                model.add(r==0).only_enforce_if(hit)
                current[t]=r
            previous=current;self.tray_states.append(current)
        for r in previous.values():model.add(r==0)
        total_counts=Counter(self.runtime.types[id] for id in self.ids)+Counter(self.tray)
        for t,hits in hits_by_type.items():model.add(sum(hits)==total_counts[t]//3)
        if self.m:self._build_colors()

    def add_hint(self, sequence):
        if self.encoding in ('events','schedule'):
            state=copy.deepcopy(self.runtime);tray=list(self.tray);event=0
            for id in sequence:
                if id not in self.index or id not in state.remaining or event>=self.eliminations:break
                self.model.add_hint(self.positions[self.index[id]],event)
                t=state.types[id];tray.append(t);hit=tray.count(t)==3
                self.model.add_hint(self.event_last[self.index[id]],int(hit))
                if hit:tray=[v for v in tray if v!=t];event+=1
                state.remove(id,hit)
            return
        seen=set()
        for s,id in enumerate(sequence):
            if id not in self.index or id in seen or s>=self.n:break
            self.model.add_hint(self.positions[self.index[id]],s);seen.add(id)

    def assume_positions(self, assignments):
        """Assignments use continuation step -> ID, not an immutable prefix."""
        if self.symmetry:raise ValueError('Position assumptions require symmetry=False')
        if self.encoding in ('events','schedule'):raise ValueError('Click position assumptions are not event assignments')
        for step,id in assignments.items():
            step=int(step)
            if id not in self.index or not 0<=step<self.n:raise ValueError('Invalid position assumption')
            b=self.equal(self.positions[self.index[id]],step,f'fix{step}_{self.index[id]}')
            self.assumptions[b.index]=(step,id,b)
        self.activate_assumptions(self.assumptions)

    def assume_segments(self,sequence):
        """Fix event membership/type/end conditions, retaining segment freedom."""
        if self.encoding not in ('events','schedule'):raise ValueError('Segment assumptions need the event model')
        state=copy.deepcopy(self.runtime);tray=list(self.tray);event=0
        for step,id in enumerate(sequence):
            if id not in state.remaining or self.parents[id]&state.remaining:
                raise ValueError('Invalid segment assumption route')
            t=state.types[id];tray.append(t);hit=tray.count(t)==3
            if hit:tray=[v for v in tray if v!=t]
            if len(tray)>=7:raise ValueError('Segment assumption route overflows tray')
            facts={'step':step,'card':id,'event':event,'type':t,'last':hit}
            b=self.decision_literal(facts)
            self.assumptions[b.index]=(step,id,b,facts)
            state.remove(id,hit)
            if hit:event+=1
        self.activate_assumptions(self.assumptions)

    def assume_triples(self,sequence):
        """Preserve candidate triple membership, allowing all click orders
        and event timings to change, including earlier than its dead prefix.
        """
        if self.encoding!='schedule':raise ValueError('Triple assumptions need the schedule model')
        for step,fact in enumerate(triple_decisions(self.runtime,self.tray,self.parents,sequence)):
            b=self.decision_literal(fact);self.assumptions[b.index]=(step,fact['card'],b,fact)
        self.activate_assumptions(self.assumptions)

    def fix_triples(self,sequence):
        """Scheduling subproblem only. Its infeasibility does not prove the map dead."""
        if self.encoding!='schedule':raise ValueError('Fixed triples need the schedule model')
        for fact in triple_decisions(self.runtime,self.tray,self.parents,sequence):
            i=self.index[fact['card']];groups=self.type_group_indices[fact['type']]
            self.model.add(self.group_assignments[i]==groups[fact['triple']])

    def decision_literal(self,fact):
        """Equivalence is essential: a released guard cannot hide the same
        actual decision combination from an already learned conflict clause.
        """
        id=fact['card']
        if id not in self.index:raise ValueError('Conflict card is outside the boundary')
        i=self.index[id];suffix=f'{i}_{len(self.model.proto.variables)}'
        if fact.get('kind')=='picked_before':
            if self.encoding in ('events','schedule'):raise ValueError('Boundary cuts need click positions')
            step=int(fact['step'])
            if not 1<=step<=self.n:raise ValueError('Invalid dead boundary')
            return ~self.ge(self.positions[i],step,'boundary'+suffix)
        if fact.get('kind')=='triple_group':
            if self.encoding!='schedule':raise ValueError('Triple conflict needs the schedule model')
            t=int(fact['type']);ordinal=int(fact['triple'])
            groups=self.type_group_indices.get(t,[])
            if not 0<=ordinal<len(groups):raise ValueError('Invalid triple conflict')
            return self.equal(self.group_assignments[i],groups[ordinal],'decisiongroup'+suffix)
        if self.encoding not in ('events','schedule'):
            step=int(fact['step'])
            if not 0<=step<self.n:raise ValueError('Conflict step is outside the boundary')
            return self.equal(self.positions[i],step,'decision'+suffix)
        event=int(fact['event']);t=int(fact['type'])
        if not 0<=event<self.eliminations or t not in self.type_ids:
            raise ValueError('Invalid event conflict condition')
        conditions=[self.equal(self.positions[i],event,'decisionbin'+suffix),
                    self.equal(self.picked_types[i],t,'decisiontype'+suffix),
                    self.event_last[i] if fact['last'] else ~self.event_last[i]]
        b=self.model.new_bool_var('decision'+suffix)
        self.model.add_bool_and(conditions).only_enforce_if(b)
        self.model.add_bool_or([~v for v in conditions]).only_enforce_if(~b)
        return b

    def restore_conflict(self,facts,proved=False):
        # A scoped, proved nogood excludes no feasible route, so it cannot
        # invalidate an existence-preserving symmetry. Arbitrary assumptions
        # still require symmetry=False.
        if self.symmetry and not proved:raise ValueError('Restoring decision conflicts requires symmetry=False')
        if not facts:raise ValueError('An empty conflict is a whole-boundary proof, not a clause')
        self.model.add_bool_or([~self.decision_literal(fact) for fact in facts])
        self.learned.append(facts)

    def assumption_facts(self,i):
        record=self.assumptions[i]
        return record[3] if len(record)>3 else {'step':record[0],'card':record[1]}

    def activate_assumptions(self, indices):
        self.model.clear_assumptions()
        self.model.add_assumptions([self.assumptions[i][2] for i in indices])

    def learn_core(self,indices):
        if not indices:return
        self.model.add_bool_or([~self.assumptions[i][2] for i in indices])
        self.learned.append([self.assumption_facts(i) for i in indices])

    def answer(self,solver):
        route=[id for _,id in sorted((solver.value(self.positions[i]),id) for i,id in enumerate(self.ids))]
        if self.encoding in ('events','schedule') and self.n:
            route=[]
            for e in range(self.eliminations):
                group={id for i,id in enumerate(self.ids) if solver.value(self.positions[i])==e}
                last=next(id for id in group if solver.value(self.event_last[self.index[id]]))
                while group-{last}:
                    options=sorted(id for id in group-{last} if not self.parents[id]&group)
                    if not options:raise ValueError('Event schedule has no legal topological order')
                    id=options[0];route.append(id);group.remove(id)
                route.append(last)
        return {'sequence':self.prefix+route,
                'engine':'global_constraint_cp_sat','mode':'exact_global_constraints',
                'encoding':self.encoding,
                'rules_engine':'color_shuffle_v1' if self.rules else 'standard'}
