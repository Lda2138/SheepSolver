"""Local capture supervisor. Proxy/trust changes require explicit CLI flags.

The child binds loopback only. A durable lease permits restoration after a
crash. Windows settings are restored on stop without taking window focus.
"""
import argparse
import contextlib
import ctypes
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import io
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
import winreg

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import Encoding

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'output/auto_capture'
LEASE = OUT / 'service_lease.json'
STOP = OUT / 'stop.request'
PROXY = '127.0.0.1:6666'
CA_OWNER = OUT / 'temporary_ca_owner.json'
REG = r'Software\Microsoft\Windows\CurrentVersion\Internet Settings'
VALUES = ('ProxyEnable', 'ProxyServer', 'ProxyOverride', 'AutoConfigURL')
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        for attempt in range(100):
            try:
                temp.replace(path);return
            except PermissionError:
                if attempt == 99:raise
                time.sleep(.01)
    finally:
        temp.unlink(missing_ok=True)


def read_proxy():
    result = {}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG) as key:
        for name in VALUES:
            try:
                value, kind = winreg.QueryValueEx(key, name)
                result[name] = {'value': value, 'kind': kind}
            except FileNotFoundError:
                result[name] = None
    return result


def notify_proxy():
    internet = ctypes.WinDLL('wininet')
    internet.InternetSetOptionW(None, 39, None, 0)
    internet.InternetSetOptionW(None, 37, None, 0)


def apply_proxy(values):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG, 0, winreg.KEY_SET_VALUE) as key:
        for name, item in values.items():
            if item is None:
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
            else:
                winreg.SetValueEx(key, name, 0, item['kind'], item['value'])
    notify_proxy()


def ca_thumbprint():
    cert = x509.load_pem_x509_certificate((ROOT / 'automatic/mitmproxy-ca-cert.pem').read_bytes())
    return cert.fingerprint(hashes.SHA1()).hex().upper()


def trusted(thumb):
    from windows_user_ca import trusted as native_trusted
    return native_trusted(thumb)


def alive(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        kernel.CloseHandle(handle)


def process_ancestors():
    """Read parent IDs only, to preserve a lease during our own UI upgrade."""
    class Entry(ctypes.Structure):
        _fields_=[('size',ctypes.c_ulong),('usage',ctypes.c_ulong),('pid',ctypes.c_ulong),
                  ('heap',ctypes.c_size_t),('module',ctypes.c_ulong),('threads',ctypes.c_ulong),
                  ('parent',ctypes.c_ulong),('priority',ctypes.c_long),('flags',ctypes.c_ulong),('name',ctypes.c_wchar*260)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateToolhelp32Snapshot.restype=ctypes.c_void_p
    kernel.Process32FirstW.argtypes=kernel.Process32NextW.argtypes=[ctypes.c_void_p,ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=kernel.CreateToolhelp32Snapshot(2,0)
    if handle in (None,ctypes.c_void_p(-1).value):return []
    parents={};entry=Entry();entry.size=ctypes.sizeof(entry)
    try:
        ok=kernel.Process32FirstW(handle,ctypes.byref(entry))
        while ok:
            parents[entry.pid]=entry.parent;ok=kernel.Process32NextW(handle,ctypes.byref(entry))
    finally:kernel.CloseHandle(handle)
    result=[];pid=os.getpid()
    for _ in range(8):
        pid=parents.get(pid,0)
        if not pid or pid in result:break
        result.append(pid)
    return result


def preserve_upgrade(lease,ancestors=None,now=None):
    marker=ROOT/'output/app_upgrade.json'
    try:
        data=json.loads(marker.read_text(encoding='utf-8-sig'))
        owners=process_ancestors() if ancestors is None else ancestors
        return (data['expires_at']>(time.time() if now is None else now)
                and data['instance']==lease.get('instance')
                and (data['old_pid'] in owners or data.get('controller_pid') in owners))
    except (OSError,ValueError,KeyError):return False


def port_open(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=0.3):
            return True
    except OSError:
        return False


def repair_transport():
    """Reattach the existing listener without restarting it or losing seeds."""
    lease=json.loads(LEASE.read_text(encoding='utf-8')) if LEASE.exists() else {}
    health={'instance':lease.get('instance'),'checked_at':datetime.now(timezone.utc).isoformat(),
            'healthy':False,'proxy_repaired':False}
    try:
        if not lease.get('running') or not alive(lease.get('pid',0)) or not port_open(6666):
            raise RuntimeError('抓取监听未运行')
        if not trusted(lease.get('ca_thumbprint') or ca_thumbprint()):
            raise RuntimeError('抓取证书信任不可用')
        expected=lease.get('applied_proxy')
        if not lease.get('proxy_applied') or not expected:
            raise RuntimeError('当前监听尚未配置游戏代理')
        current=read_proxy()
        if any(current.get(key)!=expected.get(key) for key in ('ProxyEnable','ProxyServer')):
            apply_proxy(expected);health['proxy_repaired']=True
        current=read_proxy()
        if any(current.get(key)!=expected.get(key) for key in ('ProxyEnable','ProxyServer')):
            raise RuntimeError('系统代理尚未接回本地监听')
        health.update(healthy=True,message='抓取连接已恢复' if health['proxy_repaired'] else '代理与监听正常')
        save(OUT/'transport_health.json',health)
        return health
    except Exception as exc:
        health['message']=str(exc);save(OUT/'transport_health.json',health);raise


def restore(lease):
    restored, message = False, '没有更改代理。'
    if lease.get('proxy_applied'):
        current = read_proxy()
        expected = lease['applied_proxy']
        # Another proxy application may keep ProxyEnable=1 while replacing the
        # server: avoid disabling that newer configuration.
        core_unchanged = all(current[name] == expected[name] for name in ('ProxyEnable', 'ProxyServer'))
        changes = {name: lease['previous_proxy'][name] for name in VALUES
                   if current[name] == expected[name]} if core_unchanged else {}
        if changes:
            apply_proxy(changes)
            restored = True
        message = '已恢复程序设置的代理；保留运行期间由用户修改的项目。'
    owned=False
    if CA_OWNER.exists():
        owner=json.loads(CA_OWNER.read_text(encoding='utf-8'))
        owned=owner.get('owned') and owner.get('thumb')==lease.get('ca_thumbprint')
    if lease.get('ca_added') or owned:
        thumb = lease['ca_thumbprint']
        if trusted(thumb):
            from windows_user_ca import remove
            remove(thumb)
        save(CA_OWNER,{'owned':False,'thumb':thumb})
    lease.update(running=False, restored=restored, message=message, stopped_at=datetime.now(timezone.utc).isoformat())
    save(LEASE, lease)
    return lease


class Dashboard(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            body = (ROOT / 'capture_dashboard.html').read_bytes()
            mime = 'text/html; charset=utf-8'
        elif self.path == '/status':
            status_path = OUT / 'status.json'
            status = json.loads(status_path.read_text(encoding='utf-8')) if status_path.exists() else {}
            lease = json.loads(LEASE.read_text(encoding='utf-8')) if LEASE.exists() else {}
            body = json.dumps({'capture': status, 'service': {k: lease.get(k) for k in
                              ('running', 'proxy_applied', 'ca_added', 'message')}}, ensure_ascii=False).encode()
            mime = 'application/json; charset=utf-8'
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def run(args):
    if LEASE.exists():
        old = json.loads(LEASE.read_text(encoding='utf-8'))
        if old.get('running') and alive(old['pid']):
            raise RuntimeError('抓取服务已在运行。')
        if old.get('running'):
            restore(old)
    if port_open(6666) or port_open(8766):
        raise RuntimeError('6666 或 8766 端口已被占用；未修改系统配置。')
    thumb = ca_thumbprint()
    existing_trust = trusted(thumb)
    if args.enable_proxy and not (existing_trust or args.trust_ca):
        raise RuntimeError('HTTPS 需要证书信任；请在批准后使用 --trust-ca。')
    owner=json.loads(CA_OWNER.read_text(encoding='utf-8')) if CA_OWNER.exists() else {}
    lease = {'running': True, 'pid': os.getpid(), 'started_at': datetime.now(timezone.utc).isoformat(),
             'instance': args.instance,
             'proxy_applied': False, 'ca_added': bool(existing_trust and owner.get('owned') and owner.get('thumb')==thumb), 'ca_thumbprint': thumb,
             'previous_proxy': read_proxy(), 'message': '服务启动中。'}
    save(LEASE, lease)
    STOP.unlink(missing_ok=True)
    child, server = None, None
    stopping = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    try:
        addon = 'auto_capture.py'
        command = [str(ROOT / 'venv/Scripts/mitmdump.exe'), '--listen-host', '127.0.0.1', '-p', '6666',
                   '-s', str(ROOT / addon), '--set', 'confdir=' + str(ROOT / 'automatic'),
                   '--set', 'allow_hosts=^(?:[^.]+\\.)*easygame2021\\.com(?::443)?$',
                   '--set', 'flow_detail=0', '--set', 'termlog_verbosity=error']
        # No flow archive or verbose HTTP log is created.
        with (OUT / 'service_errors.log').open('w', encoding='utf-8') as errors:
            child = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=errors, creationflags=NO_WINDOW)
            lease['child_pid'] = child.pid
            save(LEASE, lease)
            deadline = time.monotonic()+15
            while not port_open(6666):
                if child.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('抓取代理未能启动，请检查 service_errors.log。')
                time.sleep(0.1)
        # Check that the addon loaded, not merely that the TCP socket opened.
        status = json.loads((OUT / 'status.json').read_text(encoding='utf-8'))
        if status.get('phase') != 'waiting_for_game_start':
            raise RuntimeError('抓取插件没有正常进入等待状态。')
        server = ThreadingHTTPServer(('127.0.0.1', 8766), Dashboard)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        if args.trust_ca and not existing_trust:
            # Save intent first: even a crash between import and checkpoint is recoverable.
            lease['ca_added'] = True
            save(LEASE, lease)
            save(CA_OWNER,{'owned':True,'thumb':thumb})
            from windows_user_ca import add
            certificate = x509.load_pem_x509_certificate((ROOT / 'automatic/mitmproxy-ca-cert.pem').read_bytes())
            try:
                add(thumb, certificate.public_bytes(Encoding.DER))
                # Verify in a fresh process too: the game must see persistent
                # Windows trust, not only a cached handle in this supervisor.
                reply=subprocess.run([sys.executable,'-c',
                    'import sys;from windows_user_ca import trusted;sys.exit(0 if trusted(sys.argv[1]) else 1)',thumb],
                    cwd=ROOT,capture_output=True,creationflags=NO_WINDOW,timeout=10)
                if reply.returncode:raise RuntimeError('新的 Windows 进程无法确认用户证书信任')
            except Exception as exc:
                save(OUT / 'activation_error.json', {'stage':'certificate_trust','message':str(exc)})
                raise RuntimeError('当前 Windows 会话未能确认临时证书信任，代理未启用：'+str(exc)) from exc
        if args.enable_proxy:
            expected = {'ProxyEnable': {'value': 1, 'kind': winreg.REG_DWORD},
                        'ProxyServer': {'value': PROXY, 'kind': winreg.REG_SZ},
                        'ProxyOverride': {'value': '<local>', 'kind': winreg.REG_SZ}, 'AutoConfigURL': None}
            lease.update(proxy_applied=True, applied_proxy=expected)
            save(LEASE, lease)
            apply_proxy(expected)
        lease['message'] = '等待你开始游戏。' if args.enable_proxy else '监听测试通过；尚未配置微信使用此代理。'
        save(LEASE, lease)
        deadline = time.monotonic() + args.hours*3600
        while not stopping.is_set() and not STOP.exists() and time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError('抓取子进程退出，正在恢复配置。')
            stopping.wait(0.25)
    except Exception as exc:
        lease.update(startup_error=str(exc),message=str(exc))
        save(OUT/'service_start_error.json',{'instance':args.instance,'message':str(exc),
            'at':datetime.now(timezone.utc).isoformat()})
        raise
    finally:
        if server:
            server.shutdown()
            server.server_close()
        try:
            restore(lease)
        finally:
            if child and child.poll() is None:
                # Stop the owned launcher and interpreter tree together.
                subprocess.run(['taskkill.exe', '/PID', str(child.pid), '/T', '/F'],
                               capture_output=True, creationflags=NO_WINDOW, timeout=10)
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
            STOP.unlink(missing_ok=True)


def startup_error(instance):
    for path in (OUT/'service_start_error.json',LEASE):
        try:
            error=json.loads(path.read_text(encoding='utf-8'))
            if error.get('instance')==instance:
                message=error.get('startup_error') or error.get('message')
                if message and message!='服务启动中。':return message
        except (OSError,ValueError):pass
    return '抓取服务启动失败，请查看后台服务日志。'


def desktop_dispatch(args):
    """The user's ROOT store/proxy must be read by the signed-in desktop token.

    Some host-launched processes inherit a different registry/certificate view.
    Dispatch a fixed project worker; never accept shell text from the request.
    """
    # Preserve upgrade ownership before the desktop broker changes the parent
    # chain. The service instance and expiring marker still have to match.
    if args.action=='stop' and LEASE.exists():
        lease=json.loads(LEASE.read_text(encoding='utf-8'))
        if preserve_upgrade(lease):
            print('应用升级交接：保留当前抓取服务、真实种子和解密状态。');return
    request=uuid.uuid4().hex;folder=OUT/'session_commands';folder.mkdir(parents=True,exist_ok=True)
    request_path=folder/(request+'.request.json');result_path=folder/(request+'.result.json')
    save(request_path,{'action':args.action,'enable_proxy':args.enable_proxy,'trust_ca':args.trust_ca,
        'hours':args.hours})
    try:
        launched=subprocess.run([str(ROOT/'DesktopSessionLauncher.exe'),request],cwd=ROOT,
            capture_output=True,creationflags=NO_WINDOW,timeout=8)
        if launched.returncode:raise RuntimeError('无法连接 Windows 桌面后台启动器：'+launched.stderr.decode('utf-8',errors='replace').strip())
        deadline=time.monotonic()+35
        while time.monotonic()<deadline:
            if result_path.exists():
                result=json.loads(result_path.read_text(encoding='utf-8'))
                if not result['ok']:raise RuntimeError(result['message'])
                if result.get('output'):print(result['output'].strip())
                return
            time.sleep(.1)
        raise RuntimeError('Windows 后台启动器尚未返回结果，请查看服务状态后重试')
    finally:
        # Worker deletes the request after completing its operation; a timeout
        # must not remove a request it may still be processing.
        if result_path.exists():
            result_path.unlink(missing_ok=True);request_path.unlink(missing_ok=True)


def desktop_worker(request):
    if len(request)!=32 or any(c not in '0123456789abcdef' for c in request):
        raise ValueError('Invalid desktop service request')
    folder=OUT/'session_commands';path=folder/(request+'.request.json');reply=folder/(request+'.result.json')
    os.environ['PYTHONIOENCODING']='utf-8'
    output=io.StringIO()
    try:
        command=json.loads(path.read_text(encoding='utf-8'));action=command['action']
        if action not in ('start','stop','check','repair'):raise ValueError('Unknown desktop service action')
        argv=[action,'--desktop-child','--hours',str(float(command.get('hours',4)))]
        for key,flag in [('enable_proxy','--enable-proxy'),('trust_ca','--trust-ca')]:
            if command.get(key):argv.append(flag)
        with contextlib.redirect_stdout(output):main(argv)
        result={'ok':True,'output':output.getvalue()}
    except Exception as exc:
        result={'ok':False,'message':str(exc)}
    save(reply,result);path.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['check', 'start', 'run', 'stop', 'desktop','repair'])
    parser.add_argument('--enable-proxy', action='store_true')
    parser.add_argument('--trust-ca', action='store_true')
    parser.add_argument('--hours', type=float, default=4)
    parser.add_argument('--instance', default='')
    parser.add_argument('--desktop-child',action='store_true')
    parser.add_argument('--request',default='')
    args = parser.parse_args(argv)
    if not 0 < args.hours <= 24:
        parser.error('--hours 必须在 0 到 24 之间')
    OUT.mkdir(parents=True, exist_ok=True)
    if args.action=='desktop':return desktop_worker(args.request)
    if args.action in ('start','stop','check','repair') and not args.desktop_child:return desktop_dispatch(args)
    if args.action=='repair':
        print(json.dumps(repair_transport(),ensure_ascii=False))
    elif args.action == 'check':
        print(json.dumps({'ca_trusted': trusted(ca_thumbprint()), 'proxy': read_proxy(),
                          'port_6666_open': port_open(6666), 'port_8766_open': port_open(8766)}, ensure_ascii=False))
    elif args.action == 'start':
        if LEASE.exists():
            old = json.loads(LEASE.read_text(encoding='utf-8'))
            if old.get('running') and alive(old['pid']):
                raise RuntimeError('服务已运行；更改配置前请先运行停止脚本。')
        instance = uuid.uuid4().hex
        command = [sys.executable, str(Path(__file__)), 'run', '--hours', str(args.hours), '--instance', instance]
        if args.enable_proxy:
            command.append('--enable-proxy')
        if args.trust_ca:
            command.append('--trust-ca')
        with (OUT / 'supervisor_errors.log').open('w', encoding='utf-8') as errors:
            child = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=errors,
                                     creationflags=NO_WINDOW)
        deadline = time.monotonic()+25
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError(startup_error(instance))
            if LEASE.exists():
                lease = json.loads(LEASE.read_text(encoding='utf-8'))
                if lease.get('instance') == instance and not lease.get('running'):
                    raise RuntimeError(startup_error(instance))
                if lease.get('instance') == instance and lease.get('running') and lease.get('message') != '服务启动中。':
                    print(json.dumps({'pid': lease['pid'], 'dashboard': 'http://127.0.0.1:8766',
                                      'message': lease['message']}, ensure_ascii=False))
                    return
            time.sleep(0.1)
        raise RuntimeError('服务初始化超时；可运行停止脚本恢复。')
    elif args.action == 'run':
        run(args)
    elif LEASE.exists():
        lease = json.loads(LEASE.read_text(encoding='utf-8'))
        if preserve_upgrade(lease):
            print('应用升级交接：保留当前抓取服务、真实种子和解密状态。')
            return
        if lease.get('running') and alive(lease['pid']):
            STOP.touch()
            deadline = time.monotonic()+20
            while alive(lease['pid']) and time.monotonic() < deadline:
                time.sleep(0.1)
            if alive(lease['pid']):
                raise RuntimeError('服务仍在关闭，请稍后重试。')
            # Reconcile durable CA ownership even when an older supervisor
            # did not read the new ownership marker during its cleanup.
            restore(json.loads(LEASE.read_text(encoding='utf-8')))
            print('服务已停止，配置恢复结果见 service_lease.json。')
        else:
            restore(lease)
            print('已恢复遗留配置。')


if __name__ == '__main__':
    try:main()
    except Exception as exc:
        print(str(exc),file=sys.stderr);raise SystemExit(1)
