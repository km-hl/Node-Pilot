import copy
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from nodepilot_pkg import certificates
from nodepilot_pkg.common import PilotError, read_json
from nodepilot_pkg.installation import LAUNCHER, preflight_program, recover_uninstall, uninstall
from nodepilot_pkg.manager import Manager, OWNER
from nodepilot_pkg.system import dns_matches, http01_bindings
from test_config import fixture
from test_manager import FakeCores, FakeServices


class HttpFamiliesTests(unittest.TestCase):
    def test_caddy_ipv4_only_uses_ipv4_http_listener(self):
        from nodepilot_pkg.caddy import Caddy,configuration
        from test_caddy import self_node
        n=self_node();n['caddy']['certificate_mode']='acme'
        c=Caddy(Path('/test'),MagicMock())
        with patch.object(c,'assert_owned'),patch('nodepilot_pkg.caddy.dns_matches',return_value={'8.8.8.8'}), \
             patch('nodepilot_pkg.caddy.public_ips'),patch('nodepilot_pkg.caddy.port_free') as bind:
            c.preflight(n,None,[])
        self.assertEqual([call.args[0] for call in bind.call_args_list],['127.0.0.1','0.0.0.0'])
        self.assertEqual(configuration([n],'/test')['apps']['http']['servers']['http01']['listen'],['tcp4/0.0.0.0:80'])

    def test_only_published_families_require_port_80(self):
        for addresses, expected in (({'8.8.8.8'}, ['0.0.0.0']),
                                    ({'2001:4860:4860::8888'}, ['::'])):
            n = fixture('anytls')
            n['tls'].update(mode='acme', domain=n['sni'])
            with patch('nodepilot_pkg.certificates.dns_matches', return_value=addresses), \
                 patch('nodepilot_pkg.certificates.public_ips'), patch('nodepilot_pkg.common.private_dir'), \
                 patch('nodepilot_pkg.certificates.shutil.which', return_value='certbot'), \
                 patch('nodepilot_pkg.certificates.port_free') as bind, patch('nodepilot_pkg.certificates.run'):
                with patch('nodepilot_pkg.certificates.atomic_bytes'):
                    certificates.acme(n)
            self.assertEqual([c.args[0] for c in bind.call_args_list], expected)

    def test_ipv4_dns_with_wrong_aaaa_is_not_accepted(self):
        answers = [(None, None, None, None, ('8.8.8.8', 0)), (None, None, None, None, ('2001:4860:4860::8888', 0))]
        with patch('nodepilot_pkg.system.socket.getaddrinfo', return_value=answers), self.assertRaises(PilotError):
            dns_matches('example.com', {'ipv4': '8.8.8.8', 'ipv6': ''})
        with self.assertRaises(PilotError):
            http01_bindings(set())

    def test_busy_challenge_port_stops_before_certbot(self):
        n = fixture('anytls')
        n['tls']['mode'] = 'acme'
        with patch('nodepilot_pkg.certificates.dns_matches', return_value={'8.8.8.8'}), \
             patch('nodepilot_pkg.certificates.public_ips'), patch('nodepilot_pkg.certificates.port_free', side_effect=PilotError('busy')), \
             patch('nodepilot_pkg.certificates.run') as command, self.assertRaises(PilotError):
            certificates.acme(n)
        command.assert_not_called()


@unittest.skipUnless(shutil.which('openssl'), 'requires OpenSSL')
class CertificateReuseTests(unittest.TestCase):
    def test_valid_certificate_reused_without_dns_or_acme(self):
        from nodepilot_pkg.common import run
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            run(['openssl', 'req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256',
                 '-nodes', '-days', '90', '-subj', '/CN=example.com', '-addext', 'subjectAltName=DNS:example.com',
                 '-keyout', root/'original.key', '-out', root/'original.pem'])
            n = fixture('anytls')
            n['tls'].update(mode='acme', certificate=str(root/'original.pem'), key=str(root/'original.key'))
            destination = root/'revision'; destination.mkdir()
            with patch('nodepilot_pkg.certificates.acme') as acme:
                certificates.prepare(n, destination, state=root)
            acme.assert_not_called()
            self.assertEqual((destination/'certificate.pem').read_bytes(), (root/'original.pem').read_bytes())
            with patch('nodepilot_pkg.certificates.acme', side_effect=PilotError('renew failed')), self.assertRaises(PilotError):
                certificates.prepare(n, destination, renew=True, state=root)
            certificates.validate_pair(root/'original.pem', root/'original.key', n['sni'])


@unittest.skipUnless(os.name == 'posix', 'Linux transaction')
class UninstallRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.program = self.root/'program'; self.program.mkdir(); (self.program/'.owner').write_text(OWNER)
        self.launcher = self.root/'nodepilot'; self.launcher.write_text(LAUNCHER)
        self.services = FakeServices(self.root/'units')
        self.m = Manager(self.root/'state', FakeCores(), self.services)
        self.m.initialize(); self.m.caddy = MagicMock(); self.m.caddy.active.return_value = False
        self.port = patch('nodepilot_pkg.manager.port_free'); self.port.start()
        self.cmd = patch('nodepilot_pkg.installation.run', return_value=''); self.command=self.cmd.start()
        self.nodes = [self.m.apply(fixture(port=p), probe=False) for p in (443, 8443)]

    def tearDown(self):
        self.cmd.stop(); self.port.stop(); self.tmp.cleanup()

    def test_second_service_failure_recovers_first_and_keeps_all_configs(self):
        original = self.services.remove
        def remove(node):
            if node['id'] == self.nodes[1]['id']:
                raise PilotError('second stop failed')
            original(node)
        with patch.object(self.services, 'remove', side_effect=remove), self.assertRaises(PilotError):
            uninstall(self.m, self.program, self.launcher, self.services.units)
        self.assertEqual(self.m.nodes(), sorted(self.nodes, key=lambda n: n['id']))
        self.assertTrue(all(self.services.active(n) for n in self.nodes))
        self.assertEqual(read_json(self.m.state/'uninstall.json')['phase'], 'rolled_back')
        self.assertTrue(self.launcher.exists())
        # The isolated fixture has no renewal unit: do not operate a real global timer.
        changes=[call.args[0] for call in self.command.call_args_list if call.args[0][1] in {'start','enable','stop','disable'}]
        self.assertFalse(any('nodepilot-renew.timer' in command for command in changes))

    def test_final_cleanup_interruption_is_resumable_and_backup_retained(self):
        with patch('nodepilot_pkg.installation.remove_program_files', side_effect=PilotError('cleanup interrupted')), self.assertRaises(PilotError):
            uninstall(self.m, self.program, self.launcher, self.services.units)
        record = read_json(self.m.state/'uninstall.json')
        self.assertEqual(record['phase'], 'finalizing')
        self.assertTrue(Path(record['backup']).is_file())
        self.assertTrue(recover_uninstall(self.m, self.program, self.launcher, self.services.units))
        self.assertEqual(read_json(self.m.state/'uninstall.json')['phase'], 'complete')
        self.assertFalse(self.launcher.exists())
        self.assertTrue(Path(record['backup']).is_file())

    def test_invalid_later_manifest_prevents_any_program_deletion(self):
        app = self.program/'app'; app.mkdir()
        first = app/('a'*64); first.mkdir(); (first/'nodepilot').write_text('keep')
        (first/'files.json').write_text('{"schema":1,"files":["nodepilot"]}')
        last = app/('b'*64); last.mkdir(); (last/'files.json').write_text('{"schema":1,"files":["../../outside"]}')
        with self.assertRaises(PilotError):
            preflight_program(self.program)
        self.assertEqual((first/'nodepilot').read_text(), 'keep')

    def test_recovery_record_cannot_change_external_paths(self):
        with patch('nodepilot_pkg.installation.remove_program_files', side_effect=PilotError('interrupted')), self.assertRaises(PilotError):
            uninstall(self.m, self.program, self.launcher, self.services.units)
        with self.assertRaises(PilotError):
            recover_uninstall(self.m)


if __name__ == '__main__':
    unittest.main()
