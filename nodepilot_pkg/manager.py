"""Serialized, durable node revisions with native validation and restart rollback."""
import base64
import copy
import json
import hashlib
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

LEGACY_OWNER = "NodePilot schema=1\n"
OWNER = "NodePilot schema=2\n"


class Manager:
    def __init__(self, state=Path("/etc/nodepilot"), cores=None, services=None, caddy=None, migrate_owner=True):
        self.state = Path(state)
        self.cores = cores or Cores()
        self.services = services or Services(state)
        self.caddy = caddy or Caddy(self.state, self.cores, self.services.units)
        self.migrate_owner = migrate_owner

    def initialize(self, migrate_owner=None):
        marker = self.state / ".owner"
        if self.state.exists() and (self.state.is_symlink() or marker.is_symlink() or not marker.is_file() or marker.read_text() not in {OWNER, LEGACY_OWNER}):
            raise PilotError("配置目录已有非 NodePilot 内容；拒绝接管/覆盖。")
        private_dir(self.state)
        migrate = self.migrate_owner if migrate_owner is None else migrate_owner
        # Existing markers must not be rewritten before flock: a contender could
        # otherwise publish a stale schema=1 after an upgrade writes schema=2.
        if not marker.is_file() or migrate:
            atomic_bytes(marker, OWNER.encode())
        for directory in ("nodes", "backups", "acme"):
            private_dir(self.state / directory)

    @contextmanager
    def locked(self, migrate_owner=None, existing_only=False):
        import fcntl
        if existing_only:
            marker = self.state / '.owner'
            if (self.state.is_symlink() or not self.state.is_dir() or marker.is_symlink() or
                    not marker.is_file() or marker.read_text() not in {OWNER, LEGACY_OWNER}):
                raise PilotError('只读状态锁需要现有合法配置所有权；未创建或修复状态目录。')
        else:
            self.initialize(migrate_owner=False)
        lock = self.state / ".lock"
        # Never chmod/follow an external lock, or recreate missing state merely
        # to approve a binding / read-only complete finalization.
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
        if not existing_only:
            flags |= os.O_CREAT
        with os.fdopen(os.open(lock, flags, 0o600), 'a') as f:
            import stat
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode) or os.fstat(f.fileno()).st_nlink != 1:
                raise PilotError('状态锁不是独占普通文件；拒绝操作外部路径。')
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as e:
                raise PilotError("另一个 NodePilot 操作正在运行，请稍后重试。") from e
            try:
                if not existing_only:
                    os.fchmod(f.fileno(), 0o600)
                migrate = False if existing_only else self.migrate_owner if migrate_owner is None else migrate_owner
                if migrate:
                    atomic_bytes(self.state / '.owner', OWNER.encode())
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

    def assert_transactions_idle(self, root):
        """Read-only bind-source barrier. Never recover destructive operations here.

        Pending files are active until their normal recovery removes them. Only
        uninstall/adoption records have durable terminal phases; backups and
        immutable revisions are deliberately outside this inventory.
        """
        def record(path):
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 256 * 1024:
                raise PilotError(f'事务不是有界普通文件：{path}；来源未改变。')
            value = read_json(path)
            if not isinstance(value, dict) or type(value.get('schema')) is not int:
                raise PilotError(f'未知/损坏事务：{path}；来源未改变。')
            return value

        def block(path):
            raise PilotError(f'存在活动或未知事务：{path}；请先用可信管理器核对恢复，来源未改变。')

        for path in (Path(root) / 'program-pending.json', self.state / 'core-pending.json'):
            if path.exists() or path.is_symlink():
                block(path)
        uninstall = self.state / 'uninstall.json'
        if uninstall.exists() or uninstall.is_symlink():
            r = record(uninstall)
            required = {'root','launcher','units','backup','nodes','removed','running','enabled',
                        'renew','timer_enabled','timer_active','caddy_active'}
            if (r['schema'] != 1 or not isinstance(r.get('phase'), str) or r['phase'] not in {'complete','rolled_back'} or
                    not required <= set(r) or r['root'] != str(root) or
                    r['launcher'] != '/usr/local/bin/nodepilot' or r['units'] != '/etc/systemd/system' or
                    not isinstance(r['backup'], str) or not isinstance(r['nodes'], list) or
                    any(not isinstance(n, dict) for n in r['nodes']) or
                    not isinstance(r['removed'], list) or not isinstance(r['running'], dict) or
                    not isinstance(r['enabled'], dict) or not isinstance(r['renew'], dict) or
                    set(r['renew']) != {'nodepilot-renew.service','nodepilot-renew.timer'} or
                    any(type(v) is not bool for v in r['renew'].values()) or
                    not isinstance(r['timer_enabled'], str) or r['timer_enabled'] not in {'','disabled','enabled','enabled-runtime'} or
                    type(r['timer_active']) is not bool or type(r['caddy_active']) is not bool):
                block(uninstall)
            ids = {validate(n)['id'] for n in r['nodes']}
            if (len(ids) != len(r['nodes']) or set(r['running']) != ids or set(r['enabled']) != ids or
                    any(type(v) is not bool for v in r['running'].values()) or
                    any(not isinstance(v, str) for v in r['enabled'].values()) or
                    any(not isinstance(v, str) or v not in ids for v in r['removed'])):
                block(uninstall)

        def children(directory):
            if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
                block(directory)
            if directory.exists():
                yield from directory.iterdir()

        for path in children(self.state / 'nodes'):
            if path.is_symlink() or not path.is_dir():
                block(path)
            pending = path / 'pending.json'
            if pending.exists() or pending.is_symlink():
                block(pending)
        for path in children(self.state / 'deletions'):
            block(path)  # Deletion intents are removed only after durable cleanup.
        from .adoption import Adoptions
        adoptions = Adoptions(self)
        for path in children(adoptions.root):
            if path.is_symlink() or not path.is_dir():
                block(path)
            r = record(path / 'record.json')
            if r['schema'] != 1 or not isinstance(r.get('phase'), str) or r['phase'] not in {'active','returned','rolled_back'}:
                block(path)
            if not isinstance(r.get('nodes'), list) or any(not isinstance(n, dict) for n in r['nodes']):
                block(path)
            r = adoptions.load(path.name)
            if type(r.get('authorized')) is not bool or (r['phase'] in {'active','returned'} and not r['authorized']):
                block(path)
            source = r.get('source')
            if (not isinstance(source, dict) or not isinstance(source.get('service'), str) or
                    source.get('core') not in ('xray','sing-box') or not isinstance(source.get('config'), str) or
                    not isinstance(source.get('files'), list) or not source['files'] or
                    type(source.get('was_active')) is not bool or
                    source.get('enabled') not in ('enabled','enabled-runtime','disabled','static')):
                block(path)
            for entry in source['files']:
                if (not isinstance(entry, dict) or any(not isinstance(entry.get(k), str)
                        for k in ('path','target','sha256','data'))):
                    block(path)
        credentials = self.state / 'credentials'
        if credentials.is_symlink() or (credentials.exists() and not credentials.is_dir()):
            block(credentials)
        for path in children(credentials / 'pending'):
            block(path)

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
        from .dns_credentials import profiles, recover_pending
        try:
            return self._apply(node, probe, renew)
        finally:
            # Failed validation/preflight is covered as well as restart failure.
            recover_pending(self.state, owned=profiles(node), only_owned=True)

    def _apply(self, node, probe=True, renew=False):
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
        caddy_previous = None
        caddy_path=self.state/'caddy/config.json'
        if managed and (self.services.units/'nodepilot-caddy.service').exists():
            if caddy_path.is_symlink() or not caddy_path.is_file() or caddy_path.stat().st_size>256*1024:
                raise PilotError('Caddy 原配置无法安全备份，拒绝切换。')
            caddy_previous=caddy_path.read_bytes()
        private_dir(path)
        revision = private_dir(private_dir(path / "revisions") / ("r-" + secrets.token_hex(8)))
        pending = path / "pending.json"
        atomic_json(pending, {"schema":2,"kind": "node", "old": str(old_revision) if old else None, "node": n,
            'caddy_previous':base64.b64encode(caddy_previous).decode() if caddy_previous is not None else None})
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
            from .dns_credentials import commit_node
            commit_node(self.state, n)
        except (Exception, KeyboardInterrupt) as error:
            try:
                if old:
                    self.point(n["id"], old_revision)
                    if managed:
                        if caddy_previous is not None:
                            self.caddy.restore_configuration(caddy_previous)
                        else:
                            self.caddy.sync(self.nodes())
                    self.services.restart(old)
                else:
                    self.services.assert_owned(n, missing=True)
                    if (self.services.units / n["service"]).exists():
                        self.services.remove(n)
                    (path / "current").unlink(missing_ok=True)
                    if managed:
                        if caddy_previous is not None:
                            self.caddy.restore_configuration(caddy_previous)
                        else:
                            self.caddy.sync(self.nodes())
                    shutil.rmtree(path)
            except Exception as rollback:
                raise PilotError(f"操作失败且自动回滚未完成：{rollback}；保留 pending.json，重新进入菜单恢复。") from error
            if old:
                pending.unlink(missing_ok=True)
            if (n.get('tls',{}).get('mode')=='acme') or (own_target(n) and n['caddy']['certificate_mode']=='dns01'):
                certificates.event(self.state,n,'证书切换/服务加载失败，已回滚原修订')
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
        atomic_json(path / "pending.json", {"schema":2,"kind": "delete", "node": n})
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
        self.finish_delete(n)
        return backup

    def finish_delete(self, n):
        # This journal survives partial rmtree of the node itself.
        directory=private_dir(self.state/'deletions')
        journal=directory/(n['id']+'.json')
        atomic_json(journal,{'schema':2,'node':n})
        self.services.assert_owned(n,missing=True)
        if (self.services.units/n['service']).exists():
            self.services.remove(n)
        path=self.node_dir(n['id'])
        acme_dir = self.state / "acme" / n['id']
        if acme_dir.is_symlink():
            raise PilotError('ACME 清理路径为外部链接；保留删除事务。')
        if acme_dir.is_dir() and not acme_dir.is_symlink():
            shutil.rmtree(acme_dir)
        if path.exists():
            shutil.rmtree(path)
        fsync_dir(self.state/'nodes')
        journal.unlink();fsync_dir(directory)

    def restart(self, n):
        if own_target(n):
            self.caddy.sync(self.nodes())
            self.caddy.ready(n)
        self.services.restart(n)

    def recover(self):
        directory=self.state/'deletions'
        if directory.is_symlink():
            raise PilotError('删除事务目录为外部链接。')
        for journal in sorted(directory.glob('*.json')):
            if journal.is_symlink():
                raise PilotError('删除事务为外部链接。')
            record=read_json(journal);n=validate(record['node'])
            if record.get('schema')!=2 or journal.stem!=n['id']:
                raise PilotError('删除事务 schema/ID 不匹配。')
            self.finish_delete(n)
        for path in sorted((self.state / "nodes").iterdir()):
            pending = path / "pending.json"
            if not pending.exists():
                continue
            record = read_json(pending)
            if record.get('schema',1) not in {1,2} or record.get('kind') not in {'node','delete'}:
                raise PilotError('节点事务 schema/类型不受支持，拒绝恢复。')
            n = validate(record["node"])
            if n["id"] != path.name:
                raise PilotError("恢复记录 ID 与目录不匹配。")
            self.services.assert_owned(n, missing=True)
            if record["kind"] == "delete":
                if (self.services.units / n["service"]).exists():
                    self.services.remove(n)
                if own_target(n):
                    self.caddy.sync([o for o in self.nodes() if o["id"] != n["id"]])
                self.finish_delete(n)
            elif record.get("old"):
                self.point(n["id"], Path(record["old"]))
                if own_target(n) or own_target(self.get(n["id"])):
                    if record.get('caddy_previous') is not None:
                        self.caddy.restore_configuration(base64.b64decode(record['caddy_previous'],validate=True))
                    else:
                        self.caddy.sync(self.nodes())
                self.services.restart(self.get(n["id"]))
                pending.unlink()
            else:
                if (self.services.units / n["service"]).exists():
                    self.services.remove(n)
                (path / "current").unlink(missing_ok=True)
                if own_target(n):
                    if record.get('caddy_previous') is not None:
                        self.caddy.restore_configuration(base64.b64decode(record['caddy_previous'],validate=True))
                    else:
                        self.caddy.sync(self.nodes())
                shutil.rmtree(path)
        from .dns_credentials import recover_pending
        recover_pending(self.state)

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
        caddy_previous=None
        if core=='caddy' and affected:
            config=self.state/'caddy/config.json'
            if config.is_symlink() or not config.is_file() or config.stat().st_size>256*1024:
                raise PilotError('Caddy 原配置无法备份，拒绝内核切换。')
            caddy_previous=config.read_bytes()
        pending = self.state / "core-pending.json"
        atomic_json(pending, {"schema":2,"core": core, "old": str(old) if old else None,
            'caddy_previous':base64.b64encode(caddy_previous).decode() if caddy_previous is not None else None})
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
                    self.caddy.restore_configuration(caddy_previous)
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
        if pending.is_symlink():raise PilotError('内核事务为外部链接，保留证据。')
        if pending.exists():
            # A legal 256KiB Caddy snapshot expands to ~342KiB in base64 JSON.
            if not pending.is_file() or pending.stat().st_size>512*1024:
                raise PilotError('内核事务不是有界普通文件。')
            record = read_json(pending)
            if not isinstance(record,dict) or type(record.get('schema',1)) is not int or record.get('schema',1) not in {1,2}:
                raise PilotError('内核事务 schema 不受支持。')
            core = record.get("core")
            if not isinstance(core,str) or core not in {"xray", "sing-box", "caddy"} or 'old' not in record:
                raise PilotError("非法内核恢复记录。")
            affected=[n for n in self.nodes() if n['core']==core or (core=='caddy' and own_target(n))]
            for n in affected:self.services.assert_owned(n)
            if core=='caddy' and affected:self.caddy.assert_owned()
            caddy_previous=None
            if core=='caddy' and record.get('caddy_previous') is not None:
                try:
                    caddy_previous=base64.b64decode(record['caddy_previous'],validate=True)
                    if len(caddy_previous)>256*1024 or not isinstance(json.loads(caddy_previous),dict):raise ValueError()
                except (ValueError,TypeError):
                    raise PilotError('Caddy 原配置证据损坏；未切换内核，保留事务。') from None
            if record["old"]:
                if not isinstance(record['old'],str):raise PilotError('内核恢复路径无效。')
                old = Path(record["old"])
                if not old.is_absolute() or old.is_symlink() or not old.is_dir() or old.resolve().parent != (self.cores.root / core).resolve() or old.name=='current':
                    raise PilotError("内核恢复路径越界。")
                self.cores.switch(core, old)
                if core == "caddy":
                    if caddy_previous is not None:
                        self.caddy.restore_configuration(caddy_previous)
                    else:
                        self.caddy.sync(self.nodes(), restart=True)
                for n in affected:self.services.restart(n)
            else:
                if record['old'] is not None or affected:
                    raise PilotError('无旧内核的恢复记录与现有节点冲突，保留事务。')
                (self.cores.root / core / "current").unlink(missing_ok=True)
            pending.unlink()

    @contextmanager
    def restore_plan(self, backup, expected_digest=None):
        """Validate the WHOLE portable bundle before publishing any node/service."""
        if self.nodes():
            raise PilotError("恢复只允许空节点目录，防止覆盖现有节点；请先备份并删除节点。")
        self.assert_transactions_idle(Path('/opt/nodepilot'))
        path = Path(backup)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 8*1024*1024:
            raise PilotError("备份必须为不超过 8MiB 的普通文件，不能是链接。")
        raw=path.read_bytes()
        if len(raw)>8*1024*1024:raise PilotError('备份文件过大。')
        digest=hashlib.sha256(raw).hexdigest()
        if expected_digest is not None and digest!=expected_digest:
            raise PilotError('备份在预览后已改变；请重新预览确认，未修改服务。')
        try:bundle=json.loads(raw)
        except (ValueError,UnicodeError):raise PilotError('备份 JSON 损坏。') from None
        if not isinstance(bundle,dict) or type(bundle.get('schema')) is not int or bundle['schema']!=1 or not isinstance(bundle.get('nodes'),list) or len(bundle['nodes'])>100:
            raise PilotError("备份格式无效。")
        import tempfile
        nodes,summaries=[],[]
        with tempfile.TemporaryDirectory(prefix='restore-',dir=self.state) as tmp:
            for entry in bundle['nodes']:
                try:
                    if not isinstance(entry,dict) or not isinstance(entry.get('node'),dict) or not isinstance(entry.get('files'),dict):
                        raise PilotError('备份节点/文件格式无效。')
                    n=copy.deepcopy(entry['node']);validate(n)
                    if 'adoption' in n:
                        raise PilotError('便携备份含接管节点；不能恢复外部服务控制权或引用不存在的接管记录。请在原机核对接管记录，或重新显式导入。')
                    if any(o['id']==n['id'] for o in nodes) or self.node_dir(n['id']).exists():
                        raise PilotError('备份 ID 重复或存在未完成节点目录。')
                    directory=private_dir(Path(tmp)/n['id'])
                    original='无本地证书'
                    if n['protocol']!='reality' or own_target(n):
                        original=certificates.restore_import(n,entry['files'],directory)
                    elif entry['files']:
                        raise PilotError('第三方 Reality 备份不应包含本地证书文件。')
                    validate(n)
                    from .dns_credentials import profiles
                    if profiles(n):raise PilotError('恢复节点仍含 DNS 凭据引用，拒绝恢复。')
                    for other in nodes:
                        if conflicts(n,other):raise PilotError('备份内部存在监听端口冲突。')
                    self.services.assert_owned(n,missing=True)
                    port_free(n['listen'],n['port'],n['transport'])
                    if own_target(n):self.caddy.preflight(n,None,nodes)
                    elif any(own_target(o) and n['transport']=='tcp' and n['port']==o['caddy']['port'] for o in nodes):
                        raise PilotError('备份节点与 Caddy 目标端口冲突。')
                    atomic_json(directory/'config.json',generate(n))
                    self.cores.check(n,directory/'config.json')
                    nodes.append(n)
                    summaries.append({'id':n['id'],'name':n['name'],'protocol':n['protocol'],
                        'certificate_mode':original,'restored_mode':'import' if original!='无本地证书' else original,
                        'notice':certificates.renewal_notice(n)})
                except (KeyError,TypeError,ValueError,AttributeError) as error:
                    raise PilotError('备份节点元数据损坏或不兼容；未修改服务。') from error
            if any(own_target(n) for n in nodes):self.caddy.check(nodes)
            yield {'sha256':digest,'nodes':summaries},nodes

    def preview_restore(self,backup):
        with self.restore_plan(backup) as (summary,nodes):return summary

    def restore(self, backup, expected_digest=None):
        # Per-node durable rollback is reused; never take over external services.
        restored = []
        with self.restore_plan(backup,expected_digest) as (summary,nodes):
            try:
                for n in nodes:
                    saved = self.apply(n)
                    restored.append(saved["id"])
            except (Exception, KeyboardInterrupt) as error:
                failures=[]
                for node_id in reversed(restored):
                    try:self.delete(node_id)
                    except (Exception,KeyboardInterrupt):failures.append(node_id)
                if failures:
                    raise PilotError('备份恢复失败，部分已恢复节点清理未完成：'+','.join(failures)+'；保留节点/事务，请核对恢复。') from error
                raise
        return restored
