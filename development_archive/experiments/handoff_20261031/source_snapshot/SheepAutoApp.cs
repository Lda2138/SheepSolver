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

sealed class LiveBoard : Control {
    Bitmap bitmap;
    public LiveBoard(){DoubleBuffered=true;BackColor=Color.FromArgb(235,241,230);}
    public void LoadPath(string path){using(var stream=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.ReadWrite|FileShare.Delete))using(var image=Image.FromStream(stream)){var old=bitmap;bitmap=new Bitmap(image);if(old!=null)old.Dispose();}Invalidate();}
    protected override void OnPaint(PaintEventArgs e){base.OnPaint(e);if(bitmap==null){TextRenderer.DrawText(e.Graphics,"进入游戏后自动显示棋盘",Font,ClientRectangle,Color.FromArgb(100,120,105),TextFormatFlags.HorizontalCenter|TextFormatFlags.VerticalCenter);return;}double scale=Math.Min((Width-16.0)/bitmap.Width,(Height-16.0)/bitmap.Height);int w=(int)(bitmap.Width*scale),h=(int)(bitmap.Height*scale);e.Graphics.InterpolationMode=InterpolationMode.HighQualityBicubic;e.Graphics.DrawImage(bitmap,new Rectangle((Width-w)/2,(Height-h)/2,w,h));}
    protected override void Dispose(bool disposing){if(disposing&&bitmap!=null)bitmap.Dispose();base.Dispose(disposing);}
}

sealed class AutoSheepForm : Form {
    readonly string root;readonly bool preview,autoStart;readonly JavaScriptSerializer json=new JavaScriptSerializer{MaxJsonLength=4*1024*1024};
    readonly Color green=Color.FromArgb(43,112,78),ink=Color.FromArgb(32,56,42),muted=Color.FromArgb(105,121,107);
    readonly LiveBoard board=new LiveBoard();readonly Label title=new StableLabel(),message=new StableLabel(),mode=new StableLabel(),heartbeat=new StableLabel(),searchText=new StableLabel(),stepText=new StableLabel(),caption=new StableLabel();
    readonly Label[] stages={new StableLabel(),new StableLabel(),new StableLabel(),new StableLabel()};readonly Button primary=new Button(),pause=new Button(),stop=new Button(),details=new Button(),refresh=new Button();
    readonly ComboBox performance=new ComboBox(),speed=new ComboBox();readonly NumericUpDown seconds=new NumericUpDown();readonly ProgressBar searchBar=new ProgressBar(),playBar=new ProgressBar();
    readonly System.Windows.Forms.Timer timer=new System.Windows.Forms.Timer();readonly StringBuilder events=new StringBuilder();Dictionary<string,object> snapshot;
    bool connecting,closing,closeReady,suppress,autoSent;DateTime lastConnect=DateTime.MinValue,lastImageTime=DateTime.MinValue;string lastImage="",lastEvent="",state="connecting";
    public AutoSheepForm(string directory,bool demo=false,bool start=false){
        root=directory;preview=demo;autoStart=start;DoubleBuffered=true;AutoScaleMode=AutoScaleMode.None;Font=new Font("Microsoft YaHei UI",9.5f);BackColor=Color.FromArgb(247,248,241);ForeColor=ink;Text="羊羊助手 · 自动托管";StartPosition=FormStartPosition.CenterScreen;
        string icon=Path.Combine(root,"assets","sheep.ico");if(File.Exists(icon))Icon=new Icon(icon);Build();float scale;using(var g=Graphics.FromHwnd(IntPtr.Zero))scale=g.DpiX/96f;ScaleTree(this,scale);
        MinimumSize=new Size((int)(960*scale),(int)(750*scale));Size=new Size(Math.Min((int)(1120*scale),Screen.PrimaryScreen.WorkingArea.Width-40),Math.Min((int)(830*scale),Screen.PrimaryScreen.WorkingArea.Height-30));
        if(preview){Demo();DemoSearch();}else{timer.Interval=250;timer.Tick+=async delegate{await Poll();};Shown+=async delegate{timer.Start();await Poll();};FormClosing+=OnClosing;}
        FormClosed+=delegate{timer.Dispose();};
    }
    Label TextLabel(string text,float size=9.5f){return new StableLabel{Text=text,Dock=DockStyle.Fill,Font=new Font("Microsoft YaHei UI",size),ForeColor=ink,Margin=new Padding(0),TextAlign=ContentAlignment.MiddleLeft,AutoEllipsis=true};}
    void ButtonStyle(Button b,string text,bool main=false){b.Text=text;b.Dock=DockStyle.Fill;b.FlatStyle=FlatStyle.Flat;b.FlatAppearance.BorderSize=main?0:1;b.FlatAppearance.BorderColor=Color.FromArgb(210,220,207);b.BackColor=main?green:Color.White;b.ForeColor=main?Color.White:ink;b.Cursor=Cursors.Hand;b.Margin=new Padding(0,4,0,4);}
    static void ScaleTree(Control c,float factor){c.Padding=new Padding((int)(c.Padding.Left*factor),(int)(c.Padding.Top*factor),(int)(c.Padding.Right*factor),(int)(c.Padding.Bottom*factor));c.Margin=new Padding((int)(c.Margin.Left*factor),(int)(c.Margin.Top*factor),(int)(c.Margin.Right*factor),(int)(c.Margin.Bottom*factor));var table=c as TableLayoutPanel;if(table!=null){foreach(RowStyle r in table.RowStyles)if(r.SizeType==SizeType.Absolute)r.Height*=factor;foreach(ColumnStyle col in table.ColumnStyles)if(col.SizeType==SizeType.Absolute)col.Width*=factor;}foreach(Control child in c.Controls)ScaleTree(child,factor);}
    TableLayoutPanel Pair(Control a,Control b,int left=40){var t=new StableLayout{ColumnCount=2,Dock=DockStyle.Fill,Margin=new Padding(0)};t.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,left));t.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100-left));a.Dock=b.Dock=DockStyle.Fill;a.Margin=new Padding(0,0,5,0);b.Margin=new Padding(5,0,0,0);t.Controls.Add(a,0,0);t.Controls.Add(b,1,0);return t;}
    void Row(TableLayoutPanel t,Control c,int height){int n=t.RowCount++;t.RowStyles.Add(new RowStyle(SizeType.Absolute,height));c.Dock=DockStyle.Fill;t.Controls.Add(c,0,n);}
    void Build(){
        var layout=new StableLayout{Dock=DockStyle.Fill,Padding=new Padding(20,12,20,12),ColumnCount=1,RowCount=4};layout.RowStyles.Add(new RowStyle(SizeType.Absolute,58));layout.RowStyles.Add(new RowStyle(SizeType.Absolute,34));layout.RowStyles.Add(new RowStyle(SizeType.Percent,100));layout.RowStyles.Add(new RowStyle(SizeType.Absolute,36));Controls.Add(layout);
        var name=TextLabel("羊羊助手",22);name.Font=new Font(Font.FontFamily,22,FontStyle.Bold);var motto=TextLabel("自动识别关次 · 自动求解与对齐 · 自动续接",9);motto.ForeColor=muted;motto.TextAlign=ContentAlignment.MiddleRight;layout.Controls.Add(Pair(name,motto,40),0,0);
        mode.Dock=DockStyle.Fill;mode.ForeColor=green;mode.Text="模式与关次自动识别";mode.TextAlign=ContentAlignment.MiddleLeft;layout.Controls.Add(mode,0,1);
        var body=new StableLayout{Dock=DockStyle.Fill,ColumnCount=2,Margin=new Padding(0)};body.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,61));body.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,39));layout.Controls.Add(body,0,2);
        var left=new StableLayout{Dock=DockStyle.Fill,ColumnCount=1,RowCount=3,Padding=new Padding(12),Margin=new Padding(0,0,14,0),BackColor=Color.White};left.RowStyles.Add(new RowStyle(SizeType.Absolute,38));left.RowStyles.Add(new RowStyle(SizeType.Percent,100));left.RowStyles.Add(new RowStyle(SizeType.Absolute,32));body.Controls.Add(left,0,0);
        ButtonStyle(refresh,"重新核对");refresh.Click+=delegate{Send("refresh");};left.Controls.Add(Pair(TextLabel("实时棋盘",12),refresh,73),0,0);board.Dock=DockStyle.Fill;left.Controls.Add(board,0,1);caption.Dock=DockStyle.Fill;caption.ForeColor=muted;caption.Text="绿框核对位置，红框标出下一步";caption.TextAlign=ContentAlignment.MiddleCenter;left.Controls.Add(caption,0,2);
        var right=new StableLayout{Dock=DockStyle.Fill,ColumnCount=1,Padding=new Padding(12,5,12,5),Margin=new Padding(0),BackColor=Color.White};body.Controls.Add(right,1,0);
        title.Font=new Font(Font.FontFamily,14,FontStyle.Bold);title.Text="正在连接后台";Row(right,title,32);message.ForeColor=muted;message.Text="后台准备就绪后，点击一次开始托管。";message.TextAlign=ContentAlignment.TopLeft;Row(right,message,50);
        var status=new StableLayout{Dock=DockStyle.Fill,ColumnCount=2,RowCount=4,BackColor=Color.FromArgb(239,244,230),Padding=new Padding(8,2,8,2),Margin=new Padding(0,2,0,5)};status.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,25));status.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,75));string[] names={"抓取","种子","地图","解答"};for(int i=0;i<4;i++){status.RowStyles.Add(new RowStyle(SizeType.Percent,25));status.Controls.Add(TextLabel(names[i],9),0,i);stages[i].ForeColor=muted;stages[i].TextAlign=ContentAlignment.MiddleLeft;status.Controls.Add(stages[i],1,i);stages[i].Dock=DockStyle.Fill;}Row(right,status,84);
        var search=new StableLayout{Dock=DockStyle.Fill,RowCount=2,ColumnCount=1,Margin=new Padding(0)};search.RowStyles.Add(new RowStyle(SizeType.Percent,100));search.RowStyles.Add(new RowStyle(SizeType.Absolute,14));searchText.ForeColor=muted;searchText.Font=new Font(Font.FontFamily,8.5f);searchText.Text="搜索进度会实时显示";searchText.Dock=DockStyle.Fill;search.Controls.Add(searchText,0,0);searchBar.Dock=DockStyle.Fill;searchBar.Margin=new Padding(0,2,0,2);search.Controls.Add(searchBar,0,1);Row(right,search,122);
        ButtonStyle(primary,"开始托管",true);primary.Font=new Font(Font.FontFamily,11,FontStyle.Bold);primary.Click+=delegate{Send("start");};Row(right,primary,44);
        ButtonStyle(pause,"暂停托管");ButtonStyle(stop,"停止托管");pause.Click+=delegate{Send(state=="paused"?"start":"pause");};stop.Click+=delegate{Send("stop");};Row(right,Pair(pause,stop,50),39);
        playBar.Margin=new Padding(0,4,0,2);Row(right,playBar,17);stepText.ForeColor=muted;stepText.Text="执行进度 · 尚未开始";Row(right,stepText,25);
        speed.DropDownStyle=ComboBoxStyle.DropDownList;speed.Items.AddRange(new object[]{"稳妥 · 约 2 秒/步","快速 · 约 1.2 秒/步","极速 · 动态核对"});speed.SelectedIndex=1;speed.SelectedIndexChanged+=delegate{if(!suppress)Send("settings");};Row(right,Pair(TextLabel("点击速度",9),speed,30),34);
        performance.DropDownStyle=ComboBoxStyle.DropDownList;performance.Items.AddRange(new object[]{"省资源 · 单路","快速 · 自动并行","全速 · 空闲资源"});performance.SelectedIndex=2;performance.SelectedIndexChanged+=delegate{if(!suppress)Send("settings");};Row(right,Pair(TextLabel("求解性能",9),performance,30),34);
        seconds.Minimum=30;seconds.Maximum=300;seconds.Increment=30;seconds.Value=180;seconds.TextAlign=HorizontalAlignment.Center;seconds.ValueChanged+=delegate{if(!suppress)Send("settings");};ButtonStyle(details,"记录 / 高级设置");details.Click+=delegate{ShowDetails();};Row(right,Pair(Pair(TextLabel("每轮 / 秒",8.5f),seconds,55),details,51),40);
        right.RowCount++;right.RowStyles.Add(new RowStyle(SizeType.Percent,100));
        heartbeat.Dock=DockStyle.Fill;heartbeat.Text="后台状态初始化中";heartbeat.ForeColor=muted;heartbeat.Font=new Font(Font.FontFamily,8.5f);heartbeat.TextAlign=ContentAlignment.MiddleLeft;layout.Controls.Add(heartbeat,0,3);
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
    static void SetProgress(ProgressBar bar,int maximum,int value,ProgressBarStyle style){
        maximum=Math.Max(1,maximum);value=Math.Max(0,Math.Min(maximum,value));
        if(bar.Value>maximum)bar.Value=value;
        if(bar.Maximum!=maximum)bar.Maximum=maximum;
        if(bar.Value!=value)bar.Value=value;
        if(bar.Style!=style)bar.Style=style;
    }
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
        string searchCaption;ProgressBarStyle searchStyle=ProgressBarStyle.Blocks;
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
            if(V(search,"workers","0")!="0")searchStyle=ProgressBarStyle.Marquee;
        }else if(original){
            searchCaption="开源项目原版 Python · 自带选牌策略\n按当前牌型计算完整路线\n"+V(search,"message",state=="waiting_new_map"?"当前画面与缓存地图不同，尚未开始搜索":state=="waiting_window"?"窗口恢复显示后自动继续":running?"等待当前棋盘与地图就绪":"点击开始托管后求解")+"\n牌型改变后作废旧路线并重新计算";
            if(searchState=="searching")searchCaption+="\n已用 "+V(search,"elapsed_seconds","0")+" 秒 · 最深 "+V(search,"deepest","0")+" · "+V(search,"nodes_per_second","0")+" 次尝试/秒\n"+V(search,"workers","0")+" / "+V(search,"target_workers","1")+" 路 · 已处理 "+V(search,"completed_branches","0")+" 个分支"; if(D(search,"resources").Count>0)searchCaption+="\nCPU "+V(D(search,"resources"),"system_cpu_percent")+"% · 搜索内存 "+V(D(search,"resources"),"solver_memory_mb")+" MB";
            if(V(search,"workers","0")!="0")searchStyle=ProgressBarStyle.Marquee;
        }else if(standard){
            searchCaption="普通关 · 验证完整通关路线\n优先 5 格，必要时放宽到 6 格\n"+V(search,"message")+"\n后续路线持续保留，暂停后按记录续接";
            if(V(search,"workers","0")!="0")searchStyle=ProgressBarStyle.Marquee;
        }else if(rolling){
            searchCaption="牌槽上限 "+V(search,"tray_capacity","5")+" 格 · 每段优先 5 格，必要时用 6 格\n已确认 "+V(search,"committed_steps","0")+" 步 · 前瞻消除 "+V(search,"lookahead_events","0")+" / "+V(search,"horizon_target","3")+" 次\n"+V(search,"message")+"\n局部前瞻已回放，最终通关仍未确定";
            if(V(search,"workers","0")!="0")searchStyle=ProgressBarStyle.Marquee;
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
        SetText(title,text);Color titleColor=state=="blocked"?Color.FromArgb(157,91,25):ink;if(title.ForeColor!=titleColor)title.ForeColor=titleColor;
        SetText(message,mainMessage);SetText(mode,modeText);
        SetText(stages[0],B(d,"service_running")?(d.ContainsKey("transport_ready")&&!B(d,"transport_ready")?"监听在线 · 抓取连接待恢复":"已连接 · 已收到 "+V(d,"game_requests","0")+" 次请求"):"未启用");
        SetText(stages[1],state=="waiting_new_map"?"旧局种子 · 等待本局更新":B(d,"seed_received")?"真实种子已取得":V(d,"capture_phase")=="waiting_for_seed_decryption"?"已收密文，等待解密":"等待开局数据");
        SetText(stages[2],state=="waiting_new_map"?"旧局地图 · 等待本局更新":V(d,"maps_received","0")+" / "+V(d,"map_count","0")+" 份已收到");SetText(stages[3],answerText);
        SetText(searchText,searchCaption);SetProgress(searchBar,searchMax,deep,searchStyle);
        SetText(stepText,stepCaption);SetProgress(playBar,playMax,playValue,ProgressBarStyle.Blocks);
        SetText(primary,state=="search_limited"?"重新求解":state=="paused"?"继续托管":state=="blocked"||state=="stopped"?"重新核对并续接":B(d,"can_resume")&&!running?"从第 "+(next+1)+" 步继续托管":running?"托管中 · 自动接续":"开始托管");
        primary.Enabled=!running;SetText(pause,state=="paused"?"继续托管":"暂停托管");pause.Enabled=running||state=="paused";stop.Enabled=running||state=="paused"||B(d,"busy");refresh.Enabled=!running&&!B(d,"busy");
        if(state=="prop_ready"){SetText(primary,"道具路线待人工操作");primary.Enabled=false;}
        SetText(details,state=="prop_ready"?"查看移出操作说明":"记录 / 高级设置");
        suppress=true;try{if(settings.Count>0){
            int p=Math.Max(0,Math.Min(2,Convert.ToInt32(V(settings,"performance","2")))),s=Math.Max(0,Math.Min(2,Convert.ToInt32(V(settings,"click_speed","1"))));decimal sec=Math.Max(30,Math.Min(300,Convert.ToDecimal(V(settings,"seconds","180"))));
            if(performance.SelectedIndex!=p)performance.SelectedIndex=p;if(speed.SelectedIndex!=s)speed.SelectedIndex=s;if(seconds.Value!=sec)seconds.Value=sec;
        }}finally{suppress=false;}
        performance.Enabled=true;
        suppress=true;try{
            string[] labels=constraint?new string[]{"整局约束 · 单路","整局约束 · 半数核心","整局约束 · 全部核心"}:original?new string[]{"原版 Python · 单路","原版分支 · 多核","原版分支 · 全速"}:new string[]{"省资源 · 单路","快速 · 自动并行","全速 · 空闲资源"};
            for(int i=0;i<performance.Items.Count;i++)if(Convert.ToString(performance.Items[i])!=labels[i])performance.Items[i]=labels[i];
        }finally{suppress=false;}
        string image=V(d,"preview_path");if(image!=""&&File.Exists(image)){DateTime stamp=File.GetLastWriteTimeUtc(image);if(image!=lastImage||stamp!=lastImageTime){board.LoadPath(image);lastImage=image;lastImageTime=stamp;}SetText(caption,"棋盘帧 "+stamp.ToLocalTime().ToString("HH:mm:ss")+" · 红框为下一步");}
        string phase=state=="verifying"?"核对点击结果":state=="playing"?"标注并点击目标":text;
        SetText(heartbeat,"状态刷新 "+DateTime.Now.ToString("HH:mm:ss")+" · 后台在线 · "+phase+" · F8 暂停，F9 / Esc 停止点击");
        string key=state+"|"+mainMessage;if(key!=lastEvent&&state!="playing"&&state!="verifying"&&state!="identifying"){Event(text+"："+mainMessage);lastEvent=key;}
    }
    void Event(string text){events.AppendLine(DateTime.Now.ToString("HH:mm:ss")+"  "+text);if(events.Length>30000)events.Remove(0,15000);}
    void ShowDetails(){if(state=="prop_ready"){string solution=V(D(snapshot,"search"),"prop_solution_path");if(solution!=""){string file=Path.Combine(Path.GetDirectoryName(Path.GetFullPath(Path.Combine(root,solution))),"使用移出路线说明.md");if(File.Exists(file)){Process.Start(new ProcessStartInfo{FileName=file,UseShellExecute=true});return;}}}using(var form=new Form{Text="运行记录与高级设置",Size=new Size(780,560),StartPosition=FormStartPosition.CenterParent,Font=Font,BackColor=Color.White}){var panel=new StableLayout{Dock=DockStyle.Fill,ColumnCount=1,Padding=new Padding(14),RowCount=3};panel.RowStyles.Add(new RowStyle(SizeType.Absolute,42));panel.RowStyles.Add(new RowStyle(SizeType.Percent,100));panel.RowStyles.Add(new RowStyle(SizeType.Absolute,45));form.Controls.Add(panel);panel.Controls.Add(TextLabel("模式、关次、求解、对齐与断点续接均自动处理。",10),0,0);var log=new TextBox{Dock=DockStyle.Fill,Multiline=true,ReadOnly=true,ScrollBars=ScrollBars.Both,Text=events.ToString()};panel.Controls.Add(log,0,1);var restore=new Button();ButtonStyle(restore,"停止抓取并恢复代理与证书");restore.Click+=delegate{Send("restore");form.Close();};panel.Controls.Add(restore,0,2);form.ShowDialog(this);}}
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
