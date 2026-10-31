"""Bounded exact endgame proofs shared by native worker processes."""
import os
import time
import array
import json
import struct


def legacy_hash(record):
    """Native MemoKey hash, used only to migrate an existing v1 proof cache."""
    mask=(1<<64)-1;v=struct.unpack('<9Qi',record)
    h=(v[0]*0x9e3779b185ebca87 ^ v[1]*0xc2b2ae3d27d4eb4f ^ v[2]*0x165667b19e3779f9 ^ v[3]*0x85ebca77c2b2ae63 ^ v[4])&mask
    h^=h>>33;h=h*0xff51afd7ed558ccd&mask;h^=h>>33;h&=0xffffffff
    for x in (*v[5:9],v[9]&0xffffffff):h=((h^x)*1099511628211)&mask
    h^=h>>33;h=h*0xff51afd7ed558ccd&mask;h^=h>>33
    return h&0xffffffff


class DeadStateCache:
    RECORD=80
    LIMIT=250000

    def __init__(self,path,digest):
        self.path=path;self.header=b'SSDEAD02'+digest.encode('ascii');self.records=set()
        self.dirty=False;self.last_save=0;self.write_deferred=False
        self.snapshot=None
        try:self.merge(path)
        except OSError:pass

    def merge(self,path):
        try:data=path.read_bytes()
        except OSError:return
        legacy=data[:8]==b'SSDEAD01';size=76 if legacy else self.RECORD
        if data[8:72]!=self.header[8:] or data[:8] not in (b'SSDEAD01',b'SSDEAD02') or (len(data)-72)%size:return
        for i in range(72,len(data),size):
            record=data[i:i+size]
            if legacy:record+=struct.pack('<I',legacy_hash(record))
            if record in self.records:continue
            if len(self.records)>=self.LIMIT:self.records.pop()
            self.records.add(record);self.dirty=True

    def save(self,force=False):
        if not self.dirty or (not force and time.monotonic()-self.last_save<10):return
        temp=self.path.with_name(self.path.name+f'.{os.getpid()}.tmp')
        self.last_save=time.monotonic()
        try:
            with temp.open('wb') as f:
                f.write(self.header)
                for record in self.records:f.write(record)
        except OSError:
            self.write_deferred=True;return False
        for attempt in range(12):
            try:os.replace(temp,self.path);break
            except PermissionError:
                if attempt==11:self.write_deferred=True;return False
                time.sleep(.02)
        self.last_save=time.monotonic();self.dirty=False;self.write_deferred=False
        try:self.make_snapshot()
        except OSError:self.write_deferred=True
        return True

    def make_snapshot(self):
        # Immutable tables allow workers to map the same pages without
        # rebuilding 250,000 managed hash entries on every invocation.
        capacity=1024
        while capacity<len(self.records)*2:capacity*=2
        buckets=array.array('i',[-1])*capacity;records=list(self.records)
        for i,record in enumerate(records):
            slot=struct.unpack_from('<I',record,76)[0]&(capacity-1)
            while buckets[slot]!=-1:slot=(slot+1)&(capacity-1)
            buckets[slot]=i
        path=self.path.with_name(f'{self.path.stem}.{time.time_ns()}.index.bin')
        with path.open('wb') as f:
            f.write(b'SSDTBL02'+self.header[8:]);f.write(struct.pack('<ii',capacity,len(records)))
            f.write(buckets.tobytes())
            for record in records:f.write(record)
        self.snapshot=path

    def prune_snapshots(self,leased):
        keep={str(p) for p in leased if p}
        if self.snapshot:keep.add(str(self.snapshot))
        for path in self.path.parent.glob(self.path.stem+'.*.index.bin'):
            if str(path) not in keep:
                try:path.unlink()
                except OSError:pass
