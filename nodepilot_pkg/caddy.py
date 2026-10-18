"""One isolated Caddy for all self-hosted Reality targets; no system Caddy takeover."""
import time
from pathlib import Path

from .common import PilotError, atomic_bytes, atomic_json, private_dir, run
from .model import validate
from .system import dns_matches, http01_bindings, port_free, probe_reality, public_ips

UNIT = "nodepilot-caddy.service"


def own_target(n):
    return n["protocol"] == "reality" and n["reality"].get("mode") == "self"


def configuration(nodes, state):
    """Pure aggregate config. Each node has its own loopback listener and SNI."""
    state = Path(state) / "caddy"
    servers, policies, automate, loads = {}, [], [], []
    for n in nodes:
        if not own_target(n):
            continue
        validate(n)
        c, domain = n["caddy"], n["sni"]
        handler = {"handler": "static_response", "status_code": 200,
                   "headers": {"Content-Type": ["text/html; charset=utf-8"]},
                   "body": "<!doctype html><html><title>Welcome</title><body><h1>Welcome</h1></body></html>"}
        if c.get("upstream"):
            upstream, _ = c["upstream"].rsplit(":", 1)
            handler = {"handler": "reverse_proxy", "upstreams": [{"dial": c["upstream"]}],
                       "transport": {"protocol": "http", "tls": {"server_name": upstream}},
                       "headers": {"request": {"set": {"Host": [upstream]}}}}
        servers[n["id"]] = {
            "listen": [f"127.0.0.1:{c['port']}"], "protocols": ["h1", "h2"],
            "read_header_timeout": 10000000000, "idle_timeout": 60000000000,
            "automatic_https": {"disable_redirects": True},
            "listener_wrappers": [{"wrapper": "proxy_protocol", "allow": ["127.0.0.1/32"],
                                   "fallback_policy": "REJECT", "timeout": 2000000000}, {"wrapper": "tls"}],
            "tls_connection_policies": [{"match": {"sni": [domain]}, "protocol_min": "tls1.3", "alpn": ["h2", "http/1.1"]}],
            "routes": [{"match": [{"host": [domain]}], "handle": [handler]}]}
        if c["certificate_mode"] == "acme":
            issuer = {"module": "acme", "ca": "https://acme-v02.api.letsencrypt.org/directory",
                      "challenges": {"tls-alpn": {"disabled": True}}}
            if c.get("email"):
                issuer["email"] = c["email"]
            policies.append({"subjects": [domain], "issuers": [issuer]})
            automate.append(domain)
        else:
            servers[n["id"]]["automatic_https"]["disable"] = True
            loads.append({"certificate": c["certificate"], "key": c["key"]})
    if automate:
        # Caddy handles HTTP-01 on this shared server; Xray retains its public port.
        bindings = {address for n in nodes if own_target(n) and n["caddy"]["certificate_mode"] == "acme"
                    for address in n["caddy"].get("http01_listen", ["tcp4/0.0.0.0:80", "tcp6/[::]:80"])}
        # Migrate legacy metadata without depending on IPV6_V6ONLY defaults.
        migrated = set()
        for address in bindings:
            if address in {':80', '[::]:80'}:
                migrated.update({'tcp4/0.0.0.0:80', 'tcp6/[::]:80'})
            else:
                migrated.add('tcp4/0.0.0.0:80' if address=='0.0.0.0:80' else address)
        bindings = migrated
        servers["http01"] = {"listen": sorted(bindings), "protocols": ["h1"],
                             "routes": [{"handle": [{"handler": "static_response", "status_code": 404}]}]}
    return {"admin": {"listen": "unix/" + (state / "admin.sock").as_posix(), "config": {"persist": False}},
            "storage": {"module": "file_system", "root": (state / "storage").as_posix()},
            "logging": {"logs": {"default": {"level": "WARN"}}},
            "apps": {"http": {"servers": servers},
                     "tls": {"certificates": {"automate": automate, "load_files": loads},
                             "automation": {"policies": policies}}}}


class Caddy:
    def __init__(self, state, cores, units=Path("/etc/systemd/system")):
        self.state, self.cores, self.units = Path(state), cores, Path(units)

    def content(self):
        directory = self.state / "caddy"
        binary = self.cores.root / "caddy/current/caddy"
        return ("# NodePilot shared Caddy owned schema=1\n[Unit]\nDescription=NodePilot local Reality targets\n"
                "After=network-online.target\nWants=network-online.target\n[Service]\nType=simple\nUser=root\n"
                f"ExecStart={binary} run --config {directory / 'config.json'}\n"
                "Restart=on-failure\nRestartSec=3\nTimeoutStopSec=15\nUMask=0077\nNoNewPrivileges=true\n"
                "ProtectSystem=strict\nProtectHome=true\nPrivateTmp=true\nProtectKernelTunables=true\n"
                "ProtectKernelModules=true\nProtectControlGroups=true\n"
                f"ReadWritePaths={directory}\n"
                "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK\nCapabilityBoundingSet=CAP_NET_BIND_SERVICE\n"
                "Environment=GOMEMLIMIT=32MiB\nEnvironment=GOGC=50\nEnvironment=GOMAXPROCS=1\n"
                "LimitNOFILE=4096\nTasksMax=64\n[Install]\nWantedBy=multi-user.target\n")

    def assert_owned(self, missing=False):
        path = self.units / UNIT
        if path.is_symlink() or (path.exists() and path.read_text() != self.content()):
            raise PilotError("拒绝接管非本项目或已被修改的 Caddy 单元。")
        loaded = run(["systemctl", "show", UNIT, "-p", "FragmentPath", "--value"])
        if loaded and (not path.exists() or loaded != str(path)):
            raise PilotError('已加载的同名 Caddy 来自外部路径，拒绝操作。')
        if path.exists():
            if run(["systemctl", "show", UNIT, "-p", "DropInPaths", "--value"]):
                raise PilotError("NodePilot Caddy 存在外部 drop-in，拒绝接管。")
        elif not missing:
            raise PilotError("NodePilot Caddy 单元缺失或同名服务由其他位置提供。")
        return path

    def active(self):
        try:
            return run(["systemctl", "is-active", UNIT]) == "active"
        except PilotError:
            return False

    def preflight(self, n, old, others):
        self.assert_owned(missing=True)
        if not own_target(n):
            return
        c = n["caddy"]
        for other in others:
            if other["id"] == n["id"]:
                continue
            if own_target(other) and (other["sni"] == n["sni"] or other["caddy"]["port"] == c["port"]):
                raise PilotError("自建目标域名/本机端口已由另一节点使用。")
            if other["transport"] == "tcp" and other["port"] == c["port"]:
                raise PilotError("Caddy 本机端口与节点 TCP 端口冲突。")
        if not (old and own_target(old) and old["caddy"]["port"] == c["port"] and self.active()):
            port_free("127.0.0.1", c["port"], "tcp")
        if c["certificate_mode"] == "acme":
            if old and own_target(old) and old["sni"] == n["sni"] and old["caddy"]["certificate_mode"] == "acme":
                from .certificates import validate_pair
                try:
                    files = self.certificate_files(old)
                    validate_pair(files["certificate.pem"], files["key.pem"], n["sni"])
                    # A reusable certificate does not prove future challenge listeners.
                    pass
                except PilotError:
                    pass
            bindings = http01_bindings(dns_matches(n["sni"], public_ips()))
            c["http01_listen"] = ["tcp6/[::]:80" if address == "::" else "tcp4/0.0.0.0:80" for address in bindings]
            existing = [o for o in others if own_target(o) and o['caddy']['certificate_mode']=='acme']
            for address, listener in zip(bindings, c['http01_listen']):
                legacy = '0.0.0.0:80' if address=='0.0.0.0' else '[::]:80'
                if self.active() and any(listener in o['caddy'].get('http01_listen',[]) or legacy in o['caddy'].get('http01_listen',[]) for o in existing):
                    self.verify_http_listener(address)
                else:
                    port_free(address, 80, "tcp", v6only=True)

    def verify_http_listener(self, address):
        """A local family-specific connection proves only the local listener."""
        import socket
        from .services import listening
        pid = int(run(['systemctl','show',UNIT,'-p','MainPID','--value']) or '0')
        if not listening({'listen':address,'port':80,'transport':'tcp'}, pid, strict_family=True):
            raise PilotError('Caddy HTTP-01 监听归属/地址族未验证；检查服务，或改用 DNS-01。')
        family = socket.AF_INET6 if address=='::' else socket.AF_INET
        try:
            with socket.socket(family,socket.SOCK_STREAM) as sock:
                sock.settimeout(2)
                sock.connect(('::1' if family==socket.AF_INET6 else '127.0.0.1',80))
        except OSError:
            raise PilotError('Caddy HTTP-01 本机分族连接失败；检查监听或使用 DNS-01。') from None

    def prepare(self, n, revision, renew=False):
        if own_target(n) and n["caddy"]["certificate_mode"] != "acme":
            from .certificates import holder, prepare
            c = n["caddy"]
            certificate_node = holder(n)
            prepare(certificate_node, revision, renew, self.state)
            c.update(certificate=certificate_node['tls']['certificate'], key=certificate_node['tls']['key'])

    def check(self, nodes, binary=None):
        directory = private_dir(self.state / "caddy")
        private_dir(directory / "storage")
        candidate = directory / "candidate.json"
        atomic_json(candidate, configuration(nodes, self.state))
        try:
            run([binary or self.cores.binary("caddy"), "validate", "--config", candidate], timeout=30)
            return candidate.read_bytes()
        finally:
            candidate.unlink(missing_ok=True)

    def load(self, restart=False):
        directory = self.state / "caddy"
        if self.active() and not restart:
            run([self.cores.binary("caddy"), "reload", "--address", "unix/" + str(directory / "admin.sock"),
                 "--config", directory / "config.json"], timeout=30)
        else:
            run(["systemctl", "reset-failed", UNIT])
            run(["systemctl", "restart", UNIT])
        for _ in range(4):
            time.sleep(0.5)
            if not self.active():
                raise PilotError("NodePilot Caddy 启动后退出；请检查独立服务日志。")

    def sync(self, nodes, restart=False):
        own = [n for n in nodes if own_target(n)]
        path = self.units / UNIT
        if not own and not path.exists() and not path.is_symlink():
            return
        self.assert_owned(missing=True)
        if not own:
            run(["systemctl", "disable", "--now", UNIT])
            path.unlink()
            run(["systemctl", "daemon-reload"])
            return
        data = self.check(nodes)
        config = self.state / "caddy/config.json"
        if config.is_symlink():
            raise PilotError('Caddy 配置为外部链接，拒绝覆盖。')
        previous = config.read_bytes() if config.exists() else None
        installed = path.exists()
        try:
            atomic_bytes(config, data)
            if not installed:
                atomic_bytes(path, self.content().encode(), mode=0o644)
                run(["systemctl", "daemon-reload"])
                run(["systemctl", "enable", UNIT])
            self.load(restart)
            import json
            listeners=json.loads(data)['apps']['http']['servers'].get('http01',{}).get('listen',[])
            for address in listeners:
                self.verify_http_listener('::' if address.startswith('tcp6/') else '0.0.0.0')
        except (Exception, KeyboardInterrupt):
            if previous is not None:
                atomic_bytes(config, previous)
                if installed:
                    self.load()
            elif path.exists():
                self.assert_owned()
                run(["systemctl", "disable", "--now", UNIT])
                path.unlink()
                run(["systemctl", "daemon-reload"])
            raise

    def restore_configuration(self, data):
        """Restore exact pre-transaction bytes, including legacy listener semantics."""
        self.assert_owned(missing=True)
        if len(data)>256*1024:
            raise PilotError('Caddy 回滚配置超过上限。')
        directory=private_dir(self.state/'caddy')
        candidate=directory/'rollback.json'
        atomic_bytes(candidate,data)
        try:
            run([self.cores.binary('caddy'),'validate','--config',candidate])
        finally:
            candidate.unlink(missing_ok=True)
        atomic_bytes(directory/'config.json',data)
        path=self.units/UNIT
        if not path.exists():
            atomic_bytes(path,self.content().encode(),mode=0o644)
            run(['systemctl','daemon-reload'])
            run(['systemctl','enable',UNIT])
        self.load()

    def ready(self, n, timeout=180):
        c = n["caddy"]
        cafile = c.get("certificate") if c["certificate_mode"] in {"import",'selfsigned'} else None
        deadline = time.monotonic() + timeout
        while True:
            try:
                probe_reality(n["reality"]["target"], n["sni"], proxy=True, cafile=cafile, timeout=3)
                return
            except PilotError:
                if time.monotonic() >= deadline or not self.active():
                    raise PilotError("Caddy 目标证书/TLS 1.3/h2 尚未就绪；检查域名 DNS、TCP 80 和 nodepilot-caddy 日志。")
                time.sleep(2)

    def certificate_files(self, n):
        if not own_target(n):
            return {}
        c = n["caddy"]
        if c["certificate_mode"] != "acme":
            return {"certificate.pem": Path(c["certificate"]), "key.pem": Path(c["key"])}
        domain = n["sni"].lower()
        base = self.state / "caddy/storage/certificates/acme-v02.api.letsencrypt.org-directory" / domain
        files = {"certificate.pem": base / (domain + ".crt"), "key.pem": base / (domain + ".key")}
        if not all(p.is_file() and not p.is_symlink() for p in files.values()):
            raise PilotError("Caddy 证书尚不可备份；拒绝执行会丢失证书的操作。")
        return files
