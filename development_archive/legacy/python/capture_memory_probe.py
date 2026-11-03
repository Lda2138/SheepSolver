"""Read a bounded snapshot from our own CPython 3.13 capture program.

No injection or process writes. Only the known AutoCapture instance fields
needed for game-seed diagnostics are decoded. No raw dump or stream is saved.
"""
import ctypes as C
import struct
from map_acquire import K, MBI


class Reader:
    def __init__(self, pid):
        self.handle=K.OpenProcess(0x0400|0x0010,False,pid)
        if not self.handle:
            raise ValueError('无法读取抓取程序')

    def read(self,address,size):
        if not 0<address<0x7fffffffffff or not 0<size<=2*1024**2:
            raise ValueError('读取范围无效')
        buffer=C.create_string_buffer(size);actual=C.c_size_t()
        if not K.ReadProcessMemory(self.handle,address,buffer,size,C.byref(actual)) or actual.value!=size:
            raise ValueError('读取未完成')
        return buffer.raw

    def u64(self,address):
        return struct.unpack('<Q',self.read(address,8))[0]

    def regions(self):
        address=total=0
        while address<0x7fffffffffff and total<512*1024**2:
            mbi=MBI()
            if not K.VirtualQueryEx(self.handle,address,C.byref(mbi),C.sizeof(mbi)):break
            base,size=mbi.base or 0,mbi.size;address=base+size
            if not size:break
            if mbi.state!=0x1000 or mbi.type!=0x20000 or mbi.protect&0x101 or not mbi.protect&0xcc:continue
            for offset in range(0,size,1024**2):
                length=min(size-offset,1024**2)
                try:data=self.read(base+offset,length)
                except ValueError:continue
                total+=length
                yield base+offset,data

    def typename(self,address):
        typeptr=self.u64(address+8)
        nameptr=self.u64(typeptr+24)
        return self.read(nameptr,64).split(b'\0',1)[0].decode('ascii')

    def value(self,address,depth=0):
        if depth>5:raise ValueError('对象嵌套过深')
        name=self.typename(address)
        if name=='NoneType':return None
        if name in ('int','bool'):
            tag=self.u64(address+16);size=tag>>3
            if size>3:raise ValueError('整数过大')
            value=0
            if size:
                digits=struct.unpack('<'+'I'*size,self.read(address+24,4*size))
                value=sum(d<<(30*i) for i,d in enumerate(digits))
            return -value if tag&3==2 else value
        if name=='str':
            length=self.u64(address+16);state=struct.unpack('<I',self.read(address+32,4))[0]
            if length>1024 or not state&32:raise ValueError('只允许短紧凑字符串')
            if state&64:return self.read(address+40,max(1,length))[:length].decode('ascii')
            kind=(state>>2)&7
            if kind not in (1,2,4):raise ValueError('字符串编码异常')
            raw=self.read(address+56,max(1,length*kind))[:length*kind]
            return raw.decode({1:'latin1',2:'utf-16-le',4:'utf-32-le'}[kind])
        if name=='bytes':
            length=self.u64(address+16)
            if length>1024**2:raise ValueError('密文长度异常')
            return self.read(address+32,max(1,length))[:length]
        if name in ('list','tuple'):
            length=self.u64(address+16)
            if length>20:raise ValueError('序列长度异常')
            start=self.u64(address+24) if name=='list' else address+24
            return [self.value(self.u64(start+8*i),depth+1) for i in range(length)]
        if name=='dict':
            used=self.u64(address+16);keys=self.u64(address+32);vals=self.u64(address+40)
            if used>50 or vals:raise ValueError('只允许小型合并字典')
            header=self.read(keys,32);logbytes=header[9];kind=header[10]
            nentries=struct.unpack_from('<Q',header,24)[0]
            if nentries>60 or logbytes>10 or kind not in (0,1):raise ValueError('字典布局无效')
            entries=keys+32+(1<<logbytes);result={}
            for i in range(nentries):
                at=entries+i*(24 if kind==0 else 16)
                kp=self.u64(at+(8 if kind==0 else 0));vp=self.u64(at+(16 if kind==0 else 8))
                if kp and vp:result[self.value(kp,depth+1)]=self.value(vp,depth+1)
            if len(result)!=used:raise ValueError('字典未稳定')
            return result
        raise ValueError('对象类型未允许')

    def close(self):
        K.CloseHandle(self.handle)


def snapshot(pid,session):
    r=Reader(pid)
    try:
        regions=list(r.regions())
        names=[]
        for base,data in regions:
            at=0
            while True:
                at=data.find(b'AutoCapture\0',at)
                if at<0:break
                names.append(base+at);at+=1
        types=set()
        for name in names:
            needle=struct.pack('<Q',name)
            for base,data in regions:
                at=0
                while True:
                    at=data.find(needle,at)
                    if at<0:break
                    if at>=24 and (base+at-24)%8==0:
                        candidate=base+at-24
                        try:
                            if r.u64(candidate+32)==16 and r.typename(candidate)=='type':types.add(candidate)
                        except (ValueError,UnicodeError):pass
                    at+=1
        for typ in types:
            needle=struct.pack('<Q',typ)
            for base,data in regions:
                at=0
                while True:
                    at=data.find(needle,at)
                    if at<0:break
                    obj=base+at-8;at+=1
                    if obj%8:continue
                    try:
                        # CPython 3.13 inline instance values: third word is
                        # values header, followed by attributes in source order.
                        state=r.value(r.u64(obj+18*8))
                        if not isinstance(state,dict) or state.get('capture_pid')!=pid or state.get('session')!=session:continue
                        current=r.value(r.u64(obj+12*8))
                        if current.get('id')!=session:continue
                        return {'current':current,'streams':r.value(r.u64(obj+16*8)),
                                'seed_prefix':r.value(r.u64(obj+17*8)),
                                'pending':r.value(r.u64(obj+15*8))}
                    except (ValueError,UnicodeError,AttributeError,TypeError):pass
        raise ValueError('未定位到当前抓取对象')
    finally:r.close()
