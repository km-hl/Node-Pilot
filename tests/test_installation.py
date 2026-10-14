import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nodepilot_pkg.common import PilotError
from nodepilot_pkg.installation import LAUNCHER, RENEW_SERVICE, remove_program_files, uninstall
from nodepilot_pkg.manager import OWNER
from tools.package import package


class InstallationTests(unittest.TestCase):
    def test_uninstall_refuses_external_renewal_dropin(self):
        with tempfile.TemporaryDirectory() as d:
            root, units = Path(d) / "program", Path(d) / "units"
            root.mkdir()
            units.mkdir()
            (root / ".owner").write_text(OWNER)
            launcher = Path(d) / "nodepilot"
            launcher.write_text(LAUNCHER)
            unit = units / "nodepilot-renew.service"
            unit.write_text(RENEW_SERVICE)
            with patch("nodepilot_pkg.installation.run", return_value="/external/dropin.conf"):
                with self.assertRaises(PilotError):
                    uninstall(object(), root, launcher, units)
            self.assertEqual(unit.read_text(), RENEW_SERVICE)
            self.assertEqual(launcher.read_text(), LAUNCHER)
    def test_reproducible_allowlisted_package(self):
        import tarfile
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "a.tar.gz", Path(d) / "b.tar.gz"
            self.assertEqual(package(a), package(b))
            with tarfile.open(a) as t:
                names = t.getnames()
            self.assertIn("nodepilot_pkg/deploy.py", names)
            self.assertFalse(any(x.startswith(("tests/", ".test-tools/", "AGENTS", "docs/")) for x in names))

    def test_program_uninstall_preserves_foreign_files(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "program"
            release = root / "app" / ("a" * 64)
            release.mkdir(parents=True)
            (root / ".owner").write_text(OWNER)
            (release / "nodepilot").write_text("owned")
            (release / "files.json").write_text(json.dumps({"schema": 1, "files": ["nodepilot"]}))
            foreign = release / "user-notes.txt"
            foreign.write_text("keep")
            outside = root / "external-data.txt"
            outside.write_text("keep too")
            remove_program_files(root)
            self.assertEqual(foreign.read_text(), "keep")
            self.assertEqual(outside.read_text(), "keep too")
            self.assertFalse((release / "nodepilot").exists())


if __name__ == "__main__":
    unittest.main()
