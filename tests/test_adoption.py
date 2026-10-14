import base64
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nodepilot_pkg.adoption import Adoptions, SourceService, snapshot_file
from nodepilot_pkg.common import PilotError, read_json
from nodepilot_pkg.config import generate
from nodepilot_pkg.importer import convert, parse_json
from nodepilot_pkg.manager import Manager
from test_config import fixture
from test_manager import FakeCores, FakeServices


IDENTITY = "a-" + "a" * 16


class ImporterTests(unittest.TestCase):
    def convert(self, config, core="xray"):
        return convert(config, core, "8.8.8.8", lambda key: (key, "B" * 43), IDENTITY)

    def test_jsonc_preserves_quoted_urls_and_rejects_duplicate_keys(self):
        text = b'{/*comment*/"url":"https://example.com/\\\"//", "list":[1, // comment\n],}'
        self.assertEqual(parse_json(text)["list"], [1])
        self.assertTrue(parse_json(text)["url"].startswith("https://"))
        with self.assertRaises(PilotError):
            parse_json(b'{"a":1,"a":2}')

    def test_reality_identity_target_and_proxy_header_preserved(self):
        original = fixture()
        config = generate(original)
        config["inbounds"][0]["streamSettings"]["realitySettings"]["xver"] = 1
        config["policy"] = {"levels": {"0": {"handshake": 2}}}
        nodes = self.convert(config)
        n = nodes[0]
        self.assertEqual(n["reality"]["uuid"], original["reality"]["uuid"])
        self.assertEqual(n["reality"]["short_id"], original["reality"]["short_id"])
        generated = generate(n)
        self.assertEqual(generated["inbounds"][0]["streamSettings"]["realitySettings"]["xver"], 1)
        self.assertEqual(generated["policy"], config["policy"])

    def test_mixed_singbox_tcp_udp_credentials_and_tls_kept(self):
        a, b = fixture("anytls", 8443), fixture("hysteria2", 8443)
        a["password"] = "legacy"
        a["adoption"] = IDENTITY
        config = generate(a)
        config["inbounds"].extend(generate(b)["inbounds"])
        result = self.convert(config, "sing-box")
        self.assertEqual([n["transport"] for n in result], ["tcp", "udp"])
        self.assertEqual(result[0]["password"], "legacy")
        self.assertEqual(result[1]["tls"]["certificate"], b["tls"]["certificate"])

    def test_complex_route_extra_user_or_protocol_never_partly_imported(self):
        for change in (lambda c: c.update(routing={"rules": [{"outboundTag": "block"}]}),
                       lambda c: c["inbounds"].append({"protocol": "vmess"}),
                       lambda c: c["inbounds"][0]["settings"]["clients"].append({"id": "other"})):
            config = generate(fixture())
            change(config)
            with self.assertRaises(PilotError):
                self.convert(config)

    def test_missing_provenance_does_not_allow_weak_new_credentials(self):
        node = fixture("anytls")
        node["password"] = "old"
        with self.assertRaises(PilotError):
            generate(node)
        node["adoption"] = IDENTITY
        generate(node)

    def test_missing_original_private_key_never_generates_a_new_identity(self):
        config = generate(fixture())
        config["inbounds"][0]["streamSettings"]["realitySettings"]["privateKey"] = None
        from unittest.mock import MagicMock
        derive = MagicMock()
        with self.assertRaises(PilotError):
            convert(config, "xray", "8.8.8.8", derive, IDENTITY)
        derive.assert_not_called()


class KeyCores(FakeCores):
    def keypair(self, private):
        return private, "B" * 43


class FakeSource:
    def __init__(self, config):
        self.config = config
        self.running, self.enabled = True, "enabled"
        self.events = []
        self.changed, self.fail_restore = False, False

    def inspect(self, service):
        return {"service": service, "core": "xray", "config": str(self.config), "files": [snapshot_file(self.config)],
                "was_active": self.running, "enabled": self.enabled}

    def unchanged(self, record):
        if self.changed:
            raise PilotError("external change")
        return self.inspect(record["service"])

    def stop(self, record):
        self.unchanged(record)
        self.events.append("stop")
        self.running = False

    def disable(self, record):
        self.unchanged(record)
        self.events.append("disable")
        self.enabled = "disabled"

    def restore(self, record):
        self.unchanged(record)
        self.events.append("restore")
        if self.fail_restore:
            self.fail_restore = False
            raise PilotError("old service failed")
        self.running, self.enabled = record["was_active"], record["enabled"]


@unittest.skipUnless(os.name == "posix", "service transaction requires Linux symlinks/flock")
class AdoptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.config = root / "source.json"
        self.config.write_text(json.dumps(generate(fixture())))
        self.original = self.config.read_bytes()
        self.cores, self.services = KeyCores(), FakeServices(root / "units")
        self.manager = Manager(root / "state", self.cores, self.services)
        self.manager.initialize()
        self.source = FakeSource(self.config)
        self.adoptions = Adoptions(self.manager, self.source)
        self.ports = patch("nodepilot_pkg.manager.port_free")
        self.ports.start()

    def tearDown(self):
        self.ports.stop()
        self.tmp.cleanup()

    def prepared(self):
        return self.adoptions.prepare("xray.service", "8.8.8.8")

    def test_prepare_and_cancel_do_not_stop_original_service(self):
        record = self.prepared()
        self.assertTrue(self.source.running)
        self.assertFalse(self.source.events)
        with self.assertRaises(PilotError):
            self.adoptions.execute(record["id"])
        self.assertEqual(self.config.read_bytes(), self.original)

    def test_successful_handover_and_return_preserve_original_files_states(self):
        record = self.prepared()
        self.adoptions.execute(record["id"], confirmed=True)
        self.assertFalse(self.source.running)
        self.assertEqual(self.source.enabled, "disabled")
        self.assertEqual(len(self.manager.nodes()), 1)
        self.assertEqual(self.config.read_bytes(), self.original)
        with self.assertRaises(PilotError):
            self.adoptions.assert_can_uninstall()
        self.adoptions.restore(record["id"], confirmed=True)
        self.assertTrue(self.source.running)
        self.assertEqual(self.source.enabled, "enabled")
        self.assertFalse(self.manager.nodes())
        self.assertEqual(self.adoptions.load(record["id"])["phase"], "returned")

    def test_native_validation_failure_before_stop(self):
        self.cores.reject = True
        with self.assertRaises(PilotError):
            self.prepared()
        self.assertFalse(self.source.events)
        self.assertTrue(self.source.running)

    def test_realistic_restart_failure_restores_original_and_removes_new_nodes(self):
        record = self.prepared()
        self.services.fail_next = True
        with self.assertRaises(PilotError):
            self.adoptions.execute(record["id"], confirmed=True)
        self.assertTrue(self.source.running)
        self.assertEqual(self.source.enabled, "enabled")
        self.assertFalse(self.manager.nodes())
        self.assertEqual(self.adoptions.load(record["id"])["phase"], "rolled_back")

    def test_external_source_change_prevents_stop(self):
        record = self.prepared()
        self.source.changed = True
        with self.assertRaises(PilotError):
            self.adoptions.execute(record["id"], confirmed=True)
        self.assertFalse(self.source.events)
        self.assertTrue(self.source.running)

    def test_interruption_after_stop_recovered_from_persistent_record(self):
        record = self.prepared()
        record["authorized"] = True
        self.adoptions.save(record, "migrating")
        self.source.stop(record["source"])
        self.manager.apply(record["nodes"][0], probe=False)
        self.adoptions.recover()
        self.assertTrue(self.source.running)
        self.assertFalse(self.manager.nodes())

    def test_failed_return_rolls_back_to_working_nodepilot_nodes(self):
        record = self.prepared()
        self.adoptions.execute(record["id"], confirmed=True)
        self.source.fail_restore = True
        with self.assertRaises(PilotError):
            self.adoptions.restore(record["id"], confirmed=True)
        self.assertFalse(self.source.running)
        self.assertEqual(len(self.manager.nodes()), 1)
        self.assertTrue(self.services.active(self.manager.nodes()[0]))
        self.assertEqual(self.adoptions.load(record["id"])["phase"], "active")


class SourceSafetyTests(unittest.TestCase):
    def test_critical_service_and_panel_not_stopped(self):
        source = SourceService()
        with patch("nodepilot_pkg.adoption.run") as command:
            for service in ("ssh.service", "systemd-resolved.service", "nodepilot-caddy.service", "xray.service;id"):
                with self.assertRaises(PilotError):
                    source.inspect(service)
            command.assert_not_called()
        properties = "ExecStart={ path=/usr/local/x-ui/x-ui ; argv[]=/usr/local/x-ui/x-ui ; ignore_errors=no ; pid=1 ; status=0/0 }\n"
        with patch("nodepilot_pkg.adoption.run", return_value=properties):
            with self.assertRaisesRegex(PilotError, "面板"):
                source.inspect("x-ui.service")
