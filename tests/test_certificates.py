import shutil
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from nodepilot_pkg.certificates import prepare, validate_pair
from nodepilot_pkg.common import PilotError
from test_config import fixture


class AcmeContactTests(unittest.TestCase):
    def test_email_is_optional_without_disabling_tls_validation(self):
        from nodepilot_pkg.certificates import acme
        for contact in ("", "contact@example.com"):
            n = fixture("anytls")
            n["tls"].update(mode="acme", domain=n["sni"], email=contact)
            with patch("nodepilot_pkg.certificates.dns_matches", return_value={"8.8.8.8"}), patch("nodepilot_pkg.certificates.public_ips"), \
                 patch("nodepilot_pkg.certificates.port_free"), patch("nodepilot_pkg.certificates.shutil.which", return_value="certbot"), \
                 patch("nodepilot_pkg.common.private_dir"), patch("nodepilot_pkg.certificates.run") as command:
                with patch('nodepilot_pkg.certificates.atomic_bytes'):
                    acme(n)
            args = command.call_args.args[0]
            self.assertIn("--agree-tos", args)
            if contact:
                self.assertIn("--email", args)
                self.assertIn(contact, args)
            else:
                self.assertIn("--register-unsafely-without-email", args)
                self.assertNotIn("--email", args)


@unittest.skipUnless(shutil.which("openssl"), "需要 openssl")
class CertificateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def certificate(self, directory, sni="example.com"):
        directory.mkdir()
        n = fixture("anytls")
        n["sni"] = sni
        n["tls"] = dict(mode="selfsigned", certificate="/nonexistent", key="/nonexistent")
        prepare(n, directory)
        return n

    def test_certificate_sni_mismatch_refused(self):
        n = self.certificate(self.root / "one")
        with self.assertRaises(PilotError):
            validate_pair(n["tls"]["certificate"], n["tls"]["key"], "wrong.example.com")

    def test_certificate_key_mismatch_refused(self):
        a = self.certificate(self.root / "one")
        b = self.certificate(self.root / "two")
        with self.assertRaises(PilotError):
            validate_pair(a["tls"]["certificate"], b["tls"]["key"], a["sni"])

    def test_ip_san_and_private_copy(self):
        n = self.certificate(self.root / "one", "8.8.8.8")
        destination = self.root / "import"
        destination.mkdir()
        n["tls"]["mode"] = "import"
        prepare(n, destination)
        validate_pair(n["tls"]["certificate"], n["tls"]["key"], n["sni"])
        self.assertEqual((destination / "key.pem").stat().st_mode & 0o777, 0o600)
