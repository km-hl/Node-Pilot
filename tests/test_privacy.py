import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.privacy_audit import allowed, audit, git_candidates
from tools.client_probe import outbound, xray_client
from nodepilot_pkg.share import share
from test_config import fixture


class PrivacyTests(unittest.TestCase):
    def test_private_paths_disallowed(self):
        for name in ("AGENTS.md", ".test-output/clients.json", ".test-tools/private_known_hosts",
                     ".env", "nodepilot_pkg/__pycache__/x.pyc", "backups/backup.json"):
            self.assertFalse(allowed(name))

    def test_scan_exact_staged_bytes_even_when_working_copy_cleaned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
            def git(*args):
                subprocess.run(["git", *args], cwd=root, env=env, capture_output=True, check=True)
            git("init", "--initial-branch=main")
            needle = "operator" + "-session"
            path = root / "README.md"
            path.write_text(needle)
            git("add", "--", "README.md")
            path.write_text("clean working copy")
            with patch("tools.privacy_audit.ROOT", root):
                paths, reader = git_candidates(staged=True)
                issues = audit(paths, [needle], reader)
            self.assertTrue(any("private value" in reason for _, reason in issues))

    def test_exported_uri_client_has_no_server_private_key(self):
        for protocol in ("reality", "anytls", "hysteria2"):
            n = fixture(protocol)
            client = {"protocol": protocol, "uri": share(n)}
            config = outbound(client, "/trusted-test-certificate.pem")
            text = str(config)
            self.assertNotIn("private_key", text)
            if protocol == "reality":
                self.assertNotIn(n["reality"]["private_key"], text)
                self.assertEqual(xray_client(client, 10000)["outbounds"][0]["streamSettings"]["realitySettings"]["password"], n["reality"]["public_key"])

    def test_probe_refuses_insecure_uri(self):
        n = fixture("anytls")
        with self.assertRaises(ValueError):
            outbound({"uri": share(n).replace("insecure=0", "insecure=1")})


if __name__ == "__main__":
    unittest.main()
