// AGPL-3.0. Exact short-horizon elimination checks and ordinary-card symmetry.
using System;

partial class FastOriginalSolver {
    readonly int[] tightAvailable=new int[64];int[] tightHeads;
    Key[] childMasks;int[] symmetryClass,symmetryStamps;int symmetryStamp;long tightChecks,symmetryPrunes;
    // With two slots left, either two exposed matching cards suffice, or
    // one exposed card reveals a matching card with exactly one parent left.
    // Colors stay fixed until the elimination. No PRNG prediction is needed.
    bool CanCrushInTwo(){
        Array.Clear(tightAvailable,0,64);
        for(int h=0;h<mainCount;h++){
            int t=currentTypes[main[h]];
            if(types[t]==2||(++tightAvailable[t]>=2&&types[t]==1))return true;
        }
        for(int h=0;h<mainCount;h++){
            int i=main[h],t=currentTypes[i];
            foreach(int child in cards[i].Children)if(parents[child]==1){
                int u=currentTypes[child];
                if(types[u]==2||(u==t&&types[t]==1))return true;
            }
        }
        return false;
    }
    bool CanCrushSoon(int depth){
        tightChecks++;
        if(trayCount==5)return CanCrushInTwo();
        if(trayCount!=4)return true;
        // Cheap sufficient tests before the bounded one-move lookahead.
        if(CanCrushInTwo())return true;
        for(int t=1;t<64;t++)if(tightAvailable[t]+types[t]>=3)return true;
        if(tightHeads==null)tightHeads=new int[n];
        int count=mainCount;Array.Copy(main,tightHeads,count);
        for(int h=0;h<count;h++){
            int i=tightHeads[h],t=currentTypes[i],old=types[t];
            int pos=TakeCard(i,depth);bool possible=CanCrushInTwo();
            UndoCard(i,depth,t,old,pos);
            if(possible)return true;
        }
        return false;
    }
    void InitSymmetry(){
        if(childMasks!=null)return;childMasks=new Key[n];
        for(int i=0;i<n;i++)foreach(int child in cards[i].Children)PutBit(ref childMasks[i],child);
        symmetryClass=new int[n];symmetryStamps=new int[n];int groups=0;
        for(int i=0;i<n;i++)if(!isSpecial[i]){
            int representative=-1;
            for(int j=0;j<i;j++)if(!isSpecial[j]&&currentTypes[j]==currentTypes[i]&&childMasks[j].Equals(childMasks[i])){representative=j;break;}
            symmetryClass[i]=representative<0?groups++:symmetryClass[representative];
        }
    }
    int RemoveSymmetricHeads(int[] head,int count){
        InitSymmetry();int kept=0;
        if(symmetryStamp==int.MaxValue){Array.Clear(symmetryStamps,0,n);symmetryStamp=0;}
        symmetryStamp++;
        for(int h=0;h<count;h++){
            int i=head[h];bool duplicate=false;
            // Every child of an exposed, unpicked card is still unpicked:
            // this card itself is a mandatory parent. Its full static child
            // mask is therefore exactly its remaining child mask.
            // Ordinary type + child mask defines a future-equivalent class.
            if(!isSpecial[i]){int group=symmetryClass[i];duplicate=symmetryStamps[group]==symmetryStamp;symmetryStamps[group]=symmetryStamp;}
            if(duplicate)symmetryPrunes++;else head[kept++]=i;
        }
        return kept;
    }
}
