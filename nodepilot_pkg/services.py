"""Only exact project-owned units may be changed or stopped."""
import time
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
        if not path.exists():
            # Also reject vendor/generated units with the same name.
            try:
                loaded = run(["systemctl", "show", n["service"], "-p", "FragmentPath", "--value"])
            except PilotError:
                loaded = ""
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

    def restart(self, n):
        self.assert_owned(n)
        run(["systemctl", "reset-failed", n["service"]])
        run(["systemctl", "restart", n["service"]])
        # Do not mistake immediate 'active' before the core exits for a healthy restart.
        for _ in range(4):
            time.sleep(0.5)
            if not self.active(n):
                raise PilotError(f"{n['service']} 启动后退出；检查本机 journalctl。")

    def remove(self, n):
        path = self.assert_owned(n)
        run(["systemctl", "disable", "--now", n["service"]])
        path.unlink()
        run(["systemctl", "daemon-reload"])

    def logs(self, n):
        self.assert_owned(n)
        return run(["journalctl", "-u", n["service"], "-n", "60", "--no-pager"], timeout=15)
