// Exact energy/alien state search. Offline only; no mouse/window operations.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Web.Script.Serialization;

class EnergySearch {
    static JavaScriptSerializer json=new JavaScriptSerializer{MaxJsonLength=8*1024*1024};
    static object[] Items(object value){return ((System.Collections.IEnumerable)value).Cast<object>().ToArray();}
    EnergyRules initial;Dictionary<string,object>[] cards;HashSet<string> dead=new HashSet<string>();
    List<string> path=new List<string>(),best=new List<string>();Stopwatch clock=Stopwatch.StartNew();
    double seconds,lastReport;long nodes;bool limited;string status;int stateLimit=100000;Random random;double[] tie;
    int bestRemoved;bool cancelled;
    void Report(string state){
        lastReport=clock.Elapsed.TotalSeconds;
        var payload=new Dictionary<string,object>{{"state",state},{"rules","energy_alien_v1"},{"engine","energy_search"},
            {"nodes",nodes},{"dead_states",dead.Count},{"deepest",bestRemoved},{"clicks",best.Count},{"total",cards.Length},
            {"elapsed_seconds",lastReport},{"workers",1},{"message","电量牌与种子飞碟规则搜索"}};
        string temp=status+".tmp";File.WriteAllText(temp,json.Serialize(payload));
        for(int n=0;n<10;n++)try{if(File.Exists(status))File.Replace(temp,status,null);else File.Move(temp,status);break;}catch(IOException){System.Threading.Thread.Sleep(15);}
    }
    bool Budget(){
        if(clock.Elapsed.TotalSeconds-lastReport>.5){Report("searching");
            string control=Path.Combine(Path.GetDirectoryName(status),"search_control.json");
            if(File.Exists(control))try{var value=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(control));if(value.ContainsKey("action")&&Convert.ToString(value["action"])=="stop"){limited=true;cancelled=true;}}catch(IOException){}
        }
        if(clock.Elapsed.TotalSeconds>=seconds){limited=true;}return !limited;
    }
    bool Search(EnergyRules board){
        nodes++;if(!Budget())return false;
        int removed=board.Remaining.Count(v=>!v);if(removed>bestRemoved){bestRemoved=removed;best=new List<string>(path);}
        if(removed==cards.Length)return board.Tray.Length==0;
        string key=board.Key();if(dead.Contains(key))return false;
        var options=Enumerable.Range(0,cards.Length).Where(i=>board.Exposed(i)&&(board.Tray.Length<6||board.Held(board.Type(i))==2)).ToArray();
        // Completing a pair is urgent. Charged energy cards and their direct
        // unlocks then take priority; these scores never prove infeasibility.
        Array.Sort(options,Comparer<int>.Create((i,j)=>Score(board,j).CompareTo(Score(board,i))));
        foreach(int i in options){
            var next=board.Copy();next.Step(board.Id(i));path.Add(board.Id(i));
            if(Search(next))return true;path.RemoveAt(path.Count-1);if(limited)return false;
        }
        if(dead.Count<stateLimit)dead.Add(key);return false;
    }
    double Score(EnergyRules board,int i){
        int held=board.Held(board.Type(i)),charge=board.Charge(i);
        double score=(held==2?10000:held==1?60:0)+(charge>0?300+40*(4-charge):0)+Convert.ToInt32(cards[i]["layer"])*.8+tie[i];
        // Favor picks that uncover a still charged battery. Old exposed
        // batteries drain, newly uncovered batteries retain their full charge.
        foreach(int j in Enumerable.Range(0,cards.Length))if(board.Remaining[j]&&board.Charge(j)>0&&Convert.ToInt32(cards[i]["layer"])>Convert.ToInt32(cards[j]["layer"])&&
            Math.Abs(Convert.ToInt32(cards[i]["rolNum"])-Convert.ToInt32(cards[j]["rolNum"]))<8&&Math.Abs(Convert.ToInt32(cards[i]["rowNum"])-Convert.ToInt32(cards[j]["rowNum"]))<8)score+=5;
        return score;
    }
    static int Main(string[] args){
        try{
            var solution=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(args[0]));
            var s=new EnergySearch{cards=Items(solution["cards"]).Select(o=>(Dictionary<string,object>)o).ToArray(),
                seconds=double.Parse(args[3],System.Globalization.CultureInfo.InvariantCulture),status=args[2],random=new Random(int.Parse(args[4]))};
            s.initial=new EnergyRules(s.cards,(Dictionary<string,object>)solution["rules"]);
            s.tie=s.cards.Select(c=>s.random.NextDouble()*3).ToArray();s.Report("searching");bool solved=s.Search(s.initial);
            s.Report(solved?"candidate":s.cancelled?"cancelled":s.limited?"limited":"exhausted");
            var answer=new Dictionary<string,object>{{"sequence",solved?s.path:s.best},{"engine","energy_search"},{"rules_engine","energy_alien_v1"},{"nodes",s.nodes},{"elapsed_seconds",s.clock.Elapsed.TotalSeconds}};
            File.WriteAllText(solved?args[1]:args[1]+".best.json",json.Serialize(answer));
            return solved?0:2;
        }catch(Exception e){Console.Error.WriteLine(e.ToString());return 1;}
    }
}
