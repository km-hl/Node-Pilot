import unittest
from unittest.mock import MagicMock, patch

from nodepilot_pkg.menu import Menu
from nodepilot_pkg.model import new_node, validate


class RealityMenuTests(unittest.TestCase):
    def menu(self, domain):
        menu = object.__new__(Menu)
        menu.manager = MagicMock()
        menu.manager.nodes.return_value = []
        menu.manager.cores.keypair.return_value = ("A" * 43, "B" * 43)
        menu.ensure_core = MagicMock()
        menu.say = MagicMock()
        menu.confirm = lambda text: True
        menu.secret = lambda *args: "auto"
        def ask(prompt, default=None):
            if prompt.startswith("自有域名"):
                return domain
            if prompt.startswith("ACME 邮箱"):
                return ""
            return str(default) if default is not None else ""
        menu.ask = ask
        return menu

    def test_domain_defaults_to_caddy_acme_with_optional_email_and_bound_sni(self):
        menu = self.menu("node.example.com")
        n = new_node("reality", "node", "8.8.8.8")
        with patch("nodepilot_pkg.system.port_free"), patch("nodepilot_pkg.targets.rank_targets") as rank:
            menu.reality_input(n, None)
        validate(n)
        self.assertEqual(n["reality"]["mode"], "self")
        self.assertEqual(n["caddy"]["certificate_mode"], "acme")
        self.assertEqual(n["caddy"]["email"], "")
        self.assertEqual(n["sni"], n["caddy"]["domain"])
        self.assertEqual(n["reality"]["target"], f"127.0.0.1:{n['caddy']['port']}")
        rank.assert_not_called()
        menu.ensure_core.assert_called_once_with("caddy")

    def test_no_domain_uses_current_best_rank_and_does_not_install_caddy(self):
        menu = self.menu("")
        n = new_node("reality", "node", "8.8.8.8")
        best = {"sni": "fast.example.com", "target": "fast.example.com:443", "median_ms": 10, "spread_ms": 1}
        with patch("nodepilot_pkg.targets.rank_targets", return_value=[best]):
            menu.reality_input(n, None)
        validate(n)
        self.assertEqual(n["reality"]["mode"], "external")
        self.assertEqual(n["sni"], best["sni"])
        self.assertNotIn("caddy", n)
        menu.ensure_core.assert_not_called()

    def test_manual_custom_target_port_remains_available_and_is_screened(self):
        menu = self.menu("")
        original = menu.ask
        def ask(prompt, default=None):
            if prompt.startswith("候选域名"):
                return "manual"
            if prompt.startswith("第三方客户端 SNI"):
                return "manual.example.com"
            if prompt.startswith("第三方 Reality 目标"):
                return "origin.example.com:10443"
            return original(prompt, default)
        menu.ask = ask
        n = new_node("reality", "node", "8.8.8.8")
        with patch("nodepilot_pkg.targets.cloudflare_networks", return_value=[]), \
             patch("nodepilot_pkg.targets.inspect_target", return_value={"median_ms": 10}) as screen:
            menu.reality_input(n, None)
        validate(n)
        screen.assert_called_once_with("origin.example.com:10443", "manual.example.com", [])
        self.assertEqual(n["reality"]["target"], "origin.example.com:10443")

    def test_restore_preview_precedes_confirmation_and_digest_is_forwarded(self):
        menu=self.menu('');menu.ask=MagicMock(side_effect=['2','/trusted/backup.json'])
        summary={'sha256':'a'*64,'nodes':[{'id':'n-1','name':'test','protocol':'anytls','certificate_mode':'acme',
                 'restored_mode':'import','notice':'自动续期已关闭，需要重新配置'}]}
        menu.manager.preview_restore.return_value=summary
        menu.manager.restore.return_value=['n-1']
        def confirm(text):
            shown=' '.join(str(c.args[0]) for c in menu.say.call_args_list)
            self.assertIn('将恢复 1 个节点',shown);self.assertIn('anytls',shown)
            self.assertIn('acme → import',shown);self.assertIn('自动续期已关闭',shown)
            return True
        menu.confirm=confirm;menu.backup_restore()
        menu.manager.restore.assert_called_once_with('/trusted/backup.json',expected_digest='a'*64)
