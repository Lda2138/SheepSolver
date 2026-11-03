using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

sealed class BoardPreview : Control {
    public Bitmap Image;public string EmptyTitle="先启用抓取，再进入游戏";
    public string EmptyDetail="完整地图就绪后，在右侧求解并对齐棋盘。";
    public BoardPreview(){DoubleBuffered=true;BackColor=Color.FromArgb(235,241,230);}
    protected override void OnPaint(PaintEventArgs e) {
        base.OnPaint(e);e.Graphics.SmoothingMode=SmoothingMode.AntiAlias;
        if(Image==null) {
            int cy=Height/2-50;using(var pen=new Pen(Color.FromArgb(138,166,134),2))e.Graphics.DrawRectangle(pen,Width/2-28,cy-62,56,62);
            using(var titleFont=new Font("Microsoft YaHei UI",14))TextRenderer.DrawText(e.Graphics,EmptyTitle,titleFont,new Rectangle(20,cy+25,Width-40,40),Color.FromArgb(38,64,46),TextFormatFlags.HorizontalCenter|TextFormatFlags.WordBreak);
            TextRenderer.DrawText(e.Graphics,EmptyDetail,Font,new Rectangle(30,cy+70,Width-60,70),Color.FromArgb(101,121,102),TextFormatFlags.HorizontalCenter|TextFormatFlags.WordBreak);return;
        }
        int pad=12;double scale=Math.Min((Width-2.0*pad)/Image.Width,(Height-2.0*pad)/Image.Height);
        int w=(int)(Image.Width*scale),h=(int)(Image.Height*scale);e.Graphics.InterpolationMode=InterpolationMode.HighQualityBicubic;
        e.Graphics.DrawImage(Image,new Rectangle((Width-w)/2,(Height-h)/2,w,h));
    }
    public void LoadImage(string path) {
        using(var stream=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.ReadWrite))using(var src=System.Drawing.Image.FromStream(stream)){var old=Image;Image=new Bitmap(src);if(old!=null)old.Dispose();}Invalidate();
    }
    public void ClearImage(){if(Image!=null){Image.Dispose();Image=null;}Invalidate();}
    protected override void Dispose(bool disposing){if(disposing&&Image!=null)Image.Dispose();base.Dispose(disposing);}
}

sealed class SheepApplication : Form {
    static readonly Color Ink=Color.FromArgb(32,56,42),Green=Color.FromArgb(43,112,78),Muted=Color.FromArgb(105,121,107),Paper=Color.FromArgb(247,248,241);
    readonly string root;readonly bool demo;readonly JavaScriptSerializer json=new JavaScriptSerializer{MaxJsonLength=4*1024*1024};
    readonly BoardPreview board=new BoardPreview();readonly Label headline=new Label(),subhead=new Label(),hint=new Label(),captureValue=new Label(),mapValue=new Label(),answerValue=new Label(),progressText=new Label(),modeInfo=new Label(),boardCaption=new Label();
    readonly Label footer=new Label(),searchText=new Label(),searchMetrics=new Label();readonly ComboBox levels=new ComboBox(),performance=new ComboBox(),clickSpeed=new ComboBox();readonly ProgressBar progress=new ProgressBar(),searchProgress=new ProgressBar();readonly CheckBox reviewed=new CheckBox();readonly Button cancelSearch=new Button();string searchFolder="";bool solving,searchActive;
    readonly NumericUpDown seconds=new NumericUpDown(),markDelay=new NumericUpDown();readonly NumericUpDown[] calibration=new NumericUpDown[4];
    readonly Button daily=new Button(),world=new Button(),enable=new Button(),restore=new Button(),solve=new Button(),align=new Button(),play=new Button(),pause=new Button(),stop=new Button(),refresh=new Button();
    readonly TextBox log=new TextBox();readonly System.Windows.Forms.Timer poll=new System.Windows.Forms.Timer();
    string mode="daily",session="",preparedPath="",displayImage="",activePlayFolder="";DateTime displayTime;int mapCount=0;bool busy,closing,closeReady,ownsCapture;Process playback;string lastPlaybackState="";Dictionary<string,object> capture;TableLayoutPanel mainLayout;Task activeWork;readonly object commandLock=new object();Process activeCommand;string activeScript="";

    public SheepApplication(string directory,bool preview=false) {
        root=directory;demo=preview;
        // Scale table row/column sizes explicitly: .NET Framework otherwise
        // leaves absolute TableLayoutPanel rows unscaled on this high-DPI host.
        AutoScaleMode=AutoScaleMode.None;Font=new Font("Microsoft YaHei UI",9.5f);
        Text="羊羊助手 · SheepSolver";BackColor=Paper;ForeColor=Ink;StartPosition=FormStartPosition.CenterScreen;
        string iconPath=Path.Combine(root,"assets","sheep.ico");if(File.Exists(iconPath))Icon=new Icon(iconPath);
        Build();float dpiFactor;using(var g=Graphics.FromHwnd(IntPtr.Zero))dpiFactor=g.DpiX/96f;
        ScaleLayout(this,dpiFactor);MinimumSize=new Size((int)(880*dpiFactor),(int)(750*dpiFactor));
        Size=new Size(Math.Min((int)(1120*dpiFactor),Screen.PrimaryScreen.WorkingArea.Width-60),Math.Min((int)(830*dpiFactor),Screen.PrimaryScreen.WorkingArea.Height-50));
        poll.Interval=900;poll.Tick+=delegate{Poll();};
        if(demo)Demo();else {poll.Start();Shown+=delegate{Poll();};FormClosing+=OnClosing;}
        FormClosed+=delegate{poll.Dispose();};
    }
    Label LabelText(string text,float size=9.5f,Color? color=null) {return new Label{Text=text,Font=new Font("Microsoft YaHei UI",size),ForeColor=color??Ink,Dock=DockStyle.Fill,AutoSize=false,TextAlign=ContentAlignment.MiddleLeft,Margin=new Padding(0),AutoEllipsis=true};}
    static Padding ScaledPadding(Padding p,float f){return new Padding((int)(p.Left*f),(int)(p.Top*f),(int)(p.Right*f),(int)(p.Bottom*f));}
    static void ScaleLayout(Control c,float f){
        c.Padding=ScaledPadding(c.Padding,f);c.Margin=ScaledPadding(c.Margin,f);
        var table=c as TableLayoutPanel;if(table!=null){foreach(RowStyle row in table.RowStyles)if(row.SizeType==SizeType.Absolute)row.Height*=f;foreach(ColumnStyle col in table.ColumnStyles)if(col.SizeType==SizeType.Absolute)col.Width*=f;}
        var tabs=c as TabControl;if(tabs!=null)tabs.Padding=new Point((int)(tabs.Padding.X*f),(int)(tabs.Padding.Y*f));
        foreach(Control child in c.Controls)ScaleLayout(child,f);
    }
    static void StyleButton(Button b,string text,bool primary=false) {b.Text=text;b.Dock=DockStyle.Fill;b.FlatStyle=FlatStyle.Flat;b.FlatAppearance.BorderSize=primary?0:1;b.FlatAppearance.BorderColor=Color.FromArgb(208,220,206);b.BackColor=primary?Green:Color.White;b.ForeColor=primary?Color.White:Ink;b.Cursor=Cursors.Hand;b.Margin=new Padding(0,4,0,4);b.Height=40;}
    TableLayoutPanel Stack(){var t=new TableLayoutPanel{Dock=DockStyle.Top,AutoSize=true,ColumnCount=1,Padding=new Padding(13),BackColor=Color.White};t.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));return t;}
    void AddRow(TableLayoutPanel t,Control c,int height){int row=t.RowCount++;t.RowStyles.Add(new RowStyle(SizeType.Absolute,height));t.Controls.Add(c,0,row);c.Dock=DockStyle.Fill;}
    TableLayoutPanel Pair(Control a,Control b,int width=50){var t=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=2,Margin=new Padding(0)};t.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,width));t.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100-width));t.Controls.Add(a,0,0);t.Controls.Add(b,1,0);a.Dock=b.Dock=DockStyle.Fill;a.Margin=new Padding(0,0,5,0);b.Margin=new Padding(5,0,0,0);return t;}
    void Build() {
        mainLayout=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=1,RowCount=4,Padding=new Padding(22,15,22,12)};
        mainLayout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));mainLayout.RowStyles.Add(new RowStyle(SizeType.Absolute,70));mainLayout.RowStyles.Add(new RowStyle(SizeType.Absolute,52));mainLayout.RowStyles.Add(new RowStyle(SizeType.Percent,100));mainLayout.RowStyles.Add(new RowStyle(SizeType.Absolute,40));Controls.Add(mainLayout);
        var header=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=2,Margin=new Padding(0)};header.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,65));header.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,35));
        var title=LabelText("羊羊助手",23);title.Font=new Font("Microsoft YaHei UI",23,FontStyle.Bold);header.Controls.Add(title,0,0);
        var tagline=LabelText("完整地图 · 原项目策略 · 红框点击",9.5f,Muted);tagline.TextAlign=ContentAlignment.MiddleRight;header.Controls.Add(tagline,1,0);mainLayout.Controls.Add(header,0,0);
        var modes=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=3,Margin=new Padding(0,0,0,10)};modes.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,145));modes.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,170));modes.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));
        StyleButton(daily,"每日一关",true);StyleButton(world,"羊羊大世界");daily.Click+=delegate{SelectMode("daily");};world.Click+=delegate{SelectMode("world");};modes.Controls.Add(daily,0,0);modes.Controls.Add(world,1,0);modeInfo.Text="进入微信对应模式后，自动识别新一局";modeInfo.ForeColor=Muted;modeInfo.Dock=DockStyle.Fill;modeInfo.TextAlign=ContentAlignment.MiddleRight;modes.Controls.Add(modeInfo,2,0);mainLayout.Controls.Add(modes,0,1);
        var body=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=2,Margin=new Padding(0)};body.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,64));body.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,36));mainLayout.Controls.Add(body,0,2);
        var left=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=1,RowCount=4,Margin=new Padding(0,0,14,0),BackColor=Color.White,Padding=new Padding(12)};left.RowStyles.Add(new RowStyle(SizeType.Absolute,48));left.RowStyles.Add(new RowStyle(SizeType.Percent,100));left.RowStyles.Add(new RowStyle(SizeType.Absolute,34));left.RowStyles.Add(new RowStyle(SizeType.Absolute,130));body.Controls.Add(left,0,0);
        var boardHeader=Pair(LabelText("棋盘预览",12),refresh,72);StyleButton(refresh,"刷新棋盘");refresh.Click+=async delegate{await RunCapture();};left.Controls.Add(boardHeader,0,0);board.Dock=DockStyle.Fill;left.Controls.Add(board,0,1);boardCaption.Dock=DockStyle.Fill;boardCaption.Text="绿框核对位置，红框标出下一步";boardCaption.ForeColor=Muted;boardCaption.TextAlign=ContentAlignment.MiddleCenter;left.Controls.Add(boardCaption,0,2);
        var searchPanel=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=1,Padding=new Padding(8,3,8,3),BackColor=Color.FromArgb(242,245,237)};
        performance.DropDownStyle=ComboBoxStyle.DropDownList;performance.Items.AddRange(new object[]{"省资源 · 单路低优先级","快速 · 最多 2 路","全速 · 最多 3 路"});performance.SelectedIndex=2;AddRow(searchPanel,Pair(LabelText("求解性能",9.5f,Muted),performance,35),34);
        searchText.Text="搜索进度 · 等待完整地图";searchText.ForeColor=Ink;StyleButton(cancelSearch,"停止搜索");cancelSearch.Click+=delegate{StopSearch();};cancelSearch.Enabled=false;AddRow(searchPanel,Pair(searchText,cancelSearch,77),32);searchProgress.Margin=new Padding(0,3,0,3);AddRow(searchPanel,searchProgress,18);searchMetrics.Text="搜索深度不代表完成比例或剩余时间";searchMetrics.ForeColor=Muted;searchMetrics.Font=new Font(Font.FontFamily,8.5f);AddRow(searchPanel,searchMetrics,37);left.Controls.Add(searchPanel,0,3);
        var right=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=1,RowCount=3,Margin=new Padding(0)};right.RowStyles.Add(new RowStyle(SizeType.Absolute,154));right.RowStyles.Add(new RowStyle(SizeType.Percent,100));right.RowStyles.Add(new RowStyle(SizeType.Absolute,205));body.Controls.Add(right,1,0);
        var summary=new TableLayoutPanel{Dock=DockStyle.Fill,RowCount=3,ColumnCount=1,BackColor=Color.FromArgb(233,239,222),Padding=new Padding(14,9,14,9),Margin=new Padding(0,0,0,10)};
        summary.RowStyles.Add(new RowStyle(SizeType.Absolute,30));summary.RowStyles.Add(new RowStyle(SizeType.Absolute,54));summary.RowStyles.Add(new RowStyle(SizeType.Percent,100));headline.Dock=DockStyle.Fill;headline.Text="等待新一局";headline.Font=new Font(Font.FontFamily,13,FontStyle.Bold);subhead.Dock=DockStyle.Fill;subhead.Text="启用抓取后，在微信里进入每日一关。";subhead.ForeColor=Muted;hint.Dock=DockStyle.Fill;hint.Text="从完整地图开始，逐步核对点击。";hint.Font=new Font(Font.FontFamily,8.5f);summary.Controls.Add(headline,0,0);summary.Controls.Add(subhead,0,1);summary.Controls.Add(hint,0,2);right.Controls.Add(summary,0,0);
        var tabs=new TabControl{Dock=DockStyle.Fill,Padding=new Point(12,6)};right.Controls.Add(tabs,0,1);TabPage operation=new TabPage("操作"),calibrate=new TabPage("对齐微调"),records=new TabPage("记录");tabs.TabPages.AddRange(new[]{operation,calibrate,records});
        var scroller=new Panel{Dock=DockStyle.Fill,AutoScroll=true,BackColor=Color.White};operation.Controls.Add(scroller);var workflow=Stack();scroller.Controls.Add(workflow);
        AddRow(workflow,LabelText("01  获取当前局",10.5f),26);
        AddRow(workflow,Pair(LabelText("抓取",9.5f,Muted),captureValue,35),26);AddRow(workflow,Pair(LabelText("地图 / 种子",9.5f,Muted),mapValue,35),26);
        StyleButton(enable,"启用自动抓取",true);StyleButton(restore,"停止并恢复设置");enable.Click+=async delegate{await CaptureStart();};restore.Click+=async delegate{await CaptureStop();};AddRow(workflow,Pair(enable,restore),45);
        AddRow(workflow,LabelText("02  求解与对齐",10.5f),32);
        levels.DropDownStyle=ComboBoxStyle.DropDownList;levels.Items.Add("等待地图");levels.SelectedIndex=0;levels.SelectedIndexChanged+=delegate{if(!demo){ResetPrepared();Poll();}};AddRow(workflow,Pair(LabelText("当前关次",9.5f,Muted),levels,35),32);
        seconds.Minimum=30;seconds.Maximum=300;seconds.Increment=30;seconds.Value=180;seconds.TextAlign=HorizontalAlignment.Center;AddRow(workflow,Pair(LabelText("搜索上限（秒）",9.5f,Muted),seconds,58),30);
        performance.SelectedIndexChanged+=delegate{SavePreferences();};seconds.ValueChanged+=delegate{SavePreferences();};
        StyleButton(solve,"求解此关");StyleButton(align,"自动对齐");solve.Click+=async delegate{await Solve();};align.Click+=async delegate{await Align(false);};AddRow(workflow,Pair(solve,align),45);
        answerValue.ForeColor=Muted;answerValue.Text="尚未生成完整答案";AddRow(workflow,answerValue,28);
        var execution=Stack();execution.Dock=DockStyle.Fill;execution.AutoSize=false;execution.Padding=new Padding(12,5,12,2);execution.Margin=new Padding(0,8,0,0);right.Controls.Add(execution,0,2);
        clickSpeed.DropDownStyle=ComboBoxStyle.DropDownList;clickSpeed.Items.AddRange(new object[]{"稳妥 · 约 2 秒/步","快速 · 约 1.2 秒/步","极速 · 动态核对","自定义红框停留"});clickSpeed.SelectedIndex=1;AddRow(execution,Pair(LabelText("点击速度",9,Muted),clickSpeed,32),34);clickSpeed.SelectedIndexChanged+=delegate{SavePreferences();};
        reviewed.Text="已核对预览中的牌框位置";reviewed.AutoSize=false;reviewed.CheckedChanged+=delegate{SetButtons();};AddRow(execution,reviewed,28);
        StyleButton(play,"开始后台点击",true);play.Click+=delegate{StartPlayback();};AddRow(execution,play,43);
        StyleButton(pause,"暂停 / 继续");StyleButton(stop,"停止点击");pause.Click+=delegate{Command(lastPlaybackState=="paused"?"resume":"pause");};stop.Click+=delegate{Command("stop");};AddRow(execution,Pair(pause,stop),43);
        progress.Dock=DockStyle.Fill;progress.Margin=new Padding(0,4,0,3);AddRow(execution,progress,17);progressText.ForeColor=Muted;progressText.Text="未开始 · F8 暂停，F9 / Esc 停止";AddRow(execution,progressText,29);
        var calScroller=new Panel{Dock=DockStyle.Fill,AutoScroll=true,BackColor=Color.White};calibrate.Controls.Add(calScroller);var calStack=Stack();calScroller.Controls.Add(calStack);
        AddRow(calStack,LabelText("自动对齐后可微调",11),30);AddRow(calStack,LabelText("框没有贴住牌面时，调整下方参数，再点击“应用微调”。",9,Muted),65);
        string[] names={"横向起点","纵向起点","横向单位","纵向单位"};decimal[] initial={47.5m,137m,4.32m,4.25m};
        for(int i=0;i<4;i++){calibration[i]=new NumericUpDown{DecimalPlaces=2,Minimum=i<2?0:2,Maximum=i<2?250:8,Increment=i<2?.5m:.01m,Value=initial[i],TextAlign=HorizontalAlignment.Center};AddRow(calStack,Pair(LabelText(names[i]),calibration[i],52),38);}
        var apply=new Button();StyleButton(apply,"应用微调并重新预览");apply.Click+=async delegate{await Align(true);};AddRow(calStack,apply,46);
        markDelay.Minimum=300;markDelay.Maximum=3000;markDelay.Increment=100;markDelay.Value=650;AddRow(calStack,Pair(LabelText("红框停留（毫秒）"),markDelay,63),40);AddRow(calStack,LabelText("微调只改变窗口坐标。对齐时需保持空牌槽、尚未点牌的开局。",9,Muted),76);
        log.Multiline=true;log.ReadOnly=true;log.ScrollBars=ScrollBars.Vertical;log.Dock=DockStyle.Fill;log.BorderStyle=BorderStyle.None;log.BackColor=Color.White;log.ForeColor=Muted;log.Font=new Font("Microsoft YaHei UI",9);log.Margin=new Padding(8);records.Padding=new Padding(10);records.Controls.Add(log);
        footer.Dock=DockStyle.Fill;footer.ForeColor=Muted;footer.Text="后台点击不移动鼠标、不切换窗口。关闭应用会停止点击并恢复本次抓取设置。";footer.TextAlign=ContentAlignment.MiddleLeft;mainLayout.Controls.Add(footer,0,3);
        LoadPreferences();SetButtons();
    }
    Dictionary<string,object> Read(string path){return json.Deserialize<Dictionary<string,object>>(File.ReadAllText(path,Encoding.UTF8));}
    string Value(Dictionary<string,object> d,string key,string fallback=""){return d!=null&&d.ContainsKey(key)?Convert.ToString(d[key]):fallback;}
    bool Flag(Dictionary<string,object> d,string key){return d!=null&&d.ContainsKey(key)&&Convert.ToBoolean(d[key]);}
    string Folder(){return Path.Combine(root,"output","auto_capture","sessions",session);}
    int Level(){return Math.Max(0,levels.SelectedIndex);}
    string PlayFolder(){return Path.Combine(Folder(),"level_"+Level()+"_play");}
    bool Playing(){return playback!=null&&!playback.HasExited;}
    bool ServiceRunning() {
        try {var lease=Read(Path.Combine(root,"output","auto_capture","service_lease.json"));if(!Flag(lease,"running"))return false;return !Process.GetProcessById(Convert.ToInt32(lease["pid"])).HasExited;}catch{return false;}
    }
    bool Matches(){return capture!=null&&Value(capture,"source_path")== (mode=="world"?"/sheep/v1/game/world/game_start":"/sheep/v1/game/map_info_ex");}
    void SetButtons() {
        if(demo||IsDisposed)return;
        bool running=ServiceRunning(),playing=Playing(),ready=running&&Flag(capture,"complete")&&Matches();
        enable.Enabled=!busy&&!running;restore.Enabled=!busy&&running&&!playing;solve.Enabled=!busy&&!playing&&ready&&!searchActive;align.Enabled=!busy&&!playing&&ready;
        play.Enabled=!busy&&!playing&&ready&&preparedPath!=""&&reviewed.Checked;pause.Enabled=stop.Enabled=playing;
        levels.Enabled=seconds.Enabled=performance.Enabled=clickSpeed.Enabled=daily.Enabled=world.Enabled=!busy&&!playing;refresh.Enabled=!busy&&!playing;
    }
    void SelectMode(string next) {
        if(Playing())return;mode=next;daily.BackColor=mode=="daily"?Green:Color.White;daily.ForeColor=mode=="daily"?Color.White:Ink;world.BackColor=mode=="world"?Green:Color.White;world.ForeColor=mode=="world"?Color.White:Ink;ResetPrepared();Poll();
    }
    void ResetPrepared(){preparedPath="";reviewed.Checked=false;progress.Value=0;progressText.Text="未开始 · F8 暂停，F9 / Esc 停止";displayImage="";board.ClearImage();}
    void Poll() {
        if(demo||closing)return;if(busy){if(solving)PollSearch();return;}
        try {
            string statusPath=Path.Combine(root,"output","auto_capture","status.json");capture=File.Exists(statusPath)?Read(statusPath):null;
            bool running=ServiceRunning();string next=Value(capture,"session");
            if(next!=session){session=next;mapCount=0;ResetPrepared();}
            int count=capture!=null&&capture.ContainsKey("map_count")?Convert.ToInt32(capture["map_count"]):0;
            if(count!=mapCount){mapCount=count;levels.Items.Clear();if(count==0)levels.Items.Add("等待地图");else for(int i=0;i<count;i++)levels.Items.Add("第 "+(i+1)+" 关"+(i==0&&count>1?" · 起始关":""));levels.SelectedIndex=0;}
            captureValue.Text=running?"正在监听":"未启用";captureValue.ForeColor=running?Green:Muted;
            mapValue.Text=running?Value(capture,"complete_maps","0")+" / "+count+"  · "+(Flag(capture,"seed_received")?"种子已取得":"等待种子"):"等待新一局";
            bool match=Matches();string actual=Value(capture,"source_path").Contains("/world/")?"羊羊大世界":"每日一关";
            modeInfo.Text=running&&session!=""?"实际捕获："+actual:"选择模式，再进入微信对应入口";
            headline.Text=!running?"准备开始":!match&&session!=""?"请切换到捕获的模式":Flag(capture,"complete")?"完整地图已就绪":Value(capture,"phase")=="waiting_for_seed_decryption"?"等待真实种子":"等待开局数据";
            subhead.Text=!running?"启用抓取后，在微信里进入"+(mode=="world"?"羊羊大世界":"每日一关")+"。":Value(capture,"message","进入游戏后会自动获取地图。");
            if(running&&Value(capture,"phase")=="waiting_for_seed_decryption") {subhead.Text="地图结构已收到，种子仍为密文。\n真实种子解出后才能开始求解。";hint.Text="请正常结束一局，再开新局；保持小程序与抓取运行。";hint.ForeColor=Color.FromArgb(153,102,31);}else{hint.Text=running&&match?"请选择正在玩的关次，求解后对齐棋盘。":"历史答案不会用于新的棋盘。";hint.ForeColor=Muted;}
            if(session!="") {
                string solutionPath=Path.Combine(Folder(),"level_"+Level()+"_solution","solution.json");if(!File.Exists(solutionPath)&&Level()==count-1)solutionPath=Path.Combine(Folder(),"solution","solution.json");
                if(File.Exists(solutionPath)){var s=Read(solutionPath);answerValue.Text=Flag(s,"validated")?"完整答案已验证 · "+Value(s,"card_count")+" 步":"尚未验证";}else answerValue.Text=Value(capture,"solver")=="running"?"后台正在搜索完整答案…":"尚未生成此关答案";
                string playbackStatus=Path.Combine(Playing()?activePlayFolder:PlayFolder(),"playback_status.json");
                if(Playing()&&File.Exists(playbackStatus)) {
                    var ps=Read(playbackStatus);lastPlaybackState=Value(ps,"state");int step=Convert.ToInt32(ps["next_step"]),total=Convert.ToInt32(ps["total"]);
                    progress.Maximum=Math.Max(1,total);progress.Value=Math.Min(step,total);progressText.Text=step+" / "+total+" 步 · "+(lastPlaybackState=="paused"?"已暂停":"逐步核对中");
                    headline.Text=lastPlaybackState=="paused"?"后台点击已暂停":"正在按答案点击";subhead.Text="目标牌："+Value(ps,"message")+"\n每步先显示红框，再核对牌面与牌槽。";
                    pause.Text=lastPlaybackState=="paused"?"继续点击":"暂停点击";Display(Path.Combine(activePlayFolder,lastPlaybackState=="marking"?"next_click.png":"last_verified.png"));
                }
            }
            if(playback!=null&&playback.HasExited) {
                string psPath=Path.Combine(activePlayFolder,"playback_status.json");if(File.Exists(psPath)){var ps=Read(psPath);int step=Convert.ToInt32(ps["next_step"]),total=Convert.ToInt32(ps["total"]);progress.Maximum=Math.Max(1,total);progress.Value=Math.Min(step,total);progressText.Text=step+" / "+total+" 步 · "+(step==total?"答案已执行":"已停止");AppendLog(Value(ps,"message"));subhead.Text=Value(ps,"message");Display(Path.Combine(activePlayFolder,"last_verified.png"));}
                playback.Dispose();playback=null;preparedPath="";reviewed.Checked=false;
            }
            PollSearch();SetButtons();
        }catch(IOException){}catch(ArgumentException){}catch(Exception e){footer.Text=e.Message;}
    }
    void Display(string path) {if(!File.Exists(path))return;DateTime time=File.GetLastWriteTimeUtc(path);if(path==displayImage&&time==displayTime)return;try{board.LoadImage(path);displayImage=path;displayTime=time;}catch(IOException){}catch(ArgumentException){}}
    void AppendLog(string message){if(IsDisposed||string.IsNullOrWhiteSpace(message))return;log.AppendText(DateTime.Now.ToString("HH:mm:ss")+"  "+message+Environment.NewLine);}
    static string Q(string text){return "\""+text.Replace("\"","\\\"")+"\"";}
    async Task<string> Execute(string script,string args,int timeout=360) {
        return await Task.Run(()=>{
            var start=new ProcessStartInfo{FileName=Path.Combine(root,"venv","Scripts","python.exe"),Arguments=Q(Path.Combine(root,script))+" "+args,WorkingDirectory=root,CreateNoWindow=true,UseShellExecute=false,RedirectStandardOutput=true,RedirectStandardError=true,StandardOutputEncoding=Encoding.UTF8,StandardErrorEncoding=Encoding.UTF8};start.EnvironmentVariables["PYTHONIOENCODING"]="utf-8";
            using(var p=Process.Start(start)) {lock(commandLock){activeCommand=p;activeScript=script;}
                try{var output=p.StandardOutput.ReadToEndAsync();var error=p.StandardError.ReadToEndAsync();if(!p.WaitForExit(timeout*1000))throw new Exception("操作超时，请检查记录");Task.WaitAll(output,error);if(p.ExitCode!=0)throw new Exception(string.IsNullOrWhiteSpace(error.Result)?output.Result:error.Result);return output.Result;}
                finally{lock(commandLock){if(activeCommand==p){activeCommand=null;activeScript="";}}}
            }
        });
    }
    async Task Work(string message,Func<Task> action) {
        if(busy)return;busy=true;SetButtons();headline.Text=message;AppendLog(message);
        try{activeWork=action();await activeWork;}catch(Exception e){AppendLog(e.Message.Trim());subhead.Text=e.Message.Trim();footer.Text="操作未完成，详情见“记录”。";}finally{activeWork=null;busy=false;SetButtons();}
    }
    async Task CaptureStart(){await Work("正在启用抓取…",async delegate{await Execute("capture_service.py","start --enable-proxy --trust-ca",40);ownsCapture=true;session="";ResetPrepared();AppendLog("抓取已启用，请在微信进入新一局。首次种子未取得时，按状态提示正常结束一局后再开局。");Poll();});}
    async Task CaptureStop(){await Work("正在恢复临时设置…",async delegate{await Execute("capture_service.py","stop",30);ownsCapture=false;ResetPrepared();Poll();AppendLog("抓取已停止，代理与证书按原配置恢复。");});}
    string Arguments(){return "--mode "+mode+" --session "+Q(session)+" --level "+Level();}
    async Task Solve(){
        if(busy)return;
        searchFolder=Path.Combine(Folder(),"level_"+Level()+"_solution");solving=true;searchProgress.Style=ProgressBarStyle.Marquee;searchText.Text="搜索正在启动…";searchMetrics.Text="原项目策略 · 找到完整答案即结束";cancelSearch.Enabled=true;
        // Clear stale progress before the new command starts.
        string old=Path.Combine(searchFolder,"search_status.json");if(File.Exists(old))File.Delete(old);
        try{await Work("正在求解完整答案…",async delegate{await Execute("app_bridge.py","solve "+Arguments()+" --seconds "+(int)seconds.Value+" --performance "+PerformanceKey(),(int)seconds.Value+35);AppendLog("完整答案已生成并通过规则回放。下一步：自动对齐。");});}
        finally{solving=false;PollSearch();cancelSearch.Enabled=false;searchProgress.Style=ProgressBarStyle.Continuous;SetButtons();}
    }
    string PerformanceKey(){return performance.SelectedIndex==0?"eco":performance.SelectedIndex==1?"fast":"max";}
    void SavePreferences(){if(demo||clickSpeed.SelectedIndex<0||performance.SelectedIndex<0)return;try{string p=Path.Combine(root,"auto_capture_config.json");var settings=File.Exists(p)?Read(p):new Dictionary<string,object>();settings["solve_performance"]=PerformanceKey();settings["solve_time_limit"]=(int)seconds.Value;File.WriteAllText(p,json.Serialize(settings),new UTF8Encoding(false));File.WriteAllText(Path.Combine(root,"output","app_preferences.json"),json.Serialize(new Dictionary<string,object>{{"performance",performance.SelectedIndex},{"seconds",seconds.Value},{"click_speed",clickSpeed.SelectedIndex}}),new UTF8Encoding(false));}catch(IOException){}}
    void LoadPreferences(){if(demo)return;try{string p=Path.Combine(root,"output","app_preferences.json");if(File.Exists(p)){var d=Read(p);performance.SelectedIndex=Math.Max(0,Math.Min(2,Convert.ToInt32(d["performance"])));seconds.Value=Math.Max(30,Math.Min(300,Convert.ToDecimal(d["seconds"])));clickSpeed.SelectedIndex=Math.Max(0,Math.Min(3,Convert.ToInt32(d["click_speed"])));}}catch(Exception){}SavePreferences();}
    void StopSearch(){try{if(searchFolder!=""){Directory.CreateDirectory(searchFolder);File.WriteAllText(Path.Combine(searchFolder,"search_control.json"),"{\"action\":\"stop\"}");cancelSearch.Enabled=false;AppendLog("已请求停止搜索。");}}catch(IOException){}}
    void PollSearch(){
        try{
            if(session=="")return;string folder=solving?searchFolder:Path.Combine(Folder(),"level_"+Level()+"_solution");string path=Path.Combine(folder,"search_status.json");
            if(!File.Exists(path)&&!solving&&Level()==mapCount-1){folder=Path.Combine(Folder(),"solution");path=Path.Combine(folder,"search_status.json");}
            if(!File.Exists(path)){searchActive=false;if(!solving){searchProgress.Style=ProgressBarStyle.Continuous;searchProgress.Value=0;searchText.Text=Flag(capture,"complete")?"搜索进度 · 等待求解":"搜索进度 · 等待真实种子与地图";searchMetrics.Text="搜索深度不代表完成比例或剩余时间";cancelSearch.Enabled=false;}return;}
            var s=Read(path);if(Value(s,"session")!=session)return;string state=Value(s,"state");bool running=state=="starting"||state=="searching"||state=="validating";
            // Ignore abandoned search status left by a crashed/closed process.
            if(running&&!solving&&DateTime.UtcNow-File.GetLastWriteTimeUtc(path)>TimeSpan.FromSeconds(12))running=false;searchActive=running;
            searchFolder=folder;cancelSearch.Enabled=running;int total=Convert.ToInt32(s["total"]),deep=Convert.ToInt32(s["deepest"]);searchProgress.Style=ProgressBarStyle.Continuous;searchProgress.Maximum=Math.Max(1,total);searchProgress.Value=Math.Min(deep,total);
            string label=state=="solved"?"完整答案已验证":state=="cancelled"?"搜索已停止":state=="limited"?"未找到完整答案":state=="error"?"搜索出错":state=="validating"?"正在验证答案":"搜索深度 "+deep+" / "+total;
            searchText.Text=label;searchMetrics.Text=string.Format("{0:N0} 节点 · {1:N0}/秒 · {2:F1} 秒 · {3} 路\n搜索深度不代表完成比例或剩余时间",Convert.ToInt64(s["nodes"]),Convert.ToDouble(s["nodes_per_second"]),Convert.ToDouble(s["elapsed_seconds"]),Value(s,"workers"));
            if(running&&Matches()&&!Playing()){headline.Text="正在搜索完整答案";subhead.Text="原项目策略并行搜索，找到答案后独立验证。";}
        }catch(IOException){}catch(ArgumentException){}catch(KeyNotFoundException){}
    }
    async Task Align(bool manual){await Work("正在对齐棋盘…",async delegate{
        string args="prepare "+Arguments();if(manual){var cal=new Dictionary<string,object>{{"x_origin",calibration[0].Value},{"y_origin",calibration[1].Value},{"x_unit",calibration[2].Value},{"y_unit",calibration[3].Value}};string cp=Path.Combine(root,"output","manual_calibration.json");File.WriteAllText(cp,json.Serialize(cal),Encoding.UTF8);args+=" --calibration "+Q(cp);}
        await Execute("app_bridge.py",args,35);var prepared=Read(Path.Combine(PlayFolder(),"prepared.json"));preparedPath=Value(prepared,"plan_path");var c=(Dictionary<string,object>)prepared["calibration"];string[] keys={"x_origin","y_origin","x_unit","y_unit"};for(int i=0;i<4;i++)calibration[i].Value=Math.Max(calibration[i].Minimum,Math.Min(calibration[i].Maximum,Convert.ToDecimal(c[keys[i]])));
        reviewed.Checked=false;Display(Value(prepared,"preview_path"));boardCaption.Text="绿框应贴住可见牌；红框为第一步";AppendLog(Value(prepared,"message"));subhead.Text=Value(prepared,"message");
    });}
    async Task RunCapture(){await Work("正在刷新棋盘…",async delegate{await Task.Run(()=>{var start=new ProcessStartInfo{FileName=Path.Combine(root,"SheepClicker.Diagnostics.exe"),Arguments="--capture",WorkingDirectory=root,CreateNoWindow=true,UseShellExecute=false,RedirectStandardOutput=true,RedirectStandardError=true};using(var p=Process.Start(start)){string error=p.StandardError.ReadToEnd();p.WaitForExit();if(p.ExitCode!=0)throw new Exception(error);}});Display(Path.Combine(root,"output","程序标准化截图.png"));boardCaption.Text="当前微信窗口截图 · 尚未发送点击";});}
    void StartPlayback(){try{
        if(preparedPath==""||!reviewed.Checked||!ServiceRunning()||!Matches())throw new Exception("请先取得此局完整答案、对齐并核对预览");
        var plan=Read(preparedPath);if(Value(plan,"session")!=session||Convert.ToInt32(plan["level"])!=Level())throw new Exception("坐标计划已过期");
        int speed=clickSpeed.SelectedIndex;plan["mark_milliseconds"]=speed==0?650:speed==1?300:speed==2?120:(int)markDelay.Value;plan["verify_milliseconds"]=speed==0?1100:speed==1?650:speed==2?350:1100;plan["adaptive_verify"]=true;File.WriteAllText(preparedPath,json.Serialize(plan),Encoding.UTF8);
        string control=Path.Combine(Path.GetDirectoryName(preparedPath),"control.json");if(File.Exists(control))File.Delete(control);
        activePlayFolder=Path.GetDirectoryName(preparedPath);File.Delete(Path.Combine(activePlayFolder,"playback_status.json"));
        playback=Process.Start(new ProcessStartInfo{FileName=Path.Combine(root,"SolverPlayback.exe"),Arguments=Q(preparedPath),WorkingDirectory=root,CreateNoWindow=true,UseShellExecute=false,RedirectStandardOutput=true,RedirectStandardError=true});
        playback.OutputDataReceived+=delegate(object sender,DataReceivedEventArgs e){};
        playback.ErrorDataReceived+=delegate(object sender,DataReceivedEventArgs e){if(e.Data!=null&&!IsDisposed)BeginInvoke((Action)(()=>AppendLog(e.Data)));};playback.BeginOutputReadLine();playback.BeginErrorReadLine();AppendLog("后台点击开始。F8 暂停/继续，F9 或 Esc 停止。");SetButtons();
    }catch(Exception e){AppendLog(e.Message);subhead.Text=e.Message;}}
    void Command(string action){if(!Playing())return;string path=Path.Combine(activePlayFolder,"control.json");string temp=path+".tmp";File.WriteAllText(temp,json.Serialize(new Dictionary<string,object>{{"id",Guid.NewGuid().ToString("N")},{"action",action}}),Encoding.UTF8);if(File.Exists(path))File.Delete(path);File.Move(temp,path);AppendLog(action=="stop"?"已请求停止":action=="pause"?"已请求暂停":"已请求继续");}
    async void OnClosing(object sender,FormClosingEventArgs e) {
        if(closeReady)return;e.Cancel=true;if(closing)return;closing=true;poll.Stop();Enabled=false;
        try{Command("stop");if(Playing())await Task.Run(()=>playback.WaitForExit(4000));
            Process job=null;lock(commandLock){if(activeScript=="app_bridge.py")job=activeCommand;}
            if(job!=null&&!job.HasExited){int pid=job.Id;await Task.Run(()=>{using(var killer=Process.Start(new ProcessStartInfo{FileName="taskkill.exe",Arguments="/PID "+pid+" /T /F",UseShellExecute=false,CreateNoWindow=true,RedirectStandardOutput=true,RedirectStandardError=true})){killer.StandardOutput.ReadToEnd();killer.StandardError.ReadToEnd();killer.WaitForExit(8000);}});}
            // Finish an in-flight capture startup before restoring its lease.
            if(activeWork!=null)await activeWork;
        }catch(Exception ex){AppendLog(ex.Message);}
        if(ownsCapture){try{await Execute("capture_service.py","stop",30);}catch(Exception ex){AppendLog(ex.Message);}}
        closeReady=true;Close();
    }
    void Demo(){world.BackColor=Green;world.ForeColor=Color.White;daily.BackColor=Color.White;daily.ForeColor=Ink;mode="world";modeInfo.Text="实际捕获：羊羊大世界";headline.Text="正在搜索完整答案";subhead.Text="真实种子已取得，两关地图全部还原。";hint.Text="第 2 关 · 255 张普通牌";captureValue.Text="正在监听";captureValue.ForeColor=Green;mapValue.Text="2 / 2  · 种子已取得";levels.Items.Clear();levels.Items.Add("第 1 关 · 起始关");levels.Items.Add("第 2 关");levels.SelectedIndex=1;answerValue.Text="后台正在搜索完整答案…";searchText.Text="搜索深度 221 / 255";searchProgress.Maximum=255;searchProgress.Value=221;searchMetrics.Text="12,340,000 节点 · 760,000/秒 · 16.2 秒 · 2 路\n搜索深度不代表完成比例或剩余时间";cancelSearch.Enabled=true;progressText.Text="未开始 · F8 暂停，F9 / Esc 停止";string p=Path.Combine(root,"output","auto_capture","sessions","20261008T141544Z-a50904","main_play","reference.png");if(File.Exists(p))board.LoadImage(p);footer.Text="搜索界面示例 · 使用历史地图与示例数值，未启用抓取或点击。";AppendLog("界面预览，不连接微信，不启用代理，不点击。");}
    public void StartupCheck(){Poll();if(!File.Exists(Path.Combine(root,"venv","Scripts","python.exe")))throw new Exception("Python runtime missing");foreach(string name in new[]{"SolverPlayback.exe","FastOriginalSolver.exe","SheepClicker.Diagnostics.exe"})if(!File.Exists(Path.Combine(root,name)))throw new Exception(name+" missing");if(!ServiceRunning()&&(solve.Enabled||align.Enabled||play.Enabled))throw new Exception("Stale plan was enabled");SelectMode("world");if(!ServiceRunning()&&play.Enabled)throw new Exception("World stale plan was enabled");}
    protected override bool ShowWithoutActivation {get{return demo;}}
    public void Render(string path,int width,int height){ShowInTaskbar=false;Opacity=0;Location=new Point(-20000,-20000);StartPosition=FormStartPosition.Manual;Show();Application.DoEvents();float factor;using(var g=CreateGraphics())factor=g.DpiX/96f;Size=new Size((int)(width*factor),(int)(height*factor));PerformLayout();Application.DoEvents();using(var image=new Bitmap(Width,Height)){DrawToBitmap(image,new Rectangle(Point.Empty,Size));using(var scaled=new Bitmap(width,height))using(var g=Graphics.FromImage(scaled)){g.InterpolationMode=InterpolationMode.HighQualityBicubic;g.DrawImage(image,0,0,width,height);scaled.Save(path,ImageFormat.Png);}}Hide();}
}

static class SheepApp {
    [STAThread] static int Main(string[] args) {
        Native.SetProcessDPIAware();Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);
        string root=AppDomain.CurrentDomain.BaseDirectory;
        if(args.Length>0&&args[0]=="--self-test"){using(var form=new SheepApplication(root))form.StartupCheck();File.WriteAllText(Path.Combine(root,"output","app_startup_check.txt"),"PASS: dependencies, both mode gates, stale capture disabled; no proxy or game input");return 0;}
        if(args.Length>=2&&args[0]=="--render-preview"){using(var form=new SheepApplication(root,true))form.Render(Path.GetFullPath(args[1]),args.Length>2?int.Parse(args[2]):1120,args.Length>3?int.Parse(args[3]):830);return 0;}
        using(var mutex=new Mutex(false,"Local\\SheepSolverApplication")){if(!mutex.WaitOne(0)){MessageBox.Show("羊羊助手已经打开。","羊羊助手");return 0;}try{Application.Run(new SheepApplication(root));}finally{mutex.ReleaseMutex();}}return 0;
    }
}
