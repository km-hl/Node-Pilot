import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from nodepilot_pkg.common import PilotError
from nodepilot_pkg.menu import Menu
from nodepilot_pkg.releases import release, stable_versions, version


class ConfirmTests(unittest.TestCase):
    def menu(self, values):
        menu = object.__new__(Menu)
        menu.ask = MagicMock(side_effect=values)
        menu.say = MagicMock()
        return menu

    def test_lower_and_upper_yes_no(self):
        for value, expected in (("y", True), ("Y", True), ("n", False), ("N", False)):
            self.assertEqual(self.menu([value]).confirm("confirm"), expected)

    def test_invalid_confirmation_reprompts_without_mutation(self):
        menu = self.menu(["maybe", "YES", "N"])
        self.assertFalse(menu.confirm("confirm"))
        self.assertEqual(menu.ask.call_count, 3)
        self.assertEqual(menu.say.call_count, 2)

    def test_default_is_cancel_and_positive_default_explicit(self):
        menu = self.menu(["n", "y"])
        self.assertFalse(menu.confirm("confirm"))
        self.assertEqual(menu.ask.call_args.args[1], "n")
        self.assertTrue(menu.confirm("confirm", default=True))
        self.assertEqual(menu.ask.call_args.args[1], "y")

    def test_delete_second_n_keeps_node(self):
        from test_config import fixture
        menu = object.__new__(Menu)
        menu.select = lambda: fixture()
        menu.confirm = MagicMock(side_effect=[True, False])
        menu.manager = MagicMock()
        menu.say = MagicMock()
        menu.delete()
        menu.manager.delete.assert_not_called()


class VersionTests(unittest.TestCase):
    def test_strict_versions_prevent_url_or_command_injection(self):
        self.assertEqual(version("v26.3.27"), (26, 3, 27))
        for value in ("../main", "1.2.3-beta", "v1.2;id", "1.2", "latest", "1.2.3\n"):
            with self.assertRaises(PilotError):
                version(value)

    def fetch_json(self, data):
        def write(url, path, **kwargs):
            Path(path).write_text(json.dumps(data))
        return write

    def test_exact_tag_and_stable_record_match(self):
        record = {"tag_name": "v1.14.3", "draft": False, "prerelease": False}
        with patch("nodepilot_pkg.releases.fetch", side_effect=self.fetch_json(record)) as download:
            self.assertEqual(release("SagerNet/sing-box", "1.14.3"), record)
        self.assertIn("/tags/v1.14.3", download.call_args.args[0])
        for bad in ({**record, "prerelease": True}, {**record, "tag_name": "v1.14.2"}):
            with patch("nodepilot_pkg.releases.fetch", side_effect=self.fetch_json(bad)), self.assertRaises(PilotError):
                release("SagerNet/sing-box", "1.14.3")

    def test_version_list_excludes_beta_old_and_draft(self):
        records = [{"tag_name": tag} for tag in ("v1.14.3", "v1.12.0", "v1.10.0", "v1.15.0-beta.1")]
        records += [{"tag_name": "v1.99.0", "draft": True}]
        with patch("nodepilot_pkg.releases.fetch", side_effect=self.fetch_json(records)):
            self.assertEqual(stable_versions("SagerNet/sing-box", (1, 12, 0)), ["v1.14.3", "v1.12.0"])

    def test_menu_lists_then_selects_exact_version(self):
        menu = object.__new__(Menu)
        menu.ask = MagicMock(side_effect=["list", "1.10.0", "v1.14.3"])
        menu.say = MagicMock()
        menu.manager = MagicMock()
        menu.manager.cores.versions.return_value = ["v1.14.3"]
        self.assertEqual(menu.choose_version("sing-box"), "v1.14.3")
        menu.manager.cores.versions.assert_called_once_with("sing-box")

    def test_version_list_pages_without_loading_all_asset_catalogs(self):
        first = [{"tag_name": "v1.15.0-beta.1", "prerelease": True}] * 5
        pages = iter((first, [{"tag_name": "v1.14.3"}]))
        def write(url, path, **kwargs):
            self.assertIn("per_page=5", url)
            Path(path).write_text(json.dumps(next(pages)))
        with patch("nodepilot_pkg.releases.fetch", side_effect=write) as download:
            self.assertEqual(stable_versions("SagerNet/sing-box"), ["v1.14.3"])
        self.assertEqual(download.call_count, 2)

    def test_check_updates_never_stages_or_switches(self):
        menu = object.__new__(Menu)
        menu.manager = MagicMock()
        menu.manager.cores.installed_version.return_value = "26.3.27"
        menu.say = MagicMock()
        with patch("nodepilot_pkg.releases.release", return_value={"tag_name": "v26.3.27"}), \
             patch("nodepilot_pkg.project_update.check_update", return_value={"tag_name": "v0.3.0"}):
            menu.check_updates()
        menu.manager.update_core.assert_not_called()
        menu.manager.cores.stage.assert_not_called()
