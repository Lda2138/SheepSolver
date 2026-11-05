using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Web.Script.Serialization;

// Independent old implementations are retained here to check numerical parity
// and measure computation on identical input; this executable never sends input.
static class PlaybackPerformanceCheck {
    static readonly JavaScriptSerializer Json=new JavaScriptSerializer();
    static Point PointOf(object value){var items=((System.Collections.IEnumerable)value).Cast<object>().ToArray();return new Point(Convert.ToInt32(items[0]),Convert.ToInt32(items[1]));}
    static double LegacyScore(Bitmap image,Point p,Bitmap reference,Point q){
        double sum=0;int n=0;
        for(int y=-11;y<=11;y+=2)for(int x=-11;x<=11;x+=2){var a=Vision.At(image,p.X+x,p.Y+y);var b=Vision.At(reference,q.X+x,q.Y+y);sum+=Math.Abs(a.R-b.R)+Math.Abs(a.G-b.G)+Math.Abs(a.B-b.B);n++;}
        return sum/n;
    }
    static double LegacyTarget(Bitmap image,Point p,int type,Bitmap reference,List<Dictionary<string,object>> anchors){
        double best=1000;
        foreach(var a in anchors.Where(a=>Convert.ToInt32(a["type"])==type)){
            Point q=PointOf(a["center"]);
            for(int dy=-4;dy<=4;dy++)for(int dx=-2;dx<=2;dx++)best=Math.Min(best,LegacyScore(image,new Point(p.X+dx,p.Y+dy),reference,q));
        }
        return best;
    }
    static double LegacyDifference(Bitmap before,Bitmap after,Rectangle box){
        using(var a=Vision.Normalize(before))using(var b=Vision.Normalize(after)){
            double sum=0;int n=0;var safe=Rectangle.Intersect(box,new Rectangle(0,0,336,622));
            for(int y=safe.Top;y<safe.Bottom;y+=2)for(int x=safe.Left;x<safe.Right;x+=2){var ca=Vision.At(a,x,y);var cb=Vision.At(b,x,y);sum+=Math.Abs(ca.R-cb.R)+Math.Abs(ca.G-cb.G)+Math.Abs(ca.B-cb.B);n++;}
            return n==0?0:sum/n;
        }
    }
    static double Time(Action work,int count){var watch=Stopwatch.StartNew();for(int i=0;i<count;i++)work();return watch.Elapsed.TotalMilliseconds/count;}
    static void Equal(double oldValue,double newValue){if(oldValue!=newValue)throw new Exception("Pixel score changed: "+oldValue+" vs "+newValue);}
    static void FreshFrameChecks(){
        if(!SolverPlayback.CanReuseTargetFrame(true,0,25)||SolverPlayback.CanReuseTargetFrame(true,0,51)||SolverPlayback.CanReuseTargetFrame(true,60,20)||SolverPlayback.CanReuseTargetFrame(false,0,20)||SolverPlayback.CanReuseTargetFrame(true,0,-1))throw new Exception("Stale or delayed frame bypasses preflight");
    }
    static void Run(Bitmap image,Bitmap reference,List<Dictionary<string,object>> anchors){
        FreshFrameChecks();int checks=0;
        foreach(var anchor in anchors.Take(10)){
            var p=PointOf(anchor["center"]);int type=Convert.ToInt32(anchor["type"]);
            foreach(int shift in new[]{-2,0,2}){var q=new Point(p.X+shift,p.Y);Equal(LegacyTarget(image,q,type,reference,anchors),SolverPlayback.TargetScore(image,q,type,reference,anchors));checks++;}
        }
        var boxes=new[]{new Rectangle(22,493,286,40),new Rectangle(20,118,296,347),new Rectangle(-5,-5,25,25),new Rectangle(400,700,10,10)};
        foreach(var box in boxes){Equal(LegacyDifference(image,reference,box),Vision.PatchDifference(image,reference,box));checks++;}
        using(var scaled=new Bitmap(image,672,1244)){foreach(var box in boxes){Equal(LegacyDifference(scaled,reference,box),Vision.PatchDifference(scaled,reference,box));checks++;}}
        var first=anchors[0];Point target=PointOf(first["center"]);int targetType=Convert.ToInt32(first["type"]);
        var tray=new Rectangle(22,493,286,40);
        // Warm both paths before measuring; timings are descriptive, not a flaky assertion.
        LegacyTarget(image,target,targetType,reference,anchors);SolverPlayback.TargetScore(image,target,targetType,reference,anchors);
        double oldMatch=Time(()=>LegacyTarget(image,target,targetType,reference,anchors),12);
        double newMatch=Time(()=>SolverPlayback.TargetScore(image,target,targetType,reference,anchors),12);
        double oldDiff=Time(()=>LegacyDifference(image,reference,tray),12);
        double newDiff=Time(()=>Vision.PatchDifference(image,reference,tray),12);
        Console.WriteLine(Json.Serialize(new {parity_checks=checks,legacy_match_ms=oldMatch,optimized_match_ms=newMatch,legacy_difference_ms=oldDiff,optimized_difference_ms=newDiff,no_input=true}));
    }
    static int Main(string[] args){try{
        if(args.Length==2){
            var plan=Json.Deserialize<Dictionary<string,object>>(File.ReadAllText(args[0]));
            var anchors=((System.Collections.IEnumerable)plan["anchors"]).Cast<Dictionary<string,object>>().ToList();
            using(var raw=new Bitmap(args[1]))using(var image=Vision.Normalize(raw))using(var referenceRaw=new Bitmap((string)plan["reference_path"]))using(var reference=Vision.Normalize(referenceRaw))Run(image,reference,anchors);
        }else{
            using(var reference=new Bitmap(336,622))using(var image=new Bitmap(336,622)){
                var rng=new Random(123);for(int y=0;y<622;y++)for(int x=0;x<336;x++){var c=Color.FromArgb(rng.Next(256),rng.Next(256),rng.Next(256));reference.SetPixel(x,y,c);image.SetPixel(x,y,Color.FromArgb(c.G,c.B,c.R));}
                var anchors=new List<Dictionary<string,object>>();foreach(int x in new[]{60,110,160,210})anchors.Add(new Dictionary<string,object>{{"type",1},{"center",new int[]{x,230}}});
                Run(image,reference,anchors);
            }
        }return 0;
    }catch(Exception exc){Console.Error.WriteLine(exc);return 1;}}
}
