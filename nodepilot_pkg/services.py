"""Only exact project-owned units may be changed or stopped."""
import time
import ipaddress
import os
from pathlib import Path

from .common import PilotError, atomic_bytes, run
from .model import validate


class Services:
    def __init__(self, state=Path("/etc/nodepilot"), units=Path("/etc/systemd/system"), cores=Path("/opt/nodepilot/cores")):
        self.state, self.units, self.cores = Path(state), Path(units), Path(cores)

    def content(self, n):
        validate(n)
        binary = self.cores / n["core"] / "current" / n["core"]
        config = self.state / "nodes" / n["id"] / "current/config.json"
        args = f"run -config {config}" if n["core"] == "xray" else f"run -c {config}"
        return (f"# NodePilot owned schema=1 id={n['id']}\n[Unit]\n"
                f"Description=NodePilot {n['id']}\nAfter=network-online.target\nWants=network-online.target\n"
                f"StartLimitIntervalSec=60\nStartLimitBurst=3\n[Service]\nType=simple\nUser=root\n"
                f"ExecStart={binary} {args}\nRestart=on-failure\nRestartSec=3\nTimeoutStopSec=15\n"
                "UMask=0077\nNoNewPrivileges=true\nProtectSystem=strict\nProtectHome=true\nPrivateTmp=true\n"
                "ProtectKernelTunables=true\nProtectKernelModules=true\nProtectControlGroups=true\n"
                "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK\nCapabilityBoundingSet=CAP_NET_BIND_SERVICE\n"
                "Environment=GOMEMLIMIT=64MiB\nEnvironment=GOGC=50\nEnvironment=GOMAXPROCS=1\nLimitNOFILE=8192\nTasksMax=64\n"
                "StandardOutput=journal\nStandardError=journal\n[Install]\nWantedBy=multi-user.target\n")

    def assert_owned(self, n, missing=False):
        path = self.units / n["service"]
        if path.is_symlink() or (path.exists() and path.read_text() != self.content(n)):
            raise PilotError(f"拒绝操作非本项目或被外部修改的服务：{n['service']}")
        if not path.exists() and not missing:
            raise PilotError("本项目服务单元缺失，拒绝停止同名系统服务。")
        loaded = run(["systemctl", "show", n["service"], "-p", "FragmentPath", "--value"])
        if path.exists() and loaded and loaded!=str(path):
            raise PilotError('已加载的同名服务来自其他位置，拒绝改写/停止。')
        if not path.exists():
            # Also reject vendor/generated units with the same name.
            if loaded:
                raise PilotError("同名服务已由其他位置提供。")
        else:
            dropins = run(["systemctl", "show", n["service"], "-p", "DropInPaths", "--value"])
            if dropins:
                raise PilotError("服务存在外部 systemd drop-in，拒绝自动停止/改写，请先人工核对。")
        return path

    def install(self, n):
        path = self.assert_owned(n, missing=True)
        atomic_bytes(path, self.content(n).encode(), mode=0o644)
        run(["systemctl", "daemon-reload"])
        run(["systemctl", "enable", n["service"]])

    def active(self, n):
        self.assert_owned(n)
        try:
            return run(["systemctl", "is-active", n["service"]]) == "active"
        except PilotError:
            return False

    def runtime(self, n):
        self.assert_owned(n)
        keys = ('ActiveState','SubState','Result','MainPID','ExecMainStatus','NRestarts')
        args = ['systemctl','show',n['service'],'--no-pager']
        for key in keys:
            args += ['-p',key]
        fields = dict(line.split('=',1) for line in run(args).splitlines() if '=' in line)
        for key in ('MainPID','ExecMainStatus','NRestarts'):
            try:
                fields[key] = int(fields.get(key,0))
            except ValueError:
                raise PilotError('systemd 状态字段无效。')
        fields['listening'] = listening(n,fields['MainPID']) if fields['MainPID'] > 1 else False
        if fields.get('ActiveState')=='activating' or fields.get('SubState')=='auto-restart':
            fields['state']='服务正在重启；检查本机日志和端口冲突'
        elif fields.get('ActiveState') != 'active' or fields.get('Result') not in {'success',''} or fields['ExecMainStatus']:
            fields['state'] = '服务启动失败/未运行'
        elif fields['NRestarts']:
            fields['state'] = '服务发生异常重启'
        elif not fields['listening']:
            fields['state'] = '服务运行但未监听配置端口'
        else:
            fields['state'] = '服务运行，监听正常；公网未验证'
        fields['healthy'] = fields['state'].startswith('服务运行，监听正常')
        return fields

    def restart(self, n):
        self.assert_owned(n)
        run(["systemctl", "reset-failed", n["service"]])
        run(["systemctl", "restart", n["service"]])
        # Do not mistake immediate 'active' before the core exits for a healthy restart.
        pid, stable = None, 0
        for attempt in range(16):
            time.sleep(0.5)
            state = self.runtime(n)
            # Type=simple becomes active before a slow core binds. Permit only
            # the initial clean active/running state a bounded readiness grace;
            # never excuse an exit/restart, PID change, or a later lost listener.
            if (pid is None and attempt<8 and not state['healthy'] and
                    state.get('ActiveState')=='active' and state.get('SubState')=='running' and
                    state.get('Result') in {'','success'} and state.get('ExecMainStatus')==0 and
                    state.get('NRestarts')==0 and state.get('MainPID',0)>1 and not state.get('listening')):
                continue
            if not state['healthy'] or (pid is not None and pid != state['MainPID']):
                raise PilotError(f"{n['service']}：{state['state']}；ActiveState={state.get('ActiveState')} Result={state.get('Result')} exit={state['ExecMainStatus']} restarts={state['NRestarts']}；检查本机 journalctl。")
            pid = state['MainPID']
            stable+=1
            if stable==8:return
        raise PilotError('服务未在有界等待内达到稳定监听状态。')

    def remove(self, n):
        path = self.assert_owned(n)
        run(["systemctl", "disable", "--now", n["service"]])
        path.unlink()
        run(["systemctl", "daemon-reload"])

    def logs(self, n):
        self.assert_owned(n)
        return run(["journalctl", "-u", n["service"], "-n", "60", "--no-pager"], timeout=15)


def listening(n, pid, proc=Path('/proc'), strict_family=False):
    """Require the configured socket to belong to this service's main process."""
    try:
        sockets = set()
        for descriptor in (proc/str(pid)/'fd').iterdir():
            try:
                target = os.readlink(descriptor)
                if target.startswith('socket:['):
                    sockets.add(target[8:-1])
            except OSError:
                continue
        for suffix in ('','6'):
            path = proc/'net'/(n['transport']+suffix)
            if not path.exists():
                continue
            for line in path.read_text().splitlines()[1:]:
                fields = line.split()
                address, port = fields[1].split(':')
                if fields[9] not in sockets or int(port,16) != n['port'] or fields[3] != ('0A' if n['transport']=='tcp' else '07'):
                    continue
                raw = bytes.fromhex(address)
                raw = raw[::-1] if len(raw)==4 else b''.join(raw[i:i+4][::-1] for i in range(0,16,4))
                actual, wanted = ipaddress.ip_address(raw), ipaddress.ip_address(n['listen'])
                if strict_family and actual.version != wanted.version:
                    continue
                if actual == wanted or actual.is_unspecified or (actual.version==6 and actual.ipv4_mapped==wanted):
                    return True
    except (OSError, ValueError, IndexError):
        return False
    return False
