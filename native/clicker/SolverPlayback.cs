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
    static int StepsSinceFullCheck,LastFullCheckStep,FullCheckInterval=30;
    static int[][] TrayTrace;
    internal static int[] PushTray(int[] before,int type){var result=before.ToList();result.Add(type);if(result.Count(t=>t==type)==3)result.RemoveAll(t=>t==type);return result.ToArray();}
    static bool Ultra {get{return VerificationMode=="ultra"||VerificationMode=="safe_ultra";}}
    static string Mode(object value){string mode=Convert.ToString(value);return mode=="ultra"||mode=="safe_ultra"?mode:"normal";}
    internal static bool FullCheckDue(string mode,int steps,int interval){return mode=="safe_ultra"&&steps>=Math.Max(1,Math.Min(1000,interval));}
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
        double sum=0;int n=0;
        for(int y=-11;y<=11;y+=2)for(int x=-11;x<=11;x+=2){Color a=Vision.At(im,p.X+x,p.Y+y),b=Vision.At(reference,q.X+x,q.Y+y);sum+=Math.Abs(a.R-b.R)+Math.Abs(a.G-b.G)+Math.Abs(a.B-b.B);n++;}return sum/n;
    }
    static double TargetScore(Bitmap im,Point p,int type,Bitmap reference,List<Dictionary<string,object>> anchors) {
        double best=1000;
        foreach(var a in anchors.Where(a=>Convert.ToInt32(a["type"])==type)) {
            Point q=PointOf(a["center"]);
            for(int dy=-4;dy<=4;dy++)for(int dx=-2;dx<=2;dx++)best=Math.Min(best,Score(im,new Point(p.X+dx,p.Y+dy),reference,q));
        }return best;
    }
    static void SaveStatus(string folder,int next,int total,List<int> tray,string state,string message) {
        var payload=new Dictionary<string,object>{{"pid",System.Diagnostics.Process.GetCurrentProcess().Id},{"input_pending",UnverifiedInput},{"next_step",next},{"total",total},{"expected_tray",tray.ToArray()},{"state",state},{"message",message},{"time",DateTimeOffset.Now.ToString("o")}};
        payload["verification_mode"]=VerificationMode;payload["steps_since_full_check"]=StepsSinceFullCheck;payload["last_full_check_step"]=LastFullCheckStep;payload["full_check_interval"]=FullCheckInterval;
        if(TrayTrace!=null&&next<TrayTrace.Length){payload["confirmed_tray"]=TrayTrace[next];payload["confirmed_tray_count"]=TrayTrace[next].Length;payload["predicted_tray_count"]=tray.Count;}
        if(EnergyTrace!=null){payload["rules"]="energy_alien_v1";payload["energy_count"]=next>0?EnergyTrace[next-1].Collected:0;payload["collect_count"]=EnergyThreshold;payload["alien_activations"]=EnergyTrace.Take(next).Count(e=>e.Alien);payload["alien_pending"]=UnverifiedInput&&next<EnergyTrace.Length&&EnergyTrace[next].Alien;}
        if(RollingExecution){payload["execution_kind"]="rolling_segment_v1";payload["board_total"]=BoardTotal;payload["remaining_cards"]=BoardTotal-next-(EnergyTrace==null?0:EnergyTrace.Take(next).Sum(e=>e.AutoRemoved.Length));payload["segment_end_step"]=total;}
        string path=Path.Combine(folder,"playback_status.json"),temp=path+".tmp";File.WriteAllText(temp,Json.Serialize(payload),Encoding.UTF8);for(int attempt=0;attempt<8;attempt++)try{if(File.Exists(path))File.Replace(temp,path,null);else File.Move(temp,path);return;}catch(IOException){System.Threading.Thread.Sleep(15);}
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
            int verifyMs=plan.ContainsKey("verify_milliseconds")?Math.Max(Ultra?20:200,Math.Min(5000,Convert.ToInt32(plan["verify_milliseconds"]))):1000;
            bool adaptive=plan.ContainsKey("adaptive_verify")&&Convert.ToBoolean(plan["adaptive_verify"]);DateTime verifyDeadline=DateTime.MinValue,targetDeadline=DateTime.MinValue;Bitmap settledFrame=null;
            string lastCommand="";
            var tray=new List<int>();bool colorCrushed=false;int next=plan.ContainsKey("start_step")?Convert.ToInt32(plan["start_step"]):0,result=0,stage=0;bool stopRequested=false;bool paused=false,oldPause=false;string reason="";Bitmap before=null;Point target=Point.Empty;
            if(next<0||next>=sequence.Length)throw new Exception("恢复步数无效");
            tray=TrayTrace[next].ToList();
            var activeRemaining=new HashSet<string>(cards.Keys);for(int i=0;i<next;i++){activeRemaining.Remove(sequence[i]);if(energyMode)foreach(string auto in energySteps[i].AutoRemoved)activeRemaining.Remove(auto);}
            LastFullCheckStep=next;StepsSinceFullCheck=0;
            bool inputUltra=false,effectSampleSaved=false;DateTime inputSentAt=DateTime.MinValue,fullDeadline=DateTime.MinValue,settleDeadline=DateTime.MinValue;Point[] localRemaining=null;
            Task<string> fullTask=null;Bitmap fullFrame=null;string fullError="";
            bool dynamic=plan.ContainsKey("dynamic_anchors")&&Convert.ToBoolean(plan["dynamic_anchors"]);Bitmap learningFrame=null;DateTime learningDeadline=DateTime.MinValue;
            using(var original=new Bitmap((string)plan["reference_path"]))using(var reference=Vision.Normalize(original))using(var overlay=new Overlay())using(var timer=new Timer()) {
                using(var raw=Native.Capture(game))using(var now=Vision.Normalize(raw)) {
                    if(TrayCount(now)!=tray.Count)throw new Exception("实际牌槽与计划起点不一致");
                    using(var initialRaw=new Bitmap(plan.ContainsKey("initial_reference_path")?(string)plan["initial_reference_path"]:(string)plan["reference_path"]))using(var initial=Vision.Normalize(initialRaw))
                        if(Vision.PatchDifference(initial,now,new Rectangle(25,118,286,347))>5)throw new Exception("棋盘与坐标校准截图不一致");
                }
                DateTime due=DateTime.UtcNow;
                timer.Interval=Ultra?20:50;
                timer.Tick+=delegate {
                    try {
                        string controlPath=Path.Combine(folder,"control.json");
                        if(File.Exists(controlPath)) {
                            try {
                                var command=Read(controlPath);string id=Convert.ToString(command["id"]);
                                if(id!=lastCommand){lastCommand=id;string action=Convert.ToString(command["action"]);
                                    if(action=="stop"){if(!UnverifiedInput)throw new OperationCanceledException("用户停止点击");stopRequested=true;}
                                    if(action=="speed"){
                                        string mode=Mode(command.ContainsKey("verification_mode")?command["verification_mode"]:VerificationMode);
                                        VerificationMode=mode;
                                        if(command.ContainsKey("full_check_interval"))FullCheckInterval=Math.Max(1,Math.Min(1000,Convert.ToInt32(command["full_check_interval"])));
                                        markMs=Math.Max(Ultra?0:60,Math.Min(5000,Convert.ToInt32(command["mark_milliseconds"])));verifyMs=Math.Max(Ultra?20:200,Math.Min(5000,Convert.ToInt32(command["verify_milliseconds"])));timer.Interval=Ultra?20:50;
                                    }
                                    if(action=="pause"||action=="resume"){paused=action=="pause";if(!paused&&stage==2)verifyDeadline=DateTime.UtcNow.AddMilliseconds(VerificationWindow(energyMode&&energySteps[next].Alien,verifyMs));overlay.Hide();SaveStatus(folder,next,sequence.Length,tray,paused?"paused":"running",paused?"已暂停":"继续执行");}
                                }
                            }catch(IOException){}catch(ArgumentException){}
                        }
                        if((Native.GetAsyncKeyState(0x78)&0x8000)!=0){if(!UnverifiedInput)throw new OperationCanceledException("已按 F9 停止");stopRequested=true;overlay.Hide();}
                        bool key=(Native.GetAsyncKeyState(0x77)&0x8000)!=0;
                        if(key&&!oldPause){paused=!paused;if(!paused&&stage==2)verifyDeadline=DateTime.UtcNow.AddMilliseconds(VerificationWindow(energyMode&&energySteps[next].Alien,verifyMs));SaveStatus(folder,next,sequence.Length,tray,paused?"paused":"running","F8");overlay.Hide();}oldPause=key;
                        if(paused||DateTime.UtcNow<due)return;
                        CheckSession(plan);
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
                        if(stage==0||stage==3) {
                            if(targetDeadline==DateTime.MinValue)targetDeadline=DateTime.UtcNow.AddSeconds(6);
                            if(next>=sequence.Length||next>=limit){reason=next==sequence.Length?(rollingMode&&!boardCleared?"本段已确认，继续计算下一段":"完整答案已执行；请核对游戏结算"):"达到本次步数上限";timer.Stop();overlay.Close();Application.ExitThread();return;}
                            bool fullDue=FullCheckDue(VerificationMode,StepsSinceFullCheck,FullCheckInterval);
                            if(stage==0&&fullDue) {
                                overlay.Hide();stage=4;fullDeadline=DateTime.UtcNow.AddSeconds(10);fullError="画面尚未稳定";if(fullFrame!=null){fullFrame.Dispose();fullFrame=null;}
                                SaveStatus(folder,next,sequence.Length,tray,"full_check","正在自动全盘核对：可见牌面、地图轮廓与牌槽");return;
                            }
                            target=PointOf(positions[sequence[next]]);int type=sequenceTypes[next];
                            if(before!=null){before.Dispose();before=null;}using(var raw=Native.Capture(game))before=Vision.Normalize(raw);
                            using(var norm=Vision.Normalize(before)) {
                                if(!Ultra&&TrayCount(norm)!=tray.Count)throw new Exception("实际牌槽数量与答案状态不一致");
                                if(!Exposed(sequence[next],cards,activeRemaining))throw new Exception("当前目标仍被遮挡，停止点击");
                                // The game drops clicks sent while a triple is still
                                // clearing (step 144 of 20261103T085326Z). After a
                                // clearing step wait until the drawn tray shows the
                                // logical count; on timeout continue unchanged.
                                if(Ultra&&next>0&&TrayTrace[next].Length<TrayTrace[next-1].Length) {
                                    if(settleDeadline==DateTime.MinValue)settleDeadline=DateTime.UtcNow.AddMilliseconds(1500);
                                    int seen=-1;try{seen=TrayCount(norm);}catch(Exception){}
                                    if(seen!=tray.Count&&DateTime.UtcNow<settleDeadline){due=DateTime.UtcNow.AddMilliseconds(30);return;}
                                }
                                if(Ultra&&AnimationOccluded(reference,norm,target)) {
                                    if(!effectSampleSaved){norm.Save(Path.Combine(folder,"effect_obscured.png"),ImageFormat.Png);effectSampleSaved=true;}
                                    if(DateTime.UtcNow<targetDeadline){due=DateTime.UtcNow.AddMilliseconds(80);SaveStatus(folder,next,sequence.Length,tray,"waiting_effect","羊群特效遮挡目标；关闭游戏设置中的“消除效果”可避免等待");return;}
                                    throw new Exception("游戏消除特效持续遮挡目标；请关闭游戏设置中的“消除效果”后继续");
                                }
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
                                if(needsSample&&dynamic){
                                    if(!face){if(DateTime.UtcNow<targetDeadline){due=DateTime.UtcNow.AddMilliseconds(180);SaveStatus(folder,next,sequence.Length,tray,"waiting_target","等待临时提示或动画离开目标牌");return;}throw new Exception("目标牌面持续被遮挡，暂时停止");}
                                    if(stage!=3){learningFrame=new Bitmap(norm);learningDeadline=DateTime.UtcNow.AddSeconds(3);stage=3;due=DateTime.UtcNow.AddMilliseconds(180);SaveStatus(folder,next,sequence.Length,tray,"learning","正在学习新露出的牌型 "+type);return;}
                                    if(!(FaceVisible(learningFrame,target)||(energyTarget&&EnergyFaceVisible(learningFrame,target)))||Vision.PatchDifference(learningFrame,norm,new Rectangle(target.X-15,target.Y-16,30,32))>3){
                                        if(DateTime.UtcNow>learningDeadline)throw new Exception("新牌型图像一直在变化，停止点击");learningFrame.Dispose();learningFrame=new Bitmap(norm);due=DateTime.UtcNow.AddMilliseconds(180);return;
                                    }
                                    if(learned.ContainsKey(type))learned[type].Dispose();learned[type]=norm.Clone(new Rectangle(target.X-17,target.Y-18,34,36),PixelFormat.Format24bppRgb);learned[type].Save(Path.Combine(templates,"type_"+type+".png"),ImageFormat.Png);learningFrame.Dispose();learningFrame=null;
                                    File.AppendAllText(Path.Combine(folder,"learned_types.jsonl"),Json.Serialize(new Dictionary<string,object>{{"type",type},{"card_id",sequence[next]},{"step",next},{"source","verified_map_exposed_stable_frame"}})+Environment.NewLine);
                                }
                                score=TargetScore(norm,target,type,reference,anchors);
                                if(learned.ContainsKey(type))for(int dy=-4;dy<=4;dy++)for(int dx=-2;dx<=2;dx++)score=Math.Min(score,Score(norm,new Point(target.X+dx,target.Y+dy),learned[type],new Point(17,18)));
                                if(score>60){if(dynamic&&DateTime.UtcNow<targetDeadline){stage=0;due=DateTime.UtcNow.AddMilliseconds(180);SaveStatus(folder,next,sequence.Length,tray,"waiting_target","目标暂时被提示遮挡，等待画面恢复后继续核对");return;}throw new Exception("目标牌图案未通过核对："+sequence[next]+" score="+score.ToString("F1"));}
                                using(var g=Graphics.FromImage(norm))using(var pen=new Pen(Color.Red,2)){g.DrawRectangle(pen,target.X-(int)Math.Round(17*FaceScaleX),target.Y-(int)Math.Round(18*FaceScaleY),(int)Math.Round(34*FaceScaleX),(int)Math.Round(36*FaceScaleY));g.DrawString((next+1)+" / "+sequence.Length,SystemFonts.DefaultFont,Brushes.Red,target.X-17,target.Y-31);}
                                norm.Save(Path.Combine(folder,"next_click.png"),ImageFormat.Png);
                            }
                            overlay.Mark(new Rectangle(bounds.X+(int)Math.Round((target.X-17*FaceScaleX)*bounds.Width/336.0),bounds.Y+(int)Math.Round((target.Y-18*FaceScaleY)*bounds.Height/622.0),(int)Math.Round(34*FaceScaleX*bounds.Width/336.0),(int)Math.Round(36*FaceScaleY*bounds.Height/622.0)),(next+1)+" / "+sequence.Length+" · "+sequence[next]);
                            stage=1;due=DateTime.UtcNow.AddMilliseconds(markMs);SaveStatus(folder,next,sequence.Length,tray,"marking",sequence[next]);return;
                        }
                        if(stage==1) {
                            overlay.Hide();using(var current=Native.Capture(game))if(Vision.PatchDifference(before,current,new Rectangle(target.X-13,target.Y-13,26,26))>8){if(dynamic&&DateTime.UtcNow<targetDeadline){stage=0;due=DateTime.UtcNow.AddMilliseconds(180);SaveStatus(folder,next,sequence.Length,tray,"waiting_target","点击前出现动画，重新核对目标");return;}throw new Exception("点击前目标已变化");}
                            if(!Native.ClickNormalized(game,target,bounds.Size)){stage=0;due=DateTime.UtcNow.AddMilliseconds(180);return;}
                            UnverifiedInput=true;
                            inputUltra=Ultra;inputSentAt=DateTime.UtcNow;
                            localRemaining=activeRemaining.Where(id=>id!=sequence[next]&&(!energyMode||!energySteps[next].AutoRemoved.Contains(id))).Select(id=>PointOf(positions[id])).ToArray();
                            colorCrushed=TrayTrace[next+1].Length<TrayTrace[next].Length;tray=TrayTrace[next+1].ToList();
                            SaveStatus(folder,next,sequence.Length,tray,"input_sent",sequence[next]);
                            bool alienWait=energyMode&&energySteps[next].Alien;
                            stage=2;due=DateTime.UtcNow.AddMilliseconds(alienWait?Math.Max(3000,verifyMs):verifyMs);verifyDeadline=DateTime.UtcNow.AddMilliseconds(VerificationWindow(alienWait,verifyMs));
                            if(alienWait)SaveStatus(folder,next,sequence.Length,tray,"waiting_alien","电量已集满，等待飞碟消牌与棋盘稳定");return;
                        }
                        using(var raw=Native.Capture(game))using(var norm=Vision.Normalize(raw)) {
                            var after=norm;
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
                            bool localOnly=inputUltra&&!alienVerify&&!(next==sequence.Length-1&&boardCleared)&&TargetDisappeared(before,norm,target,localRemaining);
                            // Ambiguous identical/overlapping faces get a tray check NOW,
                            // in parallel with local evidence, never after a fixed 900 ms.
                            int observed=-1;if(!localOnly)try{observed=TrayCount(norm);}catch(Exception){if(!adaptive&&!inputUltra)throw;}
                            bool matched=VerificationEvidence(localOnly,next==sequence.Length-1&&boardCleared,observed,tray.Count,targetDiff,trayDiff);
                            bool stable=!NeedsStableFrame(localOnly,inputUltra,adaptive,alienVerify||finalVerify)||(settledFrame!=null&&Vision.PatchDifference(settledFrame,after,alienVerify?new Rectangle(10,120,316,415):new Rectangle(22,493,286,40))<3);
                            if(!matched||!stable){
                                if(settledFrame!=null){settledFrame.Dispose();settledFrame=null;}
                                if(matched)settledFrame=new Bitmap(after);
                                if((adaptive||alienVerify||inputUltra)&&DateTime.UtcNow<verifyDeadline){due=DateTime.UtcNow.AddMilliseconds(inputUltra?20:80);return;}
                                norm.Save(Path.Combine(folder,"verification_timeout.png"),ImageFormat.Png);before.Save(Path.Combine(folder,"verification_before_timeout.png"),ImageFormat.Png);
                                throw new Exception("点击后牌面/牌槽未稳定或与答案不一致，停止；observed="+observed+" expected="+tray.Count);
                            }
                            if(settledFrame!=null){settledFrame.Dispose();settledFrame=null;}
                            norm.Save(Path.Combine(folder,"last_verified.png"),ImageFormat.Png);
                            var action=new Dictionary<string,object>{{"step",next+1},{"card_id",sequence[next]},{"type",sequenceTypes[next]},{"center",new int[]{target.X,target.Y}},{"tray_count",tray.Count},{"target_difference",Math.Round(targetDiff,2)},{"tray_difference",Math.Round(trayDiff,2)},{"time",DateTimeOffset.Now.ToString("o")},{"mode","background"}};
                            action["verification"]=localOnly?"target_disappeared":inputUltra&&!alienVerify&&!finalVerify?"target_and_tray_without_stability":"stable_tray_and_target";action["verification_mode"]=VerificationMode;
                            action["confirmation_ms"]=Math.Round((DateTime.UtcNow-inputSentAt).TotalMilliseconds,1);
                            action["observed_tray_count"]=observed>=0?(object)observed:null;action["logical_tray_count"]=TrayTrace[next+1].Length;
                            if(energyMode){action["energy_count"]=energySteps[next].Collected;action["alien"]=energySteps[next].Alien;action["auto_removed"]=energySteps[next].AutoRemoved;}
                            File.AppendAllText(Path.Combine(folder,"actions.jsonl"),Json.Serialize(action)+Environment.NewLine,Encoding.UTF8);Console.WriteLine(Json.Serialize(action));
                            UnverifiedInput=false;
                        }
                        before.Dispose();before=null;activeRemaining.Remove(sequence[next]);if(energyMode)foreach(string auto in energySteps[next].AutoRemoved)activeRemaining.Remove(auto);next++;StepsSinceFullCheck++;stage=0;effectSampleSaved=false;targetDeadline=DateTime.MinValue;settleDeadline=DateTime.MinValue;due=DateTime.UtcNow.AddMilliseconds(Ultra?0:colorRuntime.Enabled&&colorCrushed?1100:40);
                        if(stopRequested)throw new OperationCanceledException("已按 F9 停止；最后一次点击已确认");SaveStatus(folder,next,sequence.Length,tray,"running","");
                    }catch(Exception e){reason=e.Message;result=1;timer.Stop();overlay.Close();Application.ExitThread();}
                };
                SaveStatus(folder,next,sequence.Length,tray,"running","");timer.Start();Application.Run();
                if(before!=null)before.Dispose();if(fullFrame!=null)fullFrame.Dispose();if(settledFrame!=null)settledFrame.Dispose();if(learningFrame!=null)learningFrame.Dispose();foreach(var im in learned.Values)im.Dispose();SaveStatus(folder,next,sequence.Length,tray,result==0?(next==sequence.Length?(rollingMode&&!boardCleared?"segment_finished":"sequence_finished"):"paused_at_limit"):"stopped",reason);Console.WriteLine(reason);return result;
            }
        }catch(Exception e){Console.Error.WriteLine(e.Message);return 1;}
    }
}
