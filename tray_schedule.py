"""Exact model using cumulative tray occupancy intervals.

Cards belong to consecutive triples of their type. Their holding interval
starts in the event where they are picked and ends just after their triple
eliminates. One event closes one triple; inclusive event occupancy is <= 7.
This represents tray capacity without per-event/per-card membership tables.
"""


def build(board):
    model=board.model;n=board.n;q=board.eliminations
    ancestors={id:set(board.parents[id]&board.runtime.remaining) for id in board.ids}
    for card in sorted(board.remaining,key=lambda c:c['layer'],reverse=True):
        for parent in list(ancestors[card['id']]):ancestors[card['id']].update(ancestors[parent])
    descendants={id:{child for child in board.ids if id in ancestors[child]} for id in board.ids}
    # Before event e exactly e triples have eliminated. At most seven
    # picked/initial cards can still be held during that event. Ancestors
    # and descendants therefore bound event ranks even without click times.
    held_total=len(board.tray)
    board.positions=[board.integer(max(0,(len(ancestors[id])+held_total-4)//3),
        min(q-1,(n+held_total-len(descendants[id])-1)//3),f'bin{i}') for i,id in enumerate(board.ids)]
    board.event_last=[model.new_bool_var(f'last{i}') for i in range(n)]
    special=set(board.special)
    palette=sorted({board.runtime.types[id] for id in board.special})
    from ortools.sat.python import cp_model
    domain=cp_model.Domain.from_values(palette) if palette else None
    board.picked_types=[model.new_int_var_from_domain(domain,f'cardtype{i}') if id in special
                        else board.runtime.types[id] for i,id in enumerate(board.ids)]
    finishes=[];intervals=[];demands=[]
    board.groups={};group_types=[];group_sizes=[];earliest_by_group=[];type_group_indices={}
    for t in board.type_ids:
        count=sum(board.runtime.types[id]==t for id in board.ids)
        held=board.tray.count(t);group_count=(count+held)//3
        if not group_count:continue
        finish=[board.integer(j,q-group_count+j,f'finish{t}_{j}') for j in range(group_count)]
        for j in range(1,group_count):model.add(finish[j]>finish[j-1])
        type_group_indices[t]=list(range(len(finishes),len(finishes)+group_count))
        finishes.extend(finish)
        group_types.extend([t]*group_count);group_sizes.extend([3-held]+[3]*(group_count-1))
        earliest_by_group.extend([0]+[f+1 for f in finish[:-1]])
        if held:
            duration=board.integer(1,q,f'initial_hold{t}')
            model.add(duration==finish[0]+1)
            intervals.append(model.new_interval_var(0,duration,duration,f'initial_tray{t}'))
            demands.append(held)
        board.groups[t]={'finishes':finish}
    assignments=[];eligible=[];membership={};last_bits=[[] for _ in board.ids]
    for i,id in enumerate(board.ids):
        types=palette if id in special else [board.runtime.types[id]]
        candidates=[j for t in types for j in type_group_indices[t]]
        group=model.new_int_var_from_domain(cp_model.Domain.from_values(candidates),f'group{i}')
        assignments.append(group);eligible.append(set(candidates))
        if id in special:model.add_element(group,group_types,board.picked_types[i])
        for j in candidates:membership[i,j]=board.equal(group,j,f'in_group{i}_{j}')
        end=board.integer(1,q,f'end{i}')
        model.add_element(group,[f+1 for f in finishes],end)
        earliest=board.integer(0,q-1,f'earliest{i}')
        model.add_element(group,earliest_by_group,earliest)
        model.add(board.positions[i]>=earliest)
        duration=board.integer(1,q,f'hold{i}')
        intervals.append(model.new_interval_var(board.positions[i],duration,end,f'tray{i}'));demands.append(1)
        if i%20==0:board.tick('holding_intervals')
    for j,finish in enumerate(finishes):
        members=[i for i in range(n) if j in eligible[i]]
        model.add(sum(membership[i,j] for i in members)==group_sizes[j])
        last=board.integer(0,len(members)-1,f'last_index{j}')
        model.add_element(last,[assignments[i] for i in members],j)
        model.add_element(last,[board.positions[i] for i in members],finish)
        for local,i in enumerate(members):last_bits[i].append(board.equal(last,local,f'last_member{i}_{j}'))
    for i in range(n):model.add(sum(last_bits[i])==board.event_last[i])
    board.group_assignments=assignments;board.type_group_indices=type_group_indices
    model.add_all_different(finishes)
    model.add_cumulative(intervals,demands,7)
    for i,id in enumerate(board.ids):
        for parent in board.parents[id]&board.runtime.remaining:
            j=board.index[parent]
            model.add(board.positions[j]+board.event_last[j]<=board.positions[i])
    if board.symmetry:
        for i,id in enumerate(board.ids):
            for j in range(i+1,n):
                other=board.ids[j]
                if id in special or other in special:continue
                if board.runtime.types[id]!=board.runtime.types[other]:continue
                if ancestors[id]<=ancestors[other] and descendants[id]>=descendants[other]:
                    model.add(board.positions[i]<=board.positions[j])
                elif ancestors[other]<=ancestors[id] and descendants[other]>=descendants[id]:
                    model.add(board.positions[j]<=board.positions[i])
    if board.m:board._build_colors()
