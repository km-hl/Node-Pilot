import io
import unittest
from unittest.mock import MagicMock, patch

from nodepilot_pkg.menu import Menu
from nodepilot_pkg.model import new_node, validate


class InputDefaultTests(unittest.TestCase):
    def menu(self, text):
        menu = object.__new__(Menu)
        menu.tty_read = io.StringIO(text)
        menu.tty = io.StringIO()
        menu.manager = MagicMock()
        menu.ensure_core = MagicMock()
        menu.ips = {"ipv4": "8.8.8.8", "ipv6": None}
        return menu

    def test_enter_uses_displayed_address_port_and_empty_defaults(self):
        menu = self.menu("\r\n  \n\n")
        self.assertEqual(menu.ask("公网分享 IP/域名", "8.8.8.8"), "8.8.8.8")
        self.assertEqual(int(menu.ask("Caddy 本机目标端口", 8003)), 8003)
        self.assertEqual(menu.ask("网页来源 HTTPS 域名", ""), "")
        self.assertIn("[8.8.8.8]", menu.tty.getvalue())
        self.assertIn("[8003]", menu.tty.getvalue())
        self.assertIn("[留空]", menu.tty.getvalue())

    def test_required_input_reprompts_without_inventing_sni(self):
        menu = self.menu("\n  \nnode.example.com\n")
        self.assertEqual(menu.ask("客户端 TLS SNI"), "node.example.com")
        self.assertEqual(menu.tty.getvalue().count("客户端 TLS SNI（必填）"), 3)

    def test_eof_never_accepts_a_default_or_confirms(self):
        for operation in (lambda m: m.ask("端口", 443), lambda m: m.confirm("确认", default=True)):
            with self.subTest(operation=operation), self.assertRaises(EOFError):
                operation(self.menu(""))

    def test_secret_enter_keeps_or_generates_without_printing_credentials(self):
        menu = self.menu("")
        with patch("getpass.getpass", side_effect=["", "", " chosen password "]) as hidden:
            self.assertEqual(menu.secret("认证密码", "old credential"), "old credential")
            generated = menu.secret("认证密码")
            self.assertGreaterEqual(len(generated), 32)
            self.assertEqual(menu.secret("认证密码"), " chosen password ")
        for call in hidden.call_args_list:
            self.assertNotIn("old credential", call.args[0])
        self.assertNotIn(generated, menu.tty.getvalue())

    def test_required_token_reprompts_and_never_generates_a_token(self):
        menu = self.menu("")
        with patch("getpass.getpass", side_effect=["", "  ", "operator supplied token"]), \
             patch("nodepilot_pkg.menu.secrets.token_urlsafe") as generate:
            self.assertEqual(menu.secret("Cloudflare API Token", required=True), "operator supplied token")
        generate.assert_not_called()
        self.assertNotIn("operator supplied token", menu.tty.getvalue())

    def test_enter_cancels_delete_and_uninstall_at_either_confirmation(self):
        from test_config import fixture
        for method in ("delete", "uninstall"):
            for text in ("\n", "y\n\n"):
                with self.subTest(method=method, text=text):
                    menu = self.menu(text)
                    menu.select = lambda: fixture()
                    with patch("nodepilot_pkg.installation.uninstall") as uninstall:
                        getattr(menu, method)()
                    menu.manager.delete.assert_not_called()
                    menu.manager.locked.assert_not_called()
                    uninstall.assert_not_called()

    def test_enter_returns_from_update_and_backup_without_side_effects(self):
        for method in ("update", "backup_restore"):
            with self.subTest(method=method):
                menu = self.menu("\n")
                getattr(menu, method)()
                self.assertEqual(menu.manager.mock_calls, [])
                self.assertIn("[0]", menu.tty.getvalue())

    def test_core_install_enter_accepts_but_explicit_n_cancels(self):
        from nodepilot_pkg.common import PilotError
        for answer in ("\n\n", "n\n"):
            with self.subTest(answer=answer):
                menu = self.menu(answer)
                menu.manager.cores.binary.side_effect = PilotError("未安装")
                if answer.startswith("n"):
                    with self.assertRaisesRegex(PilotError, "已取消"):
                        Menu.ensure_core(menu, "sing-box")
                    menu.manager.update_core.assert_not_called()
                else:
                    Menu.ensure_core(menu, "sing-box")
                    menu.manager.update_core.assert_called_once_with("sing-box", "latest")
                    self.assertIn("[y]", menu.tty.getvalue())

    def test_create_and_save_enter_accept_but_n_cancels(self):
        from test_config import fixture
        for operation in ("create", "edit"):
            for answer in ("\n", "n\n"):
                with self.subTest(operation=operation, answer=answer):
                    n = fixture("anytls")
                    menu = self.menu(answer)
                    menu.memory_notice = MagicMock()
                    menu.node_input = MagicMock(return_value=(n, False))
                    menu.manager.get.return_value = n
                    menu.manager.apply.return_value = n
                    menu.create("anytls") if operation == "create" else menu.edit(n)
                    if answer == "\n":
                        menu.manager.apply.assert_called_once_with(n, renew=False)
                        self.assertIn("[y]", menu.tty.getvalue())
                    else:
                        menu.manager.apply.assert_not_called()

    def test_update_enter_accepts_checked_core_or_script(self):
        for choice, core in (("1", "sing-box"), ("2", "caddy"), ("3", "nodepilot")):
            with self.subTest(core=core):
                menu = self.menu(choice + "\n\n\n")
                with patch("nodepilot_pkg.project_update.online_update") as online:
                    if core == "nodepilot":
                        with self.assertRaises(EOFError):
                            menu.update()
                        online.assert_called_once_with(menu.tty_read, menu.tty, "latest")
                    else:
                        menu.update()
                        menu.manager.update_core.assert_called_once_with(core, "latest")
                        online.assert_not_called()
        menu = self.menu("4\n")
        menu.check_updates = MagicMock()
        menu.update()
        menu.check_updates.assert_called_once_with()

    def test_certificate_risk_consent_stays_n_but_dependency_install_defaults_y(self):
        from nodepilot_pkg.common import PilotError
        for choice, text in (("1", "\n\n"), ("4", "\n")):
            with self.subTest(choice=choice):
                menu = self.menu(text)
                n = new_node("anytls", "test", "8.8.8.8", sni="node.example.com")
                with patch("nodepilot_pkg.menu.run") as run, self.assertRaisesRegex(PilotError, "已取消"):
                    menu.tls_input(n, choice=choice)
                run.assert_not_called()
                self.assertIn("[n]", menu.tty.getvalue())
        menu = self.menu("\ny\n\n")
        n = new_node("anytls", "test", "8.8.8.8", sni="node.example.com")
        with patch("nodepilot_pkg.menu.shutil.which", return_value=None), patch("nodepilot_pkg.menu.run") as run:
            menu.tls_input(n, choice="1")
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].args[0], ["apt-get", "update"])
        self.assertIn("[y]", menu.tty.getvalue())

    def tls_menu(self, text):
        menu = self.menu(text)
        def certificate(n, old):
            n["tls"] = {"mode": "import", "certificate": "/trusted/cert.pem", "key": "/trusted/key.pem"}
            return False
        menu.tls_input = certificate
        return menu

    def test_new_tls_nodes_default_sni_to_share_domain_only(self):
        for protocol in ("anytls", "hysteria2"):
            for server in ("node.example.com", "8.8.8.8"):
                with self.subTest(protocol=protocol, server=server):
                    # On an IP connection, an empty SNI must reprompt before using the supplied domain.
                    text = server + "\n" * 5 + ("node.example.com\n" if server == "8.8.8.8" else "") + "\n" * 4
                    menu = self.tls_menu(text)
                    with patch("nodepilot_pkg.menu.port_free"), patch("getpass.getpass", return_value=""):
                        n, renew = menu.node_input(protocol)
                    self.assertEqual(n["server"], server)
                    self.assertEqual(n["sni"], "node.example.com")
                    self.assertFalse(renew)
                    self.assertNotIn("obfs", n)
                    self.assertNotIn("up_mbps", n)
                    self.assertNotIn("down_mbps", n)
                    validate(n)

    def test_edit_enter_preserves_tls_node_settings_and_authentication(self):
        from test_config import fixture
        old = fixture("hysteria2", 10443)
        old.update(up_mbps=15, down_mbps=30, obfs={"type": "salamander", "password": "x" * 32})
        menu = self.menu("\n" * 10)
        with patch("nodepilot_pkg.menu.port_free"), patch("getpass.getpass", return_value=""):
            n, renew = menu.node_input("hysteria2", old)
        self.assertEqual(n, old)
        self.assertFalse(renew)

    def test_optional_acme_email_accepts_enter_before_explicit_consent(self):
        menu = self.menu("\ny\n")
        n = new_node("anytls", "test", "8.8.8.8", sni="node.example.com")
        with patch("nodepilot_pkg.menu.shutil.which", return_value="/usr/bin/certbot"), \
             patch("nodepilot_pkg.menu.run") as run:
            menu.tls_input(n, choice="1")
        self.assertEqual(n["tls"]["email"], "")
        self.assertEqual(n["tls"]["mode"], "acme")
        run.assert_not_called()

    def test_caddy_welcome_page_and_port_accept_enter(self):
        menu = self.menu("\n".join(["node.example.com", "", "", "", "", "y", "", "", ""]) + "\n")
        menu.manager.nodes.return_value = []
        menu.manager.cores.keypair.return_value = ("A" * 43, "B" * 43)
        n = new_node("reality", "test", "8.8.8.8")
        with patch("nodepilot_pkg.system.port_free"), patch("getpass.getpass", return_value=""):
            menu.reality_input(n, None)
        self.assertEqual(n["caddy"]["port"], 8003)
        self.assertEqual(n["caddy"]["upstream"], "")
        self.assertEqual(n["caddy"]["email"], "")
        self.assertEqual(n["reality"]["target"], "127.0.0.1:8003")
        validate(n)


class RealityMenuTests(unittest.TestCase):
    def menu(self, domain):
        menu = object.__new__(Menu)
        menu.manager = MagicMock()
        menu.manager.nodes.return_value = []
        menu.manager.cores.keypair.return_value = ("A" * 43, "B" * 43)
        menu.ensure_core = MagicMock()
        menu.say = MagicMock()
        menu.confirm = lambda text, default=False: True
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
        result=self.inspected('origin.example.com:10443','manual.example.com')
        with patch("nodepilot_pkg.targets.cloudflare_networks", return_value=[]), \
             patch("nodepilot_pkg.targets.inspect_target", return_value=result) as screen:
            menu.reality_input(n, None)
        validate(n)
        screen.assert_called_once_with("origin.example.com:10443", "manual.example.com", [])
        self.assertEqual(n["reality"]["target"], "8.8.8.8:10443")
        self.assertEqual(n['reality']['selection']['requested_target'],'origin.example.com:10443')

    def inspected(self,target='origin.example.com:443',sni='manual.example.com',http_error=None):
        import ipaddress
        from nodepilot_pkg.model import split_target,is_ip
        from nodepilot_pkg.targets import inspect_target
        from test_targets import RANGES
        original,_=split_target(target)
        address=str(ipaddress.ip_address(original)) if is_ip(original) else '8.8.8.8'
        with patch('nodepilot_pkg.targets.resolve',side_effect=lambda h:([address if h==original else '8.8.8.8'],set())), \
             patch('nodepilot_pkg.targets.tls_handshake',return_value={'elapsed_ms':10,'group':'X25519'}), \
             patch('nodepilot_pkg.targets.http_headers',side_effect=http_error,return_value=(200,{})):
            return inspect_target(target,sni,RANGES)

    def manual_menu(self,target='origin.example.com:443',mode='1',confirm=None):
        menu=self.menu('');original=menu.ask
        def ask(prompt,default=None):
            for prefix,value in [('候选域名','manual'),('第三方客户端 SNI','manual.example.com'),
                                 ('第三方 Reality 目标',target),('目标模式',mode)]:
                if prompt.startswith(prefix):return value
            return original(prompt,default)
        menu.ask=ask
        menu.confirm=confirm or MagicMock(return_value=True)
        return menu

    def test_manual_domain_pin_sni_metadata_and_generated_config_agree(self):
        from nodepilot_pkg.config import generate
        menu=self.manual_menu();n=new_node('reality','test','8.8.8.8')
        result=self.inspected()
        with patch('nodepilot_pkg.targets.cloudflare_networks',return_value=[]), \
             patch('nodepilot_pkg.targets.inspect_target',return_value=result):
            menu.reality_input(n,None)
        r=n['reality'];selected=r['selection']
        self.assertEqual(r['target'],'8.8.8.8:443');self.assertEqual(n['sni'],'manual.example.com')
        self.assertEqual(selected['target_mode'],'pinned_ip');self.assertEqual(selected['selected_family'],4)
        self.assertEqual(selected['requested_target'],'origin.example.com:443')
        inbound = generate(n)['inbounds'][0]
        self.assertEqual(inbound['tls']['reality']['handshake'], {'server': '8.8.8.8', 'server_port': 443})
        self.assertEqual(inbound['tls']['server_name'], n['sni'])

    def test_manual_literal_ip_retains_input_and_does_not_offer_dynamic_mode(self):
        for target in ('8.8.8.8:443','[2001:4860:4860:0:0:0:0:8888]:443'):
            menu=self.manual_menu(target);n=new_node('reality','test','8.8.8.8')
            result=self.inspected(target)
            with patch('nodepilot_pkg.targets.cloudflare_networks',return_value=[]), \
                 patch('nodepilot_pkg.targets.inspect_target',return_value=result):
                menu.reality_input(n,None)
            self.assertEqual(n['reality']['target'],target)
            self.assertEqual(n['reality']['selection']['target'],target)
            self.assertEqual(n['reality']['selection']['target_mode'],'literal_ip')

    def test_dynamic_domain_requires_explicit_confirmation_and_records_tested_ip(self):
        menu=self.manual_menu(mode='2');n=new_node('reality','test','8.8.8.8')
        with patch('nodepilot_pkg.targets.cloudflare_networks',return_value=[]), \
             patch('nodepilot_pkg.targets.inspect_target',return_value=self.inspected()):
            menu.reality_input(n,None)
        s=n['reality']['selection']
        self.assertEqual(n['reality']['target'],'origin.example.com:443')
        self.assertEqual(s['target_mode'],'dynamic_dns');self.assertTrue(s['dynamic_dns'])
        self.assertEqual(s['tested_target'],'8.8.8.8:443')
        self.assertIn('明确确认保留动态域名',menu.confirm.call_args_list[0].args[0])

    def test_manual_unknown_lists_reasons_and_requires_two_confirmations(self):
        from nodepilot_pkg.common import PilotError
        for answers,accepted in (([False],False),([True,False],False),([True,True,True],True)):
            menu=self.manual_menu(confirm=MagicMock(side_effect=answers));n=new_node('reality','test','8.8.8.8')
            result=self.inspected(http_error=TimeoutError())
            # Setting the legacy boolean cannot suppress the missing check.
            result['cf_screened']=True
            with patch('nodepilot_pkg.targets.cloudflare_networks',return_value=[]), \
                 patch('nodepilot_pkg.targets.inspect_target',return_value=result):
                if accepted:menu.reality_input(n,None)
                else:
                    with self.assertRaisesRegex(PilotError,'已取消'):menu.reality_input(n,None)
                    self.assertNotIn('reality',n)
            shown=' '.join(str(c.args[0]) for c in menu.say.call_args_list)
            self.assertIn('http_headers',shown);self.assertIn('超时',shown);self.assertIn('建议重新检测',shown)
            if accepted:self.assertEqual(menu.confirm.call_count,3)

    def test_cancel_selection_preserves_existing_node_and_never_applies(self):
        import copy
        from test_config import fixture
        from nodepilot_pkg.common import PilotError
        for mode in ('1','2'):
            menu=self.manual_menu(mode=mode,confirm=MagicMock(return_value=False));old=fixture();n=copy.deepcopy(old)
            with patch('nodepilot_pkg.targets.cloudflare_networks',return_value=[]), \
                 patch('nodepilot_pkg.targets.inspect_target',return_value=self.inspected()):
                with self.assertRaisesRegex(PilotError,'已取消'):menu.reality_input(n,old)
            self.assertEqual(n,old);menu.manager.apply.assert_not_called();menu.manager.cores.keypair.assert_not_called()

    def test_keep_legacy_target_sni_credentials_without_network_or_reselection(self):
        import copy
        from test_config import fixture
        for selection in (None,{'target':'legacy.example.com:443','sni':'www.example.com','cf_screened':True}):
            old=fixture();old['reality'].update(target='legacy.example.com:443',mode='external')
            if selection:old['reality']['selection']=selection
            n=copy.deepcopy(old);menu=self.menu('');original=menu.ask
            menu.ask=lambda prompt,default=None:'keep' if prompt.startswith('候选域名') else original(prompt,default)
            menu.confirm=MagicMock(return_value=False)
            with patch('nodepilot_pkg.targets.inspect_target') as scan,patch('nodepilot_pkg.targets.rank_targets') as rank:
                menu.reality_input(n,old)
            self.assertEqual(n,old);scan.assert_not_called();rank.assert_not_called();menu.manager.cores.keypair.assert_not_called()

    def test_edit_defaults_to_keep_without_forcing_legacy_reselection(self):
        import copy
        from test_config import fixture
        old=fixture();n=copy.deepcopy(old);menu=self.menu('');menu.confirm=MagicMock(return_value=False)
        with patch('nodepilot_pkg.targets.inspect_target') as scan,patch('nodepilot_pkg.targets.rank_targets') as rank:
            menu.reality_input(n,old)
        for key in ('target','uuid','short_id','private_key','public_key','server_names'):
            self.assertEqual(n['reality'][key],old['reality'][key])
        self.assertEqual(n['sni'],old['sni']);scan.assert_not_called();rank.assert_not_called()

    def test_explicit_cloudflare_rejection_never_offers_override_or_changes_parameters(self):
        from nodepilot_pkg.common import PilotError
        menu=self.manual_menu();n=new_node('reality','test','8.8.8.8')
        with patch('nodepilot_pkg.targets.cloudflare_networks',return_value=[]), \
             patch('nodepilot_pkg.targets.inspect_target',side_effect=PilotError('排除 Cloudflare')):
            with self.assertRaisesRegex(PilotError,'Cloudflare'):menu.reality_input(n,None)
        menu.confirm.assert_not_called();self.assertNotIn('reality',n)

    def test_restore_preview_precedes_confirmation_and_digest_is_forwarded(self):
        menu=self.menu('');menu.ask=MagicMock(side_effect=['2','/trusted/backup.json'])
        summary={'sha256':'a'*64,'nodes':[{'id':'n-1','name':'test','protocol':'anytls','certificate_mode':'acme',
                 'restored_mode':'import','notice':'自动续期已关闭，需要重新配置'}]}
        menu.manager.preview_restore.return_value=summary
        menu.manager.restore.return_value=['n-1']
        def confirm(text, default=False):
            shown=' '.join(str(c.args[0]) for c in menu.say.call_args_list)
            self.assertIn('将恢复 1 个节点',shown);self.assertIn('anytls',shown)
            self.assertIn('acme → import',shown);self.assertIn('自动续期已关闭',shown)
            return True
        menu.confirm=confirm;menu.backup_restore()
        menu.manager.restore.assert_called_once_with('/trusted/backup.json',expected_digest='a'*64)
