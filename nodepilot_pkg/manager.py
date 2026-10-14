"""Serialized, durable node revisions with native validation and restart rollback."""
import base64
import copy
import json
import os
import secrets
import shutil
from contextlib import contextmanager
from pathlib import Path

from . import certificates
from .caddy import Caddy, own_target
from .common import PilotError, atomic_bytes, atomic_json, fsync_dir, private_dir, read_json
from .config import generate
from .cores import Cores
from .model import ID, validate
from .services import Services
from .system import conflicts, port_free, probe_reality, same_binding

OWNER = "NodePilot schema=1\n"


class Manager:
    def __init__(self, state=Path("/etc/nodepilot"), cores=None, services=None, caddy=None):
        self.state = Path(state)
        self.cores = cores or Cores()
        self.services = services or Services(state)
        self.caddy = caddy or Caddy(self.state, self.cores, self.services.units)

    def initialize(self):
        marker = self.state / ".owner"
        if self.state.exists() and (self.state.is_symlink() or marker.is_symlink() or not marker.is_file() or marker.read_text() != OWNER):
            raise PilotError("配置目录已有非 NodePilot 内容；拒绝接管/覆盖。")
        private_dir(self.state)
        atomic_bytes(marker, OWNER.encode())
        for directory in ("nodes", "backups", "acme"):
            private_dir(self.state / directory)

    @contextmanager
    def locked(self):
        import fcntl
        self.initialize()
        lock = self.state / ".lock"
        with lock.open("a") as f:
            lock.chmod(0o600)
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as e:
                raise PilotError("另一个 NodePilot 操作正在运行，请稍后重试。") from e
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def node_dir(self, node_id):
        if not ID.fullmatch(node_id):
            raise PilotError("非法节点 ID。")
        path = self.state / "nodes" / node_id
        if path.is_symlink():
            raise PilotError("拒绝节点目录符号链接。")
        return path

    def revision(self, node_id):
        path = self.node_dir(node_id)
        current = path / "current"
        if not current.is_symlink():
            raise PilotError("节点没有有效 current 修订。")
        destination = current.resolve(strict=True)
        if destination.parent != (path / "revisions").resolve() or not destination.name.startswith("r-"):
            raise PilotError("节点修订越界。")
        return destination

    def get(self, node_id):
        n = validate(read_json(self.revision(node_id) / "node.json"))
        if n["id"] != node_id:
            raise PilotError("节点目录与元数据 ID 不一致。")
        return n

    def nodes(self):
        if not (self.state / "nodes").exists():
            return []
        return [self.get(p.name) for p in sorted((self.state / "nodes").iterdir()) if (p / "current").is_symlink()]

    def point(self, node_id, revision):
        path = self.node_dir(node_id)
        if Path(revision).resolve().parent != (path / "revisions").resolve():
            raise PilotError("修订路径越界。")
        link = path / ".next"
        link.unlink(missing_ok=True)
        link.symlink_to(Path(revision).relative_to(path), target_is_directory=True)
        os.replace(link, path / "current")
        fsync_dir(path)

    def backup(self):
        nodes = []
        for n in self.nodes():
            revision = self.revision(n["id"])
            files = {name: base64.b64encode((revision / name).read_bytes()).decode()
                     for name in ("certificate.pem", "key.pem") if (revision / name).exists()}
            if own_target(n):
                files = {name: base64.b64encode(p.read_bytes()).decode()
                         for name, p in self.caddy.certificate_files(n).items()}
            nodes.append({"node": n, "files": files})
        path = self.state / "backups" / ("backup-" + secrets.token_hex(8) + ".json")
        from .adoption import Adoptions
        bundle = {"schema": 1, "nodes": nodes, "adoptions": Adoptions(self).records()}
        if len(json.dumps(bundle, ensure_ascii=False).encode()) > 8 * 1024 * 1024:
            raise PilotError("节点/接管备份超过 8MiB；请先另存接管记录目录并清理已取消/归还记录。")
        atomic_json(path, bundle)
        return path

    def apply(self, node, probe=True, renew=False):
        # Caller holds the global lock. Revision dirs stay immutable once published.
        n = copy.deepcopy(node)
        validate(n)
        path = self.node_dir(n["id"])
        old = self.get(n["id"]) if (path / "current").is_symlink() else None
        old_revision = self.revision(n["id"]) if old else None
        for other in self.nodes():
            if other["id"] != n["id"] and conflicts(n, other):
                raise PilotError(f"端口与节点 {other['id']} 的 {other['transport']} 监听冲突。")
            if own_target(other) and n["transport"] == "tcp" and (n["port"] == other["caddy"]["port"] or
                    (n["port"] == 80 and other["caddy"]["certificate_mode"] == "acme")):
                raise PilotError("TCP 端口由 NodePilot Caddy 目标/HTTP-01 使用。")
        if not old or not same_binding(n, old) or not self.services.active(old):
            port_free(n["listen"], n["port"], n["transport"])
        managed = own_target(n) or (old and own_target(old))
        if managed:
            self.caddy.preflight(n, old, self.nodes())
        target_changed = not old or own_target(old) or (n["protocol"] == "reality" and any(
            n["reality"].get(k) != old["reality"].get(k) for k in ("target", "server_names", "xver")))
        if probe and n["protocol"] == "reality" and not own_target(n) and target_changed:
            from .targets import inspect_target, cloudflare_networks
            networks = cloudflare_networks()
            for sni in n["reality"]["server_names"]:
                inspect_target(n["reality"]["target"], sni, networks)
                probe_reality(n["reality"]["target"], sni, proxy=bool(n["reality"].get("xver")))
        if old:
            self.services.assert_owned(old)
            self.backup()
        elif path.exists():
            raise PilotError("已有未完成节点目录；请先执行恢复，不覆盖。")
        else:
            self.services.assert_owned(n, missing=True)
        private_dir(path)
        revision = private_dir(private_dir(path / "revisions") / ("r-" + secrets.token_hex(8)))
        pending = path / "pending.json"
        atomic_json(pending, {"kind": "node", "old": str(old_revision) if old else None, "node": n})
        try:
            if n["protocol"] != "reality":
                certificates.prepare(n, revision, renew, self.state)
            elif own_target(n):
                self.caddy.prepare(n, revision, renew)
            atomic_json(revision / "node.json", n)
            atomic_json(revision / "config.json", generate(n))
            self.cores.check(n, revision / "config.json")
            self.point(n["id"], revision)
            if managed:
                self.caddy.sync(self.nodes())
                if own_target(n):
                    self.caddy.ready(n)
            if not old:
                self.services.install(n)
            self.services.restart(n)
        except (Exception, KeyboardInterrupt) as error:
            try:
                if old:
                    self.point(n["id"], old_revision)
                    if managed:
                        self.caddy.sync(self.nodes())
                    self.services.restart(old)
                else:
                    self.services.assert_owned(n, missing=True)
                    if (self.services.units / n["service"]).exists():
                        self.services.remove(n)
                    (path / "current").unlink(missing_ok=True)
                    if managed:
                        self.caddy.sync(self.nodes())
                    shutil.rmtree(path)
            except Exception as rollback:
                raise PilotError(f"操作失败且自动回滚未完成：{rollback}；保留 pending.json，重新进入菜单恢复。") from error
            if old:
                pending.unlink(missing_ok=True)
            raise PilotError(f"操作失败，已回滚：{error}") from error
        pending.unlink()
        fsync_dir(path)
        if (n['protocol'] != 'reality' and n['tls']['mode'] == 'acme') or (own_target(n) and n['caddy']['certificate_mode']=='dns01'):
            certificates.event(self.state, n, '有效证书已安全安装；服务/监听通过，公网未验证')
        return n

    def delete(self, node_id):
        n = self.get(node_id)
        self.services.assert_owned(n)
        backup = self.backup()
        path = self.node_dir(node_id)
        atomic_json(path / "pending.json", {"kind": "delete", "node": n})
        try:
            self.services.remove(n)
            if own_target(n):
                self.caddy.sync([o for o in self.nodes() if o["id"] != node_id])
        except (Exception, KeyboardInterrupt):
            if not (self.services.units / n["service"]).exists():
                self.services.install(n)
            if own_target(n):
                self.caddy.sync(self.nodes())
            self.services.restart(n)
            (path / "pending.json").unlink(missing_ok=True)
            raise
        # Delete only the exact, validated random-ID subtree. ACME data is separately owned.
        shutil.rmtree(path)
        acme_dir = self.state / "acme" / node_id
        if acme_dir.is_dir() and not acme_dir.is_symlink():
            shutil.rmtree(acme_dir)
        return backup

    def restart(self, n):
        if own_target(n):
            self.caddy.sync(self.nodes())
            self.caddy.ready(n)
        self.services.restart(n)

    def recover(self):
        for path in sorted((self.state / "nodes").iterdir()):
            pending = path / "pending.json"
            if not pending.exists():
                continue
            record = read_json(pending)
            n = validate(record["node"])
            if n["id"] != path.name:
                raise PilotError("恢复记录 ID 与目录不匹配。")
            self.services.assert_owned(n, missing=True)
            if record["kind"] == "delete":
                if (self.services.units / n["service"]).exists():
                    self.services.remove(n)
                if own_target(n):
                    self.caddy.sync([o for o in self.nodes() if o["id"] != n["id"]])
                shutil.rmtree(path)
            elif record.get("old"):
                self.point(n["id"], Path(record["old"]))
                if own_target(n) or own_target(self.get(n["id"])):
                    self.caddy.sync(self.nodes())
                self.services.restart(self.get(n["id"]))
                pending.unlink()
            else:
                if (self.services.units / n["service"]).exists():
                    self.services.remove(n)
                (path / "current").unlink(missing_ok=True)
                if own_target(n):
                    self.caddy.sync(self.nodes())
                shutil.rmtree(path)

    def update_core(self, core, selected="latest"):
        affected = [n for n in self.nodes() if n["core"] == core or (core == "caddy" and own_target(n))]
        for n in affected:
            self.services.assert_owned(n)
        self.backup()
        destination = self.cores.stage(core) if selected == "latest" else self.cores.stage(core, selected)
        if core == "caddy" and affected:
            self.caddy.check(self.nodes(), destination / core)
        else:
            for n in affected:
                self.cores.check(n, self.revision(n["id"]) / "config.json", destination / core)
        current = self.cores.root / core / "current"
        old = current.resolve() if current.exists() else None
        pending = self.state / "core-pending.json"
        atomic_json(pending, {"core": core, "old": str(old) if old else None})
        try:
            self.cores.switch(core, destination)
            if core == "caddy" and affected:
                self.caddy.sync(self.nodes(), restart=True)
                for n in affected:
                    self.caddy.ready(n)
            for n in affected:
                self.services.restart(n)
        except (Exception, KeyboardInterrupt) as e:
            if old:
                self.cores.switch(core, old)
                if core == "caddy" and affected:
                    self.caddy.sync(self.nodes(), restart=True)
                for n in affected:
                    self.services.restart(n)
            else:
                current.unlink(missing_ok=True)
            pending.unlink(missing_ok=True)
            raise PilotError("内核更新失败，已恢复旧版本。") from e
        pending.unlink()
        return destination

    def recover_core(self):
        pending = self.state / "core-pending.json"
        if pending.exists():
            record = read_json(pending)
            core = record["core"]
            if core not in {"xray", "sing-box", "caddy"}:
                raise PilotError("非法内核恢复记录。")
            if record["old"]:
                old = Path(record["old"])
                if old.resolve().parent != (self.cores.root / core).resolve():
                    raise PilotError("内核恢复路径越界。")
                self.cores.switch(core, old)
                if core == "caddy":
                    self.caddy.sync(self.nodes(), restart=True)
                for n in self.nodes():
                    if n["core"] == core:
                        self.services.restart(n)
            else:
                (self.cores.root / core / "current").unlink(missing_ok=True)
            pending.unlink()

    def restore(self, backup):
        # Portable backups restore only into an empty node set, with rollback on failure.
        if self.nodes():
            raise PilotError("恢复只允许空节点目录，防止覆盖现有节点；请先备份并删除节点。")
        path = Path(backup)
        if path.stat().st_size > 8 * 1024 * 1024:
            raise PilotError("备份文件过大。")
        bundle = read_json(path)
        if bundle.get("schema") != 1 or len(bundle.get("nodes", [])) > 100:
            raise PilotError("备份格式无效。")
        import tempfile
        restored = []
        try:
            with tempfile.TemporaryDirectory(prefix="restore-", dir=self.state) as tmp:
                for entry in bundle["nodes"]:
                    n = validate(entry["node"])
                    if n["protocol"] != "reality" or own_target(n):
                        for name in ("certificate.pem", "key.pem"):
                            data = base64.b64decode(entry["files"][name], validate=True)
                            if len(data) > 256 * 1024:
                                raise PilotError("证书备份过大。")
                            atomic_bytes(Path(tmp) / name, data)
                        if own_target(n):
                            n["caddy"].update(certificate_mode="import", certificate=str(Path(tmp) / "certificate.pem"), key=str(Path(tmp) / "key.pem"))
                        else:
                            n["tls"].update(mode="import", certificate=str(Path(tmp) / "certificate.pem"), key=str(Path(tmp) / "key.pem"))
                    saved = self.apply(n)
                    restored.append(saved["id"])
        except (Exception, KeyboardInterrupt):
            for node_id in reversed(restored):
                self.delete(node_id)
            raise
        return restored
