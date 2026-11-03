"""Matched ordinary-board benchmark data with separately stored witnesses.

Public map files contain only cards. Witnesses and generation labels are never
included in solver input. Planted distributions are not uniform solvable maps.
"""
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import random


VERSION='matched-world-truth-v1'


def dump(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def flatten(data):
    return [{**c,'layer':int(k)} for k,v in sorted(data['levelData'].items(),key=lambda kv:int(kv[0])) for c in v]


def clean(data):
    result={'levelData':{}}
    for layer,items in sorted(data['levelData'].items(),key=lambda kv:int(kv[0])):
        result['levelData'][str(int(layer))]=[]
        for c in items:
            if c.get('metaType',0)!=0 or c.get('metaData',0):raise ValueError('Benchmark requires ordinary cards')
            if not isinstance(c['type'],int) or c['type']<=0:raise ValueError('Invalid type')
            result['levelData'][str(int(layer))].append({key:copy.deepcopy(c[key]) for key in ('id','rolNum','rowNum','type')})
    cards=flatten(result)
    if not cards or len({c['id'] for c in cards})!=len(cards):raise ValueError('Empty board or duplicate IDs')
    if any(n%3 for n in Counter(c['type'] for c in cards).values()):raise ValueError('Counts must be multiples of three')
    return result


def geometry_hash(data):
    rows=sorted((c['id'],c['layer'],c['rolNum'],c['rowNum']) for c in flatten(data))
    return hashlib.sha256(json.dumps(rows,separators=(',',':')).encode()).hexdigest()


def parents_for(data):
    cards=flatten(data)
    return {c['id']:{p['id'] for p in cards if p['layer']>c['layer'] and
        abs(p['rolNum']-c['rolNum'])<8 and abs(p['rowNum']-c['rowNum'])<8} for c in cards}


def topological_route(data,rng,style='random'):
    by_id={c['id']:c for c in flatten(data)};parents=parents_for(data)
    children={id:set() for id in parents}
    for id,ps in parents.items():
        for p in ps:children[p].add(id)
    available=sorted(id for id,ps in parents.items() if not ps);route=[]
    while available:
        options=available
        if style!='random' and rng.random()<.75:
            layer=(min if style=='deep' else max)(by_id[id]['layer'] for id in available)
            options=[id for id in available if by_id[id]['layer']==layer]
        id=rng.choice(options);available.remove(id);route.append(id)
        for child in sorted(children[id]):
            parents[child].remove(id)
            if not parents[child]:available.append(child)
        available.sort()
    if len(route)!=len(by_id):raise ValueError('Coverage graph has a cycle')
    return route


def legal_type_word(counts,rng,capacity):
    if not 2<=capacity<=6:raise ValueError('Capacity must be between two and six')
    left=Counter(counts);held=Counter();word=[];slots=0
    while sum(left.values()):
        options=[];weights=[]
        for t in sorted(left):
            if not left[t]:continue
            new=(held[t]+1)%3;size=slots+(-2 if held[t]==2 else 1)
            # A saturated tray with all singletons cannot complete a triple.
            has_pair=new==2 or any(v==2 for u,v in held.items() if u!=t)
            if size>capacity or (size==capacity and not has_pair):continue
            options.append(t)
            weights.append(3.0 if held[t]==0 else 1.5 if held[t]==1 else .6)
        if not options:raise AssertionError('Type-word construction stranded a partial triple')
        t=rng.choices(options,weights=weights,k=1)[0];slots+=-2 if held[t]==2 else 1
        held[t]=(held[t]+1)%3;left[t]-=1;word.append(t)
    if slots:raise AssertionError('Type-word construction did not clear')
    return word


def verify_route(data,route):
    """Independent rectangle/tray replay, deliberately not generator graph code."""
    remaining={c['id']:c for c in flatten(data)};tray=[];peak=area=0;hits=[];types=[]
    if len(route)!=len(remaining):raise ValueError('Incomplete or repeated route')
    for step,id in enumerate(route,1):
        if id not in remaining:raise ValueError('Unknown or duplicate card')
        card=remaining[id];x,y=card['rolNum'],card['rowNum']
        for other in remaining.values():
            if other['layer']<=card['layer']:continue
            width=min(x+8,other['rolNum']+8)-max(x,other['rolNum'])
            height=min(y+8,other['rowNum']+8)-max(y,other['rowNum'])
            if width>0 and height>0:raise ValueError('Covered card')
        t=card['type'];tray.append(t);types.append(t)
        if tray.count(t)==3:tray=[v for v in tray if v!=t];hits.append(step)
        if len(tray)>6:raise ValueError('Tray overflow')
        peak=max(peak,len(tray));area+=len(tray);del remaining[id]
    if remaining or tray:raise ValueError('Nonempty final state')
    return {'steps':len(route),'peak_tray':peak,'mean_tray':area/max(1,len(route)),
        'eliminations':len(hits),'adjacent_same_type_fraction':sum(a==b for a,b in zip(types,types[1:]))/max(1,len(types)-1)}


def planted(data,seed,capacity,style):
    rng=random.Random(seed);result=clean(data);route=topological_route(result,rng,style)
    counts=Counter(c['type'] for c in flatten(result));word=legal_type_word(counts,rng,capacity)
    assigned=dict(zip(route,word))
    for items in result['levelData'].values():
        for c in items:c['type']=assigned[c['id']]
    metrics=verify_route(result,route)
    # A second existing replay checks the generated witness independently.
    from desktop_assistant import validate
    validate(flatten(result),route)
    return result,route,metrics


def shuffled(data,seed):
    result=clean(data);rng=random.Random(seed);types=[c['type'] for c in flatten(result)];rng.shuffle(types)
    for c,t in zip((c for v in result['levelData'].values() for c in v),types):c['type']=t
    return result


def generate(source,output,count=20,seed=20261029,originals=()):
    output=Path(output)
    if output.exists():raise FileExistsError('Dataset directory already exists; refusing to overwrite')
    base=clean(read(source));counts=Counter(c['type'] for c in flatten(base));geometry=geometry_hash(base)
    cases=[];seen=set()
    def save(data,cohort,case_seed,route=None,profile=None,metrics=None,source_path=None):
        if geometry_hash(data)!=geometry or Counter(c['type'] for c in flatten(data))!=counts:
            raise ValueError('Unmatched geometry or type multiset')
        encoded=json.dumps(data,sort_keys=True,separators=(',',':')).encode()
        semantic=hashlib.sha256(encoded).hexdigest()
        if semantic in seen:raise ValueError('Duplicate benchmark instance')
        seen.add(semantic);id='case_'+semantic[:16];path=output/'cases'/(id+'.json');dump(path,data)
        entry={'id':id,'map':'cases/'+id+'.json','map_sha256':sha(path),'semantic_sha256':semantic,
               'cohort':cohort,'truth':'solvable' if route is not None else 'unknown',
               'generation_seed':case_seed,'profile':profile,'witness_metrics':metrics}
        if route is not None:
            witness=output/'private/witnesses'/(id+'.json')
            dump(witness,{'map_sha256':sha(path),'sequence':route,'metrics':metrics})
            entry.update(witness='private/witnesses/'+id+'.json',witness_sha256=sha(witness))
        if source_path:entry['original_file_sha256']=sha(source_path)
        cases.append(entry)
    styles=('random','deep','broad')
    for i in range(count):
        capacity=(4,5,6)[i%3];style=styles[(i//3)%3];case_seed=seed+i*1009
        data,route,metrics=planted(base,case_seed,capacity,style)
        save(data,'planted',case_seed,route,{'capacity':capacity,'topological_style':style},metrics)
    for i in range(count):save(shuffled(base,seed+1000003+i*1013),'shuffled',seed+1000003+i*1013)
    for path in [Path(source)]+[Path(p) for p in originals]:save(clean(read(path)),'real',None,source_path=path)
    manifest={'version':VERSION,'source_path':str(Path(source).resolve()),'source_file_sha256':sha(source),
              'geometry_sha256':geometry,'card_count':sum(counts.values()),'type_counts':dict(sorted(counts.items())),
              'seed':seed,'count_per_generated_cohort':count,'cases':cases,
              'limitations':['Planted distribution is biased, not uniform over solvable boards.',
                 'Uniform multiset permutations do not establish the game server distribution.',
                 'Witness paths are for parent-side validation only; never pass them to solvers.']}
    dump(output/'manifest.json',manifest);return manifest
