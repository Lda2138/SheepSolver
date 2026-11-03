// AGPL-3.0. Classic client's seeded color shuffle, shared by search and replay.
using System;
using System.Collections.Generic;
using System.Linq;

class ColorRules {
    public int[] Types,Special;public ulong A,B;public int Draws;public bool Enabled {get{return Special.Length>0;}}
    static object[] Items(object x){return ((System.Collections.IEnumerable)x).Cast<object>().ToArray();}
    static int Number(Dictionary<string,object> c,string name){return c.ContainsKey(name)?Convert.ToInt32(c[name]):0;}
    public static ColorRules FromCards(Dictionary<string,object>[] cards) {
        if(cards.Any(c=>Number(c,"metaType")<0||Number(c,"metaType")>2||Number(c,"metaData")!=0))throw new Exception("Unsupported special card rule");
        var r=new ColorRules{Types=cards.Select(c=>Number(c,"type")).ToArray()};
        r.Special=Enumerable.Range(0,cards.Length).Where(i=>Number(cards[i],"metaType")==2).OrderBy(i=>Number(cards[i],"layer")).ThenBy(i=>Number(cards[i],"rowNum")).ThenBy(i=>Number(cards[i],"rolNum")).ToArray();
        if(r.Special.Length>32)throw new Exception("Color rule supports at most 32 changing cards");return r;
    }
    public void Seed(object seed,int poolDraws) {
        var s=Items(seed);if(s.Length!=4||poolDraws<0||poolDraws>3000)throw new Exception("Invalid color seed/pool");
        A=((ulong)(uint)Convert.ToInt64(s[0])<<32)|(uint)Convert.ToInt64(s[1]);B=((ulong)(uint)Convert.ToInt64(s[2])<<32)|(uint)Convert.ToInt64(s[3]);
        Draws=0;Next();for(int i=0;i<poolDraws;i++)Next();
    }
    public double Next(){unchecked{Draws++;ulong sum=A+B,x=A,y=B;A=y;x^=x<<23;B=x^y^(x>>18)^(y>>5);return (sum>>32)*2.3283064365386963e-10+((uint)sum>>12)*2.220446049250313e-16;}}
    public void Shuffle(bool[] picked){Shuffle(picked,new int[Special.Length]);}
    public void Shuffle(bool[] picked,int[] work) {
        int count=0;foreach(int i in Special)if(!picked[i])work[count++]=i;if(count<=1)return;
        for(int k=count-1;k>=0;k--){int j=(int)Math.Floor(Next()*(k+1));int t=Types[work[k]];Types[work[k]]=Types[work[j]];Types[work[j]]=t;}
    }
    public static ColorRules FromSolution(Dictionary<string,object> solution,Dictionary<string,object>[] cards) {
        var r=FromCards(cards);if(!r.Enabled)return r;
        if(!solution.ContainsKey("rules")||!(solution["rules"] is Dictionary<string,object>))throw new Exception("Missing color rules");
        var rules=(Dictionary<string,object>)solution["rules"];if(Convert.ToString(rules["kind"])!="color_shuffle_v1")throw new Exception("Unknown color rules");
        var initial=Items(rules["special_initial"]).Select(o=>(Dictionary<string,object>)o).ToArray();
        if(initial.Length!=r.Special.Length)throw new Exception("Color initialization mismatch");
        int[] expected=(int[])r.Types.Clone();
        for(int j=0;j<r.Special.Length;j++){int i=r.Special[j];if(Convert.ToString(initial[j]["id"])!=Convert.ToString(cards[i]["id"]))throw new Exception("Color iteration order mismatch");r.Types[i]=Number(initial[j],"type");}
        r.Seed(rules["seed"],Number(rules,"pool_draws"));r.Shuffle(new bool[cards.Length]);
        if(!r.Types.SequenceEqual(expected))throw new Exception("Initial colors do not match seed");return r;
    }
}
