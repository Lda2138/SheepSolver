using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Web.Script.Serialization;
using System.Windows.Forms;

static class UiStabilityCheck {
    static readonly BindingFlags Private=BindingFlags.Instance|BindingFlags.NonPublic;
    static void Check(bool ok,string message){if(!ok)throw new Exception(message);}
    [STAThread] static int Main(string[] args){
        Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);
        string root=Path.GetFullPath(args[0]);var json=new JavaScriptSerializer();
        using(var form=new AutoSheepForm(root,true)){
            var apply=typeof(AutoSheepForm).GetMethod("Apply",Private);
            var changes=new Dictionary<string,int>();var labels=new Dictionary<string,Control>();
            foreach(string name in new[]{"title","message","mode","searchText","stepText","primary"}){
                string key=name;var control=(Control)typeof(AutoSheepForm).GetField(name,Private).GetValue(form);
                labels[key]=control;changes[key]=0;control.TextChanged+=delegate{changes[key]++;};
            }
            var stages=(Label[])typeof(AutoSheepForm).GetField("stages",Private).GetValue(form);
            changes["answer"]=0;stages[3].TextChanged+=delegate{changes["answer"]++;};
            var bar=(ProgressBar)typeof(AutoSheepForm).GetField("searchBar",Private).GetValue(form);
            var button=(Button)labels["primary"];int enabled=0,recreated=0;
            button.EnabledChanged+=delegate{enabled++;};IntPtr handle=bar.Handle;bar.HandleCreated+=delegate{recreated++;};
            Action reset=()=>{foreach(string key in new List<string>(changes.Keys))changes[key]=0;enabled=0;recreated=0;};
            Action<Dictionary<string,object>> update=d=>apply.Invoke(form,new object[]{d});
            foreach(string payload in new[]{
                "{\"state\":\"solving\",\"message\":\"整局约束搜索中\",\"mode\":\"world\",\"rules\":\"standard\",\"auto_enabled\":true,\"settings\":{\"solver_engine\":\"static_constraint_v1\"},\"search\":{\"engine\":\"static_constraint_v1\",\"state\":\"searching\",\"phase\":\"presolve\",\"workers\":22}}",
                "{\"state\":\"planning_segment\",\"message\":\"规划中\",\"mode\":\"world\",\"rules\":\"energy_alien\",\"auto_enabled\":true,\"settings\":{\"rolling\":true},\"search\":{\"execution_kind\":\"rolling_segment_v1\",\"workers\":1},\"playback\":{\"next_step\":28,\"total\":31,\"execution_kind\":\"rolling_segment_v1\",\"board_total\":243,\"remaining_cards\":215,\"rules\":\"energy_alien_v1\",\"alien_activations\":0}}",
                "{\"state\":\"solving\",\"message\":\"验证完整路线\",\"mode\":\"daily\",\"rules\":\"standard\",\"auto_enabled\":true,\"settings\":{\"rolling\":true},\"search\":{\"engine\":\"standard_complete_continuation\",\"workers\":1}}",
                "{\"state\":\"waiting_new_game\",\"message\":\"等待重新开局\",\"mode\":\"daily\",\"auto_enabled\":true}"
            }){
                var data=json.Deserialize<Dictionary<string,object>>(payload);reset();update(data);
                foreach(var item in changes)Check(item.Value<=1,"one snapshot changed "+item.Key+" multiple times");
                reset();for(int n=0;n<100;n++)update(data);
                foreach(var item in changes)Check(item.Value==0,"unchanged snapshot changed "+item.Key);
                Check(enabled==0,"unchanged snapshot toggled primary button");Check(recreated==0,"unchanged snapshot restarted progress bar");
            }
            var playing=json.Deserialize<Dictionary<string,object>>("{\"state\":\"playing\",\"message\":\"点击\",\"auto_enabled\":true,\"playback\":{\"next_step\":10,\"total\":231}}");
            update(playing);reset();
            for(int n=0;n<40;n++){playing["state"]=n%2==0?"verifying":"playing";playing["message"]=n%2==0?"核对":"点击";update(playing);}
            Check(changes["title"]==0&&changes["message"]==0&&changes["stepText"]==0,"click/verify alternation changed primary text");
            playing["state"]="paused";playing["auto_enabled"]=false;playing["message"]="暂停";update(playing);
            Check(labels["title"].Text=="托管已暂停"&&button.Enabled,"pause state did not display immediately");
            playing["state"]="blocked";playing["message"]="实际牌槽不一致";update(playing);
            Check(labels["title"].Text=="需要处理"&&labels["message"].Text=="实际牌槽不一致","blocking state did not display immediately");
            Console.WriteLine("PASS: 300 unchanged snapshots produce zero label/button/progress restarts; 40 click/verify transitions keep primary text stable; pause/errors update immediately.");
        }
        return 0;
    }
}
