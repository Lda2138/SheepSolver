// AGPL-3.0. Fixed-color diverse beam with global backfill and excavation.
using System;
using System.Collections.Generic;
using System.Linq;

partial class FastOriginalSolver {
    int staticBeamMaxFrontier;
    sealed class StaticNode {
        public DynamicKey Key;public Key Available;public StaticNode Parent;
        public int Card,Type,Depth,TrayCount;public double Score,Excavation;
    }
    struct StaticCandidate {
        public DynamicKey Key;public Key Available;public bool EasyTriple;public StaticNode Parent;
        public int Card,Type,TrayCount;public double Score,Excavation;
    }
    // A bounded min-heap avoids rescanning every retained candidate on each
    // replacement. The global heap backfills space left by empty buckets.
    sealed class StaticHeap {
        public readonly List<StaticCandidate> Items=new List<StaticCandidate>();
        readonly int limit;
        public StaticHeap(int count){limit=count;}
        public void Add(StaticCandidate value){
            if(limit<=0)return;
            if(Items.Count<limit){
                int at=Items.Count;Items.Add(value);
                while(at>0){int p=(at-1)/2;if(Items[p].Score<=value.Score)break;Items[at]=Items[p];at=p;}
                Items[at]=value;return;
            }
            if(value.Score<=Items[0].Score)return;
            int i=0;
            while(i*2+1<Items.Count){int child=i*2+1;
                if(child+1<Items.Count&&Items[child+1].Score<Items[child].Score)child++;
                if(Items[child].Score>=value.Score)break;Items[i]=Items[child];i=child;
            }
            Items[i]=value;
        }
    }
    void StaticBeamPath(StaticNode node){
        for(var at=node;at.Parent!=null;at=at.Parent){result[at.Depth-1]=at.Card;resultTypes[at.Depth-1]=at.Type;}
        if(node.Depth>deepest){deepest=node.Depth;Array.Copy(result,bestPrefix,deepest);}
    }
    static Key Intersection(Key a,Key b){return new Key{A=a.A&b.A,B=a.B&b.B,C=a.C&b.C,D=a.D&b.D,E=a.E&b.E};}
    static Key Without(Key a,Key b){return new Key{A=a.A&~b.A,B=a.B&~b.B,C=a.C&~b.C,D=a.D&~b.D,E=a.E&~b.E};}
    static void ClearStaticBit(ref Key k,int i){ulong bit=~(1UL<<(i%64));switch(i/64){case 0:k.A&=bit;break;case 1:k.B&=bit;break;case 2:k.C&=bit;break;case 3:k.D&=bit;break;case 4:k.E&=bit;break;}}
    static List<StaticCandidate> StaticSelection(StaticHeap global,StaticHeap[] buckets,int width){
        var result=new List<StaticCandidate>();var seen=new HashSet<Key>();
        foreach(var bucket in buckets)foreach(var c in bucket.Items)if(seen.Add(c.Key.Board))result.Add(c);
        foreach(var c in global.Items.OrderByDescending(c=>c.Score))if(result.Count<width&&seen.Add(c.Key.Board))result.Add(c);
        return result;
    }
    bool StaticBeamSearch(){
        if(colors.Enabled||colors.Special.Length!=0)throw new Exception("Static beam only supports ordinary fixed colors");
        if(startDepth==n)return trayCount==0;
        PrepareGraph();int width=Math.Max(128,Math.Min(131072,beamWidth));beamWidth=width;
        int bins=64,quota=Math.Max(1,width/(bins*4));
        int[] depthFromTop=new int[n];
        for(int i=n-1;i>=0;i--)foreach(int c in cards[i].Children)depthFromTop[c]=Math.Max(depthFromTop[c],depthFromTop[i]+1);
        var dig=new double[n];
        double strength=mode.EndsWith("-dig")?.04:mode.EndsWith("-balance")?.012:0;
        for(int i=0;i<n;i++)dig[i]=HasBit(sideMask,i)?0:depthFromTop[i]*depthFromTop[i]*strength;
        Key initialAvailable=new Key();for(int i=0;i<n;i++)if(!picked[i]&&!Unremoved(covers[i],pickedBits))PutBit(ref initialAvailable,i);
        var palette=cards.Select(c=>c.Type).Distinct().ToArray();
        var typed=palette.ToDictionary(t=>t,t=>Enumerable.Range(0,n).Where(i=>currentTypes[i]==t).ToArray());
        var frontier=new List<StaticNode>{new StaticNode{Key=DynamicState(),Available=initialAvailable,Depth=startDepth,TrayCount=trayCount}};
        var scratch=new Scratch();
        for(int depth=startDepth;depth<n;depth++){
            int shortlist=width*3;
            var global=new StaticHeap(shortlist);var buckets=Enumerable.Range(0,bins).Select(_=>new StaticHeap(quota*3)).ToArray();
            var seen=new HashSet<Key>();
            foreach(var parent in frontier){
                if(clock.Elapsed.TotalSeconds>limit||timeout){timeout=true;stopReason=stopReason==""?"time_limit":stopReason;return false;}
                if(clock.Elapsed.TotalSeconds-lastReport>=.5)Report("searching");
                for(int i=0;i<n;i++)if(!HasBit(parent.Key.Board,i)&&!Unremoved(covers[i],parent.Key.Board)){
                    nodes++;DynamicKey next;int t,tray;
                    if(!Step(parent.Key,i,parent.TrayCount,out next,out t,out tray)||!seen.Add(next.Board))continue;
                    double excavation=parent.Excavation+dig[i];
                    if(depth+1==n&&tray==0){StaticBeamPath(new StaticNode{Key=next,Parent=parent,Card=i,Type=t,Depth=n,TrayCount=0});return true;}
                    Key available=parent.Available;ClearStaticBit(ref available,i);
                    foreach(int child in cards[i].Children)if(!HasBit(next.Board,child)&&!Unremoved(covers[child],next.Board))PutBit(ref available,child);
                    double score=-tray*6;bool easy=false;
                    foreach(int v in palette){
                        int held=Tray(next,v),open=Count(Intersection(available,ordinaryMasks[v])),need=3-held;
                        if(open>=need&&need<=7-tray)easy=true;
                        if(held>0){score+=Math.Min(open,need)*(held==2?5.5:2.0);if(held==1)score-=1.5;}
                        else if(open>=3)score+=2;
                    }
                    score+=Math.Min(20,Count(available))*.08;
                    score+=excavation+(random.NextDouble()-.5)*.03;
                    int side=Count(new Key{A=next.Board.A&sideMask.A,B=next.Board.B&sideMask.B,C=next.Board.C&sideMask.C,D=next.Board.D&sideMask.D,E=next.Board.E&sideMask.E});
                    ulong hash=next.T0*0x9e3779b185ebca87UL^next.T1;hash^=hash>>33;
                    int bucket=(side%8)*8+(tray>=4?4:0)+(int)(hash&3);
                    var c=new StaticCandidate{Key=next,Available=available,EasyTriple=easy,Parent=parent,Card=i,Type=t,TrayCount=tray,Score=score,Excavation=excavation};
                    global.Add(c);buckets[bucket].Add(c);
                }
            }
            // Expensive dependency debt is evaluated only on a three-width
            // shortlist, not on every permutation generated by every parent.
            var refined=new StaticHeap(width);var diverse=Enumerable.Range(0,bins).Select(_=>new StaticHeap(quota)).ToArray();
            foreach(var item in StaticSelection(global,buckets,shortlist)){
                var c=item;double ignored;
                if(!c.EasyTriple&&!Evaluate(c.Key,c.TrayCount,scratch,out ignored,0))continue;
                foreach(int v in palette)if(Tray(c.Key,v)>0){
                    int held=Tray(c.Key,v),need=3-held;
                    if(Count(Intersection(c.Available,ordinaryMasks[v]))>=need)continue;
                    int a=1000,b=1000;
                    foreach(int j in typed[v])if(!HasBit(c.Key.Board,j)){
                        int debt=OtherCost(ancestors[j],c.Key.Board,ordinaryMasks[v]);
                        if(debt<a){b=a;a=debt;}else if(debt<b)b=debt;
                    }
                    c.Score-=Math.Min(24,held==1?b:a)*(mode.EndsWith("-safe")?2.5:1.0);
                }
                int side=Count(Intersection(c.Key.Board,sideMask));ulong hash=c.Key.T0*0x9e3779b185ebca87UL^c.Key.T1;hash^=hash>>33;
                int bucket=(side%8)*8+(c.TrayCount>=4?4:0)+(int)(hash&3);
                refined.Add(c);diverse[bucket].Add(c);
            }
            var keep=StaticSelection(refined,diverse,width);
            frontier=new List<StaticNode>(keep.Count);
            foreach(var c in keep)frontier.Add(new StaticNode{Key=c.Key,Available=c.Available,Parent=c.Parent,Card=c.Card,Type=c.Type,Depth=depth+1,TrayCount=c.TrayCount,Score=c.Score,Excavation=c.Excavation});
            states+=frontier.Count;beamFrontier=frontier.Count;
            staticBeamMaxFrontier=Math.Max(staticBeamMaxFrontier,frontier.Count);
            if(frontier.Count==0){stopReason="beam_frontier_empty";timeout=true;return false;}
            StaticBeamPath(frontier.OrderByDescending(x=>x.Score).First());
        }
        stopReason="beam_width";timeout=true;return false;
    }
}
