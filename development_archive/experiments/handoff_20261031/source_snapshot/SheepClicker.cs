using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;

// Independent user application. No Codex computer-use runtime is used.
static class Native
{
    public delegate bool EnumProc(IntPtr hwnd, IntPtr param);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; public POINT(int x, int y) { X=x; Y=y; } }
    [StructLayout(LayoutKind.Sequential)] public struct MOUSEINPUT { public int dx, dy; public uint mouseData, flags, time; public UIntPtr extra; }
    [StructLayout(LayoutKind.Explicit)] public struct INPUTUNION { [FieldOffset(0)] public MOUSEINPUT mouse; }
    [StructLayout(LayoutKind.Sequential)] public struct INPUT { public uint type; public INPUTUNION data; }
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc proc, IntPtr param);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr hwnd, StringBuilder text, int length);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hwnd, out RECT rect);
    [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr hwnd, out RECT rect);
    [DllImport("user32.dll")] public static extern bool ScreenToClient(IntPtr hwnd, ref POINT point);
    [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr hwnd, ref POINT point);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll", SetLastError=true)] public static extern uint SendInput(uint count, INPUT[] inputs, int size);
    [DllImport("user32.dll", SetLastError=true)] public static extern bool PostMessage(IntPtr hwnd, uint msg, IntPtr wp, IntPtr lp);
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hwnd, IntPtr hdc, uint flags);
    [DllImport("user32.dll")] public static extern IntPtr ChildWindowFromPointEx(IntPtr hwnd, POINT point, uint flags);
    [DllImport("user32.dll")] public static extern short GetAsyncKeyState(int key);
    [DllImport("user32.dll")] public static extern bool RegisterHotKey(IntPtr hwnd, int id, uint modifiers, uint key);
    [DllImport("user32.dll")] public static extern bool UnregisterHotKey(IntPtr hwnd, int id);
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr hwnd, int attribute, out RECT rect, int size);
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hwnd, IntPtr after, int x, int y, int cx, int cy, uint flags);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowLong(IntPtr hwnd, int index);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int SetWindowLong(IntPtr hwnd, int index, int value);

    public static List<IntPtr> FindGame()
    {
        var result=new List<IntPtr>();
        EnumWindows(delegate(IntPtr h, IntPtr p) {
            var name=new StringBuilder(512); GetWindowText(h,name,name.Capacity);
            if(IsWindowVisible(h) && name.ToString()=="羊了个羊：星球") result.Add(h);
            return true;
        },IntPtr.Zero);
        return result;
    }
    public static Rectangle RawBounds(IntPtr hwnd)
    {
        RECT r; if(!IsWindow(hwnd)||!GetWindowRect(hwnd,out r)||IsIconic(hwnd)) throw new InvalidOperationException("微信窗口不存在或已最小化。");
        return Rectangle.FromLTRB(r.Left,r.Top,r.Right,r.Bottom);
    }
    public static Rectangle Bounds(IntPtr hwnd)
    {
        Rectangle raw=RawBounds(hwnd);RECT frame;
        if(DwmGetWindowAttribute(hwnd,9,out frame,Marshal.SizeOf(typeof(RECT)))==0) {
            Rectangle visible=Rectangle.FromLTRB(frame.Left,frame.Top,frame.Right,frame.Bottom);
            if(visible.Width>100&&visible.Height>100&&raw.Contains(visible))return visible;
        }
        return raw;
    }
    public static Bitmap Capture(IntPtr hwnd)
    {
        Rectangle r=RawBounds(hwnd),visible=Bounds(hwnd);
        if(r.Width<100||r.Height<100||r.Width>2000||r.Height>3000) throw new InvalidOperationException("游戏窗口尺寸异常。");
        var image=new Bitmap(r.Width,r.Height,PixelFormat.Format24bppRgb);
        bool ok;
        using(var g=Graphics.FromImage(image)) { IntPtr dc=g.GetHdc(); try { ok=PrintWindow(hwnd,dc,2); } finally {g.ReleaseHdc(dc);} }
        if(visible!=r){var cropped=image.Clone(new Rectangle(visible.X-r.X,visible.Y-r.Y,visible.Width,visible.Height),PixelFormat.Format24bppRgb);image.Dispose();image=cropped;}
        if(!ok||!Vision.ValidCapture(image)) {
            if(GetForegroundWindow()!=hwnd) { image.Dispose(); throw new InvalidOperationException("微信未提供后台截图。请手动把游戏置于前台再刷新，程序不会自动抢焦点。"); }
            using(var g=Graphics.FromImage(image)) g.CopyFromScreen(visible.Location,Point.Empty,visible.Size);
            if(!Vision.ValidCapture(image)) {image.Dispose();throw new InvalidOperationException("截图不可用；已停止，不发送点击。");}
        }
        return image;
    }
    public static void Click(IntPtr hwnd, Point screen, bool background)
    {
        if(background) {
            IntPtr target=hwnd;
            // Descend at the actual point so Chromium's input child receives messages.
            for(int depth=0;depth<12;depth++) {
                POINT local=new POINT(screen.X,screen.Y); ScreenToClient(target,ref local);
                IntPtr child=ChildWindowFromPointEx(target,local,7);
                if(child==IntPtr.Zero||child==target) break;
                target=child;
            }
            POINT point=new POINT(screen.X,screen.Y); ScreenToClient(target,ref point);
            IntPtr packed=new IntPtr((point.Y<<16)|(point.X&65535));
            if(!PostMessage(target,0x200,IntPtr.Zero,packed)||!PostMessage(target,0x201,new IntPtr(1),packed)||!PostMessage(target,0x202,IntPtr.Zero,packed)) throw new InvalidOperationException("发送后台点击消息失败。");
        } else {
            if(GetForegroundWindow()!=hwnd) throw new InvalidOperationException("前台模式要求先手动切回游戏。程序不会自行切换窗口。");
            SetCursorPos(screen.X,screen.Y);
            var down=new INPUT();down.type=0;down.data.mouse.flags=0x0002;
            var up=new INPUT();up.type=0;up.data.mouse.flags=0x0004;
            if(SendInput(2,new INPUT[]{down,up},Marshal.SizeOf(typeof(INPUT)))!=2) throw new InvalidOperationException("系统没有接受鼠标输入。");
        }
    }
}

class Tile
{
    public string Type; public Point Center; public Rectangle Box; public double Score;
}
class Template
{
    public string Type; public bool TrayOnly; public List<Point> Samples=new List<Point>(); public List<Color> Colors=new List<Color>();
}
static class Vision
{
    public static readonly string[] Names={"青草","胡萝卜","玉米"};
    public static List<Template> Templates(string path)
    {
        var output=new List<Template>();
        using(var source=new Bitmap(path)) {
            if(source.Width!=336||source.Height!=622) throw new InvalidOperationException("识别模板尺寸错误。");
            Point[] centers={new Point(100,224),new Point(134,224),new Point(202,311),new Point(168,312),new Point(134,308),new Point(44,510)};
            for(int k=0;k<centers.Length;k++) {
                AddTemplate(output,source,centers[k],Names[k%3]);
            }
        }
        string native=Path.Combine(Path.GetDirectoryName(path),"clicker_reference.png");
        if(File.Exists(native))using(var source=new Bitmap(native)) {
            Point[] centers={new Point(101,206),new Point(203,206),new Point(203,240),new Point(203,307),new Point(45,511),new Point(86,511),new Point(135,224),new Point(236,224),new Point(135,309),new Point(168,309),new Point(101,241)};
            for(int k=0;k<centers.Length;k++)AddTemplate(output,source,centers[k],k<6?"玉米":(k<10?"胡萝卜":"青草"));
        }
        return output;
    }
    static void AddTemplate(List<Template> output,Bitmap source,Point center,string type)
    {
        var t=new Template();t.Type=type;
        for(int y=-12;y<=12;y+=3)for(int x=-12;x<=12;x+=3){t.Samples.Add(new Point(x,y));t.Colors.Add(source.GetPixel(center.X+x,center.Y+y));}
        output.Add(t);
        // The slot renders icons larger than board tiles; only use these variants in the tray.
        foreach(double scale in new double[]{1.12,1.22,1.32}) {
            var larger=new Template{Type=type,TrayOnly=true};
            for(int y=-12;y<=12;y+=3)for(int x=-12;x<=12;x+=3){larger.Samples.Add(new Point(x,y));larger.Colors.Add(source.GetPixel(center.X+(int)Math.Round(x/scale),center.Y+(int)Math.Round(y/scale)));}
            output.Add(larger);
        }
    }
    public static bool ValidCapture(Bitmap bitmap)
    {
        // Sparse final boards are valid captures. Include the stable game UI,
        // rather than requiring many cards in the middle of the board.
        int varied=0,green=0; Color first=bitmap.GetPixel(bitmap.Width/2,bitmap.Height/2);
        for(int y=bitmap.Height/12;y<bitmap.Height*11/12;y+=Math.Max(1,bitmap.Height/35))
            for(int x=bitmap.Width/12;x<bitmap.Width*11/12;x+=Math.Max(1,bitmap.Width/35)) {
                Color c=bitmap.GetPixel(x,y);if(Math.Abs(c.R-first.R)+Math.Abs(c.G-first.G)+Math.Abs(c.B-first.B)>30)varied++;
                if(c.G>160&&c.G>c.R+15&&c.G>c.B+20)green++;
            }
        // Completion screens can use a different background from the board.
        return varied>12;
    }
    public static Bitmap Normalize(Bitmap image)
    {
        var b=new Bitmap(336,622,PixelFormat.Format24bppRgb);
        using(var g=Graphics.FromImage(b)){g.InterpolationMode=InterpolationMode.HighQualityBicubic;g.DrawImage(image,0,0,336,622);}return b;
    }
    static int Difference(Color a, Color b) {return Math.Abs(a.R-b.R)+Math.Abs(a.G-b.G)+Math.Abs(a.B-b.B);}
    public static List<Tile> Detect(Bitmap nativeImage,List<Template> templates,bool tray)
    {
        var candidates=new List<Tile>();
        using(var image=Normalize(nativeImage)) {
            var rect=new Rectangle(0,0,image.Width,image.Height);
            BitmapData locked=image.LockBits(rect,ImageLockMode.ReadOnly,PixelFormat.Format24bppRgb);
            byte[] pixels=new byte[locked.Stride*locked.Height];Marshal.Copy(locked.Scan0,pixels,0,pixels.Length);image.UnlockBits(locked);
            int yMin=tray?493:165,yMax=tray?526:458,xMin=tray?25:24,xMax=tray?305:311;
            foreach(var t in templates.Where(t=>tray||!t.TrayOnly)) for(int y=yMin;y<=yMax;y++) for(int x=xMin;x<=xMax;x++) {
                int sum=0;
                for(int i=0;i<t.Samples.Count;i++) {
                    Point p=t.Samples[i];int pos=(y+p.Y)*locked.Stride+(x+p.X)*3;Color c=t.Colors[i];
                    sum+=Math.Abs(pixels[pos]-c.B)+Math.Abs(pixels[pos+1]-c.G)+Math.Abs(pixels[pos+2]-c.R);
                    if(sum>t.Samples.Count*52) break;
                }
                double score=(double)sum/t.Samples.Count;
                if(score<52) candidates.Add(new Tile{Type=t.Type,Center=new Point(x,y),Box=new Rectangle(x-17,y-18,34,36),Score=score});
            }
        }
        var result=new List<Tile>();
        foreach(var t in candidates.OrderBy(c=>c.Score)) if(!result.Any(c=>Math.Abs(c.Center.X-t.Center.X)<24&&Math.Abs(c.Center.Y-t.Center.Y)<24))result.Add(t);
        return result.OrderBy(c=>c.Center.Y).ThenBy(c=>c.Center.X).ToList();
    }
    public static bool UnknownTray(Bitmap source,List<Tile> known)
    {
        using(var image=Normalize(source)) {
            // Slot tiles have a pale face; fail closed if a filled slot has an unknown icon.
            for(int k=0;k<7;k++) {
                int x=44+41*k;Color a=image.GetPixel(x-12,501),b=image.GetPixel(x+12,501);
                bool filled=a.R>190&&a.G>185&&a.B>130&&b.R>190&&b.G>185&&b.B>130;
                if(filled&&!known.Any(t=>Math.Abs(t.Center.X-x)<20))return true;
            }
        }
        return false;
    }
    public static Tile ChooseNext(Bitmap image,List<Template> templates)
    {
        var board=Detect(image,templates,false);var tray=Detect(image,templates,true);
        if(UnknownTray(image,tray))throw new InvalidOperationException("牌槽中有未识别的牌。自动点击暂停；可使用手动队列。");
        foreach(var g in board.GroupBy(t=>t.Type).OrderByDescending(g=>tray.Count(t=>t.Type==g.Key))) {
            int inTray=tray.Count(t=>t.Type==g.Key);if(inTray>2)throw new InvalidOperationException("牌槽动画未结束或识别不确定，请稍后刷新。");
            if(g.Count()>=3-inTray)return g.First();
        }
        throw new InvalidOperationException("没有可靠的可见配对。已暂停；不会猜被遮住的牌，也不会使用仓库示例答案。");
    }
    public static double PatchDifference(Bitmap before,Bitmap after,Rectangle box)
    {
        using(var a=Normalize(before))using(var b=Normalize(after)) {
            double sum=0;int n=0;var safe=Rectangle.Intersect(box,new Rectangle(0,0,336,622));
            for(int y=safe.Top;y<safe.Bottom;y+=2)for(int x=safe.Left;x<safe.Right;x+=2){sum+=Difference(a.GetPixel(x,y),b.GetPixel(x,y));n++;}
            return n==0?0:sum/n;
        }
    }
}

sealed class Overlay : Form
{
    public string Caption="下一次点击";
    Size markerSize;
    public Overlay(){FormBorderStyle=FormBorderStyle.None;ShowInTaskbar=false;BackColor=Color.Fuchsia;TransparencyKey=Color.Fuchsia;TopMost=true;DoubleBuffered=true;Font=new Font("Microsoft YaHei UI",12,GraphicsUnit.Pixel);}
    protected override bool ShowWithoutActivation {get{return true;}}
    protected override CreateParams CreateParams {get{var p=base.CreateParams;p.ExStyle|=0x08000000|0x00000020|0x00080000|0x00000080;return p;}}
    protected override void OnPaint(PaintEventArgs e){using(var pen=new Pen(Color.FromArgb(242,40,55),3))e.Graphics.DrawRectangle(pen,3,23,markerSize.Width,markerSize.Height);using(var b=new SolidBrush(Color.FromArgb(242,40,55)))e.Graphics.FillRectangle(b,3,2,Math.Max(95,Width-6),21);e.Graphics.DrawString(Caption,Font,Brushes.White,5,4);}
    public void Mark(Rectangle box,string caption){Caption=caption;markerSize=box.Size;Bounds=new Rectangle(box.X-3,box.Y-23,Math.Max(104,box.Width+6),box.Height+27);if(!Visible)Show();Native.SetWindowPos(Handle,new IntPtr(-1),Left,Top,Width,Height,0x0010|0x0040);Invalidate();}
    protected override void WndProc(ref Message m){if(m.Msg==0x0084){m.Result=new IntPtr(-1);return;}base.WndProc(ref m);}
}

sealed class Clicker : Form
{
    readonly string root; readonly List<Template> templates;
    readonly PictureBox preview=new PictureBox(); readonly Label status=new Label(); readonly ListBox steps=new ListBox();
    readonly CheckBox background=new CheckBox(), automatic=new CheckBox(); readonly NumericUpDown delay=new NumericUpDown();
    readonly Overlay overlay=new Overlay();readonly Timer timer=new Timer();readonly List<Tile> queue=new List<Tile>();
    IntPtr game=IntPtr.Zero;Bitmap image,before;Rectangle snapshotBounds;Tile pending;int stage=0,count=0;DateTime due;bool continuous=false;bool stopping=false;

    public Clicker(string directory)
    {
        root=directory;templates=Vision.Templates(Path.Combine(root,"output","tutorial_after_first_click.png"));
        AutoScaleMode=AutoScaleMode.Dpi;AutoScaleDimensions=new SizeF(96,96);
        Text="SheepSolver · 点击与红框助手";Size=new Size(860,830);MinimumSize=new Size(750,720);StartPosition=FormStartPosition.CenterScreen;
        Font=new Font("Microsoft YaHei UI",9);BackColor=Color.FromArgb(240,246,236);
        var layout=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=2,RowCount=3,Padding=new Padding(14)};layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,56));layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,44));layout.RowStyles.Add(new RowStyle(SizeType.Absolute,60));layout.RowStyles.Add(new RowStyle(SizeType.Percent,100));layout.RowStyles.Add(new RowStyle(SizeType.Absolute,72));Controls.Add(layout);
        var heading=new Label{Dock=DockStyle.Fill,Text="独立点击程序  |  红框预告 → 发送点击 → 截图核对\n默认后台消息，不移动鼠标。F8 暂停/继续；F9 或 Esc 停止。",AutoSize=false};layout.Controls.Add(heading,0,0);layout.SetColumnSpan(heading,2);
        preview.Dock=DockStyle.Fill;preview.SizeMode=PictureBoxSizeMode.Zoom;preview.BackColor=Color.FromArgb(32,49,36);preview.Paint+=PaintPreview;preview.MouseClick+=SelectPoint;layout.Controls.Add(preview,0,1);
        var controls=new FlowLayoutPanel{Dock=DockStyle.Fill,FlowDirection=FlowDirection.TopDown,WrapContents=false,AutoScroll=true,Padding=new Padding(12)};layout.Controls.Add(controls,1,1);
        AddButton(controls,"连接微信并刷新截图",delegate{RunSafely(delegate{Connect();RefreshImage();Analyze();});});
        background.Text="后台消息点击（不抢焦点）";background.Checked=true;background.AutoSize=true;controls.Controls.Add(background);
        automatic.Text="自动配对：青草、萝卜、玉米";automatic.Checked=true;automatic.AutoSize=true;controls.Controls.Add(automatic);
        controls.Controls.Add(new Label{Text="红框停留时间（毫秒）",AutoSize=true});delay.Minimum=300;delay.Maximum=5000;delay.Value=900;delay.Increment=100;controls.Controls.Add(delay);
        AddButton(controls,"只标注下一张，不点击",delegate{RunSafely(delegate{RefreshImage();Prepare(false);});});
        AddButton(controls,"执行一步",delegate{Start(false);});
        AddButton(controls,"连续执行 / 暂停（F8）",delegate{if(stage!=0)Stop("已暂停");else Start(true);});
        AddButton(controls,"停止并隐藏红框（F9）",delegate{Stop("已停止");});
        AddButton(controls,"清空手动队列",delegate{Stop("队列已清空");queue.Clear();UpdateQueue();});
        AddButton(controls,"保存截图、框选和坐标计划",delegate{RunSafely(SavePlan);});
        AddButton(controls,"导入坐标计划 JSON",delegate{RunSafely(ImportPlan);});
        steps.Width=380;steps.Height=155;controls.Controls.Add(steps);
        controls.Controls.Add(new Label{Text="可在截图中点击牌面，建立手动队列。\n识别只覆盖当前可见的三种图案。\n没有完整地图时不能保证整局通关。\n后台点击无变化会暂停；前台模式需要\n你手动切回微信，再按 F8。",AutoSize=true,MaximumSize=new Size(305,0),ForeColor=Color.FromArgb(94,106,87)});
        status.Dock=DockStyle.Fill;status.Text="等待连接；程序尚未发送任何点击。";status.BackColor=Color.White;status.Padding=new Padding(12);layout.Controls.Add(status,0,2);layout.SetColumnSpan(status,2);
        timer.Interval=50;timer.Tick+=Tick;timer.Start();Shown+=delegate{Native.RegisterHotKey(Handle,1,0x4000,0x77);Native.RegisterHotKey(Handle,2,0x4000,0x78);RunSafely(delegate{Connect();RefreshImage();Analyze();});};
        FormClosing+=delegate{stopping=true;Stop("已退出");timer.Stop();Native.UnregisterHotKey(Handle,1);Native.UnregisterHotKey(Handle,2);overlay.Dispose();if(image!=null)image.Dispose();if(before!=null)before.Dispose();};
    }
    void AddButton(Control panel,string caption,Action action){var b=new Button{Text=caption,Width=380,Height=40,FlatStyle=FlatStyle.Flat,BackColor=Color.White};b.Click+=delegate{action();};panel.Controls.Add(b);}
    void RunSafely(Action action){try{action();}catch(Exception e){Stop(e.Message);}}
    void Connect(){var windows=Native.FindGame();if(windows.Count!=1)throw new InvalidOperationException("需要唯一的『羊了个羊：星球』窗口；找到 "+windows.Count+" 个。");game=windows[0];}
    void RefreshImage()
    {
        if(game==IntPtr.Zero)Connect();
        overlay.Hide();var next=Native.Capture(game);snapshotBounds=Native.Bounds(game);var old=image;image=next;preview.Image=image;if(old!=null)old.Dispose();preview.Invalidate();
    }
    void Analyze()
    {
        var board=Vision.Detect(image,templates,false);var tray=Vision.Detect(image,templates,true);
        status.Text="识别到 "+board.Count+" 张可见已知牌；牌槽已知牌："+string.Join("、",tray.Select(t=>t.Type).ToArray())+"。点击截图可建立手动队列。";
    }
    Tile AutoNext()
    {
        return Vision.ChooseNext(image,templates);
    }
    void Prepare(bool execute)
    {
        if(stage!=0)throw new InvalidOperationException("正在执行，请先暂停。");
        pending=queue.Count>0?queue[0]:(automatic.Checked?AutoNext():null);
        if(pending==null)throw new InvalidOperationException("请先在截图中选择一张牌。");
        var r=Native.Bounds(game);if(r.Size!=snapshotBounds.Size)throw new InvalidOperationException("窗口尺寸改变，请刷新截图。");
        var box=NativeBox(pending.Box,r);overlay.Mark(box,"下一张 · "+pending.Type);preview.Invalidate();
        status.Text="红框："+pending.Type+"，相对窗口坐标 "+pending.Center.X+", "+pending.Center.Y+"（标准化 336×622）。";
        if(execute){before=(Bitmap)image.Clone();stage=1;due=DateTime.UtcNow.AddMilliseconds((double)delay.Value);}
    }
    static Rectangle NativeBox(Rectangle box,Rectangle window){return new Rectangle(window.X+(int)Math.Round(box.X*window.Width/336.0),window.Y+(int)Math.Round(box.Y*window.Height/622.0),(int)Math.Round(box.Width*window.Width/336.0),(int)Math.Round(box.Height*window.Height/622.0));}
    void Start(bool repeat)
    {
        RunSafely(delegate{if(stage!=0){Stop("已暂停");return;}continuous=repeat;pending=null;RefreshImage();Prepare(true);});
    }
    void Tick(object sender,EventArgs args)
    {
        if(stopping||stage==0)return;
        if((Native.GetAsyncKeyState(0x1B)&0x8000)!=0){Stop("Esc 已停止所有点击。");return;}
        if(DateTime.UtcNow<due)return;
        RunSafely(delegate {
            if(stage==1) {
                Rectangle r=Native.Bounds(game);if(r!=snapshotBounds)throw new InvalidOperationException("微信窗口移动或缩放，已暂停；刷新后重试。");
                // Reobserve before injecting so stale manual points cannot silently run.
                overlay.Hide();using(var latest=Native.Capture(game))if(Vision.PatchDifference(before,latest,pending.Box)>12)throw new InvalidOperationException("目标牌在红框等待期间发生变化，已取消点击。");
                Point screen=new Point(r.X+(int)Math.Round(pending.Center.X*r.Width/336.0),r.Y+(int)Math.Round(pending.Center.Y*r.Height/622.0));
                Native.Click(game,screen,background.Checked);Log("sent",pending,background.Checked?"background":"foreground",0);stage=2;due=DateTime.UtcNow.AddMilliseconds(850);status.Text="已发送点击，等待核对牌面变化……";
            } else if(stage==2) {
                var target=pending;RefreshImage();double difference=Vision.PatchDifference(before,image,target.Box);
                Log("verification",target,background.Checked?"background":"foreground",difference);
                before.Dispose();before=null;
                if(difference<12)throw new InvalidOperationException(background.Checked?"后台点击后牌面未变化，已暂停。微信可能不接受后台消息；可选前台模式，手动切回微信后按 F8。":"点击后牌面未变化，已暂停，请检查目标。");
                count++;if(queue.Count>0)queue.RemoveAt(0);UpdateQueue();pending=null;stage=0;status.Text="已执行 "+count+" 次点击；观察到目标区域变化（不等同于确认消除或通关）。";
                if(continuous){Prepare(true);}else preview.Invalidate();
            }
        });
    }
    void Stop(string message){stage=0;continuous=false;overlay.Hide();pending=null;if(before!=null){before.Dispose();before=null;}status.Text=message;preview.Invalidate();}
    void Log(string kind,Tile t,string mode,double difference)
    {
        string dir=Path.Combine(root,"output");Directory.CreateDirectory(dir);
        var entry=new Dictionary<string,object>{{"time",DateTimeOffset.Now.ToString("o")},{"event",kind},{"mode",mode},{"type",t.Type},{"window_relative_normalized",new int[]{t.Center.X,t.Center.Y}},{"patch_difference",Math.Round(difference,3)}};
        File.AppendAllText(Path.Combine(dir,"click_actions.jsonl"),new JavaScriptSerializer().Serialize(entry)+Environment.NewLine,Encoding.UTF8);
    }
    Rectangle ImageArea()
    {
        if(image==null)return Rectangle.Empty;double scale=Math.Min((double)preview.ClientSize.Width/image.Width,(double)preview.ClientSize.Height/image.Height);int w=(int)(image.Width*scale),h=(int)(image.Height*scale);return new Rectangle((preview.ClientSize.Width-w)/2,(preview.ClientSize.Height-h)/2,w,h);
    }
    void SelectPoint(object sender,MouseEventArgs e)
    {
        if(stage!=0||image==null)return;var area=ImageArea();if(!area.Contains(e.Location))return;
        int x=(e.X-area.X)*336/area.Width,y=(e.Y-area.Y)*622/area.Height;
        if(x<20||x>315||y<150||y>465){status.Text="只允许选择棋盘区域，不能选择菜单、牌槽或道具。";return;}
        var point=new Tile{Type="手选牌",Center=new Point(x,y),Box=new Rectangle(x-17,y-18,34,36)};queue.Add(point);UpdateQueue();pending=queue[0];preview.Invalidate();status.Text="已添加手选目标；可点『只标注下一张』或『执行一步』。";
    }
    void UpdateQueue(){steps.Items.Clear();for(int i=0;i<queue.Count;i++)steps.Items.Add((i+1)+". "+queue[i].Type+" ("+queue[i].Center.X+","+queue[i].Center.Y+")");}
    void PaintPreview(object sender,PaintEventArgs e)
    {
        if(image==null)return;var area=ImageArea();var targets=queue.Count>0?queue:new List<Tile>();if(targets.Count==0&&pending!=null)targets.Add(pending);
        for(int i=0;i<targets.Count;i++){Tile t=targets[i];var r=new Rectangle(area.X+t.Box.X*area.Width/336,area.Y+t.Box.Y*area.Height/622,t.Box.Width*area.Width/336,t.Box.Height*area.Height/622);using(var pen=new Pen(i==0?Color.Red:Color.Orange,3))e.Graphics.DrawRectangle(pen,r);e.Graphics.DrawString((i+1).ToString(),Font,Brushes.Red,r.X,r.Y-18);}
    }
    static string Fingerprint(Bitmap source){using(var image=Vision.Normalize(source))using(var ms=new MemoryStream()){image.Save(ms,ImageFormat.Png);using(var hash=SHA256.Create())return BitConverter.ToString(hash.ComputeHash(ms.ToArray())).Replace("-","").ToLowerInvariant();}}
    void SavePlan()
    {
        if(image==null)throw new InvalidOperationException("请先刷新截图。");if(queue.Count==0&&pending==null)Prepare(false);
        var targets=queue.Count>0?queue:new List<Tile>{pending};string dir=Path.Combine(root,"output");Directory.CreateDirectory(dir);
        image.Save(Path.Combine(dir,"程序当前截图.png"),ImageFormat.Png);
        using(var marked=Vision.Normalize(image))using(var g=Graphics.FromImage(marked))using(var p=new Pen(Color.Red,3)){for(int i=0;i<targets.Count;i++){g.DrawRectangle(p,targets[i].Box);g.DrawString((i+1).ToString(),Font,Brushes.Red,targets[i].Box.X,targets[i].Box.Y-18);}marked.Save(Path.Combine(dir,"程序点击标注.png"),ImageFormat.Png);}
        var plan=new Dictionary<string,object>{{"source","独立程序保存的截图坐标计划"},{"screenshot_size",new int[]{336,622}},{"image_fingerprint",Fingerprint(image)},{"targets",targets.Select(t=>new Dictionary<string,object>{{"type",t.Type},{"center",new int[]{t.Center.X,t.Center.Y}},{"box",new int[]{t.Box.X,t.Box.Y,t.Box.Width,t.Box.Height}}}).ToArray()}};
        File.WriteAllText(Path.Combine(dir,"程序点击计划.json"),new JavaScriptSerializer().Serialize(plan),Encoding.UTF8);status.Text="已保存程序当前截图、标注图和坐标计划到 output 文件夹。";
    }
    void ImportPlan()
    {
        if(stage!=0)throw new InvalidOperationException("请先停止点击。");
        using(var dialog=new OpenFileDialog{Filter="坐标计划 JSON|*.json",InitialDirectory=Path.Combine(root,"output")}) {
            if(dialog.ShowDialog(this)!=DialogResult.OK)return;
            var data=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(File.ReadAllText(dialog.FileName));
            if(!data.ContainsKey("image_fingerprint")||!data.ContainsKey("targets"))throw new InvalidOperationException("此文件不是当前截图坐标计划。仓库示例答案和旧历史标注不能直接执行。");
            RefreshImage();if((string)data["image_fingerprint"]!=Fingerprint(image))throw new InvalidOperationException("截图与计划不一致，拒绝套用旧点击坐标；请重新建立队列。");
            var nextQueue=new List<Tile>();
            foreach(object obj in (object[])data["targets"]){var item=(Dictionary<string,object>)obj;var c=(object[])item["center"];int x=Convert.ToInt32(c[0]),y=Convert.ToInt32(c[1]);if(x<20||x>315||y<150||y>465)throw new InvalidOperationException("计划包含棋盘以外的点击。");nextQueue.Add(new Tile{Type=Convert.ToString(item["type"]),Center=new Point(x,y),Box=new Rectangle(x-17,y-18,34,36)});}
            queue.Clear();queue.AddRange(nextQueue);UpdateQueue();pending=queue.FirstOrDefault();preview.Invalidate();status.Text="计划导入成功；未发送点击。";
        }
    }
    protected override void WndProc(ref Message m){if(m.Msg==0x0312){if(m.WParam.ToInt32()==1){if(stage!=0)Stop("F8 已暂停");else Start(true);}else if(m.WParam.ToInt32()==2)Stop("F9 已停止");return;}base.WndProc(ref m);}
    protected override bool ShowWithoutActivation {get{return true;}}
}

static class Program
{
    static int BackgroundStep(string root)
    {
        var windows=Native.FindGame();if(windows.Count!=1)throw new Exception("没有唯一的游戏窗口");IntPtr game=windows[0];
        var templates=Vision.Templates(Path.Combine(root,"output","tutorial_after_first_click.png"));
        using(var before=Native.Capture(game))using(var overlay=new Overlay())using(var timer=new Timer()) {
            var target=Vision.ChooseNext(before,templates);Rectangle bounds=Native.Bounds(game);
            Rectangle box=new Rectangle(bounds.X+(int)Math.Round(target.Box.X*bounds.Width/336.0),bounds.Y+(int)Math.Round(target.Box.Y*bounds.Height/622.0),(int)Math.Round(target.Box.Width*bounds.Width/336.0),(int)Math.Round(target.Box.Height*bounds.Height/622.0));
            before.Save(Path.Combine(root,"output","后台点击测试-前.png"),ImageFormat.Png);
            using(var marked=Vision.Normalize(before))using(var g=Graphics.FromImage(marked))using(var p=new Pen(Color.Red,3)){g.DrawRectangle(p,target.Box);marked.Save(Path.Combine(root,"output","程序点击标注.png"),ImageFormat.Png);}
            overlay.Mark(box,"下一张 · "+target.Type);DateTime due=DateTime.UtcNow.AddMilliseconds(1100);int stage=0,result=2;
            timer.Interval=50;
            timer.Tick+=delegate {
                try {
                    if((Native.GetAsyncKeyState(0x1B)&0x8000)!=0)throw new Exception("Esc 已取消程序点击");
                    if(DateTime.UtcNow<due)return;
                    if(stage==0) {
                        if(Native.Bounds(game)!=bounds)throw new Exception("窗口位置改变，取消点击");
                        overlay.Hide();using(var latest=Native.Capture(game))if(Vision.PatchDifference(before,latest,target.Box)>12)throw new Exception("牌面已变化，取消点击");
                        Point screen=new Point(bounds.X+(int)Math.Round(target.Center.X*bounds.Width/336.0),bounds.Y+(int)Math.Round(target.Center.Y*bounds.Height/622.0));
                        Native.Click(game,screen,true);stage=1;due=DateTime.UtcNow.AddMilliseconds(1000);return;
                    }
                    using(var after=Native.Capture(game)) {
                        after.Save(Path.Combine(root,"output","后台点击测试-后.png"),ImageFormat.Png);
                        double difference=Vision.PatchDifference(before,after,target.Box);
                        var report=new Dictionary<string,object>{{"time",DateTimeOffset.Now.ToString("o")},{"mode","background"},{"type",target.Type},{"center",new int[]{target.Center.X,target.Center.Y}},{"input_sent",true},{"target_patch_changed",difference>=12},{"patch_difference",Math.Round(difference,3)},{"foreground_not_requested",true}};
                        string json=new JavaScriptSerializer().Serialize(report);File.WriteAllText(Path.Combine(root,"output","background_click_test.json"),json,Encoding.UTF8);File.AppendAllText(Path.Combine(root,"output","click_actions.jsonl"),json+Environment.NewLine,Encoding.UTF8);Console.WriteLine(json);result=difference>=12?0:2;
                    }
                }catch(Exception e){Console.Error.WriteLine(e.Message);result=1;}
                timer.Stop();overlay.Close();Application.ExitThread();
            };
            timer.Start();Application.Run();return result;
        }
    }
    [STAThread] static int Main(string[] args)
    {
        try { Console.OutputEncoding=Encoding.UTF8; } catch(IOException) {}
        Native.SetProcessDPIAware();string root=AppDomain.CurrentDomain.BaseDirectory;
        try {
            if(args.Length>0&&args[0]=="--background-step") {Application.EnableVisualStyles();return BackgroundStep(root);}
            if(args.Length>0&&args[0]=="--background-auto") {
                Application.EnableVisualStyles();int limit=24,executed=0,result=0;string reason="达到本次点击上限";
                if(args.Length>1&&!int.TryParse(args[1],out limit))throw new Exception("点击上限必须是整数");if(limit<1||limit>100)throw new Exception("点击上限范围为 1 到 100");
                for(int i=0;i<limit;i++) {
                    try {result=BackgroundStep(root);if(result!=0){reason="本次点击未核对成功，停止";break;}executed++;System.Threading.Thread.Sleep(250);}
                    catch(Exception e){reason=e.Message;result=1;break;}
                }
                var summary=new Dictionary<string,object>{{"mode","background-auto"},{"executed",executed},{"reason",reason},{"game_win_verified",false},{"time",DateTimeOffset.Now.ToString("o")}};
                string json=new JavaScriptSerializer().Serialize(summary);File.WriteAllText(Path.Combine(root,"output","background_auto_summary.json"),json,Encoding.UTF8);Console.WriteLine(json);return result;
            }
            if(args.Length>0&&args[0]=="--self-test") {
                var templates=Vision.Templates(Path.Combine(root,"output","tutorial_after_first_click.png"));
                using(var image=new Bitmap(Path.Combine(root,"output","tutorial_after_first_click.png"))) {
                    var board=Vision.Detect(image,templates,false);var tray=Vision.Detect(image,templates,true);
                    foreach(var t in board)Console.WriteLine(t.Type+" "+t.Center+" score="+t.Score.ToString("F1"));
                    foreach(var t in tray)Console.WriteLine("tray "+t.Type+" "+t.Center+" score="+t.Score.ToString("F1"));
                    if(board.Count(t=>t.Type=="青草")!=3||board.Count(t=>t.Type=="胡萝卜")!=3||board.Count(t=>t.Type=="玉米")!=1)throw new Exception("棋盘识别数量不符合参考截图");
                    if(tray.Count(t=>t.Type=="玉米")!=1||Vision.UnknownTray(image,tray))throw new Exception("牌槽识别校验失败");
                    string native=Path.Combine(root,"output","clicker_reference.png");
                    if(File.Exists(native))using(var current=new Bitmap(native)){var visible=Vision.Detect(current,templates,false);var currentTray=Vision.Detect(current,templates,true);if(visible.Count(t=>t.Type=="玉米")!=4||visible.Count(t=>t.Type=="胡萝卜")!=4||visible.Count(t=>t.Type=="青草")!=1||currentTray.Count!=2||Vision.UnknownTray(current,currentTray))throw new Exception("原生 DPI 截图识别失败");Console.WriteLine("PASS: native screenshot: 9 exposed tiles and 2 tray corns; no covered tiles accepted.");}
                    using(var blank=new Bitmap(336,622))using(var g=Graphics.FromImage(blank)){g.Clear(Color.FromArgb(193,255,143));if(Vision.Detect(blank,templates,false).Count!=0)throw new Exception("背景被误识别为牌");}
                    if(Vision.PatchDifference(image,image,new Rectangle(84,207,33,35))!=0)throw new Exception("图像变化检测失败");
                    using(var changed=(Bitmap)image.Clone())using(var g=Graphics.FromImage(changed)){g.FillRectangle(Brushes.Black,84,207,33,35);if(Vision.PatchDifference(image,changed,new Rectangle(84,207,33,35))<12)throw new Exception("图像变化未被检测");}
                    Console.WriteLine("PASS: reference tile detection, tray recognition and change detection; no input sent.");
                }return 0;
            }
            if(args.Length==2&&args[0]=="--capture-to") {
                var windows=Native.FindGame();if(windows.Count!=1)throw new Exception("没有唯一的游戏窗口");
                using(var image=Native.Capture(windows[0]))using(var normalized=Vision.Normalize(image))normalized.Save(Path.GetFullPath(args[1]),ImageFormat.Png);
                return 0;
            }
            if(args.Length>0&&args[0]=="--capture") {
                var windows=Native.FindGame();if(windows.Count!=1)throw new Exception("没有唯一的游戏窗口");
                using(var image=Native.Capture(windows[0])){string path=Path.Combine(root,"output","程序当前截图.png");image.Save(path,ImageFormat.Png);using(var normalized=Vision.Normalize(image))normalized.Save(Path.Combine(root,"output","程序标准化截图.png"),ImageFormat.Png);Console.WriteLine(path);Console.WriteLine("capture "+image.Size);var templates=Vision.Templates(Path.Combine(root,"output","tutorial_after_first_click.png"));foreach(var t in Vision.Detect(image,templates,false))Console.WriteLine(t.Type+" "+t.Center+" score="+t.Score.ToString("F1"));foreach(var t in Vision.Detect(image,templates,true))Console.WriteLine("tray "+t.Type+" "+t.Center+" score="+t.Score.ToString("F1"));}
                return 0;
            }
            Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);Application.Run(new Clicker(root));return 0;
        }catch(Exception e){if(args.Length>0)Console.Error.WriteLine(e.Message);else MessageBox.Show(e.Message,"点击助手",MessageBoxButtons.OK,MessageBoxIcon.Error);return 1;}
    }
}
