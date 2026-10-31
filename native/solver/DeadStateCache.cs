// AGPL-3.0. Persist exact, completely refuted endgame states across workers.
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.IO.MemoryMappedFiles;

partial class FastOriginalSolver {
    HashSet<MemoKey> sharedDead,newDead;long deadHits;int deadLoaded;
    MemoryMappedFile cacheMapping;MemoryMappedViewAccessor cacheView;int cacheCapacity;long cacheRecords;
    void ReadDeadCache(string path){
        sharedDead=new HashSet<MemoKey>();newDead=new HashSet<MemoKey>();
        if(string.IsNullOrEmpty(path)||!File.Exists(path))return;
        try{
            using(var reader=new BinaryReader(File.Open(path,FileMode.Open,FileAccess.Read,FileShare.ReadWrite|FileShare.Delete))){
                string magic=Encoding.ASCII.GetString(reader.ReadBytes(8));
                if(Encoding.ASCII.GetString(reader.ReadBytes(64))!=mapHash)return;
                if(magic=="SSDTBL02"){
                    cacheCapacity=reader.ReadInt32();deadLoaded=reader.ReadInt32();cacheRecords=80L+cacheCapacity*4L;
                    if(cacheCapacity<1024||cacheCapacity>524288||(cacheCapacity&(cacheCapacity-1))!=0||deadLoaded<0||deadLoaded>250000||reader.BaseStream.Length!=cacheRecords+deadLoaded*80L)return;
                    cacheMapping=MemoryMappedFile.CreateFromFile((FileStream)reader.BaseStream,null,0,MemoryMappedFileAccess.Read,null,HandleInheritability.None,true);
                    cacheView=cacheMapping.CreateViewAccessor(0,0,MemoryMappedFileAccess.Read);return;
                }
                int size=magic=="SSDEAD01"?76:magic=="SSDEAD02"?80:0;
                if(size==0||(reader.BaseStream.Length-72)%size!=0)return;
                while(reader.BaseStream.Position<reader.BaseStream.Length&&sharedDead.Count<250000){
                    var k=new MemoKey{Board=new Key{A=reader.ReadUInt64(),B=reader.ReadUInt64(),C=reader.ReadUInt64(),D=reader.ReadUInt64(),E=reader.ReadUInt64()},
                        C0=reader.ReadUInt64(),C1=reader.ReadUInt64(),C2=reader.ReadUInt64(),C3=reader.ReadUInt64(),Draws=reader.ReadInt32()};
                    if(size==80)reader.ReadInt32();
                    sharedDead.Add(k);
                }
                deadLoaded=sharedDead.Count;
            }
        }catch(IOException){sharedDead.Clear();deadLoaded=0;}
        catch(UnauthorizedAccessException){sharedDead.Clear();deadLoaded=0;}
    }
    bool IsDead(bool root=false){
        if(sharedDead==null||(cacheView==null&&sharedDead.Count==0)||(!root&&pickedCount<n-48))return false;
        var key=MemoState();
        if(cacheView!=null){
            int slot=key.GetHashCode()&(cacheCapacity-1);
            for(int step=0;step<cacheCapacity;step++){
                int index=cacheView.ReadInt32(80L+slot*4L);if(index<0)return false;
                if(index>=deadLoaded)return false;
                long at=cacheRecords+index*80L;
                if(cacheView.ReadInt32(at+76)==key.GetHashCode()&&
                   cacheView.ReadUInt64(at)==key.Board.A&&cacheView.ReadUInt64(at+8)==key.Board.B&&cacheView.ReadUInt64(at+16)==key.Board.C&&
                   cacheView.ReadUInt64(at+24)==key.Board.D&&cacheView.ReadUInt64(at+32)==key.Board.E&&
                   cacheView.ReadUInt64(at+40)==key.C0&&cacheView.ReadUInt64(at+48)==key.C1&&cacheView.ReadUInt64(at+56)==key.C2&&
                   cacheView.ReadUInt64(at+64)==key.C3&&cacheView.ReadInt32(at+72)==key.Draws){deadHits++;return true;}
                slot=(slot+1)&(cacheCapacity-1);
            }
            return false;
        }
        if(sharedDead.Contains(key)){deadHits++;return true;}
        return false;
    }
    bool ProveDead(int depth){
        // Restricted sibling batches do not prove the whole parent dead.
        // Cutoffs never enter this method. Only fully expanded descendants do.
        bool parent=depth==startDepth&&rootAllowed==null;
        if(newDead!=null&&(depth>=n-48||parent)&&!(depth==startDepth&&rootAllowed!=null)){
            if(parent&&newDead.Count>=50000){var it=newDead.GetEnumerator();it.MoveNext();var drop=it.Current;it.Dispose();newDead.Remove(drop);}
            if(newDead.Count<50000)newDead.Add(MemoState());
        }
        return false;
    }
    void SaveDeadCache(){
        if(newDead==null||string.IsNullOrEmpty(progressPath))return;
        string path=progressPath+".dead.bin",temp=path+".tmp";
        using(var writer=new BinaryWriter(File.Create(temp))){
            writer.Write(Encoding.ASCII.GetBytes("SSDEAD02"));writer.Write(Encoding.ASCII.GetBytes(mapHash));
            foreach(var k in newDead){writer.Write(k.Board.A);writer.Write(k.Board.B);writer.Write(k.Board.C);writer.Write(k.Board.D);writer.Write(k.Board.E);
                writer.Write(k.C0);writer.Write(k.C1);writer.Write(k.C2);writer.Write(k.C3);writer.Write(k.Draws);writer.Write(k.GetHashCode());}
        }
        if(File.Exists(path))File.Delete(path);File.Move(temp,path);
    }
}
