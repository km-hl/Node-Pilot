import shutil
import tempfile
import unittest
from pathlib import Path

from nodepilot_pkg.certificates import prepare, validate_pair
from nodepilot_pkg.common import PilotError
from test_config import fixture


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
