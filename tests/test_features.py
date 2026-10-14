import copy
import io
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from nodepilot_pkg import certificates
from nodepilot_pkg.caddy import configuration
from nodepilot_pkg.common import PilotError
from nodepilot_pkg.connectivity import classify, client_config, fingerprint, latest
from nodepilot_pkg.dns_credentials import credential_path, store_token, verify_zone
from nodepilot_pkg.exporters import mihomo_proxy, mihomo_yaml
from nodepilot_pkg.model import validate
from nodepilot_pkg.services import Services, listening
from test_caddy import self_node
from test_config import fixture


class DnsSecurityTests(unittest.TestCase):
    def test_token_redacted_before_logging_and_terminal_output(self):
        import logging
        from nodepilot_pkg.acme_runner import log_factory,RedactedOutput
        secret='a'*40
        record=log_factory(secret)('provider',logging.INFO,'test',1,'header %s',(secret,),None)
        self.assertNotIn(secret,record.getMessage())
        self.assertIn('[REDACTED]',record.getMessage())
        try:
            raise ValueError(secret)
        except ValueError:
            import sys
            record=log_factory(secret)('provider',logging.ERROR,'test',1,'API failure',(),sys.exc_info())
        self.assertNotIn(secret,logging.Formatter().format(record))
        output=io.StringIO();RedactedOutput(output,secret).write('provider error '+secret)
        self.assertNotIn(secret,output.getvalue())
        from nodepilot_pkg.acme_runner import isolate_provider_environment
        with patch.dict(os.environ,{'CLOUDFLARE_API_KEY':'unused','CF_API_TOKEN':'unused','HOME':'/old'}):
            isolate_provider_environment('/private')
            self.assertEqual(os.environ['HOME'],'/private')
            self.assertNotIn('CLOUDFLARE_API_KEY',os.environ)
            self.assertNotIn('CF_API_TOKEN',os.environ)
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.token='a'*40
        self.profile=store_token(self.root,self.token)
        self.settings={'zone_id':'0'*32,'credential_id':self.profile,'propagation_seconds':60}
    def tearDown(self):
        self.tmp.cleanup()

    @unittest.skipUnless(os.name=='posix','POSIX permissions')
    def test_token_private_and_unsafe_permissions_rejected(self):
        path=credential_path(self.root,self.profile)
        self.assertEqual(path.stat().st_mode & 0o777,0o600)
        self.assertIn(self.token,path.read_text())
        self.assertNotIn(self.token,json.dumps(self.settings))
        path.chmod(0o644)
        with self.assertRaises(PilotError):
            credential_path(self.root,self.profile)
        with self.assertRaises(PilotError):
            credential_path(self.root,'../../outside')

    def zone_response(self,name='example.com'):
        response=io.BytesIO(json.dumps({'success':True,'result':{'name':name,'id':'0'*32}}).encode())
        response.geturl=lambda: 'https://api.cloudflare.com/client/v4/zones/'+'0'*32
        return response

    def test_zone_ownership_and_no_credentials_in_provider_error(self):
        with patch('nodepilot_pkg.dns_credentials.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value=self.zone_response()
            verify_zone(self.root,self.settings,'node.example.com')
            request=opener.return_value.open.call_args.args[0]
            self.assertNotIn(self.token,request.full_url)
        with patch('nodepilot_pkg.dns_credentials.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value=self.zone_response('other.example.com')
            with self.assertRaises(PilotError) as error:
                verify_zone(self.root,self.settings,'node.example.com')
            self.assertNotIn(self.token,str(error.exception))

    def test_dns01_does_not_check_a_aaaa_or_tcp80_and_argv_has_no_token(self):
        n=fixture('hysteria2'); n['tls'].update(mode='acme',challenge='dns-cloudflare',dns=self.settings)
        with patch('nodepilot_pkg.certificates.shutil.which',return_value='certbot'), \
             patch('nodepilot_pkg.dns_credentials.verify_zone',return_value=credential_path(self.root,self.profile)), \
             patch('nodepilot_pkg.certificates.dns_matches') as dns, patch('nodepilot_pkg.certificates.port_free') as port, \
             patch('nodepilot_pkg.certificates.run',return_value='dns-cloudflare') as command:
            certificates.acme(n,state=self.root)
        args=command.call_args.args[0]
        self.assertIn('--dns-cloudflare-credentials',args)
        self.assertIn('--dns-cloudflare-propagation-seconds',args)
        self.assertNotIn(self.token,' '.join(map(str,args)))
        dns.assert_not_called(); port.assert_not_called()

    def test_dns01_caddy_loads_pem_without_plugin_or_http01(self):
        n=self_node(); n['caddy'].update(certificate_mode='dns01',dns=self.settings)
        config=configuration([n],self.root)
        self.assertNotIn('http01',config['apps']['http']['servers'])
        self.assertFalse(config['apps']['tls']['certificates']['automate'])
        self.assertEqual(len(config['apps']['tls']['certificates']['load_files']),1)
        self.assertNotIn(self.token,json.dumps(config))


class HealthTests(unittest.TestCase):
    def test_runtime_differentiates_exit_restarts_and_missing_listener(self):
        n=fixture(); s=Services()
        base='ActiveState=active\nSubState=running\nResult=success\nMainPID=100\nExecMainStatus=0\nNRestarts=0'
        for text,listener,healthy in ((base,True,True),(base,False,False),
                                     (base.replace('NRestarts=0','NRestarts=2'),True,False),
                                     (base.replace('ExecMainStatus=0','ExecMainStatus=1'),True,False),
                                     (base.replace('ActiveState=active','ActiveState=failed'),False,False)):
            with patch.object(s,'assert_owned'), patch('nodepilot_pkg.services.run',return_value=text), \
                 patch('nodepilot_pkg.services.listening',return_value=listener):
                self.assertEqual(s.runtime(n)['healthy'],healthy)

    def test_exit_after_initial_active_is_not_marked_healthy(self):
        s=Services(); healthy={'healthy':True,'MainPID':100}
        failed={'healthy':False,'MainPID':0,'state':'exited','ActiveState':'failed','Result':'exit-code','ExecMainStatus':1,'NRestarts':1}
        with patch.object(s,'assert_owned'), patch('nodepilot_pkg.services.run'), patch('nodepilot_pkg.services.time.sleep'), \
             patch.object(s,'runtime',side_effect=[healthy,failed]), self.assertRaises(PilotError):
            s.restart(fixture())

    @unittest.skipUnless(Path('/proc/net/tcp').exists(),'Linux socket ownership')
    def test_real_listener_must_match_protocol_port_and_main_pid(self):
        for transport,kind in (('tcp',socket.SOCK_STREAM),('udp',socket.SOCK_DGRAM)):
            with socket.socket(socket.AF_INET,kind) as sock:
                sock.bind(('127.0.0.1',0))
                if transport=='tcp': sock.listen(1)
                n=fixture(); n.update(listen='127.0.0.1',port=sock.getsockname()[1],transport=transport)
                self.assertTrue(listening(n,os.getpid()))
                self.assertFalse(listening(n,1))
                n['port']+=1
                self.assertFalse(listening(n,os.getpid()))


class ExportAndProbeTests(unittest.TestCase):
    def test_yaml_credentials_are_quoted_ipv6_is_plain_host_and_no_private_key(self):
        nodes=[fixture(p) for p in ('reality','anytls','hysteria2')]
        for n in nodes:
            n['name']='quote " # : 名称'; n['server']='2001:4860:4860::8888'
            if n['protocol']!='reality': n['password']='test " :\u0023 password '+'x'*20
        nodes[-1]['obfs']={'type':'salamander','password':'x'*32}
        text=mihomo_yaml(nodes)
        entries=[json.loads(line[4:]) for line in text.splitlines() if line.startswith('  - ')]
        self.assertEqual(len(entries),3)
        self.assertTrue(all(p['skip-cert-verify'] is False for p in entries))
        self.assertEqual(entries[0]['reality-opts']['public-key'],nodes[0]['reality']['public_key'])
        self.assertNotIn(nodes[0]['reality']['private_key'],text)
        self.assertEqual(entries[2]['obfs'],'salamander')
        self.assertEqual(entries[1]['password'],nodes[1]['password'])

    def test_probe_uses_public_key_only_and_stale_revision_is_unverified(self):
        n=fixture(); config=client_config(n,18080)
        self.assertNotIn(n['reality']['private_key'],json.dumps(config))
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/'probes').mkdir()
            (root/'probes'/(n['id']+'.json')).write_text(json.dumps({'fingerprint':fingerprint(n),'status':'local passed'}))
            manager=MagicMock(state=root)
            self.assertEqual(latest(manager,n)['status'],'local passed')
            n['port']=8443
            self.assertIn('未测试',latest(manager,n)['status'])
        self.assertEqual(classify(TimeoutError(),''),'timeout')
        self.assertEqual(classify(PilotError('fail'),'failed to verify certificate'),'authentication_or_reality_handshake')

    def test_original_v03_metadata_remains_valid(self):
        for protocol in ('reality','anytls','hysteria2'):
            validate(fixture(protocol))
        validate(self_node())


if __name__=='__main__':
    unittest.main()
