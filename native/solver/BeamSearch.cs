// AGPL-3.0. Diverse beam search and a necessary first-triple constraint.
// Shares the exact seeded color state with FastOriginalSolver.
using System;
using System.Collections.Generic;
using System.Linq;

partial class FastOriginalSolver {
    ColorRules stateRng;int[] stateSpecialWork;
    Key[] covers,ancestors,ordinaryMasks;Key sideMask;int[] specialIndex;
    int beamWidth=768,beamFrontier;long constraintPrunes;bool graphReady;

    static void PutBit(ref Key k,int i){ulong b=1UL<<(i%64);switch(i/64){case 0:k.A|=b;break;case 1:k.B|=b;break;case 2:k.C|=b;break;case 3:k.D|=b;break;case 4:k.E|=b;break;}}
    static bool HasBit(Key k,int i){ulong w=i<64?k.A:i<128?k.B:i<192?k.C:i<256?k.D:k.E;return (w&(1UL<<(i%64)))!=0;}
    static Key Union(Key a,Key b){return new Key{A=a.A|b.A,B=a.B|b.B,C=a.C|b.C,D=a.D|b.D,E=a.E|b.E};}
    static bool Unremoved(Key a,Key removed){return ((a.A&~removed.A)|(a.B&~removed.B)|(a.C&~removed.C)|(a.D&~removed.D)|(a.E&~removed.E))!=0;}
    static int Pop(ulong x){unchecked{x=x-((x>>1)&0x5555555555555555UL);x=(x&0x3333333333333333UL)+((x>>2)&0x3333333333333333UL);return (int)((((x+(x>>4))&0x0f0f0f0f0f0f0f0fUL)*0x0101010101010101UL)>>56);}}
    static int Count(Key k){return Pop(k.A)+Pop(k.B)+Pop(k.C)+Pop(k.D)+Pop(k.E);}
    static int OtherCost(Key a,Key removed,Key same){return Pop(a.A&~removed.A&~same.A)+Pop(a.B&~removed.B&~same.B)+Pop(a.C&~removed.C&~same.C)+Pop(a.D&~removed.D&~same.D)+Pop(a.E&~removed.E&~same.E);}
    static int Color(DynamicKey k,int j){ulong w=j<10?k.C0:j<20?k.C1:j<30?k.C2:k.C3;return (int)((w>>((j%10)*6))&63);}
    static void SetColor(ref DynamicKey k,int j,int type){int shift=(j%10)*6;ulong mask=63UL<<shift,value=(ulong)type<<shift;if(j<10)k.C0=(k.C0&~mask)|value;else if(j<20)k.C1=(k.C1&~mask)|value;else if(j<30)k.C2=(k.C2&~mask)|value;else k.C3=(k.C3&~mask)|value;}
    static int Tray(DynamicKey k,int t){return (int)(((t<32?k.T0:k.T1)>>((t%32)*2))&3);}
    static void SetTray(ref DynamicKey k,int t,int count){int shift=(t%32)*2;ulong mask=3UL<<shift,value=(ulong)count<<shift;if(t<32)k.T0=(k.T0&~mask)|value;else k.T1=(k.T1&~mask)|value;}
    int Type(DynamicKey k,int i){return specialIndex[i]<0?currentTypes[i]:Color(k,specialIndex[i]);}
    void PrepareGraph(){
        if(graphReady)return;graphReady=true;stateRng=new ColorRules{Types=new int[0],Special=new int[0]};stateSpecialWork=new int[colors.Special.Length];covers=new Key[n];ancestors=new Key[n];ordinaryMasks=new Key[64];specialIndex=Enumerable.Repeat(-1,n).ToArray();
        for(int j=0;j<colors.Special.Length;j++)specialIndex[colors.Special[j]]=j;
        for(int i=0;i<n;i++){foreach(int child in cards[i].Children)PutBit(ref covers[child],i);if(!isSpecial[i])PutBit(ref ordinaryMasks[currentTypes[i]],i);}
        Array.Copy(covers,ancestors,n);
        // Numeric layers are ordered low to high. Every parent index is later
        // than its children, so one descending pass closes all dependencies.
        for(int i=n-1;i>=0;i--)foreach(int child in cards[i].Children)ancestors[child]=Union(ancestors[child],ancestors[i]);
        int[] groups=Enumerable.Range(0,n).ToArray();
        Func<int,int> root=null;root=i=>groups[i]==i?i:(groups[i]=root(groups[i]));
        for(int i=0;i<n;i++)foreach(int child in cards[i].Children)groups[root(child)]=root(i);
        var sizes=Enumerable.Range(0,n).GroupBy(i=>root(i)).OrderByDescending(g=>g.Count()).ToArray();
        if(sizes.Length>1){int largest=sizes[0].Key;for(int i=0;i<n;i++)if(root(i)!=largest)PutBit(ref sideMask,i);}
    }

    Scratch constraintScratch=new Scratch();
    sealed class Scratch {public Key[] Masks=new Key[64];public int[] Available=new int[64],Viable=new int[64],Nearest=new int[64];}
    // Before the next triple, colors cannot change and at most 7-trayCount
    // cards can be picked. Completing type t needs 3-currentTray[t] such cards.
    // Each selected card's non-t ancestors are mandatory. If fewer than need
    // cards can fit those ancestors plus need matching picks in that budget,
    // this type cannot form the first triple. If no type can, the state is dead.
    // Shared ancestors are counted only within one card: this is a lower bound,
    // so it deliberately permits false positives but never removes a solution.
    bool Evaluate(DynamicKey k,int trayCount,Scratch work,out double score,int style){
        Array.Copy(ordinaryMasks,work.Masks,64);Array.Clear(work.Available,0,64);Array.Clear(work.Viable,0,64);
        for(int t=0;t<64;t++)work.Nearest[t]=1000;
        for(int j=0;j<colors.Special.Length;j++){int t=Color(k,j);if(t>0)PutBit(ref work.Masks[t],colors.Special[j]);}
        int free=7-trayCount;bool feasible=false;int exposed=0;
        for(int i=0;i<n;i++)if(!HasBit(k.Board,i)){
            int t=Type(k,i),need=3-Tray(k,t);
            bool open=!Unremoved(covers[i],k.Board);if(open){work.Available[t]++;exposed++;}
            if(work.Viable[t]<need||Tray(k,t)>0){
                int debt=OtherCost(ancestors[i],k.Board,work.Masks[t]);
                if(debt<work.Nearest[t])work.Nearest[t]=debt;
                if(debt+need<=free&&++work.Viable[t]>=need)feasible=true;
            }
        }
        if(!feasible){constraintPrunes++;score=double.NegativeInfinity;return false;}
        score=-trayCount*(style==1?3.0:6.0);int singles=0;
        for(int t=1;t<64;t++){
            int held=Tray(k,t),available=work.Available[t];
            if(held>0){
                if(held==1)singles++;
                score+=Math.Min(available,3-held)*(held==2?5.5:2.0);
                score-=Math.Min(20,work.Nearest[t])*(held==2?2.0:1.0);
            }else if(available>=3)score+=style==2?4.0:2.0;
        }
        score-=singles*1.5;score+=Math.Min(20,exposed)*.08;
        return true;
    }

    bool Step(DynamicKey parent,int i,int trayCount,out DynamicKey next,out int t,out int tray){
        t=Type(parent,i);int held=Tray(parent,t);next=parent;PutBit(ref next.Board,i);tray=trayCount+1;
        SetTray(ref next,t,held==2?0:held+1);if(held==2)tray-=3;if(tray>=7)return false;
        if(specialIndex[i]>=0)SetColor(ref next,specialIndex[i],0);
        if(held==2&&colors.Enabled){
            int count=0;for(int j=0;j<colors.Special.Length;j++)if(Color(next,j)>0)stateSpecialWork[count++]=j;
            if(count>1){stateRng.A=next.R0;stateRng.B=next.R1;for(int k=count-1;k>=0;k--){int at=(int)(stateRng.Next()*(k+1)),a=stateSpecialWork[k],b=stateSpecialWork[at];int v=Color(next,a);SetColor(ref next,a,Color(next,b));SetColor(ref next,b,v);}next.R0=stateRng.A;next.R1=stateRng.B;}
        }
        return true;
    }
    sealed class BeamNode {public DynamicKey Key;public BeamNode Parent;public int Card,Type,Depth,TrayCount;public double Score;}
    struct Candidate {public DynamicKey Key;public BeamNode Parent;public int Card,Type,TrayCount;public double Score;}
    sealed class Bucket {
        public readonly int Limit;public readonly List<Candidate> Items=new List<Candidate>();int worst;double worstScore;
        public Bucket(int limit){Limit=limit;worstScore=double.PositiveInfinity;}
        void FindWorst(){worst=0;worstScore=Items[0].Score;for(int i=1;i<Items.Count;i++)if(Items[i].Score<worstScore){worst=i;worstScore=Items[i].Score;}}
        public void Add(Candidate c){if(Items.Count<Limit){Items.Add(c);if(Items.Count==Limit)FindWorst();return;}if(c.Score>worstScore){Items[worst]=c;FindWorst();}}
    }
    void BeamPath(BeamNode node){for(var at=node;at.Parent!=null;at=at.Parent){result[at.Depth-1]=at.Card;resultTypes[at.Depth-1]=at.Type;}if(node.Depth>deepest){deepest=node.Depth;Array.Copy(result,bestPrefix,deepest);}}
    bool BeamSearch(){
        PrepareGraph();int width=Math.Max(128,Math.Min(8192,beamWidth));beamWidth=width;
        int bins=16;while(bins*2<=Math.Min(128,width/8))bins*=2;int per=Math.Max(1,width/bins),style=random.Next(3);
        var initial=new BeamNode{Key=DynamicState(),Depth=startDepth,TrayCount=trayCount};var frontier=new List<BeamNode>{initial};var scratch=new Scratch();
        for(int depth=startDepth;depth<n;depth++){
            var buckets=Enumerable.Range(0,bins).Select(_=>new Bucket(per)).ToArray();var seen=new HashSet<DynamicKey>();
            foreach(var parent in frontier){
                if(clock.Elapsed.TotalSeconds>limit||timeout){timeout=true;stopReason=stopReason==""?"time_limit":stopReason;return false;}
                if(clock.Elapsed.TotalSeconds-lastReport>=.5)Report("searching");
                for(int i=0;i<n;i++)if(!HasBit(parent.Key.Board,i)&&!Unremoved(covers[i],parent.Key.Board)){
                    nodes++;DynamicKey next;int t,tray;if(!Step(parent.Key,i,parent.TrayCount,out next,out t,out tray))continue;
                    if(!seen.Add(next))continue;
                    if(depth+1==n&&tray==0){var winner=new BeamNode{Key=next,Parent=parent,Card=i,Type=t,Depth=n,TrayCount=0};BeamPath(winner);return true;}
                    double score;if(!Evaluate(next,tray,scratch,out score,style))continue;
                    // Preserve variation in side-stack use and tray composition.
                    // Pure top-K otherwise collapses into one attractive family.
                    int side=Pop(next.Board.A&sideMask.A)+Pop(next.Board.B&sideMask.B)+Pop(next.Board.C&sideMask.C)+Pop(next.Board.D&sideMask.D)+Pop(next.Board.E&sideMask.E);
                    ulong trayHash=next.T0*0x9e3779b185ebca87UL^next.T1;trayHash^=trayHash>>33;
                    int bucket=((side%8)*8+(tray>=4?4:0)+(int)(trayHash&3))%bins;
                    score+=(random.NextDouble()-.5)*(style==1?1.5:.15);
                    buckets[bucket].Add(new Candidate{Key=next,Parent=parent,Card=i,Type=t,TrayCount=tray,Score=score});
                }
            }
            frontier=new List<BeamNode>(width);
            foreach(var bucket in buckets)foreach(var c in bucket.Items)frontier.Add(new BeamNode{Key=c.Key,Parent=c.Parent,Card=c.Card,Type=c.Type,Depth=depth+1,TrayCount=c.TrayCount,Score=c.Score});
            states+=frontier.Count;beamFrontier=frontier.Count;
            if(frontier.Count==0){stopReason="beam_frontier_empty";timeout=true;return false;}
            BeamPath(frontier.OrderByDescending(x=>x.Score).First());
        }
        stopReason="beam_width";timeout=true;return false;
    }
}
