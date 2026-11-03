using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Collections.Generic;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

// One explicit repair retry of an already failed daily game. No input is
// sent outside the visible loss banner and red retry-button layout.
static class RetryDaily {
    static JavaScriptSerializer json=new JavaScriptSerializer();
    static Dictionary<string,object> Read(string p){return json.Deserialize<Dictionary<string,object>>(File.ReadAllText(p,Encoding.UTF8));}
    static bool Loss(Bitmap im){
        int banner=0,button=0;
        for(int y=100;y<153;y++)for(int x=77;x<261;x++){Color c=im.GetPixel(x,y);if(c.R>210&&c.G<65&&c.B<65)banner++;}
        for(int y=502;y<530;y++)for(int x=110;x<227;x++){Color c=im.GetPixel(x,y);if(c.R>210&&c.G<65&&c.B<65)button++;}
        return banner>500&&button>1800;
    }
    [STAThread] static int Main(string[] args){
        Native.SetProcessDPIAware();Application.EnableVisualStyles();
        try{
            if(args.Length!=2)throw new Exception("Usage: RetryDaily.exe capture_status expected_session");
            var capture=Read(args[0]);
            if(Convert.ToString(capture["session"])!=args[1]||Convert.ToString(capture["source_path"])!="/sheep/v1/game/map_info_ex")throw new Exception("Daily session changed");
            var windows=Native.FindGame();if(windows.Count!=1)throw new Exception("No unique game window");
            IntPtr game=windows[0],foreground=Native.GetForegroundWindow();Rectangle bounds=Native.Bounds(game);Point cursor=Cursor.Position;
            string root=AppDomain.CurrentDomain.BaseDirectory;
            using(var before=Native.Capture(game))using(var normalized=Vision.Normalize(before))using(var overlay=new Overlay()){
                if(!Loss(normalized))throw new Exception("Daily failure banner/retry button missing");
                normalized.Save(Path.Combine(root,"output","daily_retry_before.png"),ImageFormat.Png);
                overlay.Mark(new Rectangle(bounds.X+(int)(103*bounds.Width/336.0),bounds.Y+(int)(496*bounds.Height/622.0),(int)(132*bounds.Width/336.0),(int)(41*bounds.Height/622.0)),"重新挑战 · 完整路线修复验证");
                Application.DoEvents();Thread.Sleep(650);overlay.Hide();
                using(var frame=Native.Capture(game))using(var current=Vision.Normalize(frame)){
                    if(!Loss(current)||Vision.PatchDifference(normalized,current,new Rectangle(100,496,137,42))>5)throw new Exception("Retry screen changed");
                }
                capture=Read(args[0]);if(Convert.ToString(capture["session"])!=args[1]||bounds!=Native.Bounds(game))throw new Exception("Session/window changed before retry");
                if((Native.GetAsyncKeyState(0x1B)&0x8000)!=0||(Native.GetAsyncKeyState(0x78)&0x8000)!=0)throw new Exception("Retry cancelled");
                Native.Click(game,new Point(bounds.X+(int)Math.Round(168*bounds.Width/336.0),bounds.Y+(int)Math.Round(516*bounds.Height/622.0)),true);
                File.WriteAllText(Path.Combine(root,"output","daily_retry_action.json"),json.Serialize(new {session=args[1],center=new int[]{168,516},mode="background",input_sent=true,
                    foreground_unchanged=Native.GetForegroundWindow()==foreground,cursor_unchanged=Cursor.Position==cursor,time=DateTimeOffset.Now.ToString("o")}),new UTF8Encoding(false));
            }
            return 0;
        }catch(Exception e){Console.Error.WriteLine(e.Message);return 1;}
    }
}
