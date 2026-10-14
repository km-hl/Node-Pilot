import hashlib
import io
import os
import socket
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nodepilot_pkg.common import PilotError
from nodepilot_pkg.cores import Cores, extract_core, verify_elf
from nodepilot_pkg.download import fetch, https, verified_asset
from nodepilot_pkg.services import Services
from nodepilot_pkg.system import port_free, suggested_listen
from test_config import fixture


class SecurityTests(unittest.TestCase):
    def test_ipv6_share_address_suggests_ipv6_listener(self):
        self.assertEqual(suggested_listen("2606:4700:4700::1111", {}), "::")
        self.assertEqual(suggested_listen("example.com", {"ipv6": "2606:4700:4700::1111"}), "::")
        self.assertEqual(suggested_listen("8.8.8.8", {"ipv4": "8.8.8.8"}), "0.0.0.0")
    def test_https_only(self):
        for u in ("http://example.com", "file:///tmp/x", "https://user:pass@example.com"):
            with self.assertRaises(PilotError):
                https(u)

    def test_missing_digest_never_downloads(self):
        with patch("nodepilot_pkg.download.fetch") as download:
            with self.assertRaises(PilotError):
                verified_asset({"browser_download_url": "https://github.com/XTLS/Xray-core/releases/download/v1/x.zip"}, "/tmp")
        download.assert_not_called()

    def test_foreign_asset_refused(self):
        with self.assertRaises(PilotError):
            verified_asset({"browser_download_url": "https://evil.invalid/x", "digest": "sha256:" + "a" * 64}, "/tmp")

    def test_corrupt_download_deleted(self):
        response = io.BytesIO(b"corrupt archive")
        response.geturl = lambda: "https://github.com/asset"
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "archive"
            with patch("urllib.request.build_opener") as opener:
                opener.return_value.open.return_value = response
                with self.assertRaises(PilotError):
                    fetch("https://github.com/asset", path, "a" * 64)
            self.assertFalse(path.exists())

    def test_download_size_bound(self):
        response = io.BytesIO(b"0123456789")
        response.geturl = lambda: "https://github.com/asset"
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "archive"
            with patch("urllib.request.build_opener") as opener:
                opener.return_value.open.return_value = response
                with self.assertRaises(PilotError):
                    fetch("https://github.com/asset", path, limit=4)
            self.assertFalse(path.exists())

    def test_archive_symlink_not_extracted(self):
        with tempfile.TemporaryDirectory() as d:
            archive = Path(d) / "x.tar.gz"
            with tarfile.open(archive, "w:gz") as t:
                info = tarfile.TarInfo("v/sing-box")
                info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
                t.addfile(info)
            with self.assertRaises(PilotError):
                extract_core(archive, Path(d) / "sing-box", "sing-box")
            self.assertFalse((Path(d) / "sing-box").exists())

    def test_wrong_elf_architecture_refused(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "core"
            path.write_bytes(b"not an ELF binary")
            with self.assertRaises(PilotError):
                verify_elf(path, "amd64")

    def test_keypair_labels_across_versions(self):
        core = Cores()
        for public_label in ("Public key", "Password", "Password (PublicKey)"):
            output = "PrivateKey: " + "A" * 43 + "\n" + public_label + ": " + "B" * 43 + "\nHash32: ignored"
            with patch.object(core, "binary", return_value=Path("/test/xray")), patch("nodepilot_pkg.cores.run", return_value=output):
                self.assertEqual(core.keypair(), ("A" * 43, "B" * 43))

    def test_imported_private_key_never_passed_in_argv(self):
        import base64
        core = Cores()
        private = "A" * 43
        public_bytes = b"x" * 32
        der = bytes.fromhex("302a300506032b656e032100") + public_bytes
        with patch("nodepilot_pkg.cores.run_bytes", return_value=der) as command:
            result = core.keypair(private)
        self.assertEqual(result[1], base64.urlsafe_b64encode(public_bytes).decode().rstrip("="))
        args, data = command.call_args.args
        self.assertNotIn(private, args)
        self.assertTrue(data.endswith(b"\0" * 32))

    def test_unit_foreign_or_symlink_refused(self):
        with tempfile.TemporaryDirectory() as d:
            svc = Services(units=Path(d))
            n = fixture()
            p = Path(d) / n["service"]
            p.write_text("foreign unit")
            with self.assertRaises(PilotError):
                svc.assert_owned(n)
            self.assertEqual(p.read_text(), "foreign unit")
            if os.name == "posix":
                p.unlink()
                p.symlink_to("/etc/passwd")
                with self.assertRaises(PilotError):
                    svc.assert_owned(n)

    def test_real_tcp_udp_bind_conflicts(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp:
            tcp.bind(("127.0.0.1", 0))
            tcp.listen()
            port = tcp.getsockname()[1]
            with self.assertRaises(PilotError):
                port_free("127.0.0.1", port, "tcp")
            port_free("127.0.0.1", port, "udp")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.bind(("127.0.0.1", 0))
            with self.assertRaises(PilotError):
                port_free("127.0.0.1", udp.getsockname()[1], "udp")

    @unittest.skipUnless(os.name == "posix", "Linux TCP TIME_WAIT reuse")
    def test_tcp_time_wait_reusable_without_hiding_active_listener(self):
        with socket.socket() as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            port = server.getsockname()[1]
            with self.assertRaises(PilotError):
                port_free("127.0.0.1", port, "tcp")
            with socket.create_connection(("127.0.0.1", port)) as client:
                accepted, _ = server.accept()
                accepted.close()
                self.assertEqual(client.recv(1), b"")
        port_free("127.0.0.1", port, "tcp")


if __name__ == "__main__":
    unittest.main()
