using System;
using System.Collections.Generic;
using System.Linq;

// Independent seeded energy/alien replay. Wait for each action to settle.
class EnergyRules {
    public sealed class Event {
        public int Type,Collected,Draws,StableTrayCount;public bool Crush,Alien,CollectedOne;
        public string[] AutoRemoved;public int[] Tray;
    }
    Dictionary<string,object>[] cards;Dictionary<string,int> index;
    int[] types,charge,iteration;int[][] parents;bool[] left;List<int> tray=new List<int>();
    ColorRules rng;int collectCount,collected;public bool[] Remaining {get{return left;}}
    public int[] Tray {get{return tray.Select(i=>types[i]).ToArray();}}
    public int CollectedCount {get{return collected;}}
    public int Threshold {get{return collectCount;}}
    public int PotentialEnergy {get{return collected+Enumerable.Range(0,cards.Length).Count(i=>left[i]&&charge[i]>0);}}
    static int N(Dictionary<string,object> c,string key){return c.ContainsKey(key)?Convert.ToInt32(c[key]):0;}
    static object[] Items(object value){return ((System.Collections.IEnumerable)value).Cast<object>().ToArray();}
    public EnergyRules(Dictionary<string,object>[] source,Dictionary<string,object> rules){
        if(Convert.ToString(rules["kind"])!="energy_alien_v1")throw new Exception("Unknown energy rules");
        cards=source;types=cards.Select(c=>N(c,"type")).ToArray();charge=cards.Select(c=>N(c,"metaType")==3?N(c,"metaData"):0).ToArray();
        if(cards.Any(c=>N(c,"metaType")!=0&&N(c,"metaType")!=1&&N(c,"metaType")!=3))throw new Exception("Mixed energy/color rules unsupported");
        index=cards.Select((c,i)=>new {Id=(string)c["id"],I=i}).ToDictionary(c=>c.Id,c=>c.I);
        iteration=Enumerable.Range(0,cards.Length).OrderBy(i=>N(cards[i],"layer")).ThenBy(i=>N(cards[i],"rowNum")).ThenBy(i=>N(cards[i],"rolNum")).ToArray();
        parents=Enumerable.Range(0,cards.Length).Select(i=>Enumerable.Range(0,cards.Length).Where(j=>N(cards[j],"layer")>N(cards[i],"layer")&&Math.Abs(N(cards[j],"rolNum")-N(cards[i],"rolNum"))<8&&Math.Abs(N(cards[j],"rowNum")-N(cards[i],"rowNum"))<8).ToArray()).ToArray();
        left=Enumerable.Repeat(true,cards.Length).ToArray();collectCount=N(rules,"collect_count");
        if(collectCount<=0)throw new Exception("Invalid energy threshold");
        rng=new ColorRules{Types=types,Special=new int[0]};rng.Seed(rules["seed"],N(rules,"pool_draws"));
    }
    public bool Exposed(int i){return left[i]&&!parents[i].Any(j=>left[j]);}
    public int Type(int i){return types[i];}public int Charge(int i){return charge[i];}
    public int Held(int type){return tray.Count(i=>types[i]==type);}
    public string Id(int i){return (string)cards[i]["id"];}
    public EnergyRules Copy(){
        var other=(EnergyRules)MemberwiseClone();other.charge=(int[])charge.Clone();other.left=(bool[])left.Clone();other.tray=new List<int>(tray);
        other.rng=new ColorRules{Types=types,Special=new int[0],A=rng.A,B=rng.B,Draws=rng.Draws};return other;
    }
    public string Key(){
        // Exact state includes the seeded random cursor and battery counters.
        char[] value=new char[left.Length+3];for(int i=0;i<left.Length;i++)value[i]=(char)(left[i]?charge[i]+2:1);
        value[left.Length]=(char)collected;value[left.Length+1]=(char)tray.Count;value[left.Length+2]=(char)rng.Draws;
        // Alien matching visits tray types in first-seen order. Sorting the
        // tray merges states that consume the seeded random stream differently.
        return new string(value)+":"+rng.A+":"+rng.B+":"+string.Join(",",Tray);
    }
    void Find(List<int> selected,int count,int? type=null){
        var available=iteration.Where(i=>left[i]).ToArray();if(!type.HasValue){if(available.Length==0)return;type=types[available[(int)Math.Floor(rng.Next()*available.Length)]];}
        var candidates=available.Where(i=>types[i]==type.Value).ToArray();
        if(candidates.Length<count)throw new Exception("Alien move-out state unsupported");
        for(int k=candidates.Length-1;k>=candidates.Length-count;k--){int j=(int)Math.Floor(rng.Next()*(k+1)),v=candidates[k];candidates[k]=candidates[j];candidates[j]=v;}
        selected.AddRange(candidates.Skip(candidates.Length-count));
    }
    public Event Step(string id){
        int i;if(!index.TryGetValue(id,out i)||!Exposed(i))throw new Exception("Energy target unavailable");
        var exposed=Enumerable.Range(0,cards.Length).Where(j=>j!=i&&charge[j]>0&&Exposed(j)).ToArray();
        int t=types[i];tray.Add(i);bool crush=tray.Count(j=>types[j]==t)==3;if(crush)tray.RemoveAll(j=>types[j]==t);
        if(tray.Count>=7)throw new Exception("Energy tray overflow before alien");
        int stableTrayCount=tray.Count;
        left[i]=false;bool one=charge[i]>0;if(one)collected++;charge[i]=0;foreach(int j in exposed)charge[j]--;
        bool alien=collected>=collectCount;var selected=new List<int>();
        if(alien){
            collected-=collectCount;Array.Clear(charge,0,charge.Length);
            if(tray.Count==0)Find(selected,3);
            else foreach(int kind in tray.Select(j=>types[j]).Distinct())Find(selected,3-tray.Count(j=>types[j]==kind),kind);
            foreach(int j in selected)left[j]=false;tray.Clear();
        }
        return new Event{Type=t,Crush=crush,Alien=alien,CollectedOne=one,Collected=collected,Draws=rng.Draws-1,StableTrayCount=stableTrayCount,
            AutoRemoved=selected.Select(j=>(string)cards[j]["id"]).ToArray(),Tray=Tray};
    }
    public void Check(Event ev,Dictionary<string,object> fact,string id){
        if(Convert.ToString(fact["card"])!=id||N(fact,"type")!=ev.Type||N(fact,"energy_count")!=ev.Collected||N(fact,"rng_draws")!=ev.Draws||
           Convert.ToBoolean(fact["crush"])!=ev.Crush||Convert.ToBoolean(fact["alien"])!=ev.Alien||Convert.ToBoolean(fact["collected"])!=ev.CollectedOne||
           !Items(fact["auto_removed"]).Select(Convert.ToString).SequenceEqual(ev.AutoRemoved)||!Items(fact["tray"]).Select(Convert.ToInt32).SequenceEqual(ev.Tray))
            throw new Exception("Energy/alien trace mismatch");
    }
}
