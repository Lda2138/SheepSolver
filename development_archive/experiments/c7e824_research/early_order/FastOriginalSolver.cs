// Accelerated implementation of NB-Dragon/SheepSolver's existing policies.
// Preserves coverage graph, seven-slot pruning, type ordering and top-state memo.
// AGPL-3.0, like the upstream project. No desktop or network operations.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Web.Script.Serialization;

partial class FastOriginalSolver
{
    struct Key : IEquatable<Key> {
        public ulong A,B,C,D,E;
        public bool Equals(Key o){return A==o.A&&B==o.B&&C==o.C&&D==o.D&&E==o.E;}
        public override bool Equals(object o){return o is Key&&Equals((Key)o);}
        public override int GetHashCode(){unchecked{ulong h=A*0x9e3779b185ebca87UL^B*0xc2b2ae3d27d4eb4fUL^C*0x165667b19e3779f9UL^D*0x85ebca77c2b2ae63UL^E;h^=h>>33;h*=0xff51afd7ed558ccdUL;h^=h>>33;return (int)h;}}
    }
    struct DynamicKey : IEquatable<DynamicKey> {
        public Key Board;public ulong R0,R1,C0,C1,C2,C3,T0,T1;
        public bool Equals(DynamicKey o){return Board.Equals(o.Board)&&R0==o.R0&&R1==o.R1&&C0==o.C0&&C1==o.C1&&C2==o.C2&&C3==o.C3&&T0==o.T0&&T1==o.T1;}
        public override bool Equals(object o){return o is DynamicKey&&Equals((DynamicKey)o);}
        public override int GetHashCode(){unchecked{ulong h=(ulong)Board.GetHashCode();h=(h^R0)*1099511628211UL;h=(h^R1)*1099511628211UL;h=(h^C0)*1099511628211UL;h=(h^C1)*1099511628211UL;h=(h^C2)*1099511628211UL;h=(h^C3)*1099511628211UL;h=(h^T0)*1099511628211UL;h=(h^T1)*1099511628211UL;h^=h>>33;h*=0xff51afd7ed558ccdUL;h^=h>>33;return (int)h;}}
    }
    ColorRules colors;int[] currentTypes,resultTypes,bestPrefix;int startDepth;int fewestChanging=int.MaxValue;string stopReason="",mapHash;string bestPath;int[][] colorBackup,shuffleWork;ulong[] rngA,rngB;int[] rngDraws;
    Key pickedBits;ulong trayBits0,trayBits1;bool[] isSpecial;int specialRemaining,pickedCount,savedDepth=-1;int[][] ranks;IComparer<int> candidateComparer;
    struct MemoKey : IEquatable<MemoKey> {
        public Key Board;public ulong C0,C1,C2,C3;public int Draws;
        public bool Equals(MemoKey o){return Board.Equals(o.Board)&&C0==o.C0&&C1==o.C1&&C2==o.C2&&C3==o.C3&&Draws==o.Draws;}
        public override bool Equals(object o){return o is MemoKey&&Equals((MemoKey)o);}
        public override int GetHashCode(){unchecked{ulong h=(uint)Board.GetHashCode();h=(h^C0)*1099511628211UL;h=(h^C1)*1099511628211UL;h=(h^C2)*1099511628211UL;h=(h^C3)*1099511628211UL;h=(h^(uint)Draws)*1099511628211UL;h^=h>>33;h*=0xff51afd7ed558ccdUL;h^=h>>33;return (int)h;}}
    }
    HashSet<MemoKey>[] dynamicVisited=Enumerable.Range(0,64).Select(_=>new HashSet<MemoKey>()).ToArray();
    MemoKey MemoState(){
        // Type counts are invariant under shuffle; eliminations remove three.
        // Therefore board + remaining colors determine every tray count mod 3.
        // For a fixed seed, the draw count determines the complete PRNG state.
        bool uniform=(specialTypeMask&(specialTypeMask-1))==0;
        return new MemoKey{Board=pickedBits,C0=packedSpecial.C0,C1=packedSpecial.C1,
            C2=packedSpecial.C2,C3=packedSpecial.C3,Draws=uniform?0:colors.Draws};
    }
    DynamicKey packedSpecial;int[] packedIndex;ulong specialTypeMask;
    void SetPackedSpecial(int i,int type){SetColor(ref packedSpecial,packedIndex[i],type);}
    void RepackSpecial(){
        packedSpecial.C0=packedSpecial.C1=packedSpecial.C2=packedSpecial.C3=0;
        specialTypeMask=0;
        foreach(int i in colors.Special)if(!picked[i]){SetPackedSpecial(i,currentTypes[i]);specialTypeMask|=1UL<<currentTypes[i];}
    }
    void RefreshSpecialMask(){specialTypeMask=0;foreach(int i in colors.Special)if(!picked[i])specialTypeMask|=1UL<<currentTypes[i];}
    DynamicKey DynamicState(){
        var k=packedSpecial;k.Board=pickedBits;k.T0=trayBits0;k.T1=trayBits1;
        k.R0=colors.A;k.R1=colors.B;
        return k;
    }
    class Card {public string Id;public int Type,Layer,X,Y;public List<int> Children=new List<int>();}
    Card[] cards;int n;int[] parents,types,order,result,main;int mainCount,orderCount,trayCount;double[] tie;
    bool[] picked;int[][] heads;Key top;HashSet<Key>[] visited=Enumerable.Range(0,64).Select(_=>new HashSet<Key>()).ToArray();int states;
    bool AddState(){if(IsDead())return false;bool added;if(colors.Enabled&&specialRemaining>0){var key=MemoState();added=dynamicVisited[key.GetHashCode()&63].Add(key);}else added=visited[pickedBits.GetHashCode()&63].Add(pickedBits);if(added)states++;return added;}
    Stopwatch clock=Stopwatch.StartNew();double limit,first,lastReport;string mode,progressPath;Random random;long nodes;int deepest;bool timeout;int stateLimit=6000000;
    void Report(string state) {
        lastReport=clock.Elapsed.TotalSeconds;
        SaveBest();
        if(!string.IsNullOrEmpty(progressPath)&&File.Exists(progressPath+".control.json"))try{
            var control=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(progressPath+".control.json"));
            if(control.ContainsKey("stop")&&Convert.ToBoolean(control["stop"])){timeout=true;stopReason="stopped";}
            if(control.ContainsKey("state_limit"))stateLimit=Math.Max(50000,Convert.ToInt32(control["state_limit"]));
        }catch(IOException){}catch(ArgumentException){}

        if(string.IsNullOrEmpty(progressPath))return;
        string temp=progressPath+".tmp";
        File.WriteAllText(temp,json.Serialize(new Dictionary<string,object>{{"state",state},{"nodes",nodes},{"deepest",deepest},{"total",n},{"states",states},{"elapsed_seconds",lastReport},{"mode",mode},{"solve_first",first},{"fixed_prefix",startDepth},{"remaining_cards",n-startDepth},{"stop_reason",stopReason},{"beam_width",(mode=="beam"||mode.StartsWith("static-beam-"))?beamWidth:0},{"beam_frontier",beamFrontier},{"static_beam_max_frontier",staticBeamMaxFrontier},{"constraint_prunes",constraintPrunes},{"rollouts",rollouts},{"policy_epoch",policyEpoch},{"dead_cache_hits",deadHits},{"dead_cache_loaded",deadLoaded},{"dead_cache_exported",newDead==null?0:newDead.Count},{"macro_plans",macroPlans},{"macro_duplicates",macroDuplicates},{"macro_frontier",macroFrontier},{"tight_checks",tightChecks},{"symmetry_prunes",symmetryPrunes},{"crush_checks",crushChecks},{"crush_cache_hits",crushCacheHits},{"fewest_changing",fewestChanging==int.MaxValue?colors.Special.Length:fewestChanging}}));
        // UI readers briefly hold a Windows file handle. Progress must never
        // abort an otherwise valid search because a reader is mid-read.
        for(int attempt=0;attempt<8;attempt++)try{if(File.Exists(progressPath))File.Replace(temp,progressPath,null);else File.Move(temp,progressPath);return;}catch(IOException){System.Threading.Thread.Sleep(15);}
    }
    void SaveBest(){if(!string.IsNullOrEmpty(bestPath)&&deepest>savedDepth){
        string temp=bestPath+".tmp";File.WriteAllText(temp,json.Serialize(new Dictionary<string,object>{{"sequence",bestPrefix.Take(deepest).Select(i=>cards[i].Id).ToArray()},{"deepest",deepest}}));
        for(int a=0;a<8;a++)try{if(File.Exists(bestPath))File.Replace(temp,bestPath,null);else File.Move(temp,bestPath);savedDepth=deepest;break;}catch(IOException){System.Threading.Thread.Sleep(10);}
    }}
    void PickBit(int i,bool value){ulong b=1UL<<(i%64);switch(i/64){case 0:pickedBits.A=value?pickedBits.A|b:pickedBits.A&~b;break;case 1:pickedBits.B=value?pickedBits.B|b:pickedBits.B&~b;break;case 2:pickedBits.C=value?pickedBits.C|b:pickedBits.C&~b;break;case 3:pickedBits.D=value?pickedBits.D|b:pickedBits.D&~b;break;case 4:pickedBits.E=value?pickedBits.E|b:pickedBits.E&~b;break;}}
    void TrayBits(int t){int shift=(t%32)*2;ulong mask=3UL<<shift,value=(ulong)types[t]<<shift;if(t<32)trayBits0=(trayBits0&~mask)|value;else trayBits1=(trayBits1&~mask)|value;}
    static object[] Items(object x){return ((System.Collections.IEnumerable)x).Cast<object>().ToArray();}
    static JavaScriptSerializer json=new JavaScriptSerializer{MaxJsonLength=4*1024*1024};
    void Bit(int i,bool value){ulong b=1UL<<(i%64);int k=i/64;switch(k){case 0:top.A=value?top.A|b:top.A&~b;break;case 1:top.B=value?top.B|b:top.B&~b;break;case 2:top.C=value?top.C|b:top.C&~b;break;case 3:top.D=value?top.D|b:top.D&~b;break;case 4:top.E=value?top.E|b:top.E&~b;break;default:throw new Exception("Map too large");}}
    void Init(string path) {
        using(var sha=System.Security.Cryptography.SHA256.Create())mapHash=BitConverter.ToString(sha.ComputeHash(File.ReadAllBytes(path))).Replace("-","").ToLowerInvariant();
        var data=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(path));var levels=(Dictionary<string,object>)data["levelData"];
        var rawCards=levels.OrderBy(k=>int.Parse(k.Key)).SelectMany(k=>Items(k.Value).Select(o=>{var c=(Dictionary<string,object>)o;c["layer"]=int.Parse(k.Key);return c;})).ToArray();
        colors=ColorRules.FromCards(rawCards);if(data.ContainsKey("_has_gold_rules")&&Convert.ToBoolean(data["_has_gold_rules"]))throw new Exception("Gold rules not implemented");
        if(colors.Enabled){var pool=(Dictionary<string,object>)data["blockTypeData"];colors.Seed(data["map_seed"],pool.Values.Sum(v=>Convert.ToInt32(v))*3);colors.Shuffle(new bool[rawCards.Length]);}
        currentTypes=colors.Types;tie=Enumerable.Range(0,rawCards.Length).Select(_=>random.NextDouble()).ToArray();
        cards=levels.OrderBy(k=>int.Parse(k.Key)).SelectMany(k=>Items(k.Value).Select(o=>{var c=(Dictionary<string,object>)o;return new Card{Id=(string)c["id"],Type=Convert.ToInt32(c["type"]),Layer=int.Parse(k.Key),X=Convert.ToInt32(c["rolNum"]),Y=Convert.ToInt32(c["rowNum"])};})).ToArray();n=cards.Length;
        if(n>320||cards.Any(c=>c.Type<=0||c.Type>63))throw new Exception("Unsupported map size/types");
        parents=new int[n];picked=new bool[n];types=new int[64];order=new int[64];result=new int[n];heads=Enumerable.Range(0,n+1).Select(_=>new int[n]).ToArray();main=new int[n];resultTypes=new int[n];bestPrefix=new int[n];colorBackup=Enumerable.Range(0,n).Select(_=>new int[colors.Special.Length]).ToArray();shuffleWork=Enumerable.Range(0,n).Select(_=>new int[colors.Special.Length]).ToArray();rngA=new ulong[n];rngB=new ulong[n];rngDraws=new int[n];ranks=Enumerable.Range(0,n).Select(_=>new int[64]).ToArray();isSpecial=new bool[n];foreach(int i in colors.Special)isSpecial[i]=true;specialRemaining=colors.Special.Length;packedIndex=Enumerable.Repeat(-1,n).ToArray();for(int j=0;j<colors.Special.Length;j++)packedIndex[colors.Special[j]]=j;RepackSpecial();
        candidateComparer=Comparer<int>.Create((a,b)=>mode=="index-ascending"?a.CompareTo(b):mode=="index-descending"?b.CompareTo(a):cards[a].Layer!=cards[b].Layer?(mode=="level-bottom"?cards[a].Layer.CompareTo(cards[b].Layer):cards[b].Layer.CompareTo(cards[a].Layer)):(mode=="color-top"||mode=="constraint-top"||mode=="crush-top"||mode=="tight-top"||mode=="two-top"||mode=="symmetry-top")?tie[a].CompareTo(tie[b]):a.CompareTo(b));
        if(mode=="tight-top")candidateComparer=Comparer<int>.Create((a,b)=>(cards[b].Layer+tie[b]*16).CompareTo(cards[a].Layer+tie[a]*16));
        for(int i=0;i<n;i++)for(int j=0;j<n;j++)if(cards[i].Layer>cards[j].Layer&&Math.Abs(cards[i].X-cards[j].X)<8&&Math.Abs(cards[i].Y-cards[j].Y)<8){cards[i].Children.Add(j);parents[j]++;}
        for(int i=0;i<n;i++)if(parents[i]==0){Bit(i,true);main[mainCount++]=i;}
        AddState();
    }
    HashSet<string> rootAllowed;
    void SetRootAllowed(object value){
        if(value==null)return;
        rootAllowed=new HashSet<string>(Items(value).Select(Convert.ToString));
        if(rootAllowed.Count==0||rootAllowed.Any(id=>!main.Take(mainCount).Any(i=>cards[i].Id==id)))throw new Exception("Invalid checkpoint root alternatives");
    }
    int TakeCard(int i,int depth){int t=currentTypes[i],old=types[t],pos=-1;
            if(old==0)order[orderCount++]=t;
            types[t]++;trayCount++;
            if(types[t]==3){types[t]=0;trayCount-=3;pos=Array.IndexOf(order,t,0,orderCount);Array.Copy(order,pos+1,order,pos,orderCount-pos-1);orderCount--;}
            TrayBits(t);picked[i]=true;pickedCount++;PickBit(i,true);if(isSpecial[i]){specialRemaining--;SetPackedSpecial(i,0);RefreshSpecialMask();}Bit(i,false);int at=Array.IndexOf(main,i,0,mainCount);Array.Copy(main,at+1,main,at,mainCount-at-1);mainCount--;
            foreach(int child in cards[i].Children){parents[child]--;if(parents[child]==0){Bit(child,true);main[mainCount++]=child;}}
            result[depth]=i;resultTypes[depth]=t;
            if(colors.Enabled&&old==2){rngA[depth]=colors.A;rngB[depth]=colors.B;rngDraws[depth]=colors.Draws;for(int j=0;j<colors.Special.Length;j++)colorBackup[depth][j]=currentTypes[colors.Special[j]];colors.Shuffle(picked,shuffleWork[depth]);RepackSpecial();}
        return pos;
    }
    void UndoCard(int i,int depth,int t,int old,int pos){
            if(colors.Enabled&&old==2){colors.A=rngA[depth];colors.B=rngB[depth];colors.Draws=rngDraws[depth];for(int j=0;j<colors.Special.Length;j++)currentTypes[colors.Special[j]]=colorBackup[depth][j];RepackSpecial();}
            // Upstream restores the picked card at the end of main_zone.
            foreach(int child in cards[i].Children){if(parents[child]==0){Bit(child,false);int ix=Array.IndexOf(main,child,0,mainCount);Array.Copy(main,ix+1,main,ix,mainCount-ix-1);mainCount--;}parents[child]++;}
            main[mainCount++]=i;picked[i]=false;pickedCount--;PickBit(i,false);if(isSpecial[i]){specialRemaining++;SetPackedSpecial(i,currentTypes[i]);RefreshSpecialMask();}Bit(i,true);
            if(old==0){orderCount--;types[t]=0;trayCount--;}
            else if(old==1){types[t]=1;trayCount--;}
            else {Array.Copy(order,pos,order,pos+1,orderCount-pos);order[pos]=t;orderCount++;types[t]=2;trayCount+=2;}
            TrayBits(t);
    }
    void ApplyPrefix(string[] prefix){
        if(prefix.Length>n)throw new Exception("Checkpoint longer than map");
        var ids=Enumerable.Range(0,n).ToDictionary(i=>cards[i].Id,i=>i);
        for(int depth=0;depth<prefix.Length;depth++){
            int i;if(!ids.TryGetValue(prefix[depth],out i)||picked[i]||parents[i]!=0)throw new Exception("Invalid checkpoint: covered/duplicate/unknown card at "+depth);
            if(mode=="check-bound"){PrepareGraph();double score;Key witness;if(!Evaluate(DynamicState(),trayCount,constraintScratch,out score,0)||!PlanFirstCrush(DynamicState(),trayCount,out witness,false))throw new Exception("Constraint rejected prefix at "+depth);}
            if(mode=="check-tight"&&trayCount>=4&&trayCount<=5&&!CanCrushSoon(depth))throw new Exception("Tight bound rejected prefix at "+depth);
            TakeCard(i,depth);if(trayCount>=7)throw new Exception("Invalid checkpoint: tray overflow at "+depth);
        }
        startDepth=prefix.Length;deepest=startDepth;Array.Copy(result,bestPrefix,deepest);
        // Rebuild directly by deterministic legal replay. This derives all
        // dependencies, color permutation, PRNG words and tray state from the
        // verified map; it never trusts a stale serialized state blindly.
        foreach(var set in visited)set.Clear();foreach(var set in dynamicVisited)set.Clear();states=0;AddState();
        if(!string.IsNullOrEmpty(progressPath))File.WriteAllText(progressPath+".checkpoint.json",json.Serialize(new Dictionary<string,object>{
            {"map_sha256",mapHash},{"fixed_prefix",startDepth},{"sequence",prefix},{"remaining_types",currentTypes},
            {"picked",picked},{"parents",parents},{"exposed",main.Take(mainCount).ToArray()},
            {"tray_counts",types},{"tray_order",order.Take(orderCount).ToArray()},{"tray_count",trayCount},
            {"rng_a",colors.A.ToString("X16")},{"rng_b",colors.B.ToString("X16")}}));
    }
    int trayLimit=6;
    bool Search(int depth) {
        if(timeout||states>stateLimit){timeout=true;if(stopReason=="")stopReason="state_limit";CaptureCutoff(depth);return false;}
        if(depth==startDepth&&IsDead(true))return false;
        if(specialRemaining<fewestChanging)fewestChanging=specialRemaining;
        if(depth>deepest){deepest=depth;Array.Copy(result,bestPrefix,depth);}
        if(depth==n)return trayCount==0;
        if((++nodes&32767)==0) {
            if(clock.Elapsed.TotalSeconds>limit||states>stateLimit){timeout=true;stopReason=clock.Elapsed.TotalSeconds>limit?"time_limit":"state_limit";CaptureCutoff(depth);return false;}
            if(clock.Elapsed.TotalSeconds-lastReport>=0.5)Report("searching");if(timeout){CaptureCutoff(depth);return false;}
        }
        if(orderCount==6)return ProveDead(depth);
        if((mode=="tight-top"||mode=="two-top")&&trayCount>= (mode=="two-top"?5:4)&&trayCount<=5&&!CanCrushSoon(depth)){constraintPrunes++;return ProveDead(depth);}
        if(mode=="constraint-top"&&trayCount>=3){double score;if(!Evaluate(DynamicState(),trayCount,constraintScratch,out score,0))return ProveDead(depth);}
        if((mode=="guided-top"||mode=="crush-top")&&trayCount>=3){Key witness;if(!PlanFirstCrush(DynamicState(),trayCount,out witness,false))return ProveDead(depth);}
        int[] head=heads[depth];int count=0;
        for(int h=0;h<mainCount;h++) {
            int i=main[h],t=currentTypes[i];
            if(trayCount+(types[t]==2?-2:1)>trayLimit)continue;
            if(depth==startDepth&&rootAllowed!=null&&!rootAllowed.Contains(cards[i].Id))continue;
            if(trayCount==6?types[t]!=2:orderCount==5&&types[t]==0)continue;
            head[count++]=i;
        }
        if(mode=="index-ascending"||mode=="level-bottom"||mode=="index-descending"||mode=="level-top"||mode=="color-top"||mode=="constraint-top"||mode=="crush-top"||mode=="tight-top"||mode=="two-top"||mode=="symmetry-top")Array.Sort(head,0,count,candidateComparer);
        if(mode=="symmetry-top")count=RemoveSymmetricHeads(head,count);
        if(mode=="random")for(int i=count-1;i>0;i--){int j=random.Next(i+1),tmp=head[i];head[i]=head[j];head[j]=tmp;}
        if(depth>=first*n) {
            // The upstream stable order groups existing tray types by count.
            int[] rank=ranks[depth];for(int i=0;i<64;i++)rank[i]=100;
            int r=0;for(int c=2;c>=1;c--)for(int j=0;j<orderCount;j++)if(types[order[j]]==c)rank[order[j]]=r++;
            // Stable insertion sort, preserving the policy order within each type.
            for(int i=1;i<count;i++){int v=head[i],j=i-1;while(j>=0&&rank[currentTypes[head[j]]]>rank[currentTypes[v]]){head[j+1]=head[j];j--;}head[j+1]=v;}
        }
        if(mode=="color-distance"){
            int[] nearest=new int[64],second=new int[64];for(int t=0;t<64;t++)nearest[t]=second[t]=1000;
            for(int i=0;i<n;i++)if(!picked[i]){int t=currentTypes[i],cost=parents[i];if(cost<nearest[t]){second[t]=nearest[t];nearest[t]=cost;}else if(cost<second[t])second[t]=cost;}
            Array.Sort(head,0,count,Comparer<int>.Create((a,b)=>{
                int ta=currentTypes[a],tb=currentTypes[b];
                double sa=types[ta]*4-second[ta]*.12+cards[a].Layer*.35+(colors.Special.Contains(a)?1.5:0)+tie[a]*.6;
                double sb=types[tb]*4-second[tb]*.12+cards[b].Layer*.35+(colors.Special.Contains(b)?1.5:0)+tie[b]*.6;
                return sb.CompareTo(sa);
            }));
        }
        if(mode=="color-adaptive"){
            int[] available=new int[64];for(int h=0;h<mainCount;h++)available[currentTypes[main[h]]]++;
            Array.Sort(head,0,count,Comparer<int>.Create((a,b)=>{
                int ta=currentTypes[a],tb=currentTypes[b];
                double sa=types[ta]*1000+(types[ta]+available[ta]>=3?300:0)+(colors.Special.Contains(a)?80:0)+cards[a].Layer*3+cards[a].Children.Count*0.2;
                double sb=types[tb]*1000+(types[tb]+available[tb]>=3?300:0)+(colors.Special.Contains(b)?80:0)+cards[b].Layer*3+cards[b].Children.Count*0.2;
                return sb.CompareTo(sa);
            }));
        }
        if(mode=="guided-top")GuideOrder(head,count,depth);
        if(mode=="freeze-top")FreezeOrder(head,count,depth);
        for(int h=0;h<count;h++) {
            int i=head[h],t=currentTypes[i],old=types[t],pos=-1;
            pos=TakeCard(i,depth);
            if(trayCount<7&&AddState()&&Search(depth+1))return true;
            UndoCard(i,depth,t,old,pos);
            if(timeout){PendingFrame(depth,head,h+1,count);return false;}
        }return ProveDead(depth);
    }
    static int Main(string[] args) {
        try {
            if(args.Length<5)throw new Exception("map output mode solve_first seconds [random_seed]");
            var s=new FastOriginalSolver{mode=args[2],first=double.Parse(args[3],System.Globalization.CultureInfo.InvariantCulture),limit=double.Parse(args[4]),random=new Random(args.Length>5?int.Parse(args[5]):1),stateLimit=args.Length>6?int.Parse(args[6]):6000000};
            if(args.Length>10){s.trayLimit=int.Parse(args[10]);if(s.trayLimit!=5&&s.trayLimit!=6)throw new Exception("Invalid tray capacity");}
            s.progressPath=args.Length>7?args[7]:null;s.bestPath=args[1]+".best.json";s.Init(args[0]);if(args.Length>8){var checkpoint=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(args[8]));if(checkpoint.ContainsKey("map_sha256")&&Convert.ToString(checkpoint["map_sha256"])!=s.mapHash)throw new Exception("Checkpoint map mismatch");s.ApplyPrefix(Items(checkpoint["sequence"]).Select(Convert.ToString).ToArray());if(checkpoint.ContainsKey("allowed"))s.SetRootAllowed(checkpoint["allowed"]);if(checkpoint.ContainsKey("dead_cache"))s.ReadDeadCache(Convert.ToString(checkpoint["dead_cache"]));}if(s.mode=="beam"||s.mode=="constraint-top"||s.mode=="nrpa"||s.mode=="crush-top")s.PrepareGraph();if(args.Length>9)s.beamWidth=int.Parse(args[9]);if(s.mode=="probe-memo"){Console.WriteLine(json.Serialize(s.MemoState()));return 0;}if(s.mode=="probe-tight"){Console.WriteLine(json.Serialize(new Dictionary<string,object>{{"possible",s.CanCrushSoon(s.startDepth)},{"tray",s.trayCount}}));return 0;}if(s.mode=="check-tight"){Console.WriteLine("CHECKED_TIGHT_BOUND "+s.startDepth);return 0;}if(s.mode=="check-bound"){Console.WriteLine("CHECKED_FIRST_TRIPLE_BOUND "+s.startDepth);return 0;}s.Report("searching");bool solved=s.mode.StartsWith("static-beam-")?s.StaticBeamSearch():s.mode=="beam"?s.BeamSearch():s.mode=="nrpa"?s.RolloutSearch():s.mode=="macro"?s.MacroSearch(s.startDepth):s.Search(s.startDepth);s.SaveFrontier();s.SaveDeadCache();s.Report(solved?"candidate":s.timeout?"limited":"exhausted");File.WriteAllText(s.bestPath,json.Serialize(new Dictionary<string,object>{{"sequence",s.bestPrefix.Take(s.deepest).Select(i=>s.cards[i].Id).ToArray()},{"deepest",s.deepest}}));
            Console.WriteLine("solved="+solved+" deepest="+s.deepest+" nodes="+s.nodes+" seconds="+s.clock.Elapsed.TotalSeconds.ToString("F1"));
            if(solved){Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(args[1])));File.WriteAllText(args[1],json.Serialize(new Dictionary<string,object>{{"sequence",s.result.Select(i=>s.cards[i].Id).ToArray()},{"sequence_types",s.resultTypes},{"rules_engine",s.colors.Enabled?"color_shuffle_v1":"standard"},{"mode",s.mode},{"solve_first",s.first},{"elapsed_seconds",s.clock.Elapsed.TotalSeconds},{"engine","accelerated_original_policies"},{"nodes",s.nodes}}));return 0;}return 2;
        }catch(Exception e){Console.Error.WriteLine(e.GetType().Name+": "+e.Message);return 1;}
    }
}
