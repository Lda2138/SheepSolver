using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Collections.Generic;
using System.Text;
using System.Web.Script.Serialization;
using System.Threading;
using System.Windows.Forms;

// One specific completion-animation button, with screenshot consistency check.
static class CompletionCheck {
    static Dictionary<string,object> Read(string path){using(var stream=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.ReadWrite|FileShare.Delete))using(var reader=new StreamReader(stream,Encoding.UTF8))return new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(reader.ReadToEnd());}
    [STAThread] static int Main(string[] args) {
        Native.SetProcessDPIAware();Application.EnableVisualStyles();
        try {
            string root=AppDomain.CurrentDomain.BaseDirectory;
            if(args.Length!=1)throw new Exception("需要当前局已完成的点击计划");
            var plan=Read(args[0]);var proof=Read(Path.Combine(Path.GetDirectoryName(args[0]),"playback_status.json"));var capture=Read((string)plan["capture_status_path"]);
            if((string)proof["state"]!="sequence_finished"||Convert.ToInt32(proof["next_step"])!=Convert.ToInt32(proof["total"])||(string)capture["session"]!=(string)plan["session"]||!Convert.ToBoolean(capture["complete"]))throw new Exception("当前局尚未完成，或已重新开局，不点击结算");
            var windows=Native.FindGame();if(windows.Count!=1)throw new Exception("No unique game window");
            IntPtr game=windows[0];Rectangle bounds=Native.Bounds(game);
            using(var referenceRaw=new Bitmap(Path.Combine(root,"output","程序标准化截图.png")))using(var reference=Vision.Normalize(referenceRaw))using(var before=Native.Capture(game))using(var norm=Vision.Normalize(before))using(var overlay=new Overlay()) {
                norm.Save(Path.Combine(root,"output","completion_before.png"),ImageFormat.Png);
                // The skip caption pulses vertically during the win animation.
                if(Vision.PatchDifference(reference,norm,new Rectangle(100,586,135,30))>130)throw new Exception("Completion button changed");
                int dark=0;for(int y=592;y<=610;y++)for(int x=110;x<=225;x++){Color c=norm.GetPixel(x,y);if(c.R<80&&c.G<80&&c.B<80)dark++;}
                if(dark<80)throw new Exception("Expected skip caption missing");
                overlay.Mark(new Rectangle(bounds.X+(int)(110*bounds.Width/336.0),bounds.Y+(int)(586*bounds.Height/622.0),(int)(116*bounds.Width/336.0),(int)(28*bounds.Height/622.0)),"通关动画 · 点击跳过");
                Application.DoEvents();Thread.Sleep(700);overlay.Hide();
                if(bounds!=Native.Bounds(game))throw new Exception("Window moved");
                capture=Read((string)plan["capture_status_path"]);if((string)capture["session"]!=(string)plan["session"]||(Native.GetAsyncKeyState(0x1B)&0x8000)!=0||(Native.GetAsyncKeyState(0x78)&0x8000)!=0)throw new Exception("结算点击已取消");
                Native.Click(game,new Point(bounds.X+(int)Math.Round(168*bounds.Width/336.0),bounds.Y+(int)Math.Round(602*bounds.Height/622.0)),true);
                Thread.Sleep(1500);
                using(var after=Native.Capture(game))using(var normalized=Vision.Normalize(after))normalized.Save(Path.Combine(root,"output","game_completion.png"),ImageFormat.Png);
            }return 0;
        }catch(Exception e){Console.Error.WriteLine(e.Message);return 1;}
    }
}
