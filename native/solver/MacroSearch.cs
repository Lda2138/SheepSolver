// AGPL-3.0. Elimination-centered partial-order reduction.
// Before the next triple, postpone ordinary cards outside the dependency
// closure of its matching cards and all changing cards selected before it.
// This preserves colors, RNG, coverage and tray capacity after the postponed
// moves are restored. Optional changing cards must never be postponed.
using System;
using System.Collections.Generic;

partial class FastOriginalSolver {
    struct MacroCandidate {public Key Plan;public double Score;public int Target;}
    sealed class MacroWork {
        public Key[] Closures;public List<int>[] Types;public List<int> Changing;
        public Dictionary<Key,int> Seen=new Dictionary<Key,int>();public HashSet<Key> Planned=new HashSet<Key>();public List<MacroCandidate> Plans=new List<MacroCandidate>();
        public int[] Counts=new int[64];public int Free;
        public MacroWork(int n){Closures=new Key[n];Types=new List<int>[64];for(int t=0;t<64;t++)Types[t]=new List<int>();Changing=new List<int>();}
        public void Clear(){for(int t=0;t<64;t++)Types[t].Clear();Changing.Clear();Seen.Clear();Planned.Clear();Plans.Clear();}
    }
    long macroPlans,macroDuplicates;int macroFrontier;
    MacroWork[] macroWorkByDepth;int[][] macroMoves,macroOldCounts,macroPositions,macroTypes;
    void InitMacroWork(){if(macroWorkByDepth!=null)return;macroWorkByDepth=new MacroWork[n+1];macroMoves=new int[n][];macroOldCounts=new int[n][];macroPositions=new int[n][];macroTypes=new int[n][];}
    static bool Intersects(Key a,Key b){return ((a.A&b.A)|(a.B&b.B)|(a.C&b.C)|(a.D&b.D)|(a.E&b.E))!=0;}
    int[] SetMembers(Key k){
        int[] items=new int[Count(k)];int at=0;
        for(int w=0;w<5;w++){
            ulong bits=w==0?k.A:w==1?k.B:w==2?k.C:w==3?k.D:k.E;
            while(bits!=0){ulong low=bits&(~bits+1);int bit=0;ulong x=low;
                if((x&0xffffffffUL)==0){bit+=32;x>>=32;}if((x&0xffffUL)==0){bit+=16;x>>=16;}
                if((x&0xffUL)==0){bit+=8;x>>=8;}if((x&0xfUL)==0){bit+=4;x>>=4;}
                if((x&3UL)==0){bit+=2;x>>=2;}if((x&1UL)==0)bit++;
                items[at++]=w*64+bit;bits^=low;
            }
        }
        return items;
    }
    bool MacroBudget(){
        if(timeout)return false;
        if(clock.Elapsed.TotalSeconds>limit||states>stateLimit){timeout=true;stopReason=clock.Elapsed.TotalSeconds>limit?"time_limit":"state_limit";return false;}
        if(clock.Elapsed.TotalSeconds-lastReport>=.5)Report("searching");return !timeout;
    }
    void AddMacro(MacroWork work,Key closure,int target,int changingStart){
        int previous;
        if(work.Seen.TryGetValue(closure,out previous)&&previous<=changingStart){macroDuplicates++;return;}
        work.Seen[closure]=changingStart;
        if((++macroPlans&2047)==0&&!MacroBudget())return;
        Array.Copy(types,work.Counts,64);bool targetComplete=false;
        foreach(int i in SetMembers(closure)){
            int t=currentTypes[i];if(++work.Counts[t]> (t==target?3:2))return;
        }
        targetComplete=work.Counts[target]==3;
        if(targetComplete){
            bool terminal=false;
            foreach(int i in SetMembers(closure))if(currentTypes[i]==target&&!Intersects(childMasks[i],closure)){terminal=true;break;}
            if(terminal&&work.Planned.Add(closure)){
                int size=Count(closure);double layer=0,noise=0;foreach(int i in SetMembers(closure)){layer+=cards[i].Layer;noise+=tie[i];}
                double score=pickedCount<first*n?layer/size*10-size*.1:layer/size*.1-size*10;
                work.Plans.Add(new MacroCandidate{Plan=closure,Score=score+noise/size,Target=target});
            }
        }
        // A changing card already included by another closure needs no
        // separate choice. Enumerate all additional changing-card closures.
        for(int j=changingStart;j<work.Changing.Count;j++){
            int i=work.Changing[j];if(HasBit(closure,i))continue;
            Key next=Union(closure,work.Closures[i]);if(Count(next)>work.Free)continue;
            AddMacro(work,next,target,j+1);if(timeout)return;
        }
    }
    List<MacroCandidate> GenerateMacros(){
        PrepareGraph();InitSymmetry();InitMacroWork();var work=macroWorkByDepth[pickedCount];
        if(work==null){work=new MacroWork(n);macroWorkByDepth[pickedCount]=work;}else work.Clear();
        work.Free=7-trayCount;
        for(int i=0;i<n;i++)if(!picked[i]){
            Key closure=Except(ancestors[i],pickedBits);PutBit(ref closure,i);work.Closures[i]=closure;
            if(Count(closure)<=work.Free){work.Types[currentTypes[i]].Add(i);if(isSpecial[i])work.Changing.Add(i);}
        }
        // Seen is target-specific: the same set can be invalid for one
        // target but valid for another when its terminal type changes.
        for(int t=1;t<64;t++){
            work.Seen.Clear();int need=3-types[t];var options=work.Types[t];
            for(int a=0;a<options.Count;a++){
                Key one=work.Closures[options[a]];
                if(need==1){AddMacro(work,one,t,0);if(timeout)return work.Plans;continue;}
                for(int b=a+1;b<options.Count;b++){
                    Key two=Union(one,work.Closures[options[b]]);if(Count(two)>work.Free)continue;
                    if(need==2){AddMacro(work,two,t,0);if(timeout)return work.Plans;continue;}
                    for(int c=b+1;c<options.Count;c++){
                        Key three=Union(two,work.Closures[options[c]]);if(Count(three)>work.Free)continue;
                        AddMacro(work,three,t,0);if(timeout)return work.Plans;
                    }
                }
            }
        }
        return work.Plans;
    }
    bool MacroSearch(int depth){
        if(depth==startDepth&&rootAllowed!=null)throw new Exception("Macro experiment does not accept restricted root batches");
        if(!MacroBudget())return false;
        nodes++;
        if(depth>deepest){deepest=depth;Array.Copy(result,bestPrefix,depth);}
        if(depth==n)return trayCount==0;
        if(depth==startDepth&&IsDead(true))return false;
        var plans=GenerateMacros();if(timeout)return false;macroFrontier=plans.Count;
        plans.Sort((a,b)=>b.Score.CompareTo(a.Score));
        foreach(MacroCandidate candidate in plans){
            Key plan=candidate.Plan;
            int[] members=SetMembers(plan);int target=candidate.Target,terminal=-1;
            foreach(int i in members)if(currentTypes[i]==target){
                bool sink=!Intersects(childMasks[i],plan);
                if(sink){terminal=i;break;}
            }
            if(terminal<0)continue;
            int count=members.Length;
            if(macroMoves[depth]==null){macroMoves[depth]=new int[7];macroOldCounts[depth]=new int[7];macroPositions[depth]=new int[7];macroTypes[depth]=new int[7];}
            var moves=macroMoves[depth];var oldCounts=macroOldCounts[depth];var positions=macroPositions[depth];var pickedTypes=macroTypes[depth];
            int taken=0;
            while(taken<count){
                int next=-1;
                if(taken==count-1)next=terminal;
                else foreach(int i in members)if(i!=terminal&&!picked[i]&&parents[i]==0&&(next<0||cards[i].Layer>cards[next].Layer))next=i;
                if(next<0||parents[next]!=0)throw new Exception("Invalid macro closure ordering");
                int t=currentTypes[next];moves[taken]=next;pickedTypes[taken]=t;oldCounts[taken]=types[t];positions[taken]=TakeCard(next,depth+taken);taken++;
                if(trayCount>=7)throw new Exception("Macro exceeded tray capacity");
            }
            if(AddState()&&MacroSearch(depth+count))return true;
            for(int k=count-1;k>=0;k--)UndoCard(moves[k],depth+k,pickedTypes[k],oldCounts[k],positions[k]);
            if(timeout)return false;
        }
        return ProveDead(depth);
    }
}
