"""Persist proved decision clauses for exactly one map and replay boundary."""
import hashlib
import json
from pathlib import Path

from solve_verified_map import atomic_json


MODEL_VERSION='exact-constraints-v2'


class ConflictStore:
    def __init__(self,path,map_sha256,prefix,encoding,fixed_triples=(),proof_kind='cp_sat_infeasibility_core'):
        self.path=Path(path)
        encoded=json.dumps(list(prefix),ensure_ascii=False,separators=(',',':')).encode('utf-8')
        self.scope={'model_version':MODEL_VERSION,'map_sha256':map_sha256,
                    'boundary_sha256':hashlib.sha256(encoded).hexdigest(),
                    'fixed_prefix':len(prefix),'encoding':encoding,'proof':proof_kind}
        if fixed_triples:
            encoded=json.dumps(list(fixed_triples),ensure_ascii=False,separators=(',',':')).encode('utf-8')
            self.scope['fixed_triple_route_sha256']=hashlib.sha256(encoded).hexdigest()
            self.scope['fixed_triples']=len(fixed_triples)

    def load(self):
        try:data=json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError,ValueError):return []
        # A clause proved after immutable clicks is invalid at other replay
        # boundaries, even when card IDs and the source map are unchanged.
        if data.get('scope')!=self.scope:return []
        return data.get('conflicts',[])

    def save(self,conflicts):
        unique=[];seen=set()
        for facts in conflicts:
            key=json.dumps(facts,ensure_ascii=False,sort_keys=True)
            if key not in seen:unique.append(facts);seen.add(key)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        atomic_json(self.path,{'scope':self.scope,'conflicts':unique})

    def boundary_proven(self):
        try:data=json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError,ValueError):return False
        return data.get('scope')==self.scope and data.get('boundary_infeasible') is True

    def save_boundary_proof(self):
        conflicts=self.load();self.path.parent.mkdir(parents=True,exist_ok=True)
        atomic_json(self.path,{'scope':self.scope,'conflicts':conflicts,'boundary_infeasible':True})
