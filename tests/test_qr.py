import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from nodepilot_pkg import qr
from nodepilot_pkg.common import PilotError
from nodepilot_pkg.cores import Cores
from nodepilot_pkg.exporters import mihomo_full_yaml, mihomo_proxy
from nodepilot_pkg.manager import Manager
from nodepilot_pkg.menu import Menu
from nodepilot_pkg.share import display, share
from nodepilot_pkg.system import firewall_reference, http01_dual_stack_ready
from nodepilot_pkg.targets import supports_mlkem768
from subprocess import CompletedProcess
from test_config import fixture
from test_manager import FakeCores, FakeServices


class QRAndExporterTests(unittest.TestCase):
    def test_qr_matrix_versions_finders_and_format_bits(self):
        for payload in ("hello", "vless://" + "a" * 160, "hysteria2://" + "x" * 340):
            grid = qr.matrix(payload)
            size = len(grid)
            self.assertEqual(size, len(grid[0]))
            self.assertGreaterEqual(size, 21)
            # Dark module at (size - 8, 8)
            self.assertTrue(grid[size - 8][8])
            # Finder centers at (3,3), (3, size-4), (size-4, 3)
            self.assertTrue(grid[3][3])
            self.assertTrue(grid[3][size - 4])
            self.assertTrue(grid[size - 4][3])
        compact = qr.render("vless://demo", compact=True)
        ascii_art = qr.render("vless://demo", compact=False)
        self.assertIn("█", compact)
        self.assertIn("##", ascii_art)

    def test_mihomo_full_yaml_and_x25519mlkem768_options(self):
        reality = fixture("reality", 443)
        hy2 = fixture("hysteria2", 8443)
        hy2["mport"] = "20000-50000"
        hy2["hop_interval"] = 25
        proxy_entry = mihomo_proxy(reality)
        self.assertEqual(proxy_entry["packet-encoding"], "xudp")
        self.assertTrue(proxy_entry["reality-opts"]["support-x25519mlkem768"])
        hy2_entry = mihomo_proxy(hy2)
        self.assertEqual(hy2_entry["ports"], "20000-50000")
        self.assertEqual(hy2_entry["hop-interval"], 25)
        full = mihomo_full_yaml([reality, hy2], mixed_port=7890)
        self.assertIn("mixed-port: 7890", full)
        self.assertIn("proxy-groups:", full)
        self.assertIn("IP-CIDR,127.0.0.0/8,DIRECT,no-resolve", full)
        self.assertNotIn("GEOIP,", full)

    def test_share_display_with_qr_and_firewall_reference(self):
        hy2 = fixture("hysteria2", 14443)
        hy2["mport"] = "20000-30000"
        out = display(hy2, show_qr=True, compact_qr=False)
        self.assertIn(share(hy2), out)
        self.assertIn("##", out)
        self.assertIn("20000-30000", out)
        ref = firewall_reference(hy2)
        self.assertIn("20000:30000", ref)
        self.assertIn("14443", ref)


class PhaseTwoThreeTests(unittest.TestCase):
    def _menu(self, text):
        m = Menu.__new__(Menu)
        m.tty_read = io.StringIO(text)
        m.tty = io.StringIO()
        m.manager = MagicMock()
        m.manager.nodes.return_value = []
        m.ips = {"ipv4": "8.8.8.8", "ipv6": ""}
        m.pending_profiles = []
        return m

    def test_select_by_index_and_id_and_ask_int_retry(self):
        a, b = fixture("anytls", 8443), fixture("hysteria2", 9443)
        with tempfile.TemporaryDirectory() as d:
            m = self._menu("abc\n99\n2\n")
            m.manager.state = Path(d)
            m.manager.nodes.return_value = [a, b]
            m.manager.get.side_effect = lambda nid: {a["id"]: a, b["id"]: b}[nid]
            m.manager.services.runtime.return_value = {"state": "active"}
            chosen = m.select()
            self.assertEqual(chosen["id"], b["id"])
            self.assertIn("[1]", m.tty.getvalue())
            self.assertIn("[2]", m.tty.getvalue())

        m2 = self._menu("not_an_int\n0\n443\n")
        self.assertEqual(m2.ask_int("端口", minimum=1, maximum=65535), 443)

    def test_menu_backup_restore_full_mihomo_yaml_and_menu7_qr(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            n = fixture("reality", 443)
            out_file = root / "full.yaml"
            m = self._menu(f"2\ny\n{out_file}\n")
            m.manager.nodes.return_value = [n]
            m.backup_restore.__func__  # verify method bound cleanly
            with patch.object(m, "ask", side_effect=["6", "2", str(out_file)]), \
                 patch.object(m, "confirm", side_effect=[True]):
                m.backup_restore()
            content = out_file.read_text(encoding="utf-8")
            self.assertIn("mixed-port: 7890", content)
            self.assertIn("support-x25519mlkem768", content)

            m_qr = self._menu("")
            m_qr.manager.state = root
            m_qr.manager.nodes.return_value = [n]
            m_qr.manager.get.return_value = n
            m_qr.manager.services.runtime.return_value = {"state": "active"}
            with patch.object(m_qr, "ask", side_effect=["1", "y"]):
                m_qr.share_menu()
            self.assertIn("█", m_qr.tty.getvalue())

    def test_early_port_conflict_and_auto_suffixed_name(self):
        existing = fixture("anytls", 8443)
        existing["name"] = "anytls"
        m = self._menu("8.8.8.8\n\n8443\n\n9443\n\nexample.com\n\n")
        m.manager.nodes.return_value = [existing]
        def cert(n, old):
            n["tls"] = {"mode": "import", "certificate": "/t/c.pem", "key": "/t/k.pem"}
            return False
        m.tls_input = cert
        with patch("nodepilot_pkg.menu.port_free"), patch("getpass.getpass", return_value=""):
            n, _ = m.node_input("anytls")
        self.assertEqual(n["name"], "anytls-2")
        self.assertEqual(n["port"], 9443)
        self.assertIn("端口/监听不可用", m.tty.getvalue())

    @unittest.skipUnless(os.name == "posix", "POSIX paths")
    def test_cores_and_manager_prune_keep_active_and_skip_when_pending(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cores = Cores(root / "cores")
            parent = cores.root / "sing-box"
            versions = []
            for idx, name in enumerate(("1.12.0-aaaaaaaa", "1.13.0-bbbbbbbb", "1.14.0-cccccccc")):
                vdir = parent / name
                vdir.mkdir(parents=True)
                os.utime(vdir, (1000 + idx * 10, 1000 + idx * 10))
                versions.append(vdir)
            cores.switch("sing-box", versions[-1])
            removed = cores.prune("sing-box", keep=2)
            self.assertEqual(removed, [versions[0]])
            self.assertTrue(versions[1].exists())
            self.assertTrue(versions[2].exists())

            mgr = Manager(root / "state", FakeCores(), FakeServices(root / "units"))
            mgr.initialize()
            bdir = mgr.state / "backups"
            for idx in range(5):
                bf = bdir / f"b{idx}.json"
                bf.write_text("{}")
                os.utime(bf, (2000 + idx * 10, 2000 + idx * 10))
            mgr.prune(keep_revisions=2, keep_backups=2)
            self.assertEqual(sorted(p.name for p in bdir.iterdir()), ["b3.json", "b4.json"])

            (mgr.state / "core-pending.json").write_text("{}")
            mgr.prune(keep_revisions=1, keep_backups=1)
            self.assertEqual(len(list(bdir.iterdir())), 2)

    def test_supports_mlkem768_and_dual_stack_ready_helpers(self):
        with patch("nodepilot_pkg.targets.run_openssl", return_value=CompletedProcess([], 0, b"Server Temp Key: X25519MLKEM768\n", b"")):
            self.assertIs(supports_mlkem768("8.8.8.8", 443, "example.com"), True)
        with patch("nodepilot_pkg.targets.run_openssl", return_value=CompletedProcess([], 1, b"unknown elliptic curve\n", b"")):
            self.assertIsNone(supports_mlkem768("8.8.8.8", 443, "example.com"))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bindv6only"
            p.write_text("0\n")
            self.assertTrue(http01_dual_stack_ready(p))
            p.write_text("9\n")
            with self.assertRaises(PilotError):
                http01_dual_stack_ready(p)


if __name__ == "__main__":
    unittest.main()
