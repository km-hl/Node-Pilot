import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nodepilot_pkg.common import PilotError, atomic_json, read_json
from nodepilot_pkg.manager import Manager
from nodepilot_pkg.system import conflicts
from test_config import fixture


class FakeCores:
    def __init__(self):
        self.reject = False

    def check(self, n, path, binary=None):
        if self.reject:
            raise PilotError("模拟内核拒绝配置")


class FakeServices:
    def __init__(self, units):
        self.units = units
        self.units.mkdir()
        self.running, self.stopped = set(), []
        self.fail_next = False

    def assert_owned(self, n, missing=False):
        p = self.units / n["service"]
        if p.exists() and p.read_text() != n["id"]:
            raise PilotError("非本项目服务")
        if not p.exists() and not missing:
            raise PilotError("服务缺失")

    def install(self, n):
        self.assert_owned(n, True)
        (self.units / n["service"]).write_text(n["id"])

    def active(self, n):
        return n["id"] in self.running

    def restart(self, n):
        self.assert_owned(n)
        if self.fail_next:
            self.fail_next = False
            raise PilotError("模拟重启失败")
        self.running.add(n["id"])

    def remove(self, n):
        self.assert_owned(n)
        self.running.discard(n["id"])
        self.stopped.append(n["id"])
        (self.units / n["service"]).unlink()


@unittest.skipUnless(os.name == "posix", "修订原子链接/锁需要 Linux")
class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cores, self.services = FakeCores(), FakeServices(root / "units")
        self.manager = Manager(root / "state", self.cores, self.services)
        self.manager.initialize()
        self.port_patch = patch("nodepilot_pkg.manager.port_free")
        self.port_patch.start()

    def tearDown(self):
        self.port_patch.stop()
        self.tmp.cleanup()

    def create(self, n=None):
        with self.manager.locked():
            return self.manager.apply(n or fixture(), probe=False)

    def test_restart_failure_rolls_back_metadata_config_and_service(self):
        old = self.create()
        revision = self.manager.revision(old["id"])
        changed = copy.deepcopy(old)
        changed.update(port=8443, sni="other.example.com")
        changed["reality"]["server_names"] = [changed["sni"]]
        self.services.fail_next = True
        with self.assertRaises(PilotError), self.manager.locked():
            self.manager.apply(changed, probe=False)
        self.assertEqual(self.manager.get(old["id"]), old)
        self.assertEqual(self.manager.revision(old["id"]), revision)
        self.assertTrue(self.services.active(old))

    def test_native_check_failure_never_publishes(self):
        n = fixture()
        self.cores.reject = True
        with self.assertRaises(PilotError):
            self.create(n)
        self.assertFalse(self.manager.node_dir(n["id"]).exists())
        self.assertFalse(self.manager.nodes())
        self.assertFalse(list(self.services.units.iterdir()))

    def test_foreign_unit_is_untouched(self):
        n = fixture()
        p = self.services.units / n["service"]
        p.write_text("foreign")
        with self.assertRaises(PilotError):
            self.create(n)
        self.assertEqual(p.read_text(), "foreign")
        self.assertFalse(self.services.stopped)

    def test_multi_node_delete_isolated(self):
        a, b = self.create(), self.create(fixture(port=8443))
        with self.manager.locked():
            backup = self.manager.delete(a["id"])
        self.assertEqual(self.manager.nodes(), [b])
        self.assertTrue(self.services.active(b))
        self.assertEqual(self.services.stopped, [a["id"]])
        self.assertTrue(backup.exists())

    def test_successful_edit_updates_all_fields(self):
        old = self.create()
        n = copy.deepcopy(old)
        n["port"] = 9443
        n["sni"] = "new.example.com"
        n["reality"]["server_names"] = [n["sni"]]
        self.create(n)
        saved = self.manager.get(n["id"])
        config = read_json(self.manager.revision(n["id"]) / "config.json")
        self.assertEqual(saved["port"], config["inbounds"][0]["port"])
        self.assertEqual(saved["sni"], config["inbounds"][0]["streamSettings"]["realitySettings"]["serverNames"][0])
        self.assertTrue(list((self.manager.state / "backups").iterdir()))

    def test_port_conflict_while_service_stopped(self):
        old = self.create()
        self.services.running.clear()
        with self.assertRaises(PilotError):
            self.create(fixture())

    def test_interrupted_edit_restores_old_pointer(self):
        old = self.create()
        previous = self.manager.revision(old["id"])
        changed = copy.deepcopy(old)
        changed["port"] = 8888
        revision = previous.parent / "r-interrupted"
        revision.mkdir()
        atomic_json(revision / "node.json", changed)
        atomic_json(self.manager.node_dir(old["id"]) / "pending.json", {"kind": "node", "node": changed, "old": str(previous)})
        self.manager.point(old["id"], revision)
        with self.manager.locked():
            self.manager.recover()
        self.assertEqual(self.manager.get(old["id"]), old)

    def test_interrupted_creation_cleans_only_own_node(self):
        a = self.create()
        b = fixture(port=8443)
        path = self.manager.node_dir(b["id"])
        path.mkdir()
        atomic_json(path / "pending.json", {"kind": "node", "node": b, "old": None})
        self.services.install(b)
        with self.manager.locked():
            self.manager.recover()
        self.assertEqual(self.manager.nodes(), [a])
        self.assertFalse(path.exists())

    def test_backup_restore_no_overwrite(self):
        a = self.create()
        with self.manager.locked():
            backup = self.manager.backup()
            with self.assertRaises(PilotError):
                self.manager.restore(backup)
            self.manager.delete(a["id"])
            # Reality network probing belongs to separate native/VPS checks.
            with patch("nodepilot_pkg.manager.probe_reality"), patch("nodepilot_pkg.targets.inspect_target"), patch("nodepilot_pkg.targets.cloudflare_networks", return_value=[]):
                ids = self.manager.restore(backup)
        self.assertEqual(ids, [a["id"]])
        self.assertEqual(self.manager.get(a["id"]), a)

    def test_state_foreign_directory_refused(self):
        root = Path(self.tmp.name) / "foreign"
        root.mkdir()
        (root / "keep").write_text("keep")
        with self.assertRaises(PilotError):
            Manager(root).initialize()
        self.assertEqual((root / "keep").read_text(), "keep")

    def test_nested_lock_refused(self):
        with self.manager.locked():
            with self.assertRaises(PilotError), self.manager.locked():
                pass

    def test_private_files(self):
        n = self.create()
        path = self.manager.revision(n["id"]) / "node.json"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    def test_external_revision_link_rejected(self):
        n = self.create()
        current = self.manager.node_dir(n["id"]) / "current"
        current.unlink()
        current.symlink_to(Path(self.tmp.name))
        with self.assertRaises(PilotError):
            self.manager.get(n["id"])

    def configure_core_update(self):
        from nodepilot_pkg.cores import Cores
        cores = Cores(Path(self.tmp.name) / "cores")
        parent = cores.root / "xray"
        old, candidate = parent / "1.0.0-aaaaaaaa", parent / "2.0.0-bbbbbbbb"
        old.mkdir(parents=True)
        candidate.mkdir()
        cores.switch("xray", old)
        self.manager.cores = cores
        return cores, old, candidate

    def test_core_restart_failure_rolls_back_all_nodes(self):
        a, b = self.create(), self.create(fixture(port=8443))
        cores, old, candidate = self.configure_core_update()
        self.services.fail_next = True
        with patch.object(cores, "stage", return_value=candidate), patch.object(cores, "check"):
            with self.assertRaises(PilotError), self.manager.locked():
                self.manager.update_core("xray")
        self.assertEqual((cores.root / "xray/current").resolve(), old)
        self.assertTrue(self.services.active(a))
        self.assertTrue(self.services.active(b))
        self.assertFalse((self.manager.state / "core-pending.json").exists())

    def test_bad_candidate_core_never_switches(self):
        self.create()
        cores, old, candidate = self.configure_core_update()
        with patch.object(cores, "stage", return_value=candidate), patch.object(cores, "check", side_effect=PilotError("bad core")):
            with self.assertRaises(PilotError), self.manager.locked():
                self.manager.update_core("xray")
        self.assertEqual((cores.root / "xray/current").resolve(), old)

    def test_interrupted_core_update_recovery(self):
        n = self.create()
        cores, old, candidate = self.configure_core_update()
        cores.switch("xray", candidate)
        atomic_json(self.manager.state / "core-pending.json", {"core": "xray", "old": str(old)})
        with self.manager.locked():
            self.manager.recover_core()
        self.assertEqual((cores.root / "xray/current").resolve(), old)
        self.assertTrue(self.services.active(n))


class PortTests(unittest.TestCase):
    def test_tcp_udp_same_port_allowed(self):
        tcp, udp = fixture(), fixture("hysteria2")
        self.assertFalse(conflicts(tcp, udp))
        self.assertTrue(conflicts(tcp, fixture("anytls")))
