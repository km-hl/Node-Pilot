import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nodepilot_pkg.caddy import Caddy, configuration
from nodepilot_pkg.common import PilotError
from nodepilot_pkg.config import generate
from nodepilot_pkg.model import validate
from test_config import fixture
from test_manager import FakeCores, FakeServices
from nodepilot_pkg.manager import Manager


def self_node(port=14443, local=18003):
    n = fixture(port=port)
    n["reality"].update(mode="self", target=f"127.0.0.1:{local}")
    n["caddy"] = {"domain": n["sni"], "port": local, "certificate_mode": "import",
                  "certificate": "/test/cert.pem", "key": "/test/key.pem", "upstream": ""}
    return n


class CaddyConfigTests(unittest.TestCase):
    def test_local_target_proxy_protocol_and_no_public_https_or_quic_listener(self):
        n = self_node()
        x = generate(n)["inbounds"][0]["streamSettings"]["realitySettings"]
        self.assertEqual(x["target"], "127.0.0.1:18003")
        self.assertEqual(x["xver"], 1)
        config = configuration([n], Path("/etc/nodepilot"))
        server = config["apps"]["http"]["servers"][n["id"]]
        self.assertEqual(server["listen"], ["127.0.0.1:18003"])
        self.assertEqual(server["protocols"], ["h1", "h2"])
        self.assertEqual(server["listener_wrappers"][0]["allow"], ["127.0.0.1/32"])
        self.assertEqual(config["admin"]["listen"], "unix//etc/nodepilot/caddy/admin.sock")

    def test_acme_http01_email_optional_and_auto_renew_managed_by_caddy(self):
        n = self_node()
        n["caddy"].update(certificate_mode="acme", email="")
        config = configuration([n], "/etc/nodepilot")
        tls = config["apps"]["tls"]
        self.assertEqual(tls["certificates"]["automate"], [n["sni"]])
        issuer = tls["automation"]["policies"][0]["issuers"][0]
        self.assertNotIn("email", issuer)
        self.assertTrue(issuer["challenges"]["tls-alpn"]["disabled"])
        self.assertEqual(config["apps"]["http"]["servers"]["http01"]["listen"], ['tcp4/0.0.0.0:80','tcp6/[::]:80'])

    def test_multiple_nodes_keep_separate_listener_and_one_http01_server(self):
        a, b = self_node(), self_node(port=14444, local=18004)
        b["sni"] = b["caddy"]["domain"] = "second.example.com"
        b["reality"]["server_names"] = [b["sni"]]
        servers = configuration([a, b], "/etc/nodepilot")["apps"]["http"]["servers"]
        self.assertEqual(len(servers), 2)
        self.assertNotEqual(servers[a["id"]]["listen"], servers[b["id"]]["listen"])

    def test_domain_target_binding_and_import_paths_are_validated(self):
        for mutate in (lambda n: n["caddy"].update(domain="wrong.example.com"),
                       lambda n: n["reality"].update(target="8.8.8.8:443"),
                       lambda n: n["caddy"].update(port=n["port"]),
                       lambda n: n["caddy"].update(email="bad email"),
                       lambda n: n["caddy"].update(upstream="127.0.0.1:443")):
            n = self_node()
            mutate(n)
            with self.assertRaises(PilotError):
                validate(n)

    def test_foreign_caddy_unit_refused_without_stopping(self):
        with tempfile.TemporaryDirectory() as tmp:
            from nodepilot_pkg.cores import Cores
            p = Path(tmp)
            caddy = Caddy(p / "state", Cores(p / "cores"), p)
            (p / "nodepilot-caddy.service").write_text("foreign")
            with patch("nodepilot_pkg.caddy.run") as command:
                with self.assertRaises(PilotError):
                    caddy.sync([self_node()])
            command.assert_not_called()


class FakeCaddy:
    def __init__(self):
        self.nodes, self.fail_next = [], False

    def preflight(self, n, old, others):
        pass

    def prepare(self, n, revision, renew=False):
        for name in ("certificate.pem", "key.pem"):
            (revision / name).write_text("private fixture")
        n["caddy"].update(certificate=str(revision / "certificate.pem"), key=str(revision / "key.pem"))

    def sync(self, nodes, restart=False):
        self.nodes = copy.deepcopy(nodes)
        if self.fail_next:
            self.fail_next = False
            raise PilotError("simulated shared target failure")

    def ready(self, n):
        pass

    def check(self, nodes):
        pass

    def certificate_files(self, n):
        return {"certificate.pem": Path(n["caddy"]["certificate"]), "key.pem": Path(n["caddy"]["key"])}


@unittest.skipUnless(os.name == "posix", "transaction tests require Linux symlinks/flock")
class CaddyTransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.services, self.caddy = FakeServices(root / "units"), FakeCaddy()
        self.manager = Manager(root / "state", FakeCores(), self.services, self.caddy)
        self.port_patch = patch("nodepilot_pkg.manager.port_free")
        self.port_patch.start()

    def tearDown(self):
        self.port_patch.stop()
        self.tmp.cleanup()

    def create(self, n):
        with self.manager.locked():
            return self.manager.apply(n, probe=False)

    def test_xray_restart_failure_rolls_back_shared_target_and_metadata(self):
        a = self.create(self_node())
        b = self.create(self_node(port=15443, local=18004))
        changed = copy.deepcopy(a)
        changed["sni"] = changed["caddy"]["domain"] = "new.example.com"
        changed["reality"]["server_names"] = [changed["sni"]]
        self.services.fail_next = True
        with self.assertRaises(PilotError):
            self.create(changed)
        self.assertEqual(self.manager.get(a["id"]), a)
        self.assertEqual({n["id"]: n["sni"] for n in self.caddy.nodes}, {a["id"]: a["sni"], b["id"]: b["sni"]})
        self.assertTrue(self.services.active(b))

    def test_shared_failure_new_node_cleans_pointer_and_keeps_other_nodes(self):
        a = self.create(self_node())
        self.caddy.fail_next = True
        b = self_node(port=15443, local=18004)
        with self.assertRaises(PilotError):
            self.create(b)
        self.assertEqual(self.manager.nodes(), [a])
        self.assertEqual(self.caddy.nodes, [a])
        self.assertFalse(self.manager.node_dir(b["id"]).exists())

    def test_delete_one_shared_node_keeps_other_node_and_target(self):
        a = self.create(self_node())
        b = self.create(self_node(port=15443, local=18004))
        with self.manager.locked():
            self.manager.delete(a["id"])
        self.assertEqual(self.caddy.nodes, [b])
        self.assertTrue(self.services.active(b))

    def test_shared_delete_failure_restores_node_service(self):
        a = self.create(self_node())
        self.caddy.fail_next = True
        with self.assertRaises(PilotError), self.manager.locked():
            self.manager.delete(a["id"])
        self.assertEqual(self.manager.get(a["id"]), a)
        self.assertTrue(self.services.active(a))
        self.assertEqual(self.caddy.nodes, [a])

    def test_backup_restore_self_target_preserves_certificates_and_ownership(self):
        a = self.create(self_node())
        with self.manager.locked():
            backup = self.manager.backup()
            self.manager.delete(a["id"])
            # FakeCaddy writes text fixture PEMs; real validation is covered by
            # restore/OpenSSL tests and the native Caddy smoke.
            with patch('nodepilot_pkg.certificates.validate_pair'):
                ids = self.manager.restore(backup)
        self.assertEqual(ids, [a["id"]])
        self.assertEqual(self.manager.get(a["id"])["caddy"]["certificate_mode"], "import")
        self.assertEqual(self.caddy.nodes[0]["reality"]["mode"], "self")
