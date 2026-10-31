// AGPL-3.0. Bounded real-rule lookahead; this never claims a full-map proof.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Web.Script.Serialization;

// A state begins at the confirmed prefix. Capacity applies only to new picks,
// after ordinary triple elimination and BEFORE an alien clears the tray.
class RollingState {
    public Dictionary<string,object>[] Cards;
    public int[][] Parents;
    public bool[] Picked;
    public List<int> Tray=new List<int>();
    public ColorRules Color;
    public EnergyRules Energy;
    public int Count {get{return Cards.Length;}}
    public int[] Slots {get{return Energy!=null?Energy.Tray:Tray.ToArray();}}
    public bool Left(int i){return Energy!=null?Energy.Remaining[i]:!Picked[i];}
    public bool Exposed(int i){return Energy!=null?Energy.Exposed(i):!Picked[i]&&!Parents[i].Any(j=>!Picked[j]);}
    public int Type(int i){return Energy!=null?Energy.Type(i):Color.Types[i];}
    public string Id(int i){return Convert.ToString(Cards[i]["id"]);}
    public int Remaining {get{return Enumerable.Range(0,Count).Count(Left);}}
    public bool Complete {get{return Remaining==0&&Slots.Length==0;}}
    static int N(Dictionary<string,object> d,string k){return d.ContainsKey(k)?Convert.ToInt32(d[k]):0;}
    public RollingState(Dictionary<string,object> input){
        Cards=RollingPlanner.Items(input["cards"]).Select(o=>(Dictionary<string,object>)o).ToArray();
        var rules=input.ContainsKey("rules")?input["rules"] as Dictionary<string,object>:null;
        if(rules!=null&&Convert.ToString(rules["kind"])=="energy_alien_v1")Energy=new EnergyRules(Cards,rules);
        else Color=ColorRules.FromSolution(input,Cards);
        Picked=new bool[Count];
        Parents=Enumerable.Range(0,Count).Select(i=>Enumerable.Range(0,Count).Where(j=>N(Cards[j],"layer")>N(Cards[i],"layer")&&Math.Abs(N(Cards[j],"rolNum")-N(Cards[i],"rolNum"))<8&&Math.Abs(N(Cards[j],"rowNum")-N(Cards[i],"rowNum"))<8).ToArray()).ToArray();
    }
    public RollingState Copy(){
        var s=(RollingState)MemberwiseClone();
        if(Energy!=null)s.Energy=Energy.Copy();
        else {s.Picked=(bool[])Picked.Clone();s.Tray=new List<int>(Tray);s.Color=new ColorRules{Types=(int[])Color.Types.Clone(),Special=Color.Special,A=Color.A,B=Color.B,Draws=Color.Draws};}
        return s;
    }
    public bool Step(int i,int cap,out bool elimination){
        elimination=false;if(!Exposed(i))return false;
        int t=Type(i),held=Slots.Count(x=>x==t),stable=Slots.Length+(held==2?-2:1);
        if(stable>cap||stable>=7)return false;
        if(Energy!=null){var e=Energy.Step(Id(i));elimination=e.Crush||e.Alien;}
        else {Tray.Add(t);elimination=held==2;if(elimination)Tray.RemoveAll(x=>x==t);Picked[i]=true;if(elimination&&Color.Enabled)Color.Shuffle(Picked);}
        return true;
    }
    public string Key(){
        if(Energy!=null)return Energy.Key();
        var chars=new char[Count];for(int i=0;i<Count;i++)chars[i]=(char)(Picked[i]?1:Color.Types[i]+2);
        return new string(chars)+":"+Color.A+":"+Color.B+":"+Color.Draws+":"+string.Join(",",Tray);
    }
    public double Rank(int events,int depth){
        int[] slots=Slots;var held=slots.GroupBy(t=>t).ToDictionary(g=>g.Key,g=>g.Count());
        var visible=Enumerable.Range(0,Count).Where(Exposed).ToArray();
        double score=events*50-slots.Length*14-held.Count*5;
        foreach(var g in visible.GroupBy(Type)){
            int n=held.ContainsKey(g.Key)?held[g.Key]:0;
            if(n+g.Count()>=3)score+=n>0?14:5;
            else if(n>0)score-=9;
        }
        if(Energy!=null){
            score+=Energy.CollectedCount*12;
            if(Energy.PotentialEnergy>=Energy.Threshold)score+=35;
            score+=visible.Count(i=>Energy.Charge(i)>0)*2;
        }
        return score+visible.Length*.2;
    }
    public static void CheckLookahead(Dictionary<string,object> segment){
        if(!segment.ContainsKey("tray_capacity"))return; // Legacy segments remain inspectable.
        int cap=Convert.ToInt32(segment["tray_capacity"]),start=Convert.ToInt32(segment["start_step"]);
        if(cap!=5&&cap!=6)throw new Exception("Invalid rolling tray capacity");
        var sequence=RollingPlanner.Items(segment["sequence"]).Select(Convert.ToString).ToArray();
        var route=RollingPlanner.Items(segment["lookahead_sequence"]).Select(Convert.ToString).ToArray();
        if(route.Length>64||!route.Take(sequence.Length-start).SequenceEqual(sequence.Skip(start)))throw new Exception("Lookahead does not extend committed segment");
        var s=new RollingState(segment);var index=s.Cards.Select((c,i)=>new {Id=Convert.ToString(c["id"]),I=i}).ToDictionary(x=>x.Id,x=>x.I);
        bool hit;foreach(string id in sequence.Take(start))if(!index.ContainsKey(id)||!s.Step(index[id],6,out hit))throw new Exception("Invalid confirmed rolling prefix");
        int events=0,afterSegment=0;
        for(int k=0;k<route.Length;k++){
            string id=route[k];if(!index.ContainsKey(id)||!s.Step(index[id],cap,out hit))throw new Exception("Rolling lookahead violates coverage or capacity");
            if(hit){events++;if(k>=sequence.Length-start)afterSegment++;}
        }
        int required=Convert.ToInt32(segment["lookahead_target"]);
        if(required<3||required>8||events!=Convert.ToInt32(segment["lookahead_events"])||(!s.Complete&&(events<required||afterSegment<1)))throw new Exception("Insufficient future elimination evidence");
    }
}

class RollingPlanner {
    static JavaScriptSerializer Json=new JavaScriptSerializer{MaxJsonLength=32000000};
    public static object[] Items(object v){return ((System.Collections.IEnumerable)v).Cast<object>().ToArray();}
    sealed class Node {public RollingState State;public int[] Route;public int Events,Peak;public double Score;}
    Stopwatch clock=Stopwatch.StartNew();long nodes;int depth,cap,deepEvents;double lastWrite=-1;bool trimmed;
    string progress;int stateLimit,target;bool endgame;List<Dictionary<string,object>> attempts=new List<Dictionary<string,object>>();
    static void Write(string path,object data){
        string tmp=path+".tmp";File.WriteAllText(tmp,Json.Serialize(data),new UTF8Encoding(false));
        for(int k=0;k<8;k++)try{if(File.Exists(path))File.Replace(tmp,path,null);else File.Move(tmp,path);return;}catch(IOException){System.Threading.Thread.Sleep(10);}
        throw new IOException("Could not publish rolling search status");
    }
    void Report(string state,bool force=false){
        if(!force&&clock.Elapsed.TotalSeconds-lastWrite<.15)return;lastWrite=clock.Elapsed.TotalSeconds;
        Write(progress,new {state=state,tray_capacity=cap,horizon_target=target,lookahead_events=deepEvents,
            lookahead_depth=depth,nodes=nodes,nodes_per_second=(long)(nodes/Math.Max(.01,clock.Elapsed.TotalSeconds)),
            elapsed_seconds=Math.Round(clock.Elapsed.TotalSeconds,2),state_limit=stateLimit,limited=trimmed,attempts=attempts});
    }
    sealed class Choice {public int I,Stable;public double Score;}
    Node best;double deadline,foundAt;long searchStart;int maxDepth,candidates;
    HashSet<string> visited;
    void Explore(Node n){
        if(clock.Elapsed.TotalSeconds>=deadline||nodes-searchStart>=stateLimit){trimmed=true;return;}
        if(best!=null&&(candidates>=32||clock.Elapsed.TotalSeconds-foundAt>=.08))return;
        depth=Math.Max(depth,n.Route.Length);deepEvents=Math.Max(deepEvents,n.Events);
        if(n.State.Complete||(!endgame&&n.Events>=target)){
            n.Score=n.State.Rank(n.Events,n.Route.Length)-n.Route.Length*.3;
            if(best==null){best=n;foundAt=clock.Elapsed.TotalSeconds;}
            else if(n.State.Complete&&!best.State.Complete||n.State.Complete==best.State.Complete&&n.Score>best.Score)best=n;
            candidates++;return;
        }
        if(n.Route.Length>=maxDepth)return;
        string key=n.Events+":"+n.Route.Length+":"+n.State.Key();if(!visited.Add(key))return;
        var s=n.State;int[] slots=s.Slots;
        var held=slots.GroupBy(t=>t).ToDictionary(g=>g.Key,g=>g.Count());
        var visible=Enumerable.Range(0,s.Count).Where(s.Exposed).ToArray();
        var available=visible.GroupBy(s.Type).ToDictionary(g=>g.Key,g=>g.Count());
        var choices=new List<Choice>();
        foreach(int i in visible){
            int t=s.Type(i),count=held.ContainsKey(t)?held[t]:0,stable=slots.Length+(count==2?-2:1);
            if(stable>cap||stable>=7)continue;
            double score=count==2?500:count==1?90:0;
            if(available[t]+count>=3)score+=60;
            if(s.Energy!=null&&s.Energy.Charge(i)>0)score+=180+(5-s.Energy.Charge(i))*20;
            // Favor a useful match first. No irreversible pick is made until
            // this branch also supplies the required future eliminations.
            choices.Add(new Choice{I=i,Stable=stable,Score=score});
        }
        foreach(var c in choices.OrderByDescending(c=>c.Score).ThenBy(c=>c.I)){
            if(clock.Elapsed.TotalSeconds>=deadline||nodes-searchStart>=stateLimit){trimmed=true;break;}
            if(best!=null&&(candidates>=32||clock.Elapsed.TotalSeconds-foundAt>=.08))break;
            var next=s.Copy();bool hit;if(!next.Step(c.I,cap,out hit))continue;nodes++;
            var route=new int[n.Route.Length+1];Array.Copy(n.Route,route,n.Route.Length);route[route.Length-1]=c.I;
            Explore(new Node{State=next,Route=route,Events=n.Events+(hit?1:0),Peak=Math.Max(n.Peak,c.Stable)});
            Report("searching");
        }
    }
    Node Search(RollingState root,double until,out string outcome){
        trimmed=false;deepEvents=0;depth=0;best=null;candidates=0;foundAt=0;deadline=until;searchStart=nodes;
        visited=new HashSet<string>();maxDepth=endgame?root.Remaining:target*(cap+1);
        Explore(new Node{State=root,Route=new int[0],Events=0,Peak=0});
        // Exhaustion is scoped to this exact state, cap and event horizon.
        // A time/node limit is unknown. A reachable first elimination followed
        // by horizon failure also does not prove the original map unsolvable.
        outcome=best!=null?"lookahead_found":trimmed?"limited":deepEvents==0?"no_next_elimination":"lookahead_exhausted";
        return best;
    }
    int Run(string[] args){
        var input=Json.Deserialize<Dictionary<string,object>>(File.ReadAllText(args[0]));var root=new RollingState(input);
        var index=root.Cards.Select((c,i)=>new {Id=Convert.ToString(c["id"]),I=i}).ToDictionary(v=>v.Id,v=>v.I);
        bool hit;foreach(string id in Items(input["prefix"]).Select(Convert.ToString))if(!index.ContainsKey(id)||!root.Step(index[id],6,out hit))throw new Exception("Invalid confirmed prefix");
        double seconds=Convert.ToDouble(input["seconds"]);if(double.IsNaN(seconds)||seconds<=0||seconds>120)throw new Exception("Invalid rolling time budget");
        stateLimit=Convert.ToInt32(input["state_limit"]);target=Convert.ToInt32(input["horizon_target"]);
        if(stateLimit<1024||stateLimit>1048576||target<3||target>8)throw new Exception("Invalid lookahead bounds");
        endgame=root.Remaining<=24;progress=args[2];
        int first=input.ContainsKey("fixed_capacity")?Convert.ToInt32(input["fixed_capacity"]):5;
        if(first!=5&&first!=6)throw new Exception("Invalid initial capacity");
        string outcome="limited";Node result=null;
        for(cap=first;cap<=6;cap++){
            Report("searching",true);double begin=clock.Elapsed.TotalSeconds;long before=nodes;
            double budget=input.ContainsKey("fixed_capacity")?seconds:cap==5?seconds*.6:seconds;
            result=Search(root,budget,out outcome);
            attempts.Add(new Dictionary<string,object>{{"capacity",cap},{"state",outcome},{"seconds",Math.Round(clock.Elapsed.TotalSeconds-begin,3)},{"nodes",nodes-before},{"trimmed",trimmed}});
            if(result!=null||input.ContainsKey("fixed_capacity"))break;
        }
        cap=Math.Min(cap,6);Report(result==null?outcome:"lookahead_found",true);
        var payload=new Dictionary<string,object>{{"state",result==null?outcome:"lookahead_found"},{"tray_capacity",cap},{"attempts",attempts},{"nodes",nodes},{"elapsed_seconds",clock.Elapsed.TotalSeconds},{"horizon_target",target},{"endgame",endgame},{"sequence",new string[0]}};
        if(result!=null){payload["sequence"]=result.Route.Select(root.Id).ToArray();payload["lookahead_events"]=result.Events;payload["peak_slots"]=result.Peak;payload["complete"]=result.State.Complete;}
        Write(args[1],payload);return result==null?2:0;
    }
    static int Main(string[] args){try{if(args.Length!=3)throw new Exception("Usage: RollingPlanner.exe request result progress");return new RollingPlanner().Run(args);}catch(Exception e){Console.Error.WriteLine(e);return 1;}}
}
