import os
import tempfile
import unittest
from pathlib import Path

from nodepilot_pkg.system import existing_engines


@unittest.skipUnless(os.name == "posix", "需要 Linux 风格的 /proc 符号链接")
class ExistingCoreTests(unittest.TestCase):
    def test_panel_bundled_xray_detected_without_reading_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            p = proc / "1234"
            p.mkdir()
            (p / "comm").write_text("xray-linux-amd6\n")
            (p / "exe").symlink_to("/usr/local/x-ui/bin/xray-linux-amd64")
            (p / "cgroup").write_text("0::/system.slice/x-ui.service\n")
            self.assertEqual(existing_engines(proc), [{"name": "xray-linux-amd64", "pid": 1234, "service": "x-ui.service"}])

    def test_project_cores_not_reported_as_foreign(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            p = proc / "1234"
            p.mkdir()
            (p / "comm").write_text("sing-box\n")
            (p / "exe").symlink_to("/opt/nodepilot/cores/sing-box/version/sing-box")
            self.assertEqual(existing_engines(proc), [])
