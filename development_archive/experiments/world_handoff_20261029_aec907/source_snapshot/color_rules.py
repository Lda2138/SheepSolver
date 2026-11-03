"""Verified classic client rules: meta 2 shuffles remaining colors after a triple.

The client iterates numeric layer, row, column keys (not map array order).
Its board PRNG consumes one warmup, every pool element, then initial colors.
Mold 2 is only a visual rolling animation; it consumes a different PRNG.
"""
import copy
from core.data.ShuffleHelper import ShuffleHelper


def compatibility(data):
    cards = [c for v in data['levelData'].values() for c in v]
    if data.get('_has_gold_rules') and not data.get('goldBlockData'):
        # Older capture processes discarded protobuf field 6. Recover only
        # from the retained raw map for the same acquisition and geometry.
        import re
        from pathlib import Path
        key=data.get('_acquisition',{}).get('map_md5','')
        if isinstance(key,str) and re.fullmatch('[a-fA-F0-9]{32}',key):
            try:
                from map_protocol import decode_map
                raw=decode_map((Path(__file__).resolve().parent/'output/auto_capture/maps'/(key+'.map')).read_bytes())
                if {c['id'] for v in raw['levelData'].values() for c in v}=={c['id'] for c in cards}:
                    data['goldBlockData']=raw.get('goldBlockData')
            except (OSError,ValueError,TypeError):pass
    if data.get('_resumed_state'):
        return 'contains_special_card_rules'
    if data.get('_has_gold_rules') or data.get('goldBlockData') or any(c.get('metaType')==3 for c in cards):
        from map_protocol import valid_seed
        gold=data.get('goldBlockData',{});blocks=gold.get('blockList',[])
        # Current client initializes all cards of blockList[0].val as energy
        # cards. Mixed changing-color/energy maps need additional transitions.
        if (not valid_seed(data.get('map_seed')) or not blocks or
            not isinstance(gold.get('collectCount'),int) or gold['collectCount']<=0 or
            not isinstance(blocks[0].get('val'),int) or blocks[0]['val']<=0 or
            any(c.get('metaType',0) not in (0,1,3) or
                (c.get('metaData',0) and c.get('metaType')!=3) for c in cards)):
            return 'contains_special_card_rules'
        return 'energy_alien'
    if any(c.get('metaType', 0) not in (0, 1, 2) or c.get('metaData', 0) for c in cards):
        return 'contains_special_card_rules'
    special = [c for c in cards if c.get('metaType') == 2]
    if special:
        from map_protocol import valid_seed
        if len(special) > 32 or not valid_seed(data.get('map_seed')):
            return 'contains_special_card_rules'
        return 'color_shuffle'
    return 'standard'


def initial_cards(data):
    cards = [{**copy.deepcopy(c), 'layer': int(k)} for k, v in
             sorted(data['levelData'].items(), key=lambda kv: int(kv[0])) for c in v]
    kind = compatibility(data)
    if kind == 'contains_special_card_rules':
        raise ValueError('该地图含尚未实现的特殊规则或恢复状态')
    if kind == 'energy_alien':
        gold=data['goldBlockData'];t=gold['blockList'][0]['val']
        for c in cards:
            if c['type']==t:c.update(metaType=3,metaData=4)
        return cards,{'kind':'energy_alien_v1','seed':data['map_seed'],
            'pool_draws':sum(data.get('blockTypeData',{}).values())*3,
            'gold_type':t,'collect_count':gold['collectCount'],'initial_charge':4}
    if kind == 'standard':
        return cards, None
    special = sorted((c for c in cards if c.get('metaType') == 2),
                     key=lambda c: (c['layer'], c['rowNum'], c['rolNum']))
    rules = {'kind': 'color_shuffle_v1', 'seed': data['map_seed'],
             'pool_draws': sum(data['blockTypeData'].values()) * 3,
             'special_initial': [{'id': c['id'], 'type': c['type']} for c in special]}
    runtime = ColorState(cards, rules, initialize=True)
    for c in cards:
        c['type'] = runtime.types[c['id']]
    return cards, rules


class ColorState:
    def __init__(self, cards, rules, initialize=False):
        if any(c.get('metaType', 0) not in (0, 1, 2) or c.get('metaData', 0) for c in cards):
            raise ValueError('回放器不支持该特殊牌规则')
        self.types = {c['id']: c['type'] for c in cards}
        self.remaining = set(self.types)
        self.special = sorted((c for c in cards if c.get('metaType') == 2),
                              key=lambda c: (c['layer'], c['rowNum'], c['rolNum']))
        self.special = [c['id'] for c in self.special]
        self.rng = None
        if self.special:
            if not rules or rules.get('kind') != 'color_shuffle_v1':
                raise ValueError('变色牌缺少已验证的随机数规则')
            originals = rules['special_initial']
            if [c['id'] for c in originals] != self.special:
                raise ValueError('变色牌顺序与游戏遍历顺序不一致')
            self.rng = ShuffleHelper(rules['seed'])
            for _ in range(rules['pool_draws']):
                self.rng.get_random()
            values = [c['type'] for c in originals]
            if len(values) > 1:
                self.rng.shuffle(values, len(values))
            initial = dict(zip(self.special, values))
            if not initialize and any(self.types[id] != value for id, value in initial.items()):
                raise ValueError('开局变色牌型与真实种子不一致')
            self.types.update(initial)

    def remove(self, id, crushed):
        self.remaining.remove(id)
        if crushed and self.rng:
            ids = [id for id in self.special if id in self.remaining]
            values = [self.types[id] for id in ids]
            if len(values) > 1:
                self.rng.shuffle(values, len(values))
            self.types.update(zip(ids, values))


def solution_state(solution, steps):
    if solution.get('rules') and solution['rules'].get('kind')=='energy_alien_v1':
        from energy_rules import replay
        runtime,_=replay(solution['cards'],solution['rules'],solution['sequence'][:steps],complete=False)
        return runtime,list(runtime.tray)
    runtime = ColorState(solution['cards'], solution.get('rules'))
    tray = []
    for id in solution['sequence'][:steps]:
        t = runtime.types[id]
        tray.append(t)
        crushed = tray.count(t) == 3
        if crushed:
            tray = [v for v in tray if v != t]
        runtime.remove(id, crushed)
    return runtime, tray
