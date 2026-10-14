"""Explicit, journaled handover of standalone cores; original files are never rewritten."""
import base64
import copy
import hashlib
import os
import re
import shlex
import time
from pathlib import Path

from . import certificates
from .caddy import own_target
from .common import PilotError, atomic_bytes, atomic_json, private_dir, read_json, run
from .config import generate
from .importer import convert, parse_json
from .model import validate
from .system import conflicts

ADOPTION_ID = re.compile(r"a-[0-9a-f]{16}\Z")


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(256 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def bounded_file(path):
    p = Path(path)
    if not p.is_absolute() or not p.resolve().is_file() or p.stat().st_size > 256 * 1024:
        raise PilotError("接管源文件必须解析到明确的普通文件，且不超过 256KiB。")
    return p.read_bytes()


def snapshot_file(path):
    data = bounded_file(path)
    return {"path": str(path), "target": str(Path(path).resolve()), "sha256": hashlib.sha256(data).hexdigest(),
            "data": base64.b64encode(data).decode()}


class SourceService:
    def inspect(self, service):
        if not isinstance(service, str) or len(service) > 100 or not re.fullmatch(r"[A-Za-z0-9_.@-]+\.service", service):
            raise PilotError("请输入明确的 systemd 服务名称。")
        if service in {"ssh.service", "sshd.service"} or service.startswith(("systemd-", "nodepilot-n-", "nodepilot-caddy", "nodepilot-renew")):
            raise PilotError("不能把系统关键服务或已有 NodePilot 单元作为接管源。")
        properties = ("FragmentPath", "DropInPaths", "ExecStart", "ExecStartPre", "ExecStartPost", "MainPID",
                      "ActiveState", "UnitFileState", "TriggeredBy", "Triggers", "PartOf", "BindsTo")
        args = ["systemctl", "show", service, "--no-pager"]
        for key in properties:
            args += ["-p", key]
        raw = run(args)
        fields = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
        if any(fields.get(key) for key in ("ExecStartPre", "ExecStartPost", "TriggeredBy", "Triggers", "PartOf", "BindsTo")):
            raise PilotError("服务有关联启动命令/触发器/控制器，不能作为独立内核安全接管。")
        command = fields.get("ExecStart", "")
        match = re.fullmatch(r"\{ path=([^;]+?) ; argv\[\]=(.*?) ; ignore_errors=.*? \}", command)
        if not match:
            raise PilotError("不能识别独立内核的唯一 ExecStart；面板/包装脚本不自动接管。")
        executable = Path(match[1]).resolve()
        arguments = shlex.split(match[2])
        if not arguments or Path(arguments[0]).resolve() != executable:
            raise PilotError("ExecStart 可执行文件关联不明确。")
        name = executable.name
        core = "xray" if name == "xray" or name.startswith("xray-") else "sing-box" if name == "sing-box" else None
        if core is None:
            raise PilotError("该服务不是直接运行 Xray/sing-box；x-ui 等面板须先导出独立配置，不能误停面板。")
        if not executable.is_file() or executable.stat().st_size > 180 * 1024 * 1024:
            raise PilotError("原内核文件不存在或大小异常。")
        configs, directory, i = [], None, 1
        while i < len(arguments):
            item = arguments[i]
            if item == "run":
                i += 1
            elif item in {"-c", "--config", "-config", "-C", "--config-directory", "-D", "--directory"} and i + 1 < len(arguments):
                value = Path(arguments[i + 1])
                if not value.is_absolute() or value.is_symlink():
                    raise PilotError("仅接管明确绝对路径的配置/目录。")
                if item in {"-c", "--config", "-config"}:
                    configs.append(value)
                elif item in {"-C", "--config-directory"}:
                    if core != "sing-box" or directory is not None:
                        raise PilotError("配置目录不明确。")
                    directory = value
                    configs.extend(sorted(value.glob("*.json")))
                i += 2
            else:
                raise PilotError("原内核包含当前无法无损识别的启动参数。")
        if len(configs) != 1:
            raise PilotError("只支持单个配置文件；不合并或丢弃多配置目录。")
        if fields.get("UnitFileState") not in {"enabled", "enabled-runtime", "disabled", "static"}:
            raise PilotError("原服务启用状态不属于可恢复的标准类型。")
        active = fields.get("ActiveState") == "active"
        if fields.get("ActiveState") not in {"active", "inactive", "failed"}:
            raise PilotError("原服务正在改变状态，请稍后重新检查。")
        if active:
            pid = int(fields.get("MainPID", "0"))
            if pid <= 1 or Path(os.readlink(f"/proc/{pid}/exe")).resolve() != executable:
                raise PilotError("实际主进程不是所选独立内核。")
        unit = Path(fields.get("FragmentPath", ""))
        if not unit.is_absolute():
            raise PilotError("找不到原服务单元文件。")
        paths = [unit, *map(Path, shlex.split(fields.get("DropInPaths", ""))), configs[0]]
        files = [{**snapshot_file(p), "kind": "config" if p == configs[0] else "unit"} for p in paths]
        return {"service": service, "core": core, "config": str(configs[0]), "executable": str(executable),
                "binary_sha256": digest(executable), "argv": arguments, "files": files,
                "config_directory": str(directory) if directory else None,
                "was_active": active, "enabled": fields["UnitFileState"]}

    def unchanged(self, snapshot):
        current = self.inspect(snapshot["service"])
        for key in ("core", "config", "executable", "binary_sha256", "argv", "config_directory"):
            if current[key] != snapshot[key]:
                raise PilotError("原服务身份/配置路径已变化；保留备份，不自动覆盖外部修改。")
        actual_files = {entry["path"]: entry for entry in current["files"]}
        for entry in snapshot["files"]:
            if entry["path"] not in actual_files:
                actual_files[entry["path"]] = snapshot_file(entry["path"])
        protected = [entry for entry in snapshot["files"] if entry.get("kind") != "certificate"]
        expected = {entry["path"]: (entry["target"], entry["sha256"]) for entry in protected}
        actual = {entry["path"]: (entry["target"], entry["sha256"]) for entry in current["files"]}
        if actual != expected:
            raise PilotError("原配置或服务文件已被外部修改；拒绝自动切换/归还，请核对私有备份。")
        # Original external certificate renewers may legitimately rotate a PEM or
        # its symlink. Validate the current pair/SAN instead of undoing renewal.
        for pair in snapshot.get("certificates", []):
            certificates.validate_pair(pair["certificate"], pair["key"], pair["sni"])
        return current

    def stop(self, snapshot):
        self.unchanged(snapshot)
        run(["systemctl", "stop", snapshot["service"]])

    def disable(self, snapshot):
        self.unchanged(snapshot)
        if snapshot["enabled"].startswith("enabled"):
            args = ["systemctl", "disable"]
            if snapshot["enabled"] == "enabled-runtime":
                args.append("--runtime")
            run([*args, snapshot["service"]])

    def restore(self, snapshot):
        self.unchanged(snapshot)
        if snapshot["enabled"].startswith("enabled"):
            args = ["systemctl", "enable"]
            if snapshot["enabled"] == "enabled-runtime":
                args.append("--runtime")
            run([*args, snapshot["service"]])
        if snapshot["was_active"]:
            run(["systemctl", "reset-failed", snapshot["service"]])
            run(["systemctl", "start", snapshot["service"]])
            for _ in range(4):
                time.sleep(.5)
                if run(["systemctl", "is-active", snapshot["service"]]) != "active":
                    raise PilotError("原服务未恢复运行，保留接管恢复记录。")


class Adoptions:
    def __init__(self, manager, source=None):
        self.manager, self.source = manager, source or SourceService()
        self.root = manager.state / "adoptions"

    def path(self, identity):
        if not ADOPTION_ID.fullmatch(identity):
            raise PilotError("非法接管记录 ID。")
        path = self.root / identity
        if self.root.is_symlink() or path.is_symlink():
            raise PilotError("拒绝接管记录目录符号链接。")
        return path

    def save(self, record, phase):
        record["phase"] = phase
        atomic_json(self.path(record["id"]) / "record.json", record)

    def load(self, identity):
        record = read_json(self.path(identity) / "record.json")
        if record.get("schema") != 1 or record.get("id") != identity or not isinstance(record.get("nodes"), list) or not 1 <= len(record["nodes"]) <= 16:
            raise PilotError("接管记录格式无效。")
        ids = set()
        for node in record["nodes"]:
            validate(node)
            if node.get("adoption") != identity or node["id"] in ids:
                raise PilotError("接管节点来源不一致。")
            ids.add(node["id"])
        return record

    def records(self):
        if not self.root.exists():
            return []
        if self.root.is_symlink():
            raise PilotError("拒绝接管记录符号链接。")
        return [self.load(p.name) for p in sorted(self.root.iterdir()) if (p / "record.json").is_file()]

    def prepare(self, service, server, snis=None):
        # Caller holds manager lock. No original service mutation in this stage.
        snapshot = self.source.inspect(service)
        config_data = next(f for f in snapshot["files"] if f["path"] == snapshot["config"])
        config = parse_json(base64.b64decode(config_data["data"], validate=True))
        identity = "a-" + os.urandom(8).hex()
        nodes = convert(config, snapshot["core"], server, self.manager.cores.keypair, identity, snis)
        existing = self.manager.nodes()
        for index, node in enumerate(nodes):
            if any(conflicts(node, other) for other in existing + nodes[:index]):
                raise PilotError("导入监听与已有 NodePilot 节点或其他导入节点冲突。")
            for other in existing:
                if own_target(other):
                    bindings = [{"listen": "127.0.0.1", "port": other["caddy"]["port"], "transport": "tcp"}]
                    if other["caddy"]["certificate_mode"] == "acme":
                        bindings.append({"listen": "0.0.0.0", "port": 80, "transport": "tcp"})
                    if any(conflicts(node, binding) for binding in bindings):
                        raise PilotError("导入监听与 NodePilot Caddy 目标/挑战端口冲突。")
        for previous in self.records():
            if previous["source"]["service"] == service and previous["phase"] in {"active", "stopping", "migrating", "restoring", "rolling_back"}:
                raise PilotError("该服务已有接管记录，请先归还或恢复。")
        directory = private_dir(private_dir(self.root) / identity)
        # Also back up the certificate files without overwriting any source PEM.
        known = {f["path"] for f in snapshot["files"]}
        snapshot["certificates"] = []
        for node in nodes:
            if node["protocol"] != "reality":
                snapshot["certificates"].append({"certificate": node["tls"]["certificate"], "key": node["tls"]["key"], "sni": node["sni"]})
                for path in (node["tls"]["certificate"], node["tls"]["key"]):
                    if path not in known:
                        snapshot["files"].append({**snapshot_file(path), "kind": "certificate"})
                        known.add(path)
        for node in nodes:
            stage = private_dir(directory / node["id"])
            if node["protocol"] != "reality":
                certificates.prepare(node, stage)
            atomic_json(stage / "config.json", generate(node))
            self.manager.cores.check(node, stage / "config.json")
        record = {"schema": 1, "id": identity, "source": snapshot, "nodes": nodes, "authorized": False}
        self.save(record, "prepared")
        return record

    def execute(self, identity, confirmed=False):
        record = self.load(identity)
        if not confirmed or record["phase"] != "prepared":
            raise PilotError("接管尚未确认或记录状态不正确；原服务不变。")
        current = self.source.unchanged(record["source"])
        if current and any(current[key] != record["source"][key] for key in ("enabled", "was_active")):
            raise PilotError("原服务运行/启用状态已变化，请重新检查后接管。")
        record["authorized"] = True
        self.save(record, "stopping")
        try:
            self.source.stop(record["source"])
            self.save(record, "migrating")
            for node in record["nodes"]:
                self.manager.apply(node, probe=False)
            self.source.disable(record["source"])
            self.save(record, "active")
        except (Exception, KeyboardInterrupt) as error:
            try:
                self.rollback(record)
            except Exception as rollback:
                raise PilotError(f"接管未完成，自动恢复尚未完成：{rollback}；保留私有记录，重新进入菜单恢复。") from error
            raise PilotError("接管失败，已撤回新节点并恢复原服务。") from error
        return record

    def rollback(self, record):
        if record.get("authorized") is not True:
            raise PilotError("没有持久化接管授权，不能操作原服务。")
        self.source.unchanged(record["source"])
        self.save(record, "rolling_back")
        self.manager.recover()
        for node in reversed(record["nodes"]):
            path = self.manager.node_dir(node["id"])
            if (path / "current").is_symlink():
                if self.manager.get(node["id"]).get("adoption") != record["id"]:
                    raise PilotError("节点已由其他来源管理，拒绝停止。")
                self.manager.delete(node["id"])
        self.source.restore(record["source"])
        self.save(record, "rolled_back")

    def restore(self, identity, confirmed=False):
        record = self.load(identity)
        if not confirmed or record["phase"] != "active" or record.get("authorized") is not True:
            raise PilotError("归还未确认或接管记录不是有效运行状态。")
        self.source.unchanged(record["source"])
        original_ids = {node["id"] for node in record["nodes"]}
        for other in self.manager.nodes():
            if other["id"] not in original_ids and any(conflicts(other, n) for n in record["nodes"]):
                raise PilotError("其他 NodePilot 节点占用原监听，不能归还。")
            if other["id"] not in original_ids and own_target(other):
                bindings = [{"listen": "127.0.0.1", "port": other["caddy"]["port"], "transport": "tcp"}]
                if other["caddy"]["certificate_mode"] == "acme":
                    bindings.append({"listen": "0.0.0.0", "port": 80, "transport": "tcp"})
                if any(conflicts(binding, n) for binding in bindings for n in record["nodes"]):
                    raise PilotError("其他 Caddy 目标占用原监听，不能归还。")
        # Private portable backup makes a failed return reversible, including edits.
        bundle = read_json(self.manager.backup())
        record["return_nodes"] = [entry for entry in bundle["nodes"] if entry["node"]["id"] in original_ids]
        for entry in record["return_nodes"]:
            if entry["node"].get("adoption") != identity:
                raise PilotError("当前节点不属于该接管记录。")
        self.save(record, "restoring")
        self.finish_return(record)

    def finish_return(self, record):
        try:
            self.source.unchanged(record["source"])
            for node in reversed(record["nodes"]):
                if (self.manager.node_dir(node["id"]) / "current").is_symlink():
                    if self.manager.get(node["id"]).get("adoption") != record["id"]:
                        raise PilotError("当前节点来源已变化，拒绝停止其他来源的服务。")
                    self.manager.delete(node["id"])
            self.source.restore(record["source"])
            self.save(record, "returned")
        except (Exception, KeyboardInterrupt) as error:
            try:
                self.source.stop(record["source"])
                self.source.disable(record["source"])
                for entry in record.get("return_nodes", []):
                    node = copy.deepcopy(entry["node"])
                    if (self.manager.node_dir(node["id"]) / "current").is_symlink():
                        continue
                    if node["protocol"] != "reality" or own_target(node):
                        directory = private_dir(self.path(record["id"]) / ("return-" + node["id"]))
                        for name in ("certificate.pem", "key.pem"):
                            atomic_bytes(directory / name, base64.b64decode(entry["files"][name], validate=True))
                        if own_target(node):
                            node["caddy"].update(certificate_mode="import", certificate=str(directory / "certificate.pem"), key=str(directory / "key.pem"))
                        else:
                            node["tls"].update(mode="import", certificate=str(directory / "certificate.pem"), key=str(directory / "key.pem"))
                    self.manager.apply(node, probe=False)
                self.save(record, "active")
            except Exception as rollback:
                raise PilotError(f"归还失败且恢复 NodePilot 未完成：{rollback}；保留恢复记录。") from error
            raise PilotError("原服务未恢复；已恢复接管节点，保持 NodePilot 管理。") from error

    def recover(self):
        for record in self.records():
            if record["phase"] == "restoring":
                if record.get("authorized") is not True:
                    raise PilotError("无授权的归还记录。")
                self.finish_return(record)
            elif record["phase"] in {"stopping", "migrating", "rolling_back"}:
                self.rollback(record)

    def assert_can_uninstall(self):
        if any(r["phase"] in {"active", "stopping", "migrating", "restoring", "rolling_back"} for r in self.records()):
            raise PilotError("存在接管服务，请先在菜单 13 归还原服务，再卸载 NodePilot。")
