using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

sealed class StableLabel : Label {
    public StableLabel(){DoubleBuffered=true;ResizeRedraw=true;}
}

sealed class StableLayout : TableLayoutPanel {
    public StableLayout(){DoubleBuffered=true;}
}

// One palette, one radius, one type family for every surface.
static class Ui {
    public static readonly Color Page=Color.FromArgb(243,245,242),Card=Color.White,Soft=Color.FromArgb(246,248,245),Line=Color.FromArgb(226,231,224),
        Ink=Color.FromArgb(28,38,32),Muted=Color.FromArgb(112,124,116),Faint=Color.FromArgb(160,170,163),
        Green=Color.FromArgb(32,120,80),GreenSoft=Color.FromArgb(230,242,235),Blue=Color.FromArgb(46,104,170),Amber=Color.FromArgb(200,128,30),
        Gray=Color.FromArgb(176,184,178),Track=Color.FromArgb(233,237,232);
    public const string Family="Microsoft YaHei UI";
    public static Font F(float size,bool bold=false){return new Font(Family,size,bold?FontStyle.Bold:FontStyle.Regular);}
    public static float Radius(Control c){return 8*c.DeviceDpi/96f;}
    public static GraphicsPath Round(RectangleF r,float radius){var p=new GraphicsPath();float d=Math.Max(1,Math.Min(radius*2,Math.Min(r.Width,r.Height)));
        p.AddArc(r.X,r.Y,d,d,180,90);p.AddArc(r.Right-d,r.Y,d,d,270,90);p.AddArc(r.Right-d,r.Bottom-d,d,d,0,90);p.AddArc(r.X,r.Bottom-d,d,d,90,90);p.CloseFigure();return p;}
    public static void Surface(Graphics g,Control c,Color fill,bool border){g.SmoothingMode=SmoothingMode.AntiAlias;g.Clear(c.Parent!=null?c.Parent.BackColor:Page);
        using(var p=Round(new RectangleF(0.5f,0.5f,c.Width-1.5f,c.Height-1.5f),Radius(c))){using(var b=new SolidBrush(fill))g.FillPath(b,p);if(border)using(var pen=new Pen(Line))g.DrawPath(pen,p);}}
}

// Rounded surface; children paint on its fill colour.
sealed class CardPanel : TableLayoutPanel {
    public bool Border=true;
    public CardPanel(){DoubleBuffered=true;ResizeRedraw=true;BackColor=Ui.Card;Margin=new Padding(0);}
    protected override void OnPaintBackground(PaintEventArgs e){Ui.Surface(e.Graphics,this,BackColor,Border);}
}

sealed class ThinBar : Control {
    int maximum=1,value;ProgressBarStyle style=ProgressBarStyle.Blocks;int phase;readonly System.Windows.Forms.Timer anim=new System.Windows.Forms.Timer{Interval=30};
    public ThinBar(){DoubleBuffered=true;ResizeRedraw=true;anim.Tick+=delegate{phase=(phase+3)%300;Invalidate();};}
    public void Set(int max,int current,ProgressBarStyle kind){max=Math.Max(1,max);current=Math.Max(0,Math.Min(max,current));
        if(max==maximum&&current==value&&kind==style)return;maximum=max;value=current;style=kind;anim.Enabled=kind==ProgressBarStyle.Marquee;Invalidate();}
    protected override void OnPaint(PaintEventArgs e){var g=e.Graphics;g.SmoothingMode=SmoothingMode.AntiAlias;g.Clear(Parent!=null?Parent.BackColor:BackColor);
        float h=Math.Max(3,Height*0.6f),y=(Height-h)/2;var track=new RectangleF(0,y,Width-1,h);
        using(var b=new SolidBrush(Ui.Track))using(var p=Ui.Round(track,h/2))g.FillPath(b,p);
        RectangleF fill;
        if(style==ProgressBarStyle.Marquee){float w=Width*0.24f,x=(phase/300f)*(Width+w)-w;fill=RectangleF.Intersect(new RectangleF(x,y,w,h),track);}
        else fill=new RectangleF(0,y,track.Width*value/maximum,h);
        if(fill.Width>=1)using(var b=new SolidBrush(Ui.Green))using(var p=Ui.Round(fill,h/2))g.FillPath(b,p);}
    protected override void Dispose(bool disposing){if(disposing)anim.Dispose();base.Dispose(disposing);}
}

// Status dot plus the headline text.
sealed class Headline : Control {
    Color tone=Ui.Gray;
    public Headline(){DoubleBuffered=true;ResizeRedraw=true;}
    public Color Tone{get{return tone;}set{if(tone!=value){tone=value;Invalidate();}}}
    protected override void OnTextChanged(EventArgs e){Invalidate();base.OnTextChanged(e);}
    protected override void OnPaint(PaintEventArgs e){var g=e.Graphics;g.SmoothingMode=SmoothingMode.AntiAlias;g.Clear(Parent!=null?Parent.BackColor:BackColor);
        float d=Font.Height*0.34f,y=(Height-d)/2;
        using(var halo=new SolidBrush(Color.FromArgb(46,tone)))g.FillEllipse(halo,d*0.05f,y-d*0.45f,d*1.9f,d*1.9f);
        using(var b=new SolidBrush(tone))g.FillEllipse(b,d*0.5f,y,d,d);
        int x=(int)(d*2.6f);TextRenderer.DrawText(g,Text,Font,new Rectangle(x,0,Width-x,Height),Ui.Ink,TextFormatFlags.Left|TextFormatFlags.VerticalCenter|TextFormatFlags.EndEllipsis|TextFormatFlags.SingleLine|TextFormatFlags.NoPadding);}
}

// Context pills: mode, level, rules, engine.
sealed class ChipBar : Control {
    string[] items=new string[0];
    public ChipBar(){DoubleBuffered=true;ResizeRedraw=true;}
    public void Set(List<string> list){var next=list.ToArray();if(string.Join("\u0001",next)==string.Join("\u0001",items))return;items=next;Invalidate();}
    protected override void OnPaint(PaintEventArgs e){var g=e.Graphics;g.SmoothingMode=SmoothingMode.AntiAlias;g.Clear(Parent!=null?Parent.BackColor:BackColor);
        int h=Font.Height+DeviceDpi*10/96,y=(Height-h)/2,x=0,pad=DeviceDpi*10/96,gap=DeviceDpi*6/96;
        for(int i=0;i<items.Length;i++){var size=TextRenderer.MeasureText(g,items[i],Font,Size.Empty,TextFormatFlags.NoPadding);int w=size.Width+pad*2;if(x+w>Width)break;
            var r=new RectangleF(x+0.5f,y+0.5f,w-1,h-1);Color fill=i==0?Ui.GreenSoft:Ui.Card,text=i==0?Ui.Green:Ui.Muted;
            using(var p=Ui.Round(r,h/2f)){using(var b=new SolidBrush(fill))g.FillPath(b,p);if(i>0)using(var pen=new Pen(Ui.Line))g.DrawPath(pen,p);}
            TextRenderer.DrawText(g,items[i],Font,new Rectangle(x,y,w,h),text,TextFormatFlags.HorizontalCenter|TextFormatFlags.VerticalCenter|TextFormatFlags.NoPadding);x+=w+gap;}}
}

// Connected steps. Level: 0 done, 1 needs attention, 2 idle, 3 in progress.
sealed class Stepper : Control {
    readonly string[] names;readonly string[] values;readonly int[] levels;public Font Small;
    public Stepper(params string[] steps){names=steps;values=new string[steps.Length];levels=new int[steps.Length];for(int i=0;i<steps.Length;i++){values[i]="";levels[i]=2;}DoubleBuffered=true;ResizeRedraw=true;}
    public void Set(int i,string value,int level){if(values[i]==value&&levels[i]==level)return;values[i]=value;levels[i]=level;Invalidate();}
    static Color Tone(int level){return level==0?Ui.Green:level==1?Ui.Amber:level==3?Ui.Blue:Ui.Gray;}
    protected override void OnPaint(PaintEventArgs e){var g=e.Graphics;g.SmoothingMode=SmoothingMode.AntiAlias;g.Clear(Parent!=null?Parent.BackColor:BackColor);
        int n=names.Length;float col=Width/(float)n,r=Font.Height*0.36f,cy=r+2,stroke=Math.Max(1.5f,DeviceDpi/64f);
        for(int i=0;i<n-1;i++){float x1=col*(i+0.5f)+r+4,x2=col*(i+1.5f)-r-4;using(var pen=new Pen(levels[i]==0&&levels[i+1]==0?Ui.Green:Ui.Line,stroke))g.DrawLine(pen,x1,cy,x2,cy);}
        for(int i=0;i<n;i++){float cx=col*(i+0.5f);var dot=new RectangleF(cx-r,cy-r,r*2,r*2);Color c=Tone(levels[i]);
            if(levels[i]==0){using(var b=new SolidBrush(c))g.FillEllipse(b,dot);using(var pen=new Pen(Color.White,stroke)){g.DrawLines(pen,new[]{new PointF(cx-r*0.45f,cy),new PointF(cx-r*0.1f,cy+r*0.35f),new PointF(cx+r*0.5f,cy-r*0.35f)});}}
            else if(levels[i]==2){using(var pen=new Pen(c,stroke))g.DrawEllipse(pen,dot);}
            else{using(var halo=new SolidBrush(Color.FromArgb(40,c)))g.FillEllipse(halo,RectangleF.Inflate(dot,r*0.45f,r*0.45f));using(var b=new SolidBrush(c))g.FillEllipse(b,RectangleF.Inflate(dot,-r*0.25f,-r*0.25f));}
            int top=(int)(cy+r+DeviceDpi*8/96f),w=(int)col-4,x=(int)(cx-w/2f);
            TextRenderer.DrawText(g,names[i],Font,new Rectangle(x,top,w,Font.Height),levels[i]==2?Ui.Muted:Ui.Ink,TextFormatFlags.HorizontalCenter|TextFormatFlags.NoPadding|TextFormatFlags.EndEllipsis);
            var small=Small??Font;TextRenderer.DrawText(g,values[i],small,new Rectangle(x,top+Font.Height+2,w,small.Height),Ui.Muted,TextFormatFlags.HorizontalCenter|TextFormatFlags.NoPadding|TextFormatFlags.EndEllipsis);}}
}

sealed class FlatButton : Button {
    bool main,link,hover,down;
    public bool Main{get{return main;}set{if(main!=value){main=value;Invalidate();}}}
    public bool Link{get{return link;}set{if(link!=value){link=value;Invalidate();}}}
    public FlatButton(){SetStyle(ControlStyles.UserPaint|ControlStyles.AllPaintingInWmPaint|ControlStyles.OptimizedDoubleBuffer|ControlStyles.ResizeRedraw,true);FlatStyle=FlatStyle.Flat;Cursor=Cursors.Hand;}
    protected override void OnMouseEnter(EventArgs e){hover=true;Invalidate();base.OnMouseEnter(e);}
    protected override void OnMouseLeave(EventArgs e){hover=down=false;Invalidate();base.OnMouseLeave(e);}
    protected override void OnMouseDown(MouseEventArgs e){down=true;Invalidate();base.OnMouseDown(e);}
    protected override void OnMouseUp(MouseEventArgs e){down=false;Invalidate();base.OnMouseUp(e);}
    protected override void OnEnabledChanged(EventArgs e){Invalidate();base.OnEnabledChanged(e);}
    protected override void OnTextChanged(EventArgs e){Invalidate();base.OnTextChanged(e);}
    protected override void OnPaint(PaintEventArgs e){var g=e.Graphics;g.SmoothingMode=SmoothingMode.AntiAlias;g.Clear(Parent!=null?Parent.BackColor:Ui.Page);Color text;
        var flags=TextFormatFlags.VerticalCenter|TextFormatFlags.EndEllipsis|TextFormatFlags.SingleLine|(link&&TextAlign==ContentAlignment.MiddleLeft?TextFormatFlags.Left|TextFormatFlags.NoPadding:link&&TextAlign==ContentAlignment.MiddleRight?TextFormatFlags.Right|TextFormatFlags.NoPadding:TextFormatFlags.HorizontalCenter);
        if(link)text=!Enabled?Ui.Faint:hover?Color.FromArgb(20,90,58):Ui.Green;
        else{Color fill,border;var r=new RectangleF(0.5f,0.5f,Width-1.5f,Height-1.5f);
            if(main){fill=!Enabled?Color.FromArgb(178,208,190):down?Color.FromArgb(24,96,63):hover?Color.FromArgb(40,136,92):Ui.Green;border=fill;text=Color.White;}
            else{fill=!Enabled?Ui.Card:down?Color.FromArgb(234,240,235):hover?Ui.Soft:Ui.Card;border=Enabled?Color.FromArgb(208,216,206):Ui.Line;text=Enabled?Ui.Ink:Ui.Faint;}
            using(var p=Ui.Round(r,Ui.Radius(this))){using(var b=new SolidBrush(fill))g.FillPath(b,p);using(var pen=new Pen(border))g.DrawPath(pen,p);}}
        TextRenderer.DrawText(g,Text,Font,ClientRectangle,text,flags);}
}

sealed class LiveBoard : Control {
    Bitmap bitmap;
    public LiveBoard(){DoubleBuffered=true;ResizeRedraw=true;BackColor=Ui.Card;}
    public void LoadPath(string path){using(var stream=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.ReadWrite|FileShare.Delete))using(var image=Image.FromStream(stream)){var old=bitmap;bitmap=new Bitmap(image);if(old!=null)old.Dispose();}Invalidate();}
    protected override void OnPaint(PaintEventArgs e){var g=e.Graphics;g.Clear(BackColor);if(bitmap==null){TextRenderer.DrawText(g,"进入游戏后，这里会显示实时棋盘",Font,ClientRectangle,Ui.Faint,TextFormatFlags.HorizontalCenter|TextFormatFlags.VerticalCenter);return;}
        double scale=Math.Min((Width-2.0)/bitmap.Width,(Height-2.0)/bitmap.Height);int w=(int)(bitmap.Width*scale),h=(int)(bitmap.Height*scale);g.InterpolationMode=InterpolationMode.HighQualityBicubic;g.SmoothingMode=SmoothingMode.AntiAlias;
        var r=new Rectangle((Width-w)/2,(Height-h)/2,w,h);using(var clip=Ui.Round(r,Ui.Radius(this))){g.SetClip(clip);g.DrawImage(bitmap,r);g.ResetClip();}}
    protected override void Dispose(bool disposing){if(disposing&&bitmap!=null)bitmap.Dispose();base.Dispose(disposing);}
}

sealed class AutoSheepForm : Form {
    readonly string root;readonly bool preview,autoStart;readonly JavaScriptSerializer json=new JavaScriptSerializer{MaxJsonLength=4*1024*1024};
    readonly Color green=Ui.Green,ink=Ui.Ink,muted=Ui.Muted;
    readonly LiveBoard board=new LiveBoard();readonly Headline title=new Headline();readonly ChipBar chips=new ChipBar();
    readonly Label message=new StableLabel(),heartbeat=new StableLabel(),searchText=new StableLabel(),stepText=new StableLabel(),stepValue=new StableLabel(),caption=new StableLabel();
    readonly Stepper pipeline=new Stepper("抓取","种子","地图","解答","执行");
    readonly FlatButton primary=new FlatButton(),stop=new FlatButton(),refresh=new FlatButton(),detailToggle=new FlatButton(),propGuide=new FlatButton(),settingsLink=new FlatButton();
    readonly CardPanel detailBox=new CardPanel{Border=false,BackColor=Ui.Soft};
    readonly ComboBox performance=new ComboBox(),speed=new ComboBox();readonly NumericUpDown seconds=new NumericUpDown();readonly ThinBar playBar=new ThinBar();
    readonly System.Windows.Forms.Timer timer=new System.Windows.Forms.Timer();readonly StringBuilder events=new StringBuilder();Dictionary<string,object> snapshot;
    bool connecting,closing,closeReady,suppress,autoSent,showDetail=true;string primaryAction="start";DateTime lastConnect=DateTime.MinValue,lastImageTime=DateTime.MinValue;string lastImage="",lastEvent="",state="connecting";
    public AutoSheepForm(string directory,bool demo=false,bool start=false){
        root=directory;preview=demo;autoStart=start;DoubleBuffered=true;AutoScaleMode=AutoScaleMode.None;Font=Ui.F(9.5f);BackColor=Ui.Page;ForeColor=ink;Text="羊羊助手";StartPosition=FormStartPosition.CenterScreen;
        string icon=Path.Combine(root,"assets","sheep.ico");if(File.Exists(icon))Icon=new Icon(icon);Build();float scale;using(var g=Graphics.FromHwnd(IntPtr.Zero))scale=g.DpiX/96f;ScaleTree(this,scale);
        MinimumSize=new Size((int)(960*scale),(int)(700*scale));Size=new Size(Math.Min((int)(1120*scale),Screen.PrimaryScreen.WorkingArea.Width-40),Math.Min((int)(800*scale),Screen.PrimaryScreen.WorkingArea.Height-30));
        if(preview){Demo();DemoSearch();}else{timer.Interval=250;timer.Tick+=async delegate{await Poll();};Shown+=async delegate{timer.Start();await Poll();};FormClosing+=OnClosing;}
        FormClosed+=delegate{timer.Dispose();};
    }
    Label TextLabel(string text,float size=9.5f,bool bold=false){return new StableLabel{Text=text,Dock=DockStyle.Fill,Font=Ui.F(size,bold),ForeColor=ink,Margin=new Padding(0),TextAlign=ContentAlignment.MiddleLeft,AutoEllipsis=true};}
    void ButtonStyle(FlatButton b,string text,bool main=false,bool link=false){b.Text=text;b.Dock=DockStyle.Fill;b.Main=main;b.Link=link;b.Margin=new Padding(0);}
    static void ScaleTree(Control c,float factor){c.Padding=new Padding((int)(c.Padding.Left*factor),(int)(c.Padding.Top*factor),(int)(c.Padding.Right*factor),(int)(c.Padding.Bottom*factor));c.Margin=new Padding((int)(c.Margin.Left*factor),(int)(c.Margin.Top*factor),(int)(c.Margin.Right*factor),(int)(c.Margin.Bottom*factor));var table=c as TableLayoutPanel;if(table!=null){foreach(RowStyle r in table.RowStyles)if(r.SizeType==SizeType.Absolute)r.Height*=factor;foreach(ColumnStyle col in table.ColumnStyles)if(col.SizeType==SizeType.Absolute)col.Width*=factor;}foreach(Control child in c.Controls)ScaleTree(child,factor);}
    // Negative sizes are absolute pixels at 96 DPI; positive ones are percentages.
    static StableLayout Grid(params float[] widths){var t=new StableLayout{Dock=DockStyle.Fill,ColumnCount=widths.Length,RowCount=1,Margin=new Padding(0),Padding=new Padding(0)};
        foreach(float w in widths)t.ColumnStyles.Add(w<0?new ColumnStyle(SizeType.Absolute,-w):new ColumnStyle(SizeType.Percent,w));t.RowStyles.Add(new RowStyle(SizeType.Percent,100));return t;}
    static void Rows(TableLayoutPanel t,params float[] heights){t.RowCount=heights.Length;foreach(float h in heights)t.RowStyles.Add(h<0?new RowStyle(SizeType.Absolute,-h):new RowStyle(SizeType.Percent,h));}
    void Build(){
        var layout=new StableLayout{Dock=DockStyle.Fill,Padding=new Padding(24,14,24,10),ColumnCount=1};Rows(layout,-46,100,-26);Controls.Add(layout);

        var header=Grid(-108,100,-60);header.Margin=new Padding(0,0,0,12);header.Dock=DockStyle.Fill;
        header.Controls.Add(TextLabel("羊羊助手",14,true),0,0);chips.Font=Ui.F(9);chips.Dock=DockStyle.Fill;chips.Margin=new Padding(0);header.Controls.Add(chips,1,0);
        ButtonStyle(settingsLink,"设置",false,true);settingsLink.TextAlign=ContentAlignment.MiddleRight;settingsLink.Font=Ui.F(9.5f);settingsLink.Click+=delegate{ShowSettings();};header.Controls.Add(settingsLink,2,0);
        layout.Controls.Add(header,0,0);

        var body=Grid(41,59);body.Margin=new Padding(0,0,0,10);layout.Controls.Add(body,0,1);
        var left=new CardPanel{Dock=DockStyle.Fill,ColumnCount=1,Padding=new Padding(16,16,16,8),Margin=new Padding(0,0,16,0)};Rows(left,100,-28);body.Controls.Add(left,0,0);
        board.Dock=DockStyle.Fill;board.Margin=new Padding(0);left.Controls.Add(board,0,0);
        caption.Dock=DockStyle.Fill;caption.ForeColor=Ui.Faint;caption.BackColor=Ui.Card;caption.Font=Ui.F(8.5f);caption.Text="红框为下一步";caption.TextAlign=ContentAlignment.MiddleCenter;left.Controls.Add(caption,0,1);

        // Right card: what is happening, how far, the one thing to do.
        var right=new CardPanel{Dock=DockStyle.Fill,ColumnCount=1,Padding=new Padding(30,24,30,20)};Rows(right,-44,-50,-26,-26,-12,-30,-74,-30,-48,-38,100);body.Controls.Add(right,1,0);
        title.Font=Ui.F(17,true);title.Dock=DockStyle.Fill;title.Margin=new Padding(0);title.Text="正在连接后台";right.Controls.Add(title,0,0);
        message.Dock=DockStyle.Fill;message.Margin=new Padding(0);message.ForeColor=muted;message.BackColor=Ui.Card;message.Font=Ui.F(10);message.Text="后台准备就绪后，点击开始托管。";message.TextAlign=ContentAlignment.TopLeft;message.AutoEllipsis=true;right.Controls.Add(message,0,1);
        var progressHead=Grid(100,-90);progressHead.Margin=new Padding(0);stepText.Dock=DockStyle.Fill;stepText.ForeColor=ink;stepText.BackColor=Ui.Card;stepText.Font=Ui.F(9.5f);stepText.TextAlign=ContentAlignment.BottomLeft;stepText.AutoEllipsis=true;stepText.Text="尚未开始";
        stepValue.Dock=DockStyle.Fill;stepValue.ForeColor=muted;stepValue.BackColor=Ui.Card;stepValue.Font=Ui.F(9.5f);stepValue.TextAlign=ContentAlignment.BottomRight;progressHead.Controls.Add(stepText,0,0);progressHead.Controls.Add(stepValue,1,0);right.Controls.Add(progressHead,0,3);
        playBar.Dock=DockStyle.Fill;playBar.Margin=new Padding(0,2,0,0);playBar.BackColor=Ui.Card;right.Controls.Add(playBar,0,4);
        pipeline.Font=Ui.F(9);pipeline.Small=Ui.F(8.25f);pipeline.Dock=DockStyle.Fill;pipeline.Margin=new Padding(0);pipeline.BackColor=Ui.Card;right.Controls.Add(pipeline,0,6);

        var actions=Grid(66,34);actions.Margin=new Padding(0);ButtonStyle(primary,"开始托管",true);primary.Font=Ui.F(10.5f,true);primary.Margin=new Padding(0,0,10,0);primary.Click+=delegate{Send(primaryAction);};
        ButtonStyle(stop,"停止");stop.Font=Ui.F(10);stop.Click+=delegate{Send("stop");};actions.Controls.Add(primary,0,0);actions.Controls.Add(stop,1,0);right.Controls.Add(actions,0,8);

        var links=Grid(-76,-88,-130,100);links.Margin=new Padding(0,4,0,0);
        ButtonStyle(refresh,"重新核对",false,true);refresh.TextAlign=ContentAlignment.MiddleLeft;refresh.Click+=delegate{Send("refresh");};
        ButtonStyle(detailToggle,"隐藏详情",false,true);detailToggle.TextAlign=ContentAlignment.MiddleLeft;detailToggle.Click+=delegate{showDetail=!showDetail;detailBox.Visible=showDetail;SetText(detailToggle,showDetail?"隐藏详情":"技术详情");};
        ButtonStyle(propGuide,"查看移出操作说明",false,true);propGuide.TextAlign=ContentAlignment.MiddleLeft;propGuide.Visible=false;propGuide.Click+=delegate{OpenPropGuide();};
        var keys=TextLabel("F8 暂停 · F9 停止点击",8.5f);keys.ForeColor=Ui.Faint;keys.BackColor=Ui.Card;keys.TextAlign=ContentAlignment.MiddleRight;
        foreach(Control c in new Control[]{refresh,detailToggle,propGuide})c.Font=Ui.F(9);
        links.Controls.Add(refresh,0,0);links.Controls.Add(detailToggle,1,0);links.Controls.Add(propGuide,2,0);links.Controls.Add(keys,3,0);right.Controls.Add(links,0,9);

        detailBox.Dock=DockStyle.Fill;detailBox.Margin=new Padding(0,10,0,0);detailBox.Padding=new Padding(16,12,16,12);detailBox.ColumnCount=1;detailBox.RowCount=1;
        searchText.Dock=DockStyle.Fill;searchText.ForeColor=muted;searchText.BackColor=Ui.Soft;searchText.Font=Ui.F(8.75f);searchText.TextAlign=ContentAlignment.TopLeft;searchText.Text="";detailBox.Controls.Add(searchText,0,0);right.Controls.Add(detailBox,0,10);

        speed.DropDownStyle=ComboBoxStyle.DropDownList;speed.Items.AddRange(new object[]{"稳妥 · 2 秒/步","快速 · 1.2 秒/步","极速 · 动态核对"});speed.SelectedIndex=1;speed.SelectedIndexChanged+=delegate{if(!suppress)Send("settings");};
        performance.DropDownStyle=ComboBoxStyle.DropDownList;performance.Items.AddRange(new object[]{"省资源 · 单路","快速 · 自动并行","全速 · 空闲资源"});performance.SelectedIndex=2;performance.SelectedIndexChanged+=delegate{if(!suppress)Send("settings");};
        seconds.Minimum=30;seconds.Maximum=300;seconds.Increment=30;seconds.Value=180;seconds.ValueChanged+=delegate{if(!suppress)Send("settings");};

        heartbeat.Dock=DockStyle.Fill;heartbeat.Text="后台状态初始化中";heartbeat.ForeColor=Ui.Faint;heartbeat.Font=Ui.F(8.5f);heartbeat.TextAlign=ContentAlignment.MiddleLeft;heartbeat.AutoEllipsis=true;heartbeat.Margin=new Padding(2,0,0,0);
        layout.Controls.Add(heartbeat,0,2);
    }
    Dictionary<string,object> Read(string p){using(var stream=new FileStream(p,FileMode.Open,FileAccess.Read,FileShare.ReadWrite|FileShare.Delete))using(var reader=new StreamReader(stream,Encoding.UTF8))return json.Deserialize<Dictionary<string,object>>(reader.ReadToEnd());}
    static string V(Dictionary<string,object> d,string key,string fallback=""){return d!=null&&d.ContainsKey(key)&&d[key]!=null?Convert.ToString(d[key]):fallback;}
    static string Range(Dictionary<string,object> d,string min,string max){return V(d,min)==V(d,max)?V(d,min):V(d,min)+"～"+V(d,max);}
    static bool B(Dictionary<string,object> d,string key){return d!=null&&d.ContainsKey(key)&&d[key]!=null&&Convert.ToBoolean(d[key]);}
    static Dictionary<string,object> D(Dictionary<string,object> d,string key){return d!=null&&d.ContainsKey(key)&&d[key] is Dictionary<string,object>?(Dictionary<string,object>)d[key]:new Dictionary<string,object>();}
    Dictionary<string,object> Settings(){return new Dictionary<string,object>{{"performance",performance.SelectedIndex},{"click_speed",speed.SelectedIndex},{"seconds",(int)seconds.Value}};}
    void Send(string action){if(preview||closing)return;try{string directory=Path.Combine(root,"output","workflow","commands");Directory.CreateDirectory(directory);string id=DateTime.UtcNow.Ticks+"_"+Guid.NewGuid().ToString("N");string path=Path.Combine(directory,id+".json"),temp=path+".tmp";File.WriteAllText(temp,json.Serialize(new Dictionary<string,object>{{"action",action},{"settings",Settings()}}),new UTF8Encoding(false));File.Move(temp,path);if(action=="start"){primary.Text="正在接续…";primary.Enabled=false;}Event("操作："+(action=="start"?"开始或继续托管":action=="pause"?"暂停托管":action=="stop"?"停止托管":action=="settings"?"更新速度与性能":action));}catch(Exception e){message.Text=e.Message;}}
    async Task Connect(){if(connecting||DateTime.UtcNow-lastConnect<TimeSpan.FromSeconds(3))return;connecting=true;lastConnect=DateTime.UtcNow;try{await Task.Run(()=>{using(var p=Process.Start(new ProcessStartInfo{FileName=Path.Combine(root,"venv","Scripts","python.exe"),Arguments="\""+Path.Combine(root,"workflow_controller.py")+"\" start",WorkingDirectory=root,UseShellExecute=false,CreateNoWindow=true,RedirectStandardOutput=true,RedirectStandardError=true})){string error=p.StandardError.ReadToEnd();p.StandardOutput.ReadToEnd();if(!p.WaitForExit(10000)||p.ExitCode!=0)throw new Exception(error);}});}catch(Exception e){message.Text="后台连接失败："+e.Message;}finally{connecting=false;}}
    async Task Poll(){if(closing||preview)return;try{string path=Path.Combine(root,"output","workflow","status.json");bool alive=false;
        if(File.Exists(path)){var d=Read(path);DateTime time;if(DateTime.TryParse(V(d,"heartbeat"),out time)&&DateTime.UtcNow-time.ToUniversalTime()<TimeSpan.FromSeconds(4)){try{alive=!Process.GetProcessById(Convert.ToInt32(d["pid"])).HasExited;}catch{}if(alive)Apply(d);}}
        if(!alive){title.Text="正在重新连接后台";message.Text="后台状态尚未更新，正在自动恢复连接。";heartbeat.Text=DateTime.Now.ToString("HH:mm:ss")+" · 后台未响应，正在重连";primary.Enabled=false;await Connect();}
        else if(autoStart&&!autoSent){autoSent=true;Send("start");}
    }catch(IOException){}catch(ArgumentException){}catch(Exception e){heartbeat.Text="状态读取异常："+e.Message;}}
    static void SetText(Control control,string text){if(control.Text!=text)control.Text=text;}
    static string SearchPhase(string phase){switch(phase){case "prop_search":return "一次移出搜索";case "native":return "原生搜索";case "beam":return "快速候选搜索";case "candidate_diagnosis":return "残局核查与冲突压缩";case "repairing":return "修改候选选择";case "building":case "positions":return "构建模型";case "type_occurrences":return "三消配对";case "model_ready":return "模型就绪";case "presolve":return "预处理";case "search":return "搜索";default:return phase;}}
    static void SetProgress(ThinBar bar,int maximum,int value,ProgressBarStyle style){bar.Set(maximum,value,style);}
    void Apply(Dictionary<string,object> d){snapshot=d;state=V(d,"state");string text;
        switch(state){case "waiting_retry":text="正在自动换策略";break;case "disconnected":text="准备开始";break;case "waiting_start":text="等待游戏开局";break;case "waiting_capture":text="正在恢复抓取连接";break;case "waiting_window":text="等待游戏窗口恢复显示";break;case "waiting_new_map":text="等待本局地图与种子";break;case "waiting_resume":text="正在恢复点击进度";break;case "waiting_input":text="后台点击暂未生效";break;case "waiting_seed":text="等待种子解密";break;case "identifying":text="正在识别棋盘";break;case "solving":text="正在搜索完整答案";break;case "search_limited":text="本轮尚未找到完整路线";break;case "preparing":text="正在自动对齐";break;case "ready":text=B(d,"can_resume")?"断点已核对，可以续接":"棋盘已就绪";break;case "playing":case "verifying":text="正在自动通关";break;case "planning_segment":text="正在计算下一小段";break;case "waiting_new_game":text="等待重新开局";break;case "learning":text="正在学习新牌型";break;case "waiting_target":text="等待临时提示消失";break;case "paused":text="托管已暂停";break;case "blocked":text="需要处理";break;case "stopped":text="托管已停止";break;case "finished":text="答案已执行完毕";break;case "waiting_board":text="正在等待棋盘或地图更新";break;case "restoring":text="正在恢复设置";break;default:text="正在连接服务";break;}
        var search=D(d,"search");var ps=D(d,"playback");var settings=D(d,"settings");string searchState=V(search,"state");
        bool constraint=V(settings,"solver_engine")=="static_constraint_v1"&&V(d,"rules")=="standard";
        bool original=!constraint&&(V(settings,"solver_engine")=="upstream_normal_snapshot_v1"||V(search,"engine")=="upstream_normal_snapshot_v1");
        bool standard=constraint||V(search,"engine")=="standard_complete_continuation";
        if(standard&&(state=="solving"||state=="search_limited")&&V(search,"committed_steps","0")=="0")ps=new Dictionary<string,object>();
        bool rolling=!standard&&(V(search,"execution_kind")=="rolling_segment_v1"||V(ps,"execution_kind")=="rolling_segment_v1");
        bool running=B(d,"auto_enabled");
        if(original&&state=="waiting_start"&&!running)text="原版模式已就绪";
        string mainMessage=state=="playing"||state=="verifying"?"按已验证路线自动点击，每一步都会核对牌面和牌槽。":V(d,"message");
        int level=d.ContainsKey("level")&&d["level"]!=null?Convert.ToInt32(d["level"])+1:0;
        string game=V(d,"mode")=="world"?"羊羊大世界":V(d,"mode")=="daily"?"每日一关":"模式待核对";
        string modeText=game+(V(d,"rules")=="color_shuffle"?" · 消除后变色":V(d,"rules")=="energy_alien"?" · 电量与飞碟":"")+" · "+(level>0?"第 "+level+" / "+V(d,"map_count")+" 关":"自动识别关次")+" · "+(running?"托管中":"等待托管");
        if(constraint)modeText+=" · 整局约束 · 六格槽容量";
        else if(original)modeText+=" · 原版回溯 · 变化后重算";
        else if(B(settings,"rolling"))modeText+=V(d,"rules")=="standard"?" · 完整路线托管":" · 边算边玩";
        if(V(ps,"rules")=="energy_alien_v1")modeText+=" · "+(B(ps,"alien_pending")?"等待飞碟消牌":Convert.ToInt32(V(ps,"alien_activations","0"))>0?"飞碟已召唤":"电量 "+V(ps,"energy_count")+" / "+V(ps,"collect_count"));
        string answerText=rolling?(searchState=="segment_ready"?"当前小段已验证":state=="planning_segment"?"正在规划下一段":"边执行边规划"):
            searchState=="solved"||B(d,"ready")||state=="playing"||state=="verifying"||state=="learning"?"完整答案已验证":state=="solving"?"正在搜索":"自动准备中";
        if(original)answerText=state=="waiting_new_map"?"等待本局地图，尚未求解":state=="waiting_window"?"等待窗口显示，尚未求解":searchState=="solved"||searchState=="segment_ready"?"当前牌型路线已算出":state=="solving"?"原版算法计算中":"等待开始";
        int searchMax=Math.Max(1,Convert.ToInt32(V(search,"total","0"))),deep=Convert.ToInt32(V(search,"deepest","0"));
        string searchCaption;
        if(constraint){
            if(state=="prop_ready")text="一次移出路线已找到";
            if(searchState=="limited")answerText="预算内未知 · 棋盘保留";
            else if(searchState=="prop_ready")answerText="道具路线已验证 · 待人工操作";
            else if(searchState=="no_continuation")answerText="模型不可满足 · 范围见下方";
            searchCaption="普通七格牌槽 · 消除后最多6张\n"+V(search,"message","点击开始后求解，完整路线验证后执行");
            if(searchState=="prop_ready")searchCaption="一次移出完整路线 · "+V(search,"prop_operation_count")+" 个操作\n需人工移出、确认广告结束并校准上方区域\n正常规则是否可解仍未知；当前不会自动点击";
            if(searchState=="searching"){
                var acceleration=D(search,"acceleration");
                searchCaption+="\n阶段 "+SearchPhase(V(search,"phase","building"))+" · 已用 "+V(search,"elapsed_seconds","0")+" 秒 / "+V(search,"budget_seconds",V(settings,"seconds"))+" 秒\n"+V(search,"workers","0")+" 路 · "+(acceleration.Count>0?"提示 "+V(acceleration,"hint_cards","0")+" 步 · 已应用 "+V(acceleration,"restored_conflicts","0")+" 条冲突":"变量 "+V(search,"active_variables",V(search,"variables","0")));
            }
            var constraintResources=D(search,"resources");
            if(constraintResources.Count>0&&searchState!="prop_ready")searchCaption+="\n"+(searchState=="searching"?"CPU":"计算已停 · 当前CPU")+" 求解 "+V(constraintResources,"solver_cpu_percent","0")+"% / 全机 "+V(constraintResources,"system_cpu_percent","0")+"% · 内存 "+V(constraintResources,"solver_memory_mb","0")+" MB";
            if(searchState=="limited")searchCaption+="\n可解性未知，未执行任何候选路线";
            if(searchState=="no_continuation")searchCaption+="\n范围："+(V(search,"proof_scope")=="whole_map"?"整张地图":"当前已确认前缀")+"；未生成独立无解证明证书";
        }else if(original){
            searchCaption="开源项目原版 Python · 自带选牌策略\n按当前牌型计算完整路线\n"+V(search,"message",state=="waiting_new_map"?"当前画面与缓存地图不同，尚未开始搜索":state=="waiting_window"?"窗口恢复显示后自动继续":running?"等待当前棋盘与地图就绪":"点击开始托管后求解")+"\n牌型改变后作废旧路线并重新计算";
            if(searchState=="searching")searchCaption+="\n已用 "+V(search,"elapsed_seconds","0")+" 秒 · 最深 "+V(search,"deepest","0")+" · "+V(search,"nodes_per_second","0")+" 次尝试/秒\n"+V(search,"workers","0")+" / "+V(search,"target_workers","1")+" 路 · 已处理 "+V(search,"completed_branches","0")+" 个分支"; if(D(search,"resources").Count>0)searchCaption+="\nCPU "+V(D(search,"resources"),"system_cpu_percent")+"% · 搜索内存 "+V(D(search,"resources"),"solver_memory_mb")+" MB";
        }else if(standard){
            searchCaption="普通关 · 验证完整通关路线\n优先 5 格，必要时放宽到 6 格\n"+V(search,"message")+"\n后续路线持续保留，暂停后按记录续接";
        }else if(rolling){
            searchCaption="牌槽上限 "+V(search,"tray_capacity","5")+" 格 · 每段优先 5 格，必要时用 6 格\n已确认 "+V(search,"committed_steps","0")+" 步 · 前瞻消除 "+V(search,"lookahead_events","0")+" / "+V(search,"horizon_target","3")+" 次\n"+V(search,"message")+"\n局部前瞻已回放，最终通关仍未确定";
        }else if(search.Count>0){
            var res=D(search,"resources");var tail=D(search,"tail_search");var front=D(search,"frontier_search");var dead=D(search,"dead_cache");
            searchCaption=searchState=="solved"?"完整答案已验证 · 搜索用时 "+V(search,"elapsed_seconds")+" 秒":
                string.Format((V(search,"rules")=="energy_alien_v1"?"曾移除 {0}/{1} 张 · {2:N0} 节点/秒":"探索深度 {0}/{1} · {2:N0} 节点/秒")+"\n已用 {3} 秒 · {4}/{5} 路",deep,V(search,"total","0"),Convert.ToDouble(V(search,"nodes_per_second","0")),V(search,"elapsed_seconds"),V(search,"workers"),V(search,"target_workers",V(search,"workers")))+
                (tail.Count>0?" · 尾段 "+V(tail,"tail_workers","0")+" 路":"")+
                (front.Count>0?"\n续搜 "+V(front,"workers","0")+" 路 · 断点 "+Range(front,"fixed_min","fixed_max")+" 步":"")+
                (tail.Count>0&&Convert.ToInt32(V(tail,"tail_workers","0"))>0?"\n固定前 "+Range(tail,"fixed_prefix_min","fixed_prefix_max")+" 步 · 回退 "+Range(tail,"backtrack_min","backtrack_max")+" 步":(front.Count>0?"\n已处理 "+V(front,"completed_subtrees")+" 段 · 待搜 "+V(front,"pending_branches")+" 个分支":V(search,"rules")=="energy_alien_v1"?"\n电量与种子飞碟精确规则 · 当前单路":"\n正在收集不同的候选路线"))+
                (res.Count>0?"\nCPU 总 "+V(res,"system_cpu_percent")+"% / 搜索 "+V(res,"solver_cpu_percent")+"% · "+V(res,"solver_memory_mb")+" MB":"")+
                "\n"+(res.Count>0?"可用内存 "+V(res,"available_memory_gb")+" GB · ":"")+(dead.Count>0?"缓存跳过 "+V(dead,"hits","0")+" 次":"深度不是完成比例");
        }else searchCaption=state=="waiting_seed"?"种子解出后自动求解，无需手动操作":"答案与棋盘会自动核对，无需手动选择关次";
        int next=Convert.ToInt32(V(ps,"next_step","0")),steps=Convert.ToInt32(V(ps,"total","1")),playMax=steps,playValue=next;
        string stepCaption=ps.Count>0?"已确认 "+next+" / "+steps+" 步 · "+(state=="learning"?"采样新牌型":state=="paused"?"已暂停":state=="blocked"?"已停止，原因见上方":"自动执行并核对"):"执行进度 · 棋盘就绪后自动开始";
        if(state=="prop_ready")stepCaption="执行未启用 · 返回后核对起点与移出区";
        if(V(ps,"execution_kind")=="rolling_segment_v1"){
            int count=Convert.ToInt32(V(ps,"board_total","1")),left=Convert.ToInt32(V(ps,"remaining_cards","0"));
            playMax=count;playValue=count-left;stepCaption="已确认 "+next+" 次点击 · 棋盘剩 "+left+" 张";
        }
        // Compute the final display first. Each control receives at most one
        // text/style change per snapshot; unchanged values are left alone.
        title.Tone=state=="blocked"||state=="search_limited"||state=="prop_ready"||state=="waiting_input"?Ui.Amber:
            state=="playing"||state=="verifying"||state=="ready"||state=="finished"||state=="learning"?Ui.Green:
            state=="paused"||state=="stopped"||state=="disconnected"||state=="connecting"?Ui.Gray:Ui.Blue;
        SetText(title,text);SetText(message,mainMessage);
        var chipList=new List<string>{game};if(level>0)chipList.Add("第 "+level+" / "+V(d,"map_count")+" 关");
        if(V(d,"rules")=="color_shuffle")chipList.Add("消除后变色");else if(V(d,"rules")=="energy_alien")chipList.Add("电量与飞碟");
        chipList.Add(constraint||V(search,"engine")=="static_constraint_v1"?"整局约束求解":original?"原版回溯":rolling?"边算边玩":"完整路线");
        if(V(ps,"rules")=="energy_alien_v1")chipList.Add(B(ps,"alien_pending")?"等待飞碟":Convert.ToInt32(V(ps,"alien_activations","0"))>0?"飞碟已召唤":"电量 "+V(ps,"energy_count")+" / "+V(ps,"collect_count"));
        chips.Set(chipList);

        // Pipeline dots: 0 done, 1 needs attention, 2 idle, 3 in progress.
        bool staleMap=state=="waiting_new_map",transportDown=d.ContainsKey("transport_ready")&&!B(d,"transport_ready");
        int maps=Convert.ToInt32(V(d,"maps_received","0")),mapCount=Convert.ToInt32(V(d,"map_count","0"));bool decrypting=V(d,"capture_phase")=="waiting_for_seed_decryption";
        pipeline.Set(0,B(d,"service_running")?(transportDown?"待恢复":V(d,"game_requests","0")+" 次请求"):"未启用",B(d,"service_running")?(transportDown?1:0):2);
        pipeline.Set(1,staleMap?"等待本局":B(d,"seed_received")?"已取得":decrypting?"解密中":"等待",staleMap?1:decrypting?3:B(d,"seed_received")?0:2);
        pipeline.Set(2,staleMap?"等待本局":maps+" / "+mapCount,staleMap?1:mapCount>0&&maps>=mapCount?0:maps>0?3:2);
        bool answered=state!="solving"&&state!="search_limited"&&(searchState=="solved"||searchState=="segment_ready"||B(d,"ready")||state=="playing"||state=="verifying"||state=="finished");
        pipeline.Set(3,answered?"已验证":searchState=="prop_ready"?"需用道具":state=="search_limited"||searchState=="limited"?"未找到":state=="solving"||searchState=="searching"||state=="planning_segment"?"计算中":"等待",
            answered?0:searchState=="prop_ready"||state=="search_limited"||searchState=="limited"?1:state=="solving"||searchState=="searching"||state=="planning_segment"?3:2);
        bool executing=state=="playing"||state=="verifying"||state=="learning"||state=="waiting_target";
        pipeline.Set(4,state=="finished"?"已完成":ps.Count>0&&next>0?"第 "+next+" 步":executing?"进行中":"未开始",state=="finished"?0:executing?3:state=="blocked"&&ps.Count>0?1:2);

        // One progress line: execution once it exists, otherwise the solve.
        string progress,percent="";int barMax=1,barValue=0;ProgressBarStyle barStyle=ProgressBarStyle.Blocks;
        if(ps.Count>0&&state!="solving"&&state!="search_limited"){progress=stepCaption;barMax=playMax;barValue=playValue;percent=(100*Math.Max(0,playValue)/Math.Max(1,playMax))+"%";}
        else if(state=="solving"||searchState=="searching"||state=="planning_segment"){double budgetValue;string budget=double.TryParse(V(search,"budget_seconds",V(settings,"seconds")),out budgetValue)?Math.Round(budgetValue).ToString():"";
            progress="正在计算 · 已用 "+V(search,"elapsed_seconds","0")+" 秒"+(budget!=""?" / "+budget+" 秒":"");barStyle=ProgressBarStyle.Marquee;}
        else if(state=="prop_ready"){progress="一次移出路线 · "+V(search,"prop_operation_count")+" 个操作，需人工执行";barMax=barValue=1;}
        else if(answered){progress="完整路线已验证，等待执行";barMax=barValue=1;}
        else if(state=="search_limited"||searchState=="limited"){progress="本轮预算已用完，未执行任何步骤";}
        else progress="棋盘就绪后自动开始";
        SetText(stepText,progress);SetText(stepValue,percent);SetProgress(playBar,barMax,barValue,barStyle);
        SetText(searchText,searchCaption+(ps.Count>0?"\n"+stepCaption:""));

        // A single primary action that follows the state; stop is secondary.
        string primaryText;bool primaryEnabled=true;primaryAction="start";
        if(state=="prop_ready"){primaryText="道具路线需人工操作";primaryEnabled=false;}
        else if(running){primaryText="暂停托管";primaryAction="pause";}
        else if(state=="paused")primaryText="继续托管";
        else primaryText=state=="search_limited"?"重新求解":state=="blocked"||state=="stopped"?"重新核对并继续":B(d,"can_resume")?"从第 "+(next+1)+" 步继续":"开始托管";
        SetText(primary,primaryText);primary.Enabled=primaryEnabled;primary.Main=!running;
        stop.Enabled=running||state=="paused"||B(d,"busy");refresh.Enabled=!running&&!B(d,"busy");propGuide.Visible=state=="prop_ready";
        suppress=true;try{if(settings.Count>0){
            int p=Math.Max(0,Math.Min(2,Convert.ToInt32(V(settings,"performance","2")))),s=Math.Max(0,Math.Min(2,Convert.ToInt32(V(settings,"click_speed","1"))));decimal sec=Math.Max(30,Math.Min(300,Convert.ToDecimal(V(settings,"seconds","180"))));
            if(performance.SelectedIndex!=p)performance.SelectedIndex=p;if(speed.SelectedIndex!=s)speed.SelectedIndex=s;if(seconds.Value!=sec)seconds.Value=sec;
        }}finally{suppress=false;}
        suppress=true;try{
            string[] labels=constraint?new string[]{"整局约束 · 单路","整局约束 · 半数核心","整局约束 · 全部核心"}:original?new string[]{"原版 Python · 单路","原版分支 · 多核","原版分支 · 全速"}:new string[]{"省资源 · 单路","快速 · 自动并行","全速 · 空闲资源"};
            for(int i=0;i<performance.Items.Count;i++)if(Convert.ToString(performance.Items[i])!=labels[i])performance.Items[i]=labels[i];
        }finally{suppress=false;}
        string image=V(d,"preview_path");if(image!=""&&File.Exists(image)){DateTime stamp=File.GetLastWriteTimeUtc(image);if(image!=lastImage||stamp!=lastImageTime){board.LoadPath(image);lastImage=image;lastImageTime=stamp;}SetText(caption,"棋盘 "+stamp.ToLocalTime().ToString("HH:mm:ss")+" · 红框为下一步");}
        string phase=state=="verifying"?"核对点击结果":state=="playing"?"标注并点击目标":text;
        SetText(heartbeat,"后台在线 · "+DateTime.Now.ToString("HH:mm:ss")+" · "+phase);
        string key=state+"|"+mainMessage;if(key!=lastEvent&&state!="playing"&&state!="verifying"&&state!="identifying"){Event(text+"："+mainMessage);lastEvent=key;}
    }
    void Event(string text){events.AppendLine(DateTime.Now.ToString("HH:mm:ss")+"  "+text);if(events.Length>30000)events.Remove(0,15000);}
    void OpenPropGuide(){string solution=V(D(snapshot,"search"),"prop_solution_path");if(solution=="")return;string file=Path.Combine(Path.GetDirectoryName(Path.GetFullPath(Path.Combine(root,solution))),"使用移出路线说明.md");if(File.Exists(file))Process.Start(new ProcessStartInfo{FileName=file,UseShellExecute=true});}
    // Settings mirror the hidden controls, which stay the single source sent to the controller.
    void ShowSettings(){using(var form=new Form{Text="设置",StartPosition=FormStartPosition.CenterParent,Font=Font,BackColor=Ui.Card,FormBorderStyle=FormBorderStyle.FixedDialog,MaximizeBox=false,MinimizeBox=false,ShowInTaskbar=false}){
        float scale=DeviceDpi/96f;form.ClientSize=new Size((int)(600*scale),(int)(560*scale));
        var panel=new StableLayout{Dock=DockStyle.Fill,ColumnCount=2,Padding=new Padding(24,20,24,20),BackColor=Ui.Card};panel.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,110));panel.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));
        Rows(panel,-30,-40,-40,-40,-22,-30,100,-48);form.Controls.Add(panel);
        var head=TextLabel("托管与求解",11,true);panel.Controls.Add(head,0,0);panel.SetColumnSpan(head,2);
        var speedCopy=new ComboBox{DropDownStyle=ComboBoxStyle.DropDownList,Anchor=AnchorStyles.Left|AnchorStyles.Right};foreach(var item in speed.Items)speedCopy.Items.Add(item);speedCopy.SelectedIndex=speed.SelectedIndex;speedCopy.SelectedIndexChanged+=delegate{speed.SelectedIndex=speedCopy.SelectedIndex;};
        var perfCopy=new ComboBox{DropDownStyle=ComboBoxStyle.DropDownList,Anchor=AnchorStyles.Left|AnchorStyles.Right};foreach(var item in performance.Items)perfCopy.Items.Add(item);perfCopy.SelectedIndex=performance.SelectedIndex;perfCopy.SelectedIndexChanged+=delegate{performance.SelectedIndex=perfCopy.SelectedIndex;};
        var secCopy=new NumericUpDown{Minimum=seconds.Minimum,Maximum=seconds.Maximum,Increment=seconds.Increment,Value=seconds.Value,Anchor=AnchorStyles.Left,Width=(int)(110*scale)};secCopy.ValueChanged+=delegate{seconds.Value=secCopy.Value;};
        string[] names={"点击速度","求解性能","每轮预算（秒）"};Control[] inputs={speedCopy,perfCopy,secCopy};
        for(int i=0;i<3;i++){var label=TextLabel(names[i],9.5f);label.ForeColor=muted;panel.Controls.Add(label,0,i+1);panel.Controls.Add(inputs[i],1,i+1);}
        var logHead=TextLabel("运行记录",11,true);panel.Controls.Add(logHead,0,5);panel.SetColumnSpan(logHead,2);
        var log=new TextBox{Dock=DockStyle.Fill,Multiline=true,ReadOnly=true,ScrollBars=ScrollBars.Vertical,Text=events.ToString(),BorderStyle=BorderStyle.FixedSingle,BackColor=Ui.Soft,ForeColor=ink,Font=Ui.F(8.75f)};panel.Controls.Add(log,0,6);panel.SetColumnSpan(log,2);
        var restore=new FlatButton();ButtonStyle(restore,"停止抓取并恢复代理与证书");restore.Margin=new Padding(0,10,0,0);restore.Click+=delegate{Send("restore");form.Close();};panel.Controls.Add(restore,0,7);panel.SetColumnSpan(restore,2);
        ScaleTree(panel,scale);form.ShowDialog(this);}}
    async void OnClosing(object sender,FormClosingEventArgs e){if(closeReady)return;e.Cancel=true;if(closing)return;Send("shutdown");closing=true;timer.Stop();Enabled=false;
        await Task.Run(()=>{string lease=Path.Combine(root,"output","workflow","lease.json");for(int i=0;i<180;i++){try{if(!File.Exists(lease))break;var live=Read(lease);if(!B(live,"running"))break;try{if(Process.GetProcessById(Convert.ToInt32(live["pid"])).HasExited)break;}catch(ArgumentException){break;}}catch{}Thread.Sleep(250);}});closeReady=true;Close();}
    protected override bool ShowWithoutActivation{get{return true;}}
    void Demo(){var ps=new Dictionary<string,object>{{"next_step",43},{"total",234},{"state","stopped"}};var s=new Dictionary<string,object>{{"state","solved"},{"total",234},{"deepest",234},{"nodes_per_second",3200},{"elapsed_seconds",.3},{"workers",2}};Apply(new Dictionary<string,object>{{"state","ready"},{"message","当前棋盘与第 43 步记录一致，隐藏牌露出后自动学习。开始后会从第 44 步接着玩。"},{"mode","daily"},{"level",1},{"map_count",2},{"maps_received",2},{"seed_received",true},{"service_running",true},{"game_requests",37},{"ready",true},{"can_resume",true},{"state_seconds",1},{"playback",ps},{"search",s}});string image=Path.Combine(root,"output","auto_capture","sessions","20261008T162423Z-e73fea","level_1_play","last_verified.png");if(File.Exists(image))board.LoadPath(image);heartbeat.Text="界面预览 · 使用历史断点数据，不启用抓取或点击";}
    void DemoSearch(){var s=new Dictionary<string,object>{{"state","searching"},{"total",258},{"deepest",228},{"nodes_per_second",4500000},{"elapsed_seconds",38.7},{"workers",12},{"target_workers",14},{"rules","color_shuffle_v1"},{"round",4},{"dead_cache",new Dictionary<string,object>{{"states",150000},{"hits",73182}}},{"frontier_search",new Dictionary<string,object>{{"workers",3},{"fixed_min",190},{"fixed_max",223},{"completed_subtrees",831},{"pending_branches",2517}}},{"tail_search",new Dictionary<string,object>{{"tail_workers",9},{"fixed_prefix_min",135},{"fixed_prefix_max",219},{"backtrack_min",12},{"backtrack_max",96}}},{"resources",new Dictionary<string,object>{{"system_cpu_percent",86.5},{"solver_cpu_percent",54.8},{"solver_memory_mb",2340},{"available_memory_gb",7.62}}}};Apply(new Dictionary<string,object>{{"state","solving"},{"message","断点续搜，已处理 831 段；已排除 28 个尾段前缀。"},{"mode","world"},{"level",1},{"maps_received",2},{"map_count",2},{"seed_received",true},{"service_running",true},{"game_requests",37},{"state_seconds",39},{"auto_enabled",true},{"search",s}});heartbeat.Text="资源界面预览 · 示例数据，不启用抓取或点击";}
    public void DemoEnergy(){var s=new Dictionary<string,object>{{"state","searching"},{"total",243},{"deepest",198},{"nodes_per_second",34000},{"elapsed_seconds",60},{"workers",1},{"target_workers",1},{"rules","energy_alien_v1"}};Apply(new Dictionary<string,object>{{"state","solving"},{"message","按电量消耗和真实种子飞碟规则搜索；当前还没有完整答案。"},{"mode","world"},{"rules","energy_alien"},{"level",1},{"maps_received",2},{"map_count",2},{"seed_received",true},{"service_running",true},{"game_requests",103},{"state_seconds",60},{"auto_enabled",true},{"search",s}});heartbeat.Text="电量界面预览 · 示例数据，不启用抓取或点击";}
    public void PreviewStatus(string path){Apply(Read(path));SetText(heartbeat,"离线状态界面预览 · 不启用抓取或点击");}
    public void DemoRolling(){var s=new Dictionary<string,object>{{"execution_kind","rolling_segment_v1"},{"state","planning_segment"},{"total",243},{"deepest",28},{"committed_steps",28},{"workers",1},{"tray_capacity",6},{"horizon_target",3},{"lookahead_events",2},{"message","5 格本轮未找到前瞻路线，放宽到 6 格继续验证"}};Apply(new Dictionary<string,object>{{"state","planning_segment"},{"message","正在验证下一段之后还能继续消除，算好即执行。"},{"mode","world"},{"rules","energy_alien"},{"level",1},{"maps_received",2},{"map_count",2},{"seed_received",true},{"service_running",true},{"game_requests",140},{"state_seconds",.5},{"auto_enabled",true},{"search",s}});heartbeat.Text="容量策略界面预览 · 示例数据";}
    public void Render(string path,int width,int height){ShowInTaskbar=false;Opacity=0;StartPosition=FormStartPosition.Manual;Location=new Point(-20000,-20000);Show();Application.DoEvents();float scale;using(var g=CreateGraphics())scale=g.DpiX/96f;Size=new Size((int)(width*scale),(int)(height*scale));PerformLayout();Application.DoEvents();using(var image=new Bitmap(Width,Height)){DrawToBitmap(image,new Rectangle(Point.Empty,Size));using(var final=new Bitmap(width,height))using(var g=Graphics.FromImage(final)){g.InterpolationMode=InterpolationMode.HighQualityBicubic;g.DrawImage(image,0,0,width,height);final.Save(path,ImageFormat.Png);}}Hide();}
}

static class SheepAutoApp {
    static Dictionary<string,object> Read(string path){return new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(File.ReadAllText(path,Encoding.UTF8));}
    static void ReplaceOld(string root,int pid){
        string marker=Path.Combine(root,"output","app_upgrade.json");try{var lease=Read(Path.Combine(root,"output","auto_capture","service_lease.json"));var controller=Read(Path.Combine(root,"output","workflow","lease.json"));var data=new Dictionary<string,object>{{"old_pid",pid},{"controller_pid",controller["pid"]},{"instance",lease["instance"]},{"expires_at",(DateTime.UtcNow-new DateTime(1970,1,1)).TotalSeconds+90}};File.WriteAllText(marker,new JavaScriptSerializer().Serialize(data),new UTF8Encoding(false));using(var old=Process.GetProcessById(pid)){if(!old.ProcessName.StartsWith("羊羊助手"))throw new Exception("更新目标不是羊羊助手");if(!old.CloseMainWindow()||!old.WaitForExit(60000))throw new Exception("旧窗口暂时无法退出，已保留当前抓取");}}finally{if(File.Exists(marker))File.Delete(marker);}
    }
    [STAThread] static int Main(string[] args){Native.SetProcessDPIAware();Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);string root=AppDomain.CurrentDomain.BaseDirectory;
        if(args.Length>=2&&args[0]=="--render-preview"){using(var form=new AutoSheepForm(root,true))form.Render(Path.GetFullPath(args[1]),args.Length>2?int.Parse(args[2]):1120,args.Length>3?int.Parse(args[3]):830);return 0;}
        if(args.Length>=3&&args[0]=="--render-status-preview"){using(var form=new AutoSheepForm(root,true)){form.PreviewStatus(Path.GetFullPath(args[1]));form.Render(Path.GetFullPath(args[2]),args.Length>3?int.Parse(args[3]):1120,args.Length>4?int.Parse(args[4]):830);}return 0;}
        if(args.Length>=2&&args[0]=="--render-energy-preview"){using(var form=new AutoSheepForm(root,true)){form.DemoEnergy();form.Render(Path.GetFullPath(args[1]),args.Length>2?int.Parse(args[2]):1120,args.Length>3?int.Parse(args[3]):830);}return 0;}
        if(args.Length>=2&&args[0]=="--render-rolling-preview"){using(var form=new AutoSheepForm(root,true)){form.DemoRolling();form.Render(Path.GetFullPath(args[1]),args.Length>2?int.Parse(args[2]):1120,args.Length>3?int.Parse(args[3]):830);}return 0;}
        if(args.Length>=2&&args[0]=="--replace")ReplaceOld(root,int.Parse(args[1]));bool auto=Array.IndexOf(args,"--auto")>=0;
        using(var mutex=new Mutex(false,"Local\\SheepSolverApplication")){if(!mutex.WaitOne(0))return 0;try{Application.Run(new AutoSheepForm(root,false,auto));}finally{mutex.ReleaseMutex();}}return 0;
    }
}
