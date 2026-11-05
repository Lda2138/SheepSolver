using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Linq;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using System.Threading.Tasks;

// Separate background player of an independently validated original solver answer.
static class SolverPlayback
{
    static JavaScriptSerializer Json=new JavaScriptSerializer{MaxJsonLength=4*1024*1024};
    static bool UnverifiedInput;
    static EnergyRules.Event[] EnergyTrace;
    static int EnergyThreshold;
    static bool RollingExecution;
    static int BoardTotal;
    static double FaceScaleX=1,FaceScaleY=1;
    static string VerificationMode="normal";
    static bool FullSpeed;
    static int StepsSinceFullCheck,LastFullCheckStep,FullCheckInterval=30;
    static int[][] TrayTrace;
    static int PendingGroupStart,PendingGroupCount;
    internal static int[] PushTray(int[] before,int type){var result=before.ToList();result.Add(type);if(result.Count(t=>t==type)==3)result.RemoveAll(t=>t==type);return result.ToArray();}
    static bool Ultra {get{return VerificationMode=="ultra"||VerificationMode=="safe_ultra";}}
    static string Mode(object value){string mode=Convert.ToString(value);return mode=="ultra"||mode=="safe_ultra"?mode:"normal";}
    internal static bool FullCheckDue(string mode,int steps,int interval){return mode=="safe_ultra"&&steps>=Math.Max(1,Math.Min(1000,interval));}
    internal static bool CanReuseTargetFrame(bool ultra,int markMs,double ageMs){return ultra&&markMs==0&&ageMs>=0&&ageMs<=50;}
    // The client silently drops some clicks shortly after a triple clears. Every
    // real lost click left the target pixels identical to the pre-click frame and
    // the tray at its pre-click count. Anything less is not proof: a resend after a
    // click that only rendered late takes a card outside the route, which cannot be
    // recovered. An unprovable case stops and is reconciled from the board instead.
    internal static bool ClickLost(double elapsedMs,int retryMs,double targetDiff,int observedTray,int trayBeforeClick,bool stacked){return !stacked&&elapsedMs>=retryMs&&targetDiff<=2&&observedTray==trayBeforeClick;}
    internal const int MinRetryMs=450;
    // A tray in motion briefly reads low or not at all, so a group needs an exact
    // count. A count equal to the starting tray may simply mean nothing has landed.
    internal static bool GroupSettled(bool allGone,int seen,int expected,int trayStart,int count,double sinceMs){return allGone&&(seen==trayStart+count||seen==expected&&(expected!=trayStart||sinceMs>=250));}
    // Next clear click after the previous one. Real clears into a full tray never
    // ran closer than 458 ms in 352 complete-game cases; a burst at 220 ms overfilled
    // the tray while the last triple was still clearing, losing the game.
    internal static DateTime ClearNotBefore(DateTime lastClear,int trayBeforeClick,int fullTrayHoldMs){return lastClear==DateTime.MinValue?lastClear:lastClear.AddMilliseconds(trayBeforeClick>=6?fullTrayHoldMs:MinClearHoldMs);}
    internal const int MinClearHoldMs=320;
    // Cards of the previous triple stay drawn while it clears, so until then the
    // tray reads high and can pass for a group that has fully landed.
    const int TrayClearedMs=500;
    // Cards leave the board in about 85 ms but the tray reads true only after 350-570 ms.
    // Past the press animation, a board with every target gone over several frames proves
    // the group landed: a flying card cannot hide one spot for that long. The next group
    // then need not wait for the tray.
    const int EarlyGoneMs=200,EarlyGoneReads=3;
    // A stacked card can leave the board looking unchanged, and a tray still in flight
    // can read exactly the post-clear count, so that reading waits until flights ended.
    // The next level can draw the same face where the last card was. An emptied tray
    // proves the clear; a lost click leaves the tray as it was.
    internal static bool LastCardStayed(bool intact,bool stacked,int observed,int expected,int trayBefore){return intact&&!stacked&&!(observed==expected&&observed<trayBefore);}
    internal static bool LowCountTrusted(bool stacked,int seen,int fullCount,double sinceMs){return !stacked||seen==fullCount||sinceMs>=600;}
    internal static bool GoneEarly(bool allGone,bool noDoubt,int seen,int trayStart,int expected,double sinceMs){return allGone&&noDoubt&&seen>=Math.Min(trayStart,expected)&&sinceMs>=EarlyGoneMs;}
    // Which clicks to resend, given how many cards the tray is short. Non-stacked
    // cards still in place are certainly lost, and sending them first also uncovers
    // any card they were hiding, which the next pass then sees. Stacked cards may
    // only look present: they are resent when they alone explain the shortfall, or,
    // as one pile of one type, from the deepest card, because a click takes the top.
    internal static List<int> ClicksToResend(List<int> certain,List<int> possibleDeepestFirst,List<int> possibleBesideClear,bool onePile,int seen,int fullCount,int expected){
        if(seen<0)return new List<int>();
        // Short against the full group, or against the count after its clear.
        int full=fullCount-seen,cleared=expected-seen,most=Math.Max(full,cleared);
        if(most<=0)return new List<int>();
        if(certain.Count>0)return certain.Count<=most?certain:new List<int>();
        if(possibleDeepestFirst.Count==full||possibleDeepestFirst.Count==cleared)return possibleDeepestFirst;
        // A tray short of the post-clear count means the clear happened, so every
        // card of the cleared type arrived and only the others can be missing.
        if(cleared>0&&possibleBesideClear.Count==cleared)return possibleBesideClear;
        int fewest=cleared>0?cleared:full;
        if(onePile&&possibleDeepestFirst.Count>fewest)return possibleDeepestFirst.Take(fewest).ToList();
        return new List<int>();
    }
    // A card seen clearly before the click is lost only if its pixels never changed:
    // any change means it left, even when what remains resembles it. A card that was
    // hidden in the baseline has no such proof, so its own face decides.
    internal static bool ProvenLost(double baselineScore,double pixelDiff,bool looksPresent){return baselineScore<=60?pixelDiff<=2:looksPresent;}
    // A settled count outranks a target that only looks present, such as a look-alike
    // card underneath. A clear group ends on the count it began with, so that case
    // also needs one card proven gone: otherwise it may mean nothing landed at all.
    internal static bool CountDecides(int seen,int expected,int trayStart,bool anyGone,double sinceMs,double exactMs){return seen==expected&&sinceMs>=600&&exactMs>=150&&(expected!=trayStart||anyGone);}
    const int MaxGroupRetries=6;
    const int MaxBurst=6;
    // A full-speed group runs up to and including the next clear. Checking the tray
    // costs a card's flight time, so it happens once per clear, where a lost click
    // would be fatal; anything lost earlier only leaves the tray short. The last
    // route step stays single because it needs the settlement check.
    internal static int BurstSize(int next,int[][] trays,int limit){
        int count=0,end=Math.Min(limit,trays.Length-2);
        for(int i=next;i<end&&count<MaxBurst;i++){count++;if(trays[i+1].Length<trays[i].Length)break;}
        return count;
    }
    // While a clear animates, the drawn tray can pass through the expected count.
    // Tray evidence therefore needs the target to be gone as well, unless a card
    // exactly underneath legitimately keeps the same picture in place.
    internal static bool TrayEvidenceAllowed(bool ultra,bool last,bool stacked,double targetDiff,bool targetIntact){return !ultra||last||stacked||targetDiff>2&&!targetIntact;}
    // A passing card in the baseline frame makes an untouched target look changed,
    // and its own face then looks like a newly exposed card. A target still showing
    // its own face was not removed, whatever the comparison with the baseline says.
    internal static bool ConfirmedRemoved(bool disappeared,bool targetIntact,bool stacked){return disappeared&&(!targetIntact||stacked);}
    // On a near-full tray, one lost click trusted as success makes the next click
    // the fatal seventh card, so removal alone never confirms a step: the card must
    // also have reached the tray. A count unreadable while the tray shifts falls
    // back to time, once any press animation on the target has ended.
    internal static bool TrayMoved(int observed,int before,int expected,double sinceInputMs){return observed==expected||observed==before+1||observed<0&&sinceInputMs>=250;}
    // Radius covers the template search window, plus real cards stacked 5 px apart.
    // Only the card that becomes visible matters: a same-type card buried under
    // other cards cannot be mistaken for the target.
    static bool RevealsSameFace(string id,int type,Point point,Dictionary<string,Dictionary<string,object>> cards,HashSet<string> left,Dictionary<string,object> positions){
        int layer=Convert.ToInt32(cards[id]["layer"]);
        var below=left.Where(o=>o!=id&&Convert.ToInt32(cards[o]["layer"])<layer&&CoversExactly(point,new[]{PointOf(positions[o])})).OrderByDescending(o=>Convert.ToInt32(cards[o]["layer"])).FirstOrDefault();
        return below!=null&&Convert.ToInt32(cards[below]["type"])==type;
    }
    internal static bool CoversExactly(Point target,Point[] remaining){return remaining.Any(c=>Math.Abs(c.X-target.X)<=8&&Math.Abs(c.Y-target.Y)<=8);}
    // Start the next capture early enough to click right at the gap. This only
    // schedules the capture; clickNotBefore is what actually holds the click.
    internal static DateTime NextStepDue(DateTime sentAt,DateTime now,int gapMs){if(gapMs<=0)return now;DateTime due=sentAt.AddMilliseconds(gapMs-CaptureLeadMs);return due>now?due:now;}
    const int CaptureLeadMs=40;
    // Floors measured in complete real games. They live here so that no plan or
    // long-running controller can pace clicks into a lost game.
    internal const int MinClickGapMs=130,MinClearGapMs=140;
    internal static int ClickGap(object value){return Math.Max(MinClickGapMs,Math.Min(2000,Convert.ToInt32(value)));}
    internal static int ClearGap(object value){return Math.Max(MinClearGapMs,Math.Min(2000,Convert.ToInt32(value)));}
    const int MaxInputRetries=3;
    static Dictionary<string,object> Read(string p){using(var stream=new FileStream(p,FileMode.Open,FileAccess.Read,FileShare.ReadWrite|FileShare.Delete))using(var reader=new StreamReader(stream,Encoding.UTF8))return Json.Deserialize<Dictionary<string,object>>(reader.ReadToEnd());}
    static object[] Items(object v){return ((System.Collections.IEnumerable)v).Cast<object>().ToArray();}
    static Point PointOf(object v){var a=Items(v);return new Point(Convert.ToInt32(a[0]),Convert.ToInt32(a[1]));}
    static bool VerificationMatches(bool last,int observed,int expected,double targetDiff,double trayDiff){return (last||observed==expected)&&(targetDiff>=8||trayDiff>=8);}
    internal static bool VerificationEvidence(bool localRemoved,bool last,int observed,int expected,double targetDiff,double trayDiff){return localRemoved||VerificationMatches(last,observed,expected,targetDiff,trayDiff);}
    static int VerificationWindow(bool alien,int verifyMs){return alien?10000:Math.Max(3000,verifyMs+1500);}
    internal static bool FollowWindow(ref Rectangle bounds,Rectangle current) {
        if(current.Size!=bounds.Size)throw new Exception("窗口尺寸改变，停止点击并重新核对棋盘");
        bool moved=current.Location!=bounds.Location;bounds=current;return moved;
    }
    static bool BoardBackground(Color c){return c.G>180&&c.G>c.R+20&&c.G>c.B+30;}
    static bool TileSurface(Color c){return c.R>205&&c.G>205&&c.B>145||c.R>=85&&c.R<=215&&c.G>=110&&c.G<=225&&c.B>=50&&c.B<=180&&c.G>=c.R&&c.R>c.B+10&&c.G>c.B+20;}
    static double NeutralCoverage(Bitmap image,Point p) {
        int neutral=0,total=0;
        for(int y=Math.Max(0,p.Y-23);y<=Math.Min(image.Height-1,p.Y+23);y+=3)
            for(int x=Math.Max(0,p.X-23);x<=Math.Min(image.Width-1,p.X+23);x+=3){Color c=Vision.At(image,x,y);int hi=Math.Max(c.R,Math.Max(c.G,c.B)),lo=Math.Min(c.R,Math.Min(c.G,c.B));if(hi-lo<8&&(c.R>225||c.R<115))neutral++;total++;}
        return (double)neutral/Math.Max(1,total);
    }
    internal static bool AnimationOccluded(Bitmap before,Bitmap after,Point p){double now=NeutralCoverage(after,p);return now>.55&&now-NeutralCoverage(before,p)>.25;}
    internal static bool NeedsStableFrame(bool localRemoved,bool ultra,bool adaptive,bool alien){return !localRemoved&&(alien||adaptive&&!ultra);}
    internal static bool TargetDisappeared(Bitmap before,Bitmap after,Point p,Point[] remainingCenters=null) {
        if(AnimationOccluded(before,after,p))return false;
        double same=1000;
        for(int dy=-2;dy<=2;dy++)for(int dx=-2;dx<=2;dx++)same=Math.Min(same,Score(after,new Point(p.X+dx,p.Y+dy),before,p));
        if(same<=60)return false; // An unchanged icon, including a redraw, is not a click.
        if(FaceVisible(after,p))return true; // A different underlying face is now exposed.
        int green=0,total=0;
        for(int y=-9;y<=9;y+=3)for(int x=-9;x<=9;x+=3){Color c=Vision.At(after,p.X+x,p.Y+y);total++;if(BoardBackground(c))green++;}
        if(green>=total*.75)return true;
        if(remainingCenters==null)return false;
        // Dense world boards commonly expose only part of a still-covered,
        // dimmed lower card. Check the eight LOCAL frame samples against the
        // remaining map footprint instead of requiring a complete pale face.
        int matches=0;
        foreach(var offset in new[]{new Point(-12,-13),new Point(12,-13),new Point(-12,12),new Point(12,12),new Point(-14,-6),new Point(14,-6),new Point(-14,5),new Point(14,5)}) {
            Point q=new Point(p.X+(int)Math.Round(offset.X*FaceScaleX),p.Y+(int)Math.Round(offset.Y*FaceScaleY));
            bool covered=remainingCenters.Any(c=>Math.Abs(c.X-q.X)<16*FaceScaleX&&Math.Abs(c.Y-q.Y)<18*FaceScaleY);
            Color color=Vision.At(after,q.X,q.Y);if(covered?TileSurface(color):BoardBackground(color))matches++;
        }
        return matches>=5;
    }
    static string FullCheck(string folder,string image,int step) {
        string root=AppDomain.CurrentDomain.BaseDirectory;
        var info=new System.Diagnostics.ProcessStartInfo{FileName=Path.Combine(root,"venv","Scripts","python.exe"),
            Arguments="\""+Path.Combine(root,"app_bridge.py")+"\" verify-playback --play \""+folder+"\" --image \""+image+"\" --step "+step,
            WorkingDirectory=root,UseShellExecute=false,CreateNoWindow=true,RedirectStandardError=true,RedirectStandardOutput=true};
        using(var process=System.Diagnostics.Process.Start(info)) {
            var error=process.StandardError.ReadToEndAsync();var output=process.StandardOutput.ReadToEndAsync();
            if(!process.WaitForExit(8000)){process.Kill();return "全盘核对超时";}
            Task.WaitAll(error,output);return process.ExitCode==0?null:error.Result.Trim();
        }
    }
    static bool FaceVisible(Bitmap image,Point p){int pale=0;foreach(var offset in new[]{new Point(-12,-13),new Point(12,-13),new Point(-12,12),new Point(12,12),new Point(-14,-6),new Point(14,-6),new Point(-14,5),new Point(14,5)}){Color c=Vision.At(image,p.X+(int)Math.Round(offset.X*FaceScaleX),p.Y+(int)Math.Round(offset.Y*FaceScaleY));if(c.R>205&&c.G>205&&c.B>145)pale++;}return pale>=5;}
    static bool EnergyFaceVisible(Bitmap image,Point p){
        int yellow=0,outline=0;
        for(int y=-13;y<=13;y++)for(int x=-11;x<=11;x++){Color c=Vision.At(image,p.X+x,p.Y+y);if(c.R>200&&c.G>150&&c.B<125)yellow++;}
        foreach(int x in new[]{-16,16})for(int y=-12;y<=12;y+=4){Color c=Vision.At(image,p.X+x,p.Y+y);if(c.R<180&&c.G<210&&c.B<155)outline++;}
        return yellow>=12&&yellow<=160&&outline>=4;
    }
    static bool Exposed(string id,Dictionary<string,Dictionary<string,object>> cards,HashSet<string> left){var c=cards[id];return left.Contains(id)&&!left.Any(other=>Convert.ToInt32(cards[other]["layer"])>Convert.ToInt32(c["layer"])&&Math.Abs(Convert.ToInt32(cards[other]["rolNum"])-Convert.ToInt32(c["rolNum"]))<8&&Math.Abs(Convert.ToInt32(cards[other]["rowNum"])-Convert.ToInt32(c["rowNum"]))<8);}
    static int TrayCount(Bitmap im) {
        int n=0;bool gap=false;
        for(int k=0;k<7;k++) {
            int x=40+42*k,light=0;
            for(int y=492;y<=526;y+=3)for(int dx=-15;dx<=15;dx+=3){Color a=Vision.At(im,x+dx,y);if(a.R>185&&a.G>180&&a.B>115)light++;}
            bool filled=light>22;
            if(filled){if(gap)throw new Exception("牌槽有不连续位置，等待动画结束后重试");n++;}else gap=true;
        }return n;
    }
    static double Score(Bitmap im,Point p,Bitmap reference,Point q) {
        var a=Vision.GetPixels(im);var b=Vision.GetPixels(reference);
        if(p.X<11||p.Y<11||p.X+11>=a.Width||p.Y+11>=a.Height||q.X<11||q.Y<11||q.X+11>=b.Width||q.Y+11>=b.Height)throw new ArgumentOutOfRangeException("p","像素坐标超出图像");
        double sum=0;int n=0;
        for(int y=-11;y<=11;y+=2)for(int x=-11;x<=11;x+=2){int i=(p.Y+y)*a.Stride+(p.X+x)*3,j=(q.Y+y)*b.Stride+(q.X+x)*3;sum+=Math.Abs(a.Data[i]-b.Data[j])+Math.Abs(a.Data[i+1]-b.Data[j+1])+Math.Abs(a.Data[i+2]-b.Data[j+2]);n++;}return sum/n;
    }
    internal static double TargetScore(Bitmap im,Point p,int type,Bitmap reference,List<Dictionary<string,object>> anchors) {
        double best=1000;
        foreach(var a in anchors.Where(a=>Convert.ToInt32(a["type"])==type)) {
            Point q=PointOf(a["center"]);
            for(int dy=-4;dy<=4;dy++)for(int dx=-2;dx<=2;dx++)best=Math.Min(best,Score(im,new Point(p.X+dx,p.Y+dy),reference,q));
        }return best;
    }
    // Best match for a type among the calibration anchors and learned samples.
    // An unknown type scores 1000, so a carried frame falls back to a capture.
    internal static double MatchScore(Bitmap im,Point p,int type,Bitmap reference,List<Dictionary<string,object>> anchors,Dictionary<int,Bitmap> learned) {
        double best=TargetScore(im,p,type,reference,anchors);
        if(learned.ContainsKey(type))for(int dy=-4;dy<=4;dy++)for(int dx=-2;dx<=2;dx++)best=Math.Min(best,Score(im,new Point(p.X+dx,p.Y+dy),learned[type],new Point(17,18)));
        return best;
    }
    // Status is progress display and a crash breadcrumb; actions.jsonl stays the
    // synchronous record. Writes leave the click path, latest payload wins, and
    // FlushStatus() runs before the process reports its final state.
    static readonly object StatusGate=new object();
    static string StatusPath,StatusPayload;static Task StatusTask;
    static readonly int ProcessId=System.Diagnostics.Process.GetCurrentProcess().Id;
    static void WriteStatus(string path,string json) {
        string temp=path+".tmp";File.WriteAllText(temp,json,Encoding.UTF8);
        for(int attempt=0;attempt<8;attempt++)try{if(File.Exists(path))File.Replace(temp,path,null);else File.Move(temp,path);return;}catch(IOException){System.Threading.Thread.Sleep(15);}
    }
    static void DrainStatus() {
        while(true){
            string path,json;
            lock(StatusGate){if(StatusPayload==null){StatusTask=null;return;}path=StatusPath;json=StatusPayload;StatusPayload=null;}
            try{WriteStatus(path,json);}catch(IOException){}catch(UnauthorizedAccessException){}
        }
    }
    static void PostStatus(string path,string json){lock(StatusGate){StatusPath=path;StatusPayload=json;if(StatusTask==null)StatusTask=Task.Run((Action)DrainStatus);}}
    static void FlushStatus(){Task pending;lock(StatusGate){pending=StatusTask;}if(pending!=null)pending.Wait();}
    static void SaveStatus(string folder,int next,int total,List<int> tray,string state,string message) {
        var payload=new Dictionary<string,object>{{"pid",ProcessId},{"input_pending",UnverifiedInput},{"next_step",next},{"total",total},{"expected_tray",tray.ToArray()},{"state",state},{"message",message},{"time",DateTimeOffset.Now.ToString("o")}};
        payload["verification_mode"]=VerificationMode;payload["steps_since_full_check"]=StepsSinceFullCheck;payload["last_full_check_step"]=LastFullCheckStep;payload["full_check_interval"]=FullCheckInterval;
        payload["full_speed"]=FullSpeed;
        if(UnverifiedInput&&PendingGroupCount>0)payload["pending_group"]=new Dictionary<string,object>{{"start",PendingGroupStart},{"count",PendingGroupCount}};
        if(TrayTrace!=null&&next<TrayTrace.Length){payload["confirmed_tray"]=TrayTrace[next];payload["confirmed_tray_count"]=TrayTrace[next].Length;payload["predicted_tray_count"]=tray.Count;}
        if(EnergyTrace!=null){payload["rules"]="energy_alien_v1";payload["energy_count"]=next>0?EnergyTrace[next-1].Collected:0;payload["collect_count"]=EnergyThreshold;payload["alien_activations"]=EnergyTrace.Take(next).Count(e=>e.Alien);payload["alien_pending"]=UnverifiedInput&&next<EnergyTrace.Length&&EnergyTrace[next].Alien;}
        if(RollingExecution){payload["execution_kind"]="rolling_segment_v1";payload["board_total"]=BoardTotal;payload["remaining_cards"]=BoardTotal-next-(EnergyTrace==null?0:EnergyTrace.Take(next).Sum(e=>e.AutoRemoved.Length));payload["segment_end_step"]=total;}
        PostStatus(Path.Combine(folder,"playback_status.json"),Json.Serialize(payload));
    }
    static void CheckSession(Dictionary<string,object> plan) {
        if(!plan.ContainsKey("capture_status_path"))return;
        var capture=Read((string)plan["capture_status_path"]);var lease=Read((string)plan["service_lease_path"]);
        if(!Convert.ToBoolean(lease["running"])||!capture.ContainsKey("session")||(string)capture["session"]!=(string)plan["session"]||!Convert.ToBoolean(capture["complete"]))throw new Exception("抓取已停止或已重新开局，停止旧计划");
        string source=(string)capture["source_path"],mode=source.Contains("/world/")?"world":source=="/sheep/v1/game/map_info_ex"?"daily":"other";
        if(mode!=(string)plan["mode"])throw new Exception("游戏模式已改变");
    }
    [STAThread] static int Main(string[] args) {
        Console.OutputEncoding=Encoding.UTF8;Native.SetProcessDPIAware();Application.EnableVisualStyles();
        try {
            if(args.Length==5&&args[0]=="--check-animation-occlusion") {
                using(var oldFrame=new Bitmap(args[1]))using(var newFrame=new Bitmap(args[2])) {
                    Point point=new Point(int.Parse(args[3]),int.Parse(args[4]));
                    if(!AnimationOccluded(oldFrame,newFrame,point)||TargetDisappeared(oldFrame,newFrame,point))throw new Exception("Opaque animation accepted as target removal");
                }
                Console.WriteLine("PASS: captured sheep animation blocks target confirmation; no input");return 0;
            }
            if(args.Length==1&&args[0]=="--check-ultra") {
                Rectangle testBounds=new Rectangle(50,80,336,622);
                if(!FollowWindow(ref testBounds,new Rectangle(-600,200,336,622))||testBounds.X!=-600||FollowWindow(ref testBounds,testBounds))throw new Exception("Window translation failed");
                bool resized=false;try{FollowWindow(ref testBounds,new Rectangle(-600,200,400,622));}catch(Exception){resized=true;}if(!resized)throw new Exception("Resize accepted");
                foreach(int interval in new[]{1,7,30,1000})if(FullCheckDue("safe_ultra",interval-1,interval)||!FullCheckDue("safe_ultra",interval,interval)||FullCheckDue("ultra",interval,interval)||FullCheckDue("normal",interval,interval))throw new Exception("Checkpoint interval failed");
                if(!VerificationEvidence(false,false,2,2,30,20)||VerificationEvidence(false,false,1,2,30,20)||VerificationEvidence(false,false,2,2,0,0)||!VerificationEvidence(true,false,-1,2,30,0))throw new Exception("Immediate local/tray evidence gate failed");
                if(NeedsStableFrame(false,true,true,false)||!NeedsStableFrame(false,false,true,false)||!NeedsStableFrame(false,true,true,true)||NeedsStableFrame(true,true,true,false))throw new Exception("Ultra fallback still waits for animation stability");
                if(!ClickLost(500,500,0,5,5,false)||ClickLost(499,500,0,5,5,false)||ClickLost(500,500,172,5,5,false)||ClickLost(500,500,3,5,5,false)||ClickLost(500,500,0,6,5,false)||ClickLost(500,500,0,-1,5,false)||ClickLost(900,500,0,5,5,true))throw new Exception("Lost-click gate failed");
                if(TrayEvidenceAllowed(true,false,false,2,false)||!TrayEvidenceAllowed(true,false,false,2.5,false)||TrayEvidenceAllowed(true,false,false,90,true)||!TrayEvidenceAllowed(true,false,true,0,true)||!TrayEvidenceAllowed(true,true,false,0,true)||!TrayEvidenceAllowed(false,false,false,0,true))throw new Exception("Tray evidence gate failed");
                int[][] testTrays={new int[0],new[]{1},new[]{1,2},new int[]{2},new[]{2,3},new[]{2,3,4},new[]{3,4}};
                if(BurstSize(0,testTrays,6)!=3||BurstSize(3,testTrays,6)!=2||BurstSize(0,testTrays,2)!=2||BurstSize(5,testTrays,6)!=0)throw new Exception("Burst grouping failed");
                var none=new List<int>();
                if(!ClicksToResend(new List<int>{10},new List<int>{11},none,false,5,6,3).SequenceEqual(new[]{10})||!ClicksToResend(none,new List<int>{7},none,false,5,6,3).SequenceEqual(new[]{7})||!ClicksToResend(new List<int>{1},none,none,false,4,7,4).SequenceEqual(new[]{1})||ClicksToResend(none,new List<int>{5,6,7},none,false,6,7,4).Count!=0||!ClicksToResend(none,new List<int>{5,6,7},none,true,6,7,4).SequenceEqual(new[]{5})||ClicksToResend(new List<int>{1},none,none,false,-1,6,3).Count!=0||ClicksToResend(new List<int>{1,2},none,none,false,6,6,6).Count!=0||ClicksToResend(new List<int>{1,2},none,none,false,6,7,4).Count!=0||!ClicksToResend(none,new List<int>{128,129},new List<int>{128},false,3,7,4).SequenceEqual(new[]{128}))throw new Exception("Resend selection failed");
                if(!ClicksToResend(none,new List<int>{56},none,false,3,7,4).SequenceEqual(new[]{56}))throw new Exception("Post-clear shortfall failed");
                if(ProvenLost(0,50,true)||!ProvenLost(0,1,true)||!ProvenLost(90,200,true)||ProvenLost(90,200,false))throw new Exception("Lost proof failed");
                if(!CountDecides(4,4,4,true,600,150)||CountDecides(4,4,4,false,900,900)||!CountDecides(3,3,5,false,600,150)||CountDecides(4,4,4,true,599,150)||CountDecides(4,4,4,true,600,149)||CountDecides(5,4,4,true,900,900))throw new Exception("Count decision failed");
                if(!LastCardStayed(true,false,2,0,2)||LastCardStayed(true,false,0,0,2)||LastCardStayed(false,false,2,0,2)||LastCardStayed(true,true,2,0,2)||!LastCardStayed(true,false,-1,0,2))throw new Exception("Last card gate failed");
                if(!LowCountTrusted(false,4,7,100)||!LowCountTrusted(true,7,7,100)||LowCountTrusted(true,4,7,599)||!LowCountTrusted(true,4,7,600))throw new Exception("Low count gate failed");
                if(!GoneEarly(true,true,6,4,4,200)||GoneEarly(true,true,6,4,4,199)||GoneEarly(false,true,6,4,4,900)||GoneEarly(true,false,6,4,4,900)||GoneEarly(true,true,-1,4,4,900)||GoneEarly(true,true,3,4,4,900)||!GoneEarly(true,true,3,6,3,200))throw new Exception("Early group gate failed");
                if(!GroupSettled(true,4,4,4,3,250)||GroupSettled(true,4,4,4,3,249)||!GroupSettled(true,7,4,4,3,100)||GroupSettled(true,6,4,4,3,900)||GroupSettled(true,-1,4,4,3,900)||GroupSettled(false,4,4,4,3,900)||!GroupSettled(true,3,3,5,1,100))throw new Exception("Group settle gate failed");
                DateTime clearClock=new DateTime(2026,1,1,0,0,0,DateTimeKind.Utc);
                if(ClearNotBefore(clearClock,6,460)!=clearClock.AddMilliseconds(460)||ClearNotBefore(clearClock,5,460)!=clearClock.AddMilliseconds(MinClearHoldMs)||ClearNotBefore(DateTime.MinValue,6,460)!=DateTime.MinValue)throw new Exception("Clear hold failed");
                if(ClickGap(0)!=MinClickGapMs||ClearGap(0)!=MinClearGapMs||ClickGap(135)!=135||ClearGap(150)!=150)throw new Exception("Click gap floor failed");
                if(!TrayMoved(6,5,6,70)||!TrayMoved(7,6,4,70)||!TrayMoved(4,6,4,70)||TrayMoved(6,6,4,70)||TrayMoved(5,5,6,900)||TrayMoved(-1,5,6,249)||!TrayMoved(-1,5,6,250))throw new Exception("Tray movement gate failed");
                if(ConfirmedRemoved(true,true,false)||!ConfirmedRemoved(true,false,false)||!ConfirmedRemoved(true,true,true)||ConfirmedRemoved(false,false,false))throw new Exception("Intact target accepted as removed");
                if(!CoversExactly(new Point(100,100),new[]{new Point(300,300),new Point(105,96)})||CoversExactly(new Point(100,100),new[]{new Point(109,100),new Point(100,91)})||CoversExactly(new Point(100,100),new Point[0]))throw new Exception("Exact stack gate failed");
                DateTime clock=new DateTime(2026,1,1,0,0,0,DateTimeKind.Utc);
                if(NextStepDue(clock,clock.AddMilliseconds(70),0)!=clock.AddMilliseconds(70)||NextStepDue(clock,clock.AddMilliseconds(70),150)!=clock.AddMilliseconds(150-CaptureLeadMs)||NextStepDue(clock,clock.AddMilliseconds(200),150)!=clock.AddMilliseconds(200))throw new Exception("Click gap schedule failed");
                int[] replay=new int[0];foreach(int type in new[]{1,2,3,1,1,2,2,3,3})replay=PushTray(replay,type);if(replay.Length!=0||!PushTray(new[]{1,1,2,3,4,5},1).SequenceEqual(new[]{2,3,4,5}))throw new Exception("Triple replay count failed");
                using(var testBefore=new Bitmap(336,622)) {
                    using(var g=Graphics.FromImage(testBefore)){g.Clear(Color.FromArgb(193,255,137));g.FillRectangle(Brushes.Beige,133,217,34,36);g.FillEllipse(Brushes.DarkBlue,140,224,21,22);}
                    Point p=new Point(150,235);
                    using(var after=new Bitmap(testBefore))if(TargetDisappeared(testBefore,after,p))throw new Exception("Unchanged tile accepted");
                    using(var after=new Bitmap(testBefore)){using(var g=Graphics.FromImage(after))g.FillRectangle(Brushes.Black,130,214,40,42);if(TargetDisappeared(testBefore,after,p))throw new Exception("Opaque overlay accepted");}
                    using(var after=new Bitmap(testBefore)){using(var g=Graphics.FromImage(after))g.FillRectangle(new SolidBrush(Color.FromArgb(193,255,137)),130,214,40,42);if(!TargetDisappeared(testBefore,after,p))throw new Exception("Removed target rejected");}
                    using(var after=new Bitmap(testBefore)){using(var g=Graphics.FromImage(after)){g.FillRectangle(Brushes.Beige,133,217,34,36);g.FillEllipse(Brushes.Red,140,224,21,22);}if(!TargetDisappeared(testBefore,after,p))throw new Exception("Uncovered different tile rejected");}
                    using(var after=new Bitmap(testBefore)) {
                        using(var g=Graphics.FromImage(after)){g.Clear(Color.FromArgb(193,255,137));using(var gray=new SolidBrush(Color.FromArgb(133,155,92)))g.FillRectangle(gray,133,200,34,36);}
                        if(TargetDisappeared(testBefore,after,p)||!TargetDisappeared(testBefore,after,p,new[]{new Point(150,218)})||TargetDisappeared(testBefore,after,p,new Point[0]))throw new Exception("Covered lower tile geometry gate failed");
                    }
                }
                Console.WriteLine("PASS: translation follows, resize blocked; selectable checkpoint intervals; unchanged/obscured target blocked, removal accepted; no input");return 0;
            }
            if(args.Length==2&&args[0]=="--check-learning"){
                var testPlan=Read(args[1]);CheckSession(testPlan);var answer=Read((string)testPlan["solution_path"]);var cs=Items(answer["cards"]).Select(o=>(Dictionary<string,object>)o).ToDictionary(c=>(string)c["id"]);var seq=Items(answer["sequence"]).Select(Convert.ToString).ToArray();int start=Convert.ToInt32(testPlan["start_step"]);var ids=new HashSet<string>(seq.Skip(start));Point p=PointOf(((Dictionary<string,object>)testPlan["positions"])[seq[start]]);
                using(var source=new Bitmap((string)testPlan["initial_reference_path"]))using(var im=Vision.Normalize(source))using(var missing=new Bitmap(im)){
                    using(var g=Graphics.FromImage(missing))g.FillRectangle(Brushes.Black,p.X-17,p.Y-18,34,36);
                    if(!Exposed(seq[start],cs,ids)||!FaceVisible(im,p)||FaceVisible(missing,p)||Exposed(seq[0],cs,ids)||Vision.PatchDifference(im,missing,new Rectangle(p.X-15,p.Y-16,30,32))<=3)throw new Exception("Dynamic learning gates failed");
                }Console.WriteLine("PASS: verified uncovered stable tile can be learned; missing/covered or changed face blocked; no input");return 0;
            }
            if(args.Length==2&&args[0]=="--check-energy-faces"){
                using(var im=new Bitmap(args[1])){
                    foreach(var p in new[]{new Point(83,185),new Point(254,151),new Point(100,271)})if(!EnergyFaceVisible(im,p))throw new Exception("Battery face rejected");
                    foreach(var p in new[]{new Point(40,510),new Point(100,237),new Point(65,367)})if(EnergyFaceVisible(im,p))throw new Exception("Ordinary icon accepted as battery");
                    using(var missing=new Bitmap(im)){using(var g=Graphics.FromImage(missing))g.FillRectangle(Brushes.Black,66,167,34,36);if(EnergyFaceVisible(missing,new Point(83,185)))throw new Exception("Obscured battery accepted");}
                }Console.WriteLine("PASS: battery backgrounds accepted; coin, corn, grass and obscured faces rejected; no input");return 0;
            }
            if(args.Length==1&&args[0]=="--check-verification"){
                if(VerificationMatches(false,2,1,15,15)||VerificationMatches(false,1,1,0,0)||VerificationMatches(false,-1,0,15,15)||!VerificationMatches(false,1,1,15,0)||!VerificationMatches(true,0,2,15,0)||VerificationMatches(true,0,0,0,0))throw new Exception("Verification gates failed");
                Console.WriteLine("PASS: wrong/animating tray and unchanged frames blocked; changed settlement accepted");return 0;
            }
            if(args.Length==3&&args[0]=="--check-aligned-faces"){
                var fixture=Read(args[1]);var calibration=(Dictionary<string,object>)fixture["calibration"];
                FaceScaleX=Convert.ToDouble(calibration["face_scale_x"]);FaceScaleY=Convert.ToDouble(calibration["face_scale_y"]);
                using(var im=new Bitmap(args[2]))foreach(var anchor in Items(fixture["anchors"])){
                    Point point=PointOf(((Dictionary<string,object>)anchor)["center"]);
                    if(!FaceVisible(im,point))throw new Exception("Aligned face rejected");
                    using(var missing=new Bitmap(im)){using(var g=Graphics.FromImage(missing))g.FillRectangle(Brushes.Black,point.X-17,point.Y-18,34,36);if(FaceVisible(missing,point))throw new Exception("Missing face accepted");}
                }
                Console.WriteLine("PASS: scaled exposed faces accepted and missing faces blocked; no input");return 0;
            }
            if(args.Length==6&&args[0]=="--check-lost-click"){
                using(var oldFrame=new Bitmap(args[1]))using(var newFrame=new Bitmap(args[2])){
                    Point point=new Point(int.Parse(args[3]),int.Parse(args[4]));int trayBefore=int.Parse(args[5]);
                    double diff=Vision.PatchDifference(oldFrame,newFrame,new Rectangle(point.X-14,point.Y-14,28,28));int seen=-1;try{seen=TrayCount(newFrame);}catch(Exception){}
                    bool lost=ClickLost(3000,500,diff,seen,trayBefore,false);
                    Console.WriteLine("target_diff="+diff.ToString("F2")+" tray_seen="+seen+" tray_before="+trayBefore+" lost="+lost);
                    return lost?0:3;
                }
            }
            if(args.Length==2&&args[0]=="--check-session"){CheckSession(Read(args[1]));Console.WriteLine("PASS: session and mode gates");return 0;}
            if(args.Length<1)throw new Exception("Usage: SolverPlayback.exe plan.json [limit]");
            string planPath=Path.GetFullPath(args[0]),folder=Path.GetDirectoryName(planPath);
            var plan=Read(planPath);var solution=Read((string)plan["solution_path"]);
            VerificationMode=Mode(plan.ContainsKey("verification_mode")?plan["verification_mode"]:null);
            if(plan.ContainsKey("full_check_interval"))FullCheckInterval=Math.Max(1,Math.Min(1000,Convert.ToInt32(plan["full_check_interval"])));
            if(plan.ContainsKey("calibration")){
                var calibration=(Dictionary<string,object>)plan["calibration"];
                if(calibration.ContainsKey("face_scale_x"))FaceScaleX=Convert.ToDouble(calibration["face_scale_x"]);
                if(calibration.ContainsKey("face_scale_y"))FaceScaleY=Convert.ToDouble(calibration["face_scale_y"]);
                if(FaceScaleX<.6||FaceScaleX>1.5||FaceScaleY<.6||FaceScaleY>1.5)throw new Exception("牌框缩放参数超出范围");
            }
            CheckSession(plan);
            bool rollingMode=solution.ContainsKey("execution_kind")&&Convert.ToString(solution["execution_kind"])=="rolling_segment_v1";
            if((string)solution["map_sha256"]!=(string)plan["map_sha256"])throw new Exception("地图不匹配");
            if(rollingMode){
                if(!plan.ContainsKey("allow_rolling")||!Convert.ToBoolean(plan["allow_rolling"])||!Convert.ToBoolean(solution["segment_validated"])||Convert.ToBoolean(solution["validated"]))throw new Exception("分段计划缺少明确执行授权");
                string source=Convert.ToString(plan["source_map_path"]);using(var sha=System.Security.Cryptography.SHA256.Create())if(BitConverter.ToString(sha.ComputeHash(File.ReadAllBytes(source))).Replace("-","").ToLowerInvariant()!=(string)plan["map_sha256"])throw new Exception("分段源地图已变化");
                int from=Convert.ToInt32(solution["start_step"]),begin=plan.ContainsKey("start_step")?Convert.ToInt32(plan["start_step"]):0;
                if(from!=begin||from<0||Items(solution["sequence"]).Length<=from||Items(solution["sequence"]).Length-from>(solution.ContainsKey("exact_route")&&Convert.ToBoolean(solution["exact_route"])?int.MaxValue:6))throw new Exception("分段执行范围无效");
            }else if(!Convert.ToBoolean(solution["validated"]))throw new Exception("答案未验证");
            var cards=Items(solution["cards"]).Select(o=>(Dictionary<string,object>)o).ToDictionary(c=>(string)c["id"]);
            var sequence=Items(solution["sequence"]).Select(Convert.ToString).ToArray();
            var positions=(Dictionary<string,object>)plan["positions"];
            var anchors=Items(plan["anchors"]).Select(o=>(Dictionary<string,object>)o).ToList();
            var learned=new Dictionary<int,Bitmap>();string templates=Path.Combine(folder,"templates");Directory.CreateDirectory(templates);
            string context=Path.Combine(templates,"context.json");if(File.Exists(context)){var previous=Read(context);if(Convert.ToString(previous["map_sha256"])==Convert.ToString(plan["map_sha256"]))foreach(string file in Directory.GetFiles(templates,"type_*.png")){int type;if(int.TryParse(Path.GetFileNameWithoutExtension(file).Substring(5),out type))using(var im=new Bitmap(file))learned[type]=new Bitmap(im);}}
            File.WriteAllText(context,Json.Serialize(new Dictionary<string,object>{{"map_sha256",plan["map_sha256"]}}));
            // Replay coverage and tray rules before allowing any real input.
            var cardArray=Items(solution["cards"]).Select(o=>(Dictionary<string,object>)o).ToArray();
            var indices=cardArray.Select((c,i)=>new {Id=(string)c["id"],Index=i}).ToDictionary(v=>v.Id,v=>v.Index);
            bool energyMode=solution.ContainsKey("rules")&&solution["rules"] is Dictionary<string,object>&&Convert.ToString(((Dictionary<string,object>)solution["rules"])["kind"])=="energy_alien_v1";
            var energyRuntime=energyMode?new EnergyRules(cardArray,(Dictionary<string,object>)solution["rules"]):null;
            var energyFacts=energyMode?Items(solution["energy_events"]).Select(o=>(Dictionary<string,object>)o).ToArray():null;
            var energySteps=energyMode?new EnergyRules.Event[sequence.Length]:null;
            if(energyMode&&energyFacts.Length!=sequence.Length)throw new Exception("Incomplete energy event trace");
            var colorRuntime=energyMode?new ColorRules{Types=cardArray.Select(c=>Convert.ToInt32(c["type"])).ToArray(),Special=new int[0]}:ColorRules.FromSolution(solution,cardArray);var colorPicked=new bool[cardArray.Length];
            var sequenceTypes=new int[sequence.Length];int replayStep=0;
            var remaining=new HashSet<string>(cards.Keys);var simulated=new List<int>();var trayTrace=new List<int[]>{new int[0]};
            foreach(string id in sequence) {
                if(!remaining.Contains(id))throw new Exception("重复或未知牌编号");var c=cards[id];
                if(remaining.Any(other=>Convert.ToInt32(cards[other]["layer"])>Convert.ToInt32(c["layer"])&&Math.Abs(Convert.ToInt32(cards[other]["rolNum"])-Convert.ToInt32(c["rolNum"]))<8&&Math.Abs(Convert.ToInt32(cards[other]["rowNum"])-Convert.ToInt32(c["rowNum"]))<8))throw new Exception("答案点击了被遮挡的牌");
                int t=colorRuntime.Types[indices[id]];sequenceTypes[replayStep]=t;bool crush=simulated.Count(x=>x==t)==2;simulated=PushTray(simulated.ToArray(),t).ToList();
                if(energyMode){var ev=energyRuntime.Step(id);energyRuntime.Check(ev,energyFacts[replayStep],id);energySteps[replayStep]=ev;simulated=ev.Tray.ToList();foreach(string auto in ev.AutoRemoved)remaining.Remove(auto);}
                trayTrace.Add(simulated.ToArray());
                replayStep++;
                colorPicked[indices[id]]=true;if(crush&&colorRuntime.Enabled)colorRuntime.Shuffle(colorPicked);
                if(simulated.Count>=7)throw new Exception("答案会填满牌槽");remaining.Remove(id);Point p=PointOf(positions[id]);
                if(p.X<20||p.X>315||p.Y<125||p.Y>465)throw new Exception("点击在棋盘以外");
            }
            if(solution.ContainsKey("sequence_types")&&!Items(solution["sequence_types"]).Select(Convert.ToInt32).SequenceEqual(sequenceTypes))throw new Exception("逐步牌型与独立变色回放不一致");
            bool boardCleared=remaining.Count==0&&simulated.Count==0;
            if(!rollingMode&&!boardCleared)throw new Exception("答案不完整");
            if(rollingMode&&(Convert.ToBoolean(solution["complete"])!=boardCleared||Convert.ToInt32(solution["remaining_count"])!=remaining.Count||!Items(solution["expected_tray"]).Select(Convert.ToInt32).SequenceEqual(simulated)))throw new Exception("分段终点状态不一致");
            if(rollingMode)RollingState.CheckLookahead(solution);
            TrayTrace=trayTrace.ToArray();EnergyTrace=energySteps;if(energyMode)EnergyThreshold=Convert.ToInt32(((Dictionary<string,object>)solution["rules"])["collect_count"]);
            RollingExecution=rollingMode;BoardTotal=cards.Count;
            if(args.Length>1&&args[1]=="--dump-tray-trace"){Console.WriteLine(Json.Serialize(TrayTrace));return 0;}
            if(args.Length>1&&args[1]=="--validate-only"){Console.WriteLine(rollingMode?"PASS: rolling segment coverage, seeded rules and tray replay; no input":"PASS: complete coverage, seeded colors and seven-slot replay; no input");return 0;}
            int limit=args.Length>1?int.Parse(args[1]):sequence.Length;
            var windows=Native.FindGame();if(windows.Count!=1)throw new Exception("未找到唯一的羊了个羊窗口");IntPtr game=windows[0];Rectangle bounds=Native.Bounds(game);
            int markMs=plan.ContainsKey("mark_milliseconds")?Math.Max(Ultra?0:60,Math.Min(5000,Convert.ToInt32(plan["mark_milliseconds"]))):450;
            FullSpeed=Ultra&&plan.ContainsKey("full_speed")&&Convert.ToBoolean(plan["full_speed"]);
            int retryMs=plan.ContainsKey("retry_milliseconds")?Math.Max(MinRetryMs,Math.Min(5000,Convert.ToInt32(plan["retry_milliseconds"]))):500;
            int clearGapMs=plan.ContainsKey("clear_gap_milliseconds")?ClearGap(plan["clear_gap_milliseconds"]):150;
            int clickGapMs=plan.ContainsKey("click_gap_milliseconds")?ClickGap(plan["click_gap_milliseconds"]):135;
            int clearHoldMs=plan.ContainsKey("clear_hold_milliseconds")?Math.Max(400,Math.Min(2000,Convert.ToInt32(plan["clear_hold_milliseconds"]))):460;
            bool earlyConfirm=!plan.ContainsKey("early_confirm")||Convert.ToBoolean(plan["early_confirm"]);
            int burstGapMs=plan.ContainsKey("burst_gap_milliseconds")?Math.Max(20,Math.Min(200,Convert.ToInt32(plan["burst_gap_milliseconds"]))):35;
            int verifyMs=plan.ContainsKey("verify_milliseconds")?Math.Max(Ultra?20:200,Math.Min(5000,Convert.ToInt32(plan["verify_milliseconds"]))):1000;
            bool adaptive=plan.ContainsKey("adaptive_verify")&&Convert.ToBoolean(plan["adaptive_verify"]);DateTime verifyDeadline=DateTime.MinValue,targetDeadline=DateTime.MinValue;Bitmap settledFrame=null;
            string lastCommand="";
            var tray=new List<int>();bool colorCrushed=false;int next=plan.ContainsKey("start_step")?Convert.ToInt32(plan["start_step"]):0,result=0,stage=0;bool stopRequested=false;bool paused=false,oldPause=false;string reason="";Bitmap before=null;Point target=Point.Empty;
            if(next<0||next>=sequence.Length)throw new Exception("恢复步数无效");
            tray=TrayTrace[next].ToList();
            var activeRemaining=new HashSet<string>(cards.Keys);for(int i=0;i<next;i++){activeRemaining.Remove(sequence[i]);if(energyMode)foreach(string auto in energySteps[i].AutoRemoved)activeRemaining.Remove(auto);}
            LastFullCheckStep=next;StepsSinceFullCheck=0;
            bool inputUltra=false,effectSampleSaved=false,reusedTargetFrame=false,targetStacked=false;DateTime inputSentAt=DateTime.MinValue,firstInputAt=DateTime.MinValue,fullDeadline=DateTime.MinValue,prepareStartedAt=DateTime.MinValue,frameAt=DateTime.MinValue;double prepareMs=0;Point[] localRemaining=null;
            int burstStart=0,burstCount=0,groupReads=0,earlyReads=0;bool[] burstStacked=null;DateTime lastClearAt=DateTime.MinValue,groupReadableAt=DateTime.MinValue,exactCountSince=DateTime.MinValue;
            Bitmap unsavedVerified=null,closingFrame=null;double firstGoneMs=-1;DateTime nextVerifiedSave=DateTime.MinValue,clickNotBefore=DateTime.MinValue;int inputRetries=0;
            Action flushVerified=delegate{if(unsavedVerified==null)return;unsavedVerified.Save(Path.Combine(folder,"last_verified.png"),ImageFormat.Png);unsavedVerified.Dispose();unsavedVerified=null;};
            Task<string> fullTask=null;Bitmap fullFrame=null;string fullError="";
            bool dynamic=plan.ContainsKey("dynamic_anchors")&&Convert.ToBoolean(plan["dynamic_anchors"]);Bitmap learningFrame=null;DateTime learningDeadline=DateTime.MinValue;
            using(var original=new Bitmap((string)plan["reference_path"]))using(var reference=Vision.Normalize(original))using(var overlay=new Overlay())using(var timer=new Timer()) {
                using(var raw=Native.Capture(game))using(var now=Vision.Normalize(raw)) {
                    if(TrayCount(now)!=tray.Count)throw new Exception("实际牌槽与计划起点不一致");
                    using(var initialRaw=new Bitmap(plan.ContainsKey("initial_reference_path")?(string)plan["initial_reference_path"]:(string)plan["reference_path"]))using(var initial=Vision.Normalize(initialRaw))
                        if(Vision.PatchDifference(initial,now,new Rectangle(25,118,286,347))>5)throw new Exception("棋盘与坐标校准截图不一致");
                }
                DateTime due=DateTime.UtcNow,lastPreviewAt=DateTime.MinValue,nextControlCheck=DateTime.MinValue,nextSessionCheck=DateTime.MinValue;
                timer.Interval=Ultra?20:50;
                string controlPath=Path.Combine(folder,"control.json");
                EventHandler tick=delegate {
                    try {
                        bool freshTargetFrame=false;
                        DateTime tickAt=DateTime.UtcNow;
                        if(tickAt>=nextControlCheck&&File.Exists(controlPath)) {
                            nextControlCheck=tickAt.AddMilliseconds(50);
                            try {
                                var command=Read(controlPath);string id=Convert.ToString(command["id"]);
                                if(id!=lastCommand){lastCommand=id;string action=Convert.ToString(command["action"]);
                                    if(action=="stop"){if(!UnverifiedInput)throw new OperationCanceledException("用户停止点击");stopRequested=true;}
                                    if(action=="speed"){
                                        string mode=Mode(command.ContainsKey("verification_mode")?command["verification_mode"]:VerificationMode);
                                        VerificationMode=mode;
                                        FullSpeed=Ultra&&command.ContainsKey("full_speed")&&Convert.ToBoolean(command["full_speed"]);
                                        if(command.ContainsKey("full_check_interval"))FullCheckInterval=Math.Max(1,Math.Min(1000,Convert.ToInt32(command["full_check_interval"])));
                                        markMs=Math.Max(Ultra?0:60,Math.Min(5000,Convert.ToInt32(command["mark_milliseconds"])));verifyMs=Math.Max(Ultra?20:200,Math.Min(5000,Convert.ToInt32(command["verify_milliseconds"])));timer.Interval=Ultra?20:50;
                                        if(command.ContainsKey("retry_milliseconds"))retryMs=Math.Max(MinRetryMs,Math.Min(5000,Convert.ToInt32(command["retry_milliseconds"])));
                                        if(command.ContainsKey("clear_gap_milliseconds"))clearGapMs=ClearGap(command["clear_gap_milliseconds"]);
                                        if(command.ContainsKey("click_gap_milliseconds"))clickGapMs=ClickGap(command["click_gap_milliseconds"]);
                                    }
                                    if(action=="pause"||action=="resume"){paused=action=="pause";if(!paused&&(stage==2||stage==5))verifyDeadline=DateTime.UtcNow.AddMilliseconds(VerificationWindow(energyMode&&energySteps[next].Alien,verifyMs));overlay.Hide();SaveStatus(folder,next,sequence.Length,tray,paused?"paused":"running",paused?"已暂停":"继续执行");}
                                }
                            }catch(IOException){}catch(ArgumentException){}
                        }
                        if((Native.GetAsyncKeyState(0x78)&0x8000)!=0){if(!UnverifiedInput)throw new OperationCanceledException("已按 F9 停止");stopRequested=true;overlay.Hide();}
                        bool key=(Native.GetAsyncKeyState(0x77)&0x8000)!=0;
                        if(key&&!oldPause){paused=!paused;if(!paused&&(stage==2||stage==5))verifyDeadline=DateTime.UtcNow.AddMilliseconds(VerificationWindow(energyMode&&energySteps[next].Alien,verifyMs));SaveStatus(folder,next,sequence.Length,tray,paused?"paused":"running","F8");overlay.Hide();}oldPause=key;
                        if(paused||DateTime.UtcNow<due)return;
                        // Two JSON reads per poll would dominate a fast step.
                        // No normal-mode poll is skipped.
                        if(!Ultra||DateTime.UtcNow>=nextSessionCheck){CheckSession(plan);nextSessionCheck=DateTime.UtcNow.AddMilliseconds(250);}
                        if(FollowWindow(ref bounds,Native.Bounds(game))) {
                            overlay.Hide();
                            // A sent click still needs verification; never replay it
                            // just because the containing window moved. Before sending,
                            // restart target checks after a short quiet interval.
                            if(!UnverifiedInput&&stage!=4){stage=0;targetDeadline=DateTime.MinValue;if(learningFrame!=null){learningFrame.Dispose();learningFrame=null;}}
                            else verifyDeadline=DateTime.UtcNow.AddMilliseconds(VerificationWindow(energyMode&&energySteps[next].Alien,verifyMs));
                            due=DateTime.UtcNow.AddMilliseconds(180);return;
                        }
                        if(stage==4) {
                            if(fullTask!=null) {
                                if(!fullTask.IsCompleted)return;
                                fullError=fullTask.Result;fullTask=null;
                                if(fullError==null){
                                    File.Copy(Path.Combine(folder,"full_check_frame.png"),Path.Combine(folder,"last_verified.png"),true);
                                    LastFullCheckStep=next;StepsSinceFullCheck=0;
                                    fullDeadline=DateTime.MinValue;targetDeadline=DateTime.MinValue;stage=0;
                                    File.AppendAllText(Path.Combine(folder,"full_checks.jsonl"),Json.Serialize(new Dictionary<string,object>{{"step",next},{"verified",true},{"interval",FullCheckInterval},{"time",DateTimeOffset.Now.ToString("o")}})+Environment.NewLine,Encoding.UTF8);
                                    SaveStatus(folder,next,sequence.Length,tray,"running","全盘核对通过，继续执行");return;
                                }
                            }
                            if(DateTime.UtcNow>fullDeadline)throw new Exception("全盘自动核对未通过，已停止后续点击；"+fullError);
                            using(var raw=Native.Capture(game))using(var frame=Vision.Normalize(raw)) {
                                bool stable=fullFrame!=null&&Vision.PatchDifference(fullFrame,frame,new Rectangle(20,118,296,415))<3;
                                if(fullFrame!=null)fullFrame.Dispose();fullFrame=new Bitmap(frame);
                                if(!stable){due=DateTime.UtcNow.AddMilliseconds(100);return;}
                                string checkImage=Path.Combine(folder,"full_check_frame.png");frame.Save(checkImage,ImageFormat.Png);
                                int checkStep=next;fullTask=Task.Run(()=>FullCheck(folder,checkImage,checkStep));
                            }
                            due=DateTime.UtcNow.AddMilliseconds(100);return;
                        }
                        if(stage==5) {
                            if(DateTime.UtcNow<groupReadableAt){due=groupReadableAt;return;}
                            // One look per group replaces every per-step check. Nothing
                            // further is sent until the whole group is accounted for.
                            Bitmap groupFrame=Native.CaptureNormalized(game);
                            try {
                                int groupEnd=burstStart+burstCount,trayStart=TrayTrace[burstStart].Length,seen=-1;
                                try{seen=TrayCount(groupFrame);}catch(Exception){}
                                double sinceLast=(DateTime.UtcNow-inputSentAt).TotalMilliseconds;
                                var certainLost=new List<int>();var possibleLost=new List<int>();bool allGone=true,anyGone=false,noDoubt=!burstStacked.Any(stacked=>stacked);
                                for(int bi=burstStart;bi<groupEnd;bi++){
                                    Point bp=PointOf(positions[sequence[bi]]);int bt=sequenceTypes[bi];
                                    double bdiff=Vision.PatchDifference(before,groupFrame,new Rectangle(bp.X-14,bp.Y-14,28,28));
                                    bool knownType=learned.ContainsKey(bt)||anchors.Any(a=>Convert.ToInt32(a["type"])==bt);
                                    bool gone=knownType?MatchScore(groupFrame,bp,bt,reference,anchors,learned)>60:bdiff>8;
                                    if(!knownType||AnimationOccluded(before,groupFrame,bp))noDoubt=false;
                                    // The baseline frame may have had this card hidden, so a
                                    // known card still showing its own face is the evidence.
                                    // A stacked card may look present after leaving, so it only
                                    // counts as a candidate; the tray decides below.
                                    if(gone&&!burstStacked[bi-burstStart])anyGone=true;
                                    if(burstStacked[bi-burstStart]&&(!gone||!knownType&&FaceVisible(groupFrame,bp)))possibleLost.Add(bi);
                                    else if(!gone){allGone=false;if(knownType?ProvenLost(MatchScore(before,bp,bt,reference,anchors,learned),bdiff,true):bdiff<=2)certainLost.Add(bi);}
                                }
                                possibleLost=possibleLost.OrderBy(bi=>Convert.ToInt32(cards[sequence[bi]]["layer"])).ToList();
                                bool onePile=possibleLost.Count>0&&possibleLost.All(bi=>sequenceTypes[bi]==sequenceTypes[possibleLost[0]]&&Math.Abs(PointOf(positions[sequence[bi]]).X-PointOf(positions[sequence[possibleLost[0]]]).X)<=12&&Math.Abs(PointOf(positions[sequence[bi]]).Y-PointOf(positions[sequence[possibleLost[0]]]).Y)<=12);
                                if(seen==tray.Count&&sinceLast>=600){if(exactCountSince==DateTime.MinValue)exactCountSince=DateTime.UtcNow;}else exactCountSince=DateTime.MinValue;
                                bool countDecides=exactCountSince!=DateTime.MinValue&&CountDecides(seen,tray.Count,trayStart,anyGone,sinceLast,(DateTime.UtcNow-exactCountSince).TotalMilliseconds);
                                bool settled=GroupSettled(allGone,seen,tray.Count,trayStart,burstCount,sinceLast)&&LowCountTrusted(burstStacked.Any(stacked=>stacked),seen,trayStart+burstCount,sinceLast);
                                // A full reading cannot come from a tray in motion reading low.
                                groupReads=settled?groupReads+1:0;
                                if(allGone&&firstGoneMs<0)firstGoneMs=sinceLast;
                                earlyReads=earlyConfirm&&GoneEarly(allGone,noDoubt,seen,trayStart,tray.Count,sinceLast)?earlyReads+1:0;
                                bool goneEarly=earlyReads>=EarlyGoneReads;
                                if(settled&&groupReads<2&&seen!=trayStart+burstCount&&!goneEarly){due=DateTime.UtcNow;return;}
                                if(!settled&&!countDecides&&!goneEarly){
                                    int clearedType=sequenceTypes[groupEnd-1];
                                    var lostSteps=ClicksToResend(certainLost,possibleLost,possibleLost.Where(bi=>!colorCrushed||sequenceTypes[bi]!=clearedType).ToList(),onePile,seen,trayStart+burstCount,tray.Count);
                                    if(lostSteps.Count>0&&inputRetries<MaxGroupRetries&&sinceLast>=retryMs){
                                        inputRetries++;
                                        foreach(int bi in lostSteps){
                                            if(!Native.ClickNormalized(game,PointOf(positions[sequence[bi]]),bounds.Size))throw new Exception("窗口移动中，连点未能重发");
                                            File.AppendAllText(Path.Combine(folder,"input_recovery.jsonl"),Json.Serialize(new Dictionary<string,object>{{"time",DateTimeOffset.Now.ToString("o")},{"step",bi+1},{"card_id",sequence[bi]},{"result","resent_in_process"},{"attempt",inputRetries},{"target_delta",0},{"observed_tray",seen}})+Environment.NewLine,Encoding.UTF8);
                                            System.Threading.Thread.Sleep(burstGapMs);
                                        }
                                        // A resent card may be the one completing the triple.
                                        inputSentAt=DateTime.UtcNow;exactCountSince=DateTime.MinValue;earlyReads=0;firstGoneMs=-1;if(colorCrushed)lastClearAt=inputSentAt;due=inputSentAt.AddMilliseconds(verifyMs);verifyDeadline=inputSentAt.AddMilliseconds(VerificationWindow(false,verifyMs));return;
                                    }
                                    if(DateTime.UtcNow<verifyDeadline){due=DateTime.UtcNow;return;}
                                    groupFrame.Save(Path.Combine(folder,"verification_timeout.png"),ImageFormat.Png);before.Save(Path.Combine(folder,"verification_before_timeout.png"),ImageFormat.Png);
                                    throw new Exception("连点组未全部到位，停止；observed="+seen+" expected="+tray.Count);
                                }
                                var groupLog=new StringBuilder();
                                for(int bi=burstStart;bi<groupEnd;bi++){
                                    int bt=sequenceTypes[bi];Point bp=PointOf(positions[sequence[bi]]);
                                    if(!learned.ContainsKey(bt)&&!anchors.Any(a=>Convert.ToInt32(a["type"])==bt)&&FaceVisible(before,bp)){
                                        learned[bt]=before.Clone(new Rectangle(bp.X-17,bp.Y-18,34,36),PixelFormat.Format24bppRgb);learned[bt].Save(Path.Combine(templates,"type_"+bt+".png"),ImageFormat.Png);
                                        File.AppendAllText(Path.Combine(folder,"learned_types.jsonl"),Json.Serialize(new Dictionary<string,object>{{"type",bt},{"card_id",sequence[bi]},{"step",bi},{"source","full_speed_validated_map_confirmed_target"}})+Environment.NewLine);
                                    }
                                    groupLog.AppendLine(Json.Serialize(new Dictionary<string,object>{{"step",bi+1},{"card_id",sequence[bi]},{"type",bt},{"center",new int[]{bp.X,bp.Y}},{"tray_count",TrayTrace[bi+1].Length},{"time",DateTimeOffset.Now.ToString("o")},{"mode","background"},{"verification","burst_group"},{"verification_mode",VerificationMode},{"confirmation_ms",Math.Round((DateTime.UtcNow-firstInputAt).TotalMilliseconds,1)},{"burst_size",burstCount},{"input_retries",inputRetries},{"early_confirm",goneEarly&&!settled},{"first_gone_ms",Math.Round(firstGoneMs,1)},{"no_doubt",noDoubt},{"full_speed",true},{"observed_tray_count",seen>=0?(object)seen:null},{"logical_tray_count",TrayTrace[bi+1].Length}}));
                                    activeRemaining.Remove(sequence[bi]);
                                }
                                File.AppendAllText(Path.Combine(folder,"actions.jsonl"),groupLog.ToString(),Encoding.UTF8);Console.Write(groupLog.ToString());
                                if(unsavedVerified!=null){unsavedVerified.Dispose();unsavedVerified=null;}
                                if(DateTime.UtcNow<nextVerifiedSave)unsavedVerified=Vision.Normalize(groupFrame);
                                else{groupFrame.Save(Path.Combine(folder,"last_verified.png"),ImageFormat.Png);nextVerifiedSave=DateTime.UtcNow.AddMilliseconds(250);}
                                // The frame that closed this group is also the next group's baseline.
                                if(closingFrame!=null)closingFrame.Dispose();closingFrame=Vision.Normalize(groupFrame);
                                UnverifiedInput=false;PendingGroupCount=0;next=groupEnd;StepsSinceFullCheck+=burstCount;stage=0;targetDeadline=DateTime.MinValue;before.Dispose();before=null;
                                int groupGapMs=colorCrushed?clearGapMs:clickGapMs;clickNotBefore=inputSentAt.AddMilliseconds(groupGapMs);due=NextStepDue(inputSentAt,DateTime.UtcNow,groupGapMs);
                                if(stopRequested)throw new OperationCanceledException("已按 F9 停止；最后一次点击已确认");SaveStatus(folder,next,sequence.Length,tray,"running","");return;
                            }finally{groupFrame.Dispose();}
                        }
                        if(stage==0||stage==3) {
                            if(targetDeadline==DateTime.MinValue)targetDeadline=DateTime.UtcNow.AddSeconds(6);
                            if(next>=sequence.Length||next>=limit){reason=next==sequence.Length?(rollingMode&&!boardCleared?"本段已确认，继续计算下一段":"完整答案已执行；请核对游戏结算"):"达到本次步数上限";timer.Stop();overlay.Close();Application.ExitThread();return;}
                            bool fullDue=FullCheckDue(VerificationMode,StepsSinceFullCheck,FullCheckInterval);
                            if(stage==0&&fullDue) {
                                flushVerified();overlay.Hide();stage=4;fullDeadline=DateTime.UtcNow.AddSeconds(10);fullError="画面尚未稳定";if(fullFrame!=null){fullFrame.Dispose();fullFrame=null;}
                                SaveStatus(folder,next,sequence.Length,tray,"full_check","正在自动全盘核对：可见牌面、地图轮廓与牌槽");return;
                            }
                            int groupSize=stage==0&&FullSpeed&&Ultra&&markMs==0&&!energyMode&&!colorRuntime.Enabled?BurstSize(next,TrayTrace,limit):0;
                            if(groupSize<1&&closingFrame!=null){closingFrame.Dispose();closingFrame=null;}
                            if(groupSize>=1){
                                if(before!=null){before.Dispose();before=null;}
                                before=closingFrame??Native.CaptureNormalized(game);closingFrame=null;
                                double groupHoldMs=(clickNotBefore-DateTime.UtcNow).TotalMilliseconds;if(groupHoldMs>0)System.Threading.Thread.Sleep((int)Math.Ceiling(groupHoldMs));
                                burstStart=next;burstCount=groupSize;burstStacked=new bool[groupSize];groupReads=0;earlyReads=0;firstGoneMs=-1;exactCountSince=DateTime.MinValue;PendingGroupStart=next;PendingGroupCount=groupSize;
                                // Learn new faces from cards visible now; a lost click on a
                                // type with no template could not be identified afterwards.
                                for(int bi=0;bi<groupSize;bi++){int newType=sequenceTypes[next+bi];Point newPoint=PointOf(positions[sequence[next+bi]]);if(!learned.ContainsKey(newType)&&!anchors.Any(a=>Convert.ToInt32(a["type"])==newType)&&Exposed(sequence[next+bi],cards,activeRemaining)&&FaceVisible(before,newPoint))learned[newType]=before.Clone(new Rectangle(newPoint.X-17,newPoint.Y-18,34,36),PixelFormat.Format24bppRgb);}
                                for(int bi=0;bi<groupSize;bi++){string groupCard=sequence[next+bi];int groupType=sequenceTypes[next+bi];Point groupPoint=PointOf(positions[groupCard]);burstStacked[bi]=RevealsSameFace(groupCard,groupType,groupPoint,cards,activeRemaining,positions);}
                                Action<int> holdClear=delegate(int step){if(TrayTrace[step+1].Length>=TrayTrace[step].Length)return;double waitMs=(ClearNotBefore(lastClearAt,TrayTrace[step].Length,clearHoldMs)-DateTime.UtcNow).TotalMilliseconds;if(waitMs>0)System.Threading.Thread.Sleep((int)Math.Ceiling(waitMs));};
                                Action<int> markClear=delegate(int step){if(TrayTrace[step+1].Length<TrayTrace[step].Length)lastClearAt=DateTime.UtcNow;};
                                groupReadableAt=lastClearAt==DateTime.MinValue?DateTime.MinValue:lastClearAt.AddMilliseconds(TrayClearedMs);
                                holdClear(next);
                                if(!Native.ClickNormalized(game,PointOf(positions[sequence[next]]),bounds.Size)){due=DateTime.UtcNow.AddMilliseconds(180);return;}
                                UnverifiedInput=true;inputUltra=true;firstInputAt=DateTime.UtcNow;inputRetries=0;markClear(next);
                                for(int bi=1;bi<groupSize;bi++){
                                    // A card uncovered by this group needs the proven spacing
                                    // to come free; the others can follow at once.
                                    System.Threading.Thread.Sleep(Exposed(sequence[next+bi],cards,activeRemaining)?burstGapMs:clickGapMs);
                                    holdClear(next+bi);
                                    if(!Native.ClickNormalized(game,PointOf(positions[sequence[next+bi]]),bounds.Size))throw new Exception("窗口移动中，连点未发完");
                                    markClear(next+bi);}
                                inputSentAt=DateTime.UtcNow;int groupLast=next+groupSize-1;colorCrushed=TrayTrace[groupLast+1].Length<TrayTrace[groupLast].Length;tray=TrayTrace[groupLast+1].ToList();
                                SaveStatus(folder,next,sequence.Length,tray,"input_sent",sequence[next]);
                                stage=5;due=inputSentAt.AddMilliseconds(verifyMs);verifyDeadline=inputSentAt.AddMilliseconds(VerificationWindow(false,verifyMs));return;
                            }
                            target=PointOf(positions[sequence[next]]);int type=sequenceTypes[next];
                            prepareStartedAt=DateTime.UtcNow;
                            if(before!=null){before.Dispose();before=null;}
                            before=Native.CaptureNormalized(game);frameAt=DateTime.UtcNow;
                            {
                                var norm=before; // Read-only: the cached pixels must stay valid.
                                if(!Ultra&&TrayCount(norm)!=tray.Count)throw new Exception("实际牌槽数量与答案状态不一致");
                                if(!Exposed(sequence[next],cards,activeRemaining))throw new Exception("当前目标仍被遮挡，停止点击");
                                if(Ultra&&AnimationOccluded(reference,norm,target)) {
                                    if(!effectSampleSaved){norm.Save(Path.Combine(folder,"effect_obscured.png"),ImageFormat.Png);effectSampleSaved=true;}
                                    if(DateTime.UtcNow<targetDeadline){due=DateTime.UtcNow.AddMilliseconds(80);SaveStatus(folder,next,sequence.Length,tray,"waiting_effect","羊群特效遮挡目标；关闭游戏设置中的“消除效果”可避免等待");return;}
                                    throw new Exception("游戏消除特效持续遮挡目标；请关闭游戏设置中的“消除效果”后继续");
                                }
                                // Explicit full-speed execution trusts the independently
                                // replayed map after start/resume alignment. Confirm each
                                // sent click below; do not relearn every target beforehand.
                                if(!FullSpeed){
                                int recheckMs=Ultra?60:180;
                                bool energyTarget=energyMode&&type==Convert.ToInt32(((Dictionary<string,object>)solution["rules"])["gold_type"]);
                                bool face=FaceVisible(norm,target)||(energyTarget&&EnergyFaceVisible(norm,target));
                                bool known=anchors.Any(a=>Convert.ToInt32(a["type"])==type)||learned.ContainsKey(type);
                                double score=TargetScore(norm,target,type,reference,anchors);
                                if(learned.ContainsKey(type))for(int dy=-4;dy<=4;dy++)for(int dx=-2;dx<=2;dx++)score=Math.Min(score,Score(norm,new Point(target.X+dx,target.Y+dy),learned[type],new Point(17,18)));
                                bool needsSample=!known;
                                // Small scale/alignment changes can increase the absolute
                                // score. Resample only when this type clearly wins against
                                // every other known type, then require two stable frames.
                                if(dynamic&&known&&score>60&&score<=90&&face){
                                    double alternative=1000;
                                    foreach(int other in anchors.Select(a=>Convert.ToInt32(a["type"])).Concat(learned.Keys).Distinct().Where(t=>t!=type)){
                                        alternative=Math.Min(alternative,TargetScore(norm,target,other,reference,anchors));
                                        if(learned.ContainsKey(other))for(int dy=-4;dy<=4;dy++)for(int dx=-2;dx<=2;dx++)alternative=Math.Min(alternative,Score(norm,new Point(target.X+dx,target.Y+dy),learned[other],new Point(17,18)));
                                    }needsSample=alternative>=score+25;
                                }
                                // Charge expiry changes the battery tile background. Its
                                // yellow lightning and rectangular frame must remain present,
                                // then two stable frames are required before resampling.
                                if(dynamic&&energyTarget&&score>60&&EnergyFaceVisible(norm,target))needsSample=true;
                                bool resampled=false;
                                if(needsSample&&dynamic){
                                    if(!face){if(DateTime.UtcNow<targetDeadline){due=DateTime.UtcNow.AddMilliseconds(recheckMs);SaveStatus(folder,next,sequence.Length,tray,"waiting_target","等待临时提示或动画离开目标牌");return;}throw new Exception("目标牌面持续被遮挡，暂时停止");}
                                    if(stage!=3){learningFrame=new Bitmap(norm);learningDeadline=DateTime.UtcNow.AddSeconds(3);stage=3;due=DateTime.UtcNow.AddMilliseconds(recheckMs);SaveStatus(folder,next,sequence.Length,tray,"learning","正在学习新露出的牌型 "+type);return;}
                                    if(!(FaceVisible(learningFrame,target)||(energyTarget&&EnergyFaceVisible(learningFrame,target)))||Vision.PatchDifference(learningFrame,norm,new Rectangle(target.X-15,target.Y-16,30,32))>3){
                                        if(DateTime.UtcNow>learningDeadline)throw new Exception("新牌型图像一直在变化，停止点击");learningFrame.Dispose();learningFrame=new Bitmap(norm);due=DateTime.UtcNow.AddMilliseconds(recheckMs);return;
                                    }
                                    if(learned.ContainsKey(type))learned[type].Dispose();learned[type]=norm.Clone(new Rectangle(target.X-17,target.Y-18,34,36),PixelFormat.Format24bppRgb);learned[type].Save(Path.Combine(templates,"type_"+type+".png"),ImageFormat.Png);learningFrame.Dispose();learningFrame=null;
                                    File.AppendAllText(Path.Combine(folder,"learned_types.jsonl"),Json.Serialize(new Dictionary<string,object>{{"type",type},{"card_id",sequence[next]},{"step",next},{"source","verified_map_exposed_stable_frame"}})+Environment.NewLine);
                                    resampled=true;
                                }
                                if(resampled){score=TargetScore(norm,target,type,reference,anchors);
                                    if(learned.ContainsKey(type))for(int dy=-4;dy<=4;dy++)for(int dx=-2;dx<=2;dx++)score=Math.Min(score,Score(norm,new Point(target.X+dx,target.Y+dy),learned[type],new Point(17,18)));}
                                if(score>60){if(dynamic&&DateTime.UtcNow<targetDeadline){stage=0;due=DateTime.UtcNow.AddMilliseconds(recheckMs);SaveStatus(folder,next,sequence.Length,tray,"waiting_target","目标暂时被提示遮挡，等待画面恢复后继续核对");return;}throw new Exception("目标牌图案未通过核对："+sequence[next]+" score="+score.ToString("F1"));}
                                }
                                if(!Ultra||(DateTime.UtcNow-lastPreviewAt).TotalMilliseconds>=250){
                                using(var marked=Vision.Normalize(norm)){
                                using(var g=Graphics.FromImage(marked))using(var pen=new Pen(Color.Red,2)){g.DrawRectangle(pen,target.X-(int)Math.Round(17*FaceScaleX),target.Y-(int)Math.Round(18*FaceScaleY),(int)Math.Round(34*FaceScaleX),(int)Math.Round(36*FaceScaleY));g.DrawString((next+1)+" / "+sequence.Length,SystemFonts.DefaultFont,Brushes.Red,target.X-17,target.Y-31);}
                                marked.Save(Path.Combine(folder,"next_click.png"),ImageFormat.Png);}
                                lastPreviewAt=DateTime.UtcNow;}
                            }
                            if(markMs>0)overlay.Mark(new Rectangle(bounds.X+(int)Math.Round((target.X-17*FaceScaleX)*bounds.Width/336.0),bounds.Y+(int)Math.Round((target.Y-18*FaceScaleY)*bounds.Height/622.0),(int)Math.Round(34*FaceScaleX*bounds.Width/336.0),(int)Math.Round(36*FaceScaleY*bounds.Height/622.0)),(next+1)+" / "+sequence.Length+" · "+sequence[next]);
                            stage=1;due=DateTime.UtcNow.AddMilliseconds(markMs);
                            freshTargetFrame=CanReuseTargetFrame(Ultra,markMs,(DateTime.UtcNow-frameAt).TotalMilliseconds);
                            if(!freshTargetFrame){SaveStatus(folder,next,sequence.Length,tray,"marking",sequence[next]);return;}
                        }
                        if(stage==1) {
                            overlay.Hide();
                            // No marker delay in ultra mode: use the frame checked in
                            // this tick. Delayed/normal sends still recapture; coordinate
                            // changes are independently rejected by ClickNormalized.
                            if(!freshTargetFrame)using(var current=Native.Capture(game))if(Vision.PatchDifference(before,current,new Rectangle(target.X-13,target.Y-13,26,26))>8){if(dynamic&&DateTime.UtcNow<targetDeadline){stage=0;due=DateTime.UtcNow.AddMilliseconds(Ultra?60:180);SaveStatus(folder,next,sequence.Length,tray,"waiting_target","点击前出现动画，重新核对目标");return;}throw new Exception("点击前目标已变化");}
                            // Real routes clear from a full tray: a click inside the
                            // clear window can overfill it and lose the game. A fast
                            // capture must not pull the click ahead of the gap.
                            double holdMs=(clickNotBefore-DateTime.UtcNow).TotalMilliseconds;if(holdMs>0)System.Threading.Thread.Sleep((int)Math.Ceiling(holdMs));
                            if(!Native.ClickNormalized(game,target,bounds.Size)){stage=0;due=DateTime.UtcNow.AddMilliseconds(180);return;}
                            reusedTargetFrame=freshTargetFrame;prepareMs=(DateTime.UtcNow-prepareStartedAt).TotalMilliseconds;
                            UnverifiedInput=true;
                            inputUltra=Ultra;inputSentAt=firstInputAt=DateTime.UtcNow;inputRetries=0;
                            localRemaining=activeRemaining.Where(id=>id!=sequence[next]&&(!energyMode||!energySteps[next].AutoRemoved.Contains(id))).Select(id=>PointOf(positions[id])).ToArray();
                            // A different card underneath changes the picture when the
                            // target leaves, so only an identical one earns the exemption.
                            targetStacked=CoversExactly(target,activeRemaining.Where(id=>id!=sequence[next]&&Convert.ToInt32(cards[id]["type"])==sequenceTypes[next]).Select(id=>PointOf(positions[id])).ToArray());
                            colorCrushed=TrayTrace[next+1].Length<TrayTrace[next].Length;tray=TrayTrace[next+1].ToList();if(colorCrushed)lastClearAt=inputSentAt;
                            SaveStatus(folder,next,sequence.Length,tray,"input_sent",sequence[next]);
                            bool alienWait=energyMode&&energySteps[next].Alien;
                            stage=2;due=DateTime.UtcNow.AddMilliseconds(alienWait?Math.Max(3000,verifyMs):verifyMs);verifyDeadline=DateTime.UtcNow.AddMilliseconds(VerificationWindow(alienWait,verifyMs));
                            if(alienWait)SaveStatus(folder,next,sequence.Length,tray,"waiting_alien","电量已集满，等待飞碟消牌与棋盘稳定");return;
                        }
                        Bitmap verifyFrame=Native.CaptureNormalized(game);
                        try {
                            var norm=verifyFrame;var after=norm;
                            double targetDiff=Vision.PatchDifference(before,norm,new Rectangle(target.X-14,target.Y-14,28,28));
                            double trayDiff=Vision.PatchDifference(before,norm,new Rectangle(22,493,286,40));
                            // Last click may transition immediately to the next level or settlement.
                            bool alienVerify=energyMode&&energySteps[next].Alien;
                            bool finalVerify=next==sequence.Length-1&&boardCleared;
                            if(inputUltra&&!alienVerify&&!(next==sequence.Length-1&&boardCleared)&&AnimationOccluded(before,norm,target)) {
                                if(!effectSampleSaved){norm.Save(Path.Combine(folder,"effect_obscured.png"),ImageFormat.Png);effectSampleSaved=true;}
                                if(DateTime.UtcNow<verifyDeadline){due=DateTime.UtcNow.AddMilliseconds(50);SaveStatus(folder,next,sequence.Length,tray,"waiting_effect","羊群特效挡住了点击结果；等待特效离开，建议关闭游戏内“消除效果”");return;}
                                throw new Exception("游戏消除特效遮挡点击结果，尚未确认；请关闭游戏内“消除效果”后继续核对");
                            }
                            bool targetIntact=inputUltra&&!alienVerify&&MatchScore(norm,target,sequenceTypes[next],reference,anchors,learned)<=60;
                            // Ambiguous identical/overlapping faces get a tray check NOW,
                            // in parallel with local evidence, never after a fixed 900 ms.
                            int observed=-1;try{observed=TrayCount(norm);}catch(Exception){if(!adaptive&&!inputUltra)throw;}
                            bool localOnly=inputUltra&&!alienVerify&&!finalVerify&&ConfirmedRemoved(TargetDisappeared(before,norm,target,localRemaining),targetIntact,targetStacked)&&TrayMoved(observed,TrayTrace[next].Length,tray.Count,(DateTime.UtcNow-inputSentAt).TotalMilliseconds);
                            bool matched=VerificationEvidence(localOnly,next==sequence.Length-1&&boardCleared,observed,tray.Count,targetDiff,trayDiff)&&(localOnly||TrayEvidenceAllowed(inputUltra&&!alienVerify,finalVerify,targetStacked,targetDiff,targetIntact))&&!(finalVerify&&LastCardStayed(targetIntact,targetStacked,observed,tray.Count,TrayTrace[next].Length));
                            bool stable=!NeedsStableFrame(localOnly,inputUltra,adaptive,alienVerify||finalVerify)||(settledFrame!=null&&Vision.PatchDifference(settledFrame,after,alienVerify?new Rectangle(10,120,316,415):new Rectangle(22,493,286,40))<3);
                            if(!matched||!stable){
                                if(settledFrame!=null){settledFrame.Dispose();settledFrame=null;}
                                if(matched)settledFrame=new Bitmap(after);
                                if(inputUltra&&!alienVerify&&inputRetries<MaxInputRetries&&ClickLost((DateTime.UtcNow-inputSentAt).TotalMilliseconds,retryMs,targetDiff,observed,TrayTrace[next].Length,targetStacked)) {
                                    if(!Native.ClickNormalized(game,target,bounds.Size)){due=DateTime.UtcNow.AddMilliseconds(20);return;}
                                    inputRetries++;inputSentAt=DateTime.UtcNow;due=inputSentAt.AddMilliseconds(verifyMs);verifyDeadline=inputSentAt.AddMilliseconds(VerificationWindow(false,verifyMs));
                                    File.AppendAllText(Path.Combine(folder,"input_recovery.jsonl"),Json.Serialize(new Dictionary<string,object>{{"time",DateTimeOffset.Now.ToString("o")},{"step",next+1},{"card_id",sequence[next]},{"result","resent_in_process"},{"attempt",inputRetries},{"target_delta",Math.Round(targetDiff,2)},{"observed_tray",observed}})+Environment.NewLine,Encoding.UTF8);
                                    return;
                                }
                                if((adaptive||alienVerify||inputUltra)&&DateTime.UtcNow<verifyDeadline){due=inputUltra?DateTime.UtcNow:DateTime.UtcNow.AddMilliseconds(80);return;}
                                norm.Save(Path.Combine(folder,"verification_timeout.png"),ImageFormat.Png);before.Save(Path.Combine(folder,"verification_before_timeout.png"),ImageFormat.Png);
                                throw new Exception("点击后牌面/牌槽未稳定或与答案不一致，停止；observed="+observed+" expected="+tray.Count);
                            }
                            if(settledFrame!=null){settledFrame.Dispose();settledFrame=null;}
                            if(FullSpeed&&!learned.ContainsKey(sequenceTypes[next])&&!anchors.Any(a=>Convert.ToInt32(a["type"])==sequenceTypes[next])&&FaceVisible(before,target)){
                                int type=sequenceTypes[next];learned[type]=before.Clone(new Rectangle(target.X-17,target.Y-18,34,36),PixelFormat.Format24bppRgb);
                                learned[type].Save(Path.Combine(templates,"type_"+type+".png"),ImageFormat.Png);
                                File.AppendAllText(Path.Combine(folder,"learned_types.jsonl"),Json.Serialize(new Dictionary<string,object>{{"type",type},{"card_id",sequence[next]},{"step",next},{"source","full_speed_validated_map_confirmed_target"}})+Environment.NewLine);
                            }
                            // PNG encoding is a large share of a fast step. Keep the
                            // newest confirmed frame in memory; it is written before
                            // any exit, full check or final status.
                            if(unsavedVerified!=null){unsavedVerified.Dispose();unsavedVerified=null;}
                            if(inputUltra&&DateTime.UtcNow<nextVerifiedSave)unsavedVerified=Vision.Normalize(norm);
                            else{norm.Save(Path.Combine(folder,"last_verified.png"),ImageFormat.Png);nextVerifiedSave=DateTime.UtcNow.AddMilliseconds(250);}
                            var action=new Dictionary<string,object>{{"step",next+1},{"card_id",sequence[next]},{"type",sequenceTypes[next]},{"center",new int[]{target.X,target.Y}},{"tray_count",tray.Count},{"target_difference",Math.Round(targetDiff,2)},{"tray_difference",Math.Round(trayDiff,2)},{"time",DateTimeOffset.Now.ToString("o")},{"mode","background"}};
                            action["verification"]=localOnly?"target_disappeared":inputUltra&&!alienVerify&&!finalVerify?"target_and_tray_without_stability":"stable_tray_and_target";action["verification_mode"]=VerificationMode;
                            action["confirmation_ms"]=Math.Round((DateTime.UtcNow-firstInputAt).TotalMilliseconds,1);
                            action["prepare_ms"]=Math.Round(prepareMs,1);action["reused_target_frame"]=reusedTargetFrame;
                            action["input_retries"]=inputRetries;
                            action["full_speed"]=FullSpeed;
                            action["observed_tray_count"]=observed>=0?(object)observed:null;action["logical_tray_count"]=TrayTrace[next+1].Length;
                            if(energyMode){action["energy_count"]=energySteps[next].Collected;action["alien"]=energySteps[next].Alien;action["auto_removed"]=energySteps[next].AutoRemoved;}
                            File.AppendAllText(Path.Combine(folder,"actions.jsonl"),Json.Serialize(action)+Environment.NewLine,Encoding.UTF8);Console.WriteLine(Json.Serialize(action));
                            UnverifiedInput=false;
                        }finally{if(verifyFrame!=null)verifyFrame.Dispose();}
                        before.Dispose();before=null;activeRemaining.Remove(sequence[next]);if(energyMode)foreach(string auto in energySteps[next].AutoRemoved)activeRemaining.Remove(auto);next++;StepsSinceFullCheck++;stage=0;effectSampleSaved=false;targetDeadline=DateTime.MinValue;int stepGapMs=colorCrushed?clearGapMs:clickGapMs;clickNotBefore=Ultra?inputSentAt.AddMilliseconds(stepGapMs):DateTime.MinValue;due=Ultra?NextStepDue(inputSentAt,DateTime.UtcNow,stepGapMs):DateTime.UtcNow.AddMilliseconds(colorRuntime.Enabled&&colorCrushed?1100:40);
                        if(stopRequested)throw new OperationCanceledException("已按 F9 停止；最后一次点击已确认");SaveStatus(folder,next,sequence.Length,tray,"running","");
                    }catch(Exception e){reason=e.Message;result=1;timer.Stop();overlay.Close();Application.ExitThread();}
                };
                // WM_TIMER is quantized to about 31 ms and costs that on every
                // hop between stages. In ultra modes the loop reposts itself
                // through the same message queue, so input and exit are unchanged.
                bool pumping=false;Action pump=null;DateTime pumpedAt=DateTime.UtcNow;
                IntPtr pumpHandle=overlay.Handle; // BeginInvoke requires a created handle.
                pump=delegate {
                    if(!timer.Enabled||!Ultra){pumping=false;return;}
                    pumpedAt=DateTime.UtcNow;
                    tick(null,EventArgs.Empty);
                    if(!timer.Enabled){pumping=false;return;}
                    System.Threading.Thread.Sleep(paused?20:1);
                    overlay.BeginInvoke(pump);
                };
                // The timer stays as the fallback: it restarts a pump whose queued
                // call was lost, for example to a recreated window handle.
                timer.Tick+=delegate(object timerSender,EventArgs timerArgs){if(!Ultra){tick(timerSender,timerArgs);return;}if(!pumping||(DateTime.UtcNow-pumpedAt).TotalMilliseconds>500){pumping=true;pumpedAt=DateTime.UtcNow;overlay.BeginInvoke(pump);}};
                Native.timeBeginPeriod(1);
                SaveStatus(folder,next,sequence.Length,tray,"running","");timer.Start();Application.Run();
                flushVerified();
                if(before!=null)before.Dispose();if(fullFrame!=null)fullFrame.Dispose();if(settledFrame!=null)settledFrame.Dispose();if(learningFrame!=null)learningFrame.Dispose();foreach(var im in learned.Values)im.Dispose();SaveStatus(folder,next,sequence.Length,tray,result==0?(next==sequence.Length?(rollingMode&&!boardCleared?"segment_finished":"sequence_finished"):"paused_at_limit"):"stopped",reason);FlushStatus();Console.WriteLine(reason);return result;
            }
        }catch(Exception e){Console.Error.WriteLine(e.Message);return 1;}
    }
}
