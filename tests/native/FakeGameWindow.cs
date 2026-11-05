// Stand-in for the game window, for end-to-end playback tests. It renders a
// plain tile board, receives the player's posted mouse messages, animates cards
// into the tray, clears triples and can drop clicks the way the real client does.
// It talks to nothing but the player under test and its own log file.
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;

class FakeTile { public string Id; public int Type,Layer=1,Rol,Row; public Point Center; public bool Removed; }
class FakeFlight { public FakeTile Tile; public DateTime Start; }
class FakeSlot { public int Type; public DateTime ClearAt=DateTime.MaxValue,LandedAt=DateTime.UtcNow; }

sealed class FakeGameWindow : Form
{
    const string Title="羊了个羊：测试窗口";
    static readonly DateTime Epoch=new DateTime(1970,1,1,0,0,0,DateTimeKind.Utc);
    // Timing measured on the real client: a card reaches the tray in about 200 ms
    // and a triple is drawn for about 250 ms more. Clicks spaced 140 ms after a clear
    // and a second at 272 ms survived, while three clicks within 220 ms overfilled the
    // tray, so the model ends the game at the tenth occupied slot, clearing included.
    const int FlightMs=200,ClearMs=250,DropAfterClearMs=60,DropWindowMs=1000,OverflowSlots=10;
    static readonly Color Board=Color.FromArgb(193,255,137),Face=Color.FromArgb(240,235,190);
    static readonly Color[] Icons={Color.Crimson,Color.RoyalBlue,Color.DarkOrange,Color.Purple,Color.Teal,Color.Sienna,Color.DeepPink,Color.Navy,Color.DarkGreen,Color.Goldenrod,Color.SlateGray,Color.Maroon,Color.DodgerBlue,Color.OrangeRed};
    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
    [DllImport("winmm.dll")] static extern uint timeBeginPeriod(uint ms);

    readonly List<FakeTile> tiles;readonly List<FakeSlot> tray=new List<FakeSlot>();readonly List<FakeFlight> flights=new List<FakeFlight>();
    readonly Bitmap surface=new Bitmap(336,622,PixelFormat.Format24bppRgb);readonly string log;readonly int dropEvery;
    readonly Timer frame=new Timer{Interval=15};
    readonly Random random;readonly double dropChance;readonly int trayLagMs;
    readonly HashSet<string> pressedOnce=new HashSet<string>();
    FakeTile pressed;DateTime pressedUntil=DateTime.MinValue;
    DateTime clearedAt=DateTime.MinValue;int clears;bool dropArmed;

    // trayLagMs delays drawing a landed card, as the real tray reads true late.
    FakeGameWindow(List<FakeTile> tiles,string log,int dropEvery,int seed,double dropChance,int trayLagMs){
        this.tiles=tiles;this.log=log;this.dropEvery=dropEvery;random=new Random(seed);this.dropChance=dropChance;this.trayLagMs=trayLagMs;
        Text=Title;FormBorderStyle=FormBorderStyle.None;ShowInTaskbar=false;StartPosition=FormStartPosition.Manual;Location=new Point(40,40);ClientSize=new Size(336,622);DoubleBuffered=true;
        frame.Tick+=delegate{Advance();Render();Invalidate();};
    }
    void Record(string kind,string id,int x,int y){File.AppendAllText(log,new JavaScriptSerializer().Serialize(new Dictionary<string,object>{{"kind",kind},{"id",id},{"x",x},{"y",y},{"ms",(DateTime.UtcNow-Epoch).TotalMilliseconds}})+Environment.NewLine,Encoding.UTF8);}
    bool Covered(FakeTile t){return tiles.Any(o=>!o.Removed&&o.Layer>t.Layer&&Math.Abs(o.Rol-t.Rol)<8&&Math.Abs(o.Row-t.Row)<8);}
    static Color Icon(int type){return type<Icons.Length?Icons[type]:Color.FromArgb(40+(type*53)%180,30+(type*97)%190,40+(type*29)%180);}
    void Tile(Graphics g,Point c,int type,bool dim=false){
        Color icon=Icon(type);if(dim)icon=Color.FromArgb(icon.R/2,icon.G/2,icon.B/2);
        using(var face=new SolidBrush(dim?Color.FromArgb(133,155,92):Face))g.FillRectangle(face,c.X-17,c.Y-18,34,36);
        using(var brush=new SolidBrush(icon))g.FillEllipse(brush,c.X-9,c.Y-9,18,18);
    }
    public void Render(){
        using(var g=Graphics.FromImage(surface)){
            g.Clear(Board);
            using(var band=new SolidBrush(Color.FromArgb(90,60,40)))g.FillRectangle(band,18,486,300,48);
            // A rejected click still animates the card, so the target changes
            // briefly although nothing was removed.
            foreach(var t in tiles.Where(t=>!t.Removed).OrderBy(t=>t.Layer))Tile(g,t==pressed&&DateTime.UtcNow<pressedUntil?new Point(t.Center.X+7,t.Center.Y-8):t.Center,t.Type,Covered(t));
            var shown=tray.Where(t=>(DateTime.UtcNow-t.LandedAt).TotalMilliseconds>=trayLagMs).ToList();
            for(int k=0;k<Math.Min(7,shown.Count);k++)Tile(g,new Point(40+42*k,510),shown[k].Type);
            foreach(var f in flights){
                double p=Math.Min(1,(DateTime.UtcNow-f.Start).TotalMilliseconds/FlightMs);Point goal=new Point(40+42*Math.Min(6,tray.Count),510);
                Tile(g,new Point((int)(f.Tile.Center.X+(goal.X-f.Tile.Center.X)*p),(int)(f.Tile.Center.Y+(goal.Y-f.Tile.Center.Y)*p)),f.Tile.Type);
            }
        }
    }
    void Advance(){
        foreach(var f in flights.ToArray())if((DateTime.UtcNow-f.Start).TotalMilliseconds>=FlightMs){
            flights.Remove(f);tray.Add(new FakeSlot{Type=f.Tile.Type});
            var same=tray.Where(t=>t.Type==f.Tile.Type&&t.ClearAt==DateTime.MaxValue).ToList();
            if(same.Count==3)foreach(var slot in same)slot.ClearAt=f.Start.AddMilliseconds(FlightMs+ClearMs);
            else if(tray.Count(t=>t.ClearAt==DateTime.MaxValue)>=7)Record("overflow",f.Tile.Id,0,0);
        }
        if(tray.Any(t=>DateTime.UtcNow>=t.ClearAt)){
            tray.RemoveAll(t=>DateTime.UtcNow>=t.ClearAt);
            clearedAt=DateTime.UtcNow;clears++;dropArmed=dropEvery>0&&clears%dropEvery==0;
        }
    }
    void Receive(int x,int y){
        Advance(); // Game time, not the frame timer, decides what a click meets.
        double sinceClear=(DateTime.UtcNow-clearedAt).TotalMilliseconds;
        if(dropArmed&&sinceClear>=DropAfterClearMs&&sinceClear<=DropWindowMs){dropArmed=false;Record("dropped","",x,y);return;}
        var hit=tiles.Where(t=>!t.Removed&&Math.Abs(t.Center.X-x)<=17&&Math.Abs(t.Center.Y-y)<=18).OrderByDescending(t=>t.Layer).FirstOrDefault();
        if(hit==null){Record("miss","",x,y);return;}
        if(Covered(hit)){Record("dropped","",x,y);return;}
        if(dropChance>0&&sinceClear>=DropAfterClearMs&&sinceClear<=DropWindowMs&&random.NextDouble()<dropChance){pressed=hit;pressedUntil=DateTime.UtcNow.AddMilliseconds(90);Record("dropped","",x,y);return;}
        // drop_every -1: lose the click that would complete a triple from a full
        // tray, once per card. Trusting it makes the next click the seventh card.
        int same=tray.Count(t=>t.Type==hit.Type&&t.ClearAt==DateTime.MaxValue)+flights.Count(f=>f.Tile.Type==hit.Type);
        if(dropEvery<0&&same==2&&tray.Count+flights.Count==6&&pressedOnce.Add(hit.Id)){pressed=hit;pressedUntil=DateTime.UtcNow.AddMilliseconds(90);Record("dropped","",x,y);return;}
        if(tray.Count+flights.Count+1>=OverflowSlots)Record("overflow",hit.Id,x,y);
        hit.Removed=true;flights.Add(new FakeFlight{Tile=hit,Start=DateTime.UtcNow});Record("accepted",hit.Id,x,y);
    }
    protected override void OnPaint(PaintEventArgs e){e.Graphics.DrawImageUnscaled(surface,0,0);}
    protected override void WndProc(ref Message m){
        // Only messages posted by the player may reach the game. Real mouse input
        // passes through, and WM_LBUTTONDOWN must not start a WinForms mouse
        // capture, which would route the desktop's mouse events here.
        if(m.Msg==0x0084){m.Result=new IntPtr(-1);return;}
        if(m.Msg==0x0200||m.Msg==0x0201)return;
        if(m.Msg==0x0202){int packed=m.LParam.ToInt32();Receive(packed&65535,(packed>>16)&65535);return;}
        base.WndProc(ref m);
    }
    public void Begin(string reference){Render();surface.Save(reference,ImageFormat.Png);frame.Start();}

    // args: tiles.json reference.png clicks.jsonl drop_every ready_file [seed drop_chance]
    [STAThread] static int Main(string[] args){
        SetProcessDPIAware();timeBeginPeriod(1);
        var json=new JavaScriptSerializer{MaxJsonLength=4*1024*1024};
        var rows=json.Deserialize<List<Dictionary<string,object>>>(File.ReadAllText(args[0],Encoding.UTF8));
        var tiles=rows.Select(r=>new FakeTile{Id=Convert.ToString(r["id"]),Type=Convert.ToInt32(r["type"]),Center=new Point(Convert.ToInt32(r["x"]),Convert.ToInt32(r["y"])),Layer=r.ContainsKey("layer")?Convert.ToInt32(r["layer"]):1,Rol=r.ContainsKey("rol")?Convert.ToInt32(r["rol"]):Convert.ToInt32(r["col"])*8,Row=r.ContainsKey("rownum")?Convert.ToInt32(r["rownum"]):Convert.ToInt32(r["row"])*8}).ToList();
        using(var window=new FakeGameWindow(tiles,args[2],int.Parse(args[3]),args.Length>5?int.Parse(args[5]):1,args.Length>6?double.Parse(args[6],System.Globalization.CultureInfo.InvariantCulture):0,args.Length>7?int.Parse(args[7]):0)){
            window.Shown+=delegate{window.Begin(args[1]);File.WriteAllText(args[4],"ready");};
            Application.Run(window);
        }
        return 0;
    }
}

