// Launch only the project's fixed service command through the signed-in desktop.
// No elevation, mouse input, window activation or arbitrary command execution.
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
class DesktopSessionLauncher {
    [STAThread] static int Main(string[] args) {
        Console.OutputEncoding=Encoding.UTF8;
        object shell=null,windows=null,desktop=null;
        try {
            if(args.Length!=1||!Regex.IsMatch(args[0],"^[a-f0-9]{32}$"))throw new Exception("Invalid service request ID");
            string root=AppDomain.CurrentDomain.BaseDirectory;
            if(!File.Exists(Path.Combine(root,"output","auto_capture","session_commands",args[0]+".request.json")))throw new Exception("Missing service request");
            shell=Activator.CreateInstance(Type.GetTypeFromProgID("Shell.Application"));
            windows=((dynamic)shell).Windows();int hwnd=0;
            desktop=((dynamic)windows).FindWindowSW(0,0,8,ref hwnd,1);
            if(desktop==null)throw new Exception("Windows desktop session unavailable");
            ((dynamic)desktop).Document.Application.ShellExecute(Path.Combine(root,"venv","Scripts","pythonw.exe"),
                "\""+Path.Combine(root,"capture_service.py")+"\" desktop --request "+args[0],root,"open",0);
            return 0;
        }catch(Exception exc){Console.Error.WriteLine(exc.Message);return 1;}
        finally{foreach(object obj in new[]{desktop,windows,shell})if(obj!=null&&Marshal.IsComObject(obj))Marshal.ReleaseComObject(obj);}
    }
}
