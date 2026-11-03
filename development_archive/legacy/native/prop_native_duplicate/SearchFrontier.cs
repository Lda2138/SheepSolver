// AGPL-3.0. Save exactly the unexplored DFS frontier at a resource cutoff.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;

partial class FastOriginalSolver {
    int[] cutoffPath;readonly List<Dictionary<string,object>> pendingFrames=new List<Dictionary<string,object>>();
    void CaptureCutoff(int depth){if(cutoffPath==null)cutoffPath=result.Take(depth).ToArray();}
    void PendingFrame(int depth,int[] head,int firstRemaining,int count){
        if(cutoffPath==null||firstRemaining>=count)return;
        pendingFrames.Add(new Dictionary<string,object>{{"depth",depth},
            {"cards",head.Skip(firstRemaining).Take(count-firstRemaining).Select(i=>cards[i].Id).ToArray()}});
    }
    void SaveFrontier(){
        if(cutoffPath==null||string.IsNullOrEmpty(progressPath))return;
        string path=progressPath+".frontier.json",temp=path+".tmp";
        File.WriteAllText(temp,json.Serialize(new Dictionary<string,object>{{"version",1},{"map_sha256",mapHash},
            {"start_depth",startDepth},{"path",cutoffPath.Select(i=>cards[i].Id).ToArray()},
            {"leaf",true},{"frames",pendingFrames},{"reason",stopReason}}));
        for(int attempt=0;attempt<8;attempt++)try{if(File.Exists(path))File.Replace(temp,path,null);else File.Move(temp,path);return;}catch(IOException){System.Threading.Thread.Sleep(15);}
    }
}
