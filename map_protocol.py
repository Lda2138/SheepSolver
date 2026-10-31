"""Decode the map and seed schemas verified against the installed game package."""
import copy
import json
import re

from core.data.ShuffleHelper import ShuffleHelper


def varint(data, at):
    value = 0
    for shift in range(0, 70, 7):
        if at >= len(data):
            raise ValueError('截断的 Protobuf')
        byte = data[at]
        at += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, at
    raise ValueError('Protobuf 整数过长')


def fields(data):
    at = 0
    while at < len(data):
        tag, at = varint(data, at)
        number, wire = tag >> 3, tag & 7
        if number == 0:
            raise ValueError('非法 Protobuf 字段')
        if wire == 0:
            value, at = varint(data, at)
        elif wire == 2:
            length, at = varint(data, at)
            if at + length > len(data):
                raise ValueError('非法 Protobuf 长度')
            value = data[at:at + length]
            at += length
        elif wire in (1, 5):
            length = 8 if wire == 1 else 4
            if at + length > len(data):
                raise ValueError('截断字段')
            value = data[at:at + length]
            at += length
        else:
            raise ValueError('不支持的 Protobuf wire type')
        yield number, wire, value


def signed32(value):
    value &= 0xffffffff
    return value - 0x100000000 if value & 0x80000000 else value


def decode_node(data):
    node = {key: 0 for key in ('type', 'rolNum', 'rowNum', 'layerNum', 'moldType', 'metaType', 'metaData')}
    node['id'] = ''
    names = {2: 'type', 3: 'rolNum', 4: 'rowNum', 5: 'layerNum', 6: 'moldType', 7: 'metaType', 8: 'metaData'}
    for number, wire, value in fields(data):
        if number == 1 and wire == 2:
            node['id'] = value.decode('utf-8')
        elif number in names and wire == 0:
            node[names[number]] = signed32(value)
    if not re.fullmatch(r'\d+-\d+-\d+', node['id']):
        raise ValueError('地图牌编号不合法')
    return node


def decode_gold(data):
    result={'collectCount':0,'blockList':[]}
    for number,wire,value in fields(data):
        if number==1 and wire==0:result['collectCount']=signed32(value)
        elif number==2 and wire==2:
            block={'val':0,'count':0}
            for n,w,v in fields(value):
                if n in (1,2) and w==0:block['val' if n==1 else 'count']=signed32(v)
            result['blockList'].append(block)
    return result


def validate_geometry(data):
    levels = data.get('levelData')
    if not isinstance(levels, dict) or not levels:
        raise ValueError('缺少地图层级')
    ids = set()
    count = 0
    for level, cards in levels.items():
        int(level)
        for card in cards:
            if card['id'] in ids:
                raise ValueError('重复牌编号')
            ids.add(card['id'])
            if not re.fullmatch(r'\d+-\d+-\d+', card['id']):
                raise ValueError('地图牌编号不合法')
            if [int(x) for x in card['id'].split('-')] != [int(level), card['rolNum'], card['rowNum']]:
                raise ValueError('牌编号与层级不一致')
            for key in ('type', 'rolNum', 'rowNum'):
                if not isinstance(card[key], int) or card[key] < 0:
                    raise ValueError('牌类型或坐标无效')
            count += 1
    if count < 3 or count > 3000:
        raise ValueError('地图牌数量异常')
    return count


def decode_map(raw):
    if raw.lstrip().startswith(b'{'):
        result = json.loads(raw.decode('utf-8-sig'))
        validate_geometry(result)
        return result
    errors = []
    # Installed client removes exactly 21 bytes before GameMap.decode.
    for data in (raw[21:], raw):
        try:
            result = {'blockTypeData': {}, 'levelData': {}}
            for number, wire, value in fields(data):
                if number in (1, 2, 3, 8) and wire == 0:
                    result[{1: 'widthNum', 2: 'heightNum', 3: 'levelKey', 8: 'cdNum'}[number]] = signed32(value)
                elif number in (4, 5, 7) and wire == 2:
                    item = {n: v for n, _, v in fields(value)}
                    key = item.get(1, b'').decode('utf-8')
                    if not key.isdigit() or 2 not in item:
                        raise ValueError('地图索引错误')
                    if number == 5:
                        cards = [decode_node(v) for n, w, v in fields(item[2]) if n == 1 and w == 2]
                        result['levelData'][key] = cards
                    else:
                        target = 'blockTypeData' if number == 4 else 'blockTypeCfg'
                        result.setdefault(target, {})[key] = signed32(item[2])
                elif number == 6 and wire == 2 and value:
                    result['_has_gold_rules'] = True
                    result['goldBlockData']=decode_gold(value)
            validate_geometry(result)
            return result
        except (ValueError, TypeError, UnicodeError) as exc:
            errors.append(str(exc))
    raise ValueError('地图解码失败：' + '; '.join(errors))


def valid_seed(seed):
    return (isinstance(seed, list) and len(seed) == 4 and
            all(isinstance(x, int) and not isinstance(x, bool) and -2**31 <= x <= 2**32-1 for x in seed) and any(seed))


def decode_seed(raw):
    seed, code, seed2 = [], None, None
    for number, wire, value in fields(raw):
        if number == 1 and wire == 0:
            code = value
        elif number == 2 and wire == 0:
            seed.append(value)
        elif number == 2 and wire == 2:
            at = 0
            while at < len(value):
                item, at = varint(value, at)
                seed.append(item)
        elif number == 3 and wire == 2:
            seed2 = value.decode('utf-8')
        else:
            raise ValueError('种子消息字段不匹配')
    if code != 1 or not valid_seed(seed):
        raise ValueError('未获得成功的真实种子响应')
    return {'map_seed': seed, 'map_seed_2': seed2}


def materialize(structure, seed=None):
    result = copy.deepcopy(structure)
    validate_geometry(result)
    result['levelData'] = dict(sorted(result['levelData'].items(), key=lambda item: int(item[0])))
    cards = [c for level in result['levelData'].values() for c in level]
    unknown = [c for c in cards if c['type'] == 0]
    if unknown:
        if not valid_seed(seed):
            raise ValueError('等待真实种子，隐藏牌类型尚未齐全')
        counts = result.get('blockTypeData', {})
        if not counts or any(not isinstance(c, int) or c < 0 or c > 1000 or int(k) <= 0 for k, c in counts.items()):
            raise ValueError('牌类型计数无效')
        types = [int(key) for key, count in sorted(counts.items(), key=lambda item: int(item[0])) for _ in range(count * 3)]
        if len(types) != len(unknown):
            raise ValueError('类型数量与未知牌数不符，不能套用旧版洗牌规则')
        ShuffleHelper(seed).shuffle(types, len(types))
        for card, card_type in zip(unknown, reversed(types)):
            card['type'] = card_type
    if seed is not None:
        result['map_seed'] = seed
    from color_rules import compatibility
    result['_solver_compatibility'] = compatibility(result)
    return result


def decompress_match_data(text, limit=8*1024**2):
    """Installed _0x5cc366 uses the LZ-string decompressFromUTF16 format."""
    if not isinstance(text, str) or not text or len(text) > limit:
        raise ValueError('match_data 长度异常')
    position = 0
    def bits(count):
        nonlocal position
        value = 0
        for shift in range(count):
            index, offset = divmod(position, 15)
            if index >= len(text):
                raise ValueError('match_data 截断')
            word = ord(text[index]) - 32
            if not 0 <= word <= 32767:
                raise ValueError('match_data 编码异常')
            value |= ((word >> (14-offset)) & 1) << shift
            position += 1
        return value
    first = bits(2)
    if first not in (0, 1):
        raise ValueError('match_data 起始标志异常')
    word = chr(bits(8 if first == 0 else 16))
    dictionary = {3: word}
    size, enlarge, width = 4, 4, 3
    output, length = [word], 1
    while True:
        code = bits(width)
        if code == 2:
            return ''.join(output)
        if code in (0, 1):
            dictionary[size] = chr(bits(8 if code == 0 else 16))
            code = size
            size += 1
            enlarge -= 1
        if enlarge == 0:
            enlarge, width = 1 << width, width + 1
        entry = dictionary.get(code)
        if entry is None:
            if code != size:
                raise ValueError('match_data 字典异常')
            entry = word + word[0]
        length += len(entry)
        if length > limit or size > limit:
            raise ValueError('match_data 解压超过上限')
        output.append(entry)
        dictionary[size] = word + entry[0]
        size += 1
        enlarge -= 1
        word = entry
        if enlarge == 0:
            enlarge, width = 1 << width, width + 1


def decode_match_maps(text):
    value = json.loads(decompress_match_data(text))
    if not isinstance(value, list) or not 1 <= len(value) <= 10:
        raise ValueError('match_data 地图列表异常')
    maps = []
    for item in value:
        state = item['gameState']
        cards = state['allBlockRuntimeData']
        if not isinstance(cards, list) or not 3 <= len(cards) <= 3000:
            raise ValueError('match_data 牌数异常')
        result = {'levelData': {}, 'blockTypeData': {}}
        resumed = bool(state.get('crushAreaBlocks')) or any(state.get('moveOutAreaBlocks', []))
        for card in cards:
            clean = {key: card.get(key, 0) for key in ('type', 'rowNum', 'layerNum', 'moldType', 'metaType', 'metaData')}
            clean['rolNum'] = card['colNum']
            clean['id'] = f"{clean['layerNum']}-{clean['rolNum']}-{clean['rowNum']}"
            # Store only geometry/type metadata, never the other runtime data.
            result['levelData'].setdefault(str(clean['layerNum']), []).append(clean)
            if card.get('AreaType', 0) != 0:
                resumed = True
        validate_geometry(result)
        if any(card['type'] <= 0 for level in result['levelData'].values() for card in level):
            raise ValueError('match_data 中仍有未知牌型')
        result['_resumed_state'] = resumed
        maps.append(result)
    return maps
