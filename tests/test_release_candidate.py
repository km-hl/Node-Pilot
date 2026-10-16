"""RC2 fault injection. These tests never prove real ACME issuance."""
import copy
import hashlib
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch

from nodepilot_pkg import certificates, dns_credentials as dns
from nodepilot_pkg.common import PilotError, atomic_json, read_json
from nodepilot_pkg.manager import LEGACY_OWNER, Manager, OWNER
from test_config import fixture
from test_manager import FakeCores, FakeServices


def candidate_at(path):
    """Synthetic importable layout; execution is mocked in transaction unit tests."""
    from nodepilot_pkg.deploy import REQUIRED
    path.mkdir(parents=True, exist_ok=True)
    hashes={}
    for name in sorted(REQUIRED):
        target=path/name;target.parent.mkdir(parents=True,exist_ok=True)
        data=b'candidate' if name=='nodepilot' else b'# synthetic test module\n'
        target.write_bytes(data);hashes[name]=hashlib.sha256(data).hexdigest()
    return hashes


class TimerSystemd:
    """Stateful simulator with real temporary symlinks, NOT real systemd."""
    def __init__(self, base, units, enabled='disabled', active='inactive'):
        self.base, self.units = base, units
        self.active = active
        self.calls = []
        self.stamps = {}
        self.links = {state: directory / 'timers.target.wants/nodepilot-renew.timer'
                      for state, directory in (('enabled', units), ('enabled-runtime', base/'runtime'))}
        if enabled != 'disabled':
            self.enable(enabled)

    def enable(self, state):
        path = self.links[state]; path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(self.units / 'nodepilot-renew.timer')

    def state(self):
        enabled = next((s for s, p in self.links.items() if p.is_symlink()), 'disabled')
        return {'unit_file_state': enabled, 'active_state': self.active}

    def __call__(self, args, **kwargs):
        args = list(map(str, args)); self.calls.append(args)
        if args[0] != 'systemctl':
            return ''
        if args[1] == 'show':
            prop = args[4]
            if prop in {'ActiveEnterTimestampMonotonic','InactiveEnterTimestampMonotonic'}:
                return str(self.stamps.get((args[2],prop),''))
            if prop == 'UnitFileState': return self.state()['unit_file_state']
            if prop == 'ActiveState': return self.active
            if prop == 'FragmentPath':
                path = self.units / args[2]
                return str(path) if path.exists() else ''
            return ''
        if args[1] in {'enable', 'disable', 'start', 'stop'}:
            assert args[-1] == 'nodepilot-renew.timer', 'external service touched'
        if args[1] == 'disable':
            path = self.links['enabled-runtime' if '--runtime' in args else 'enabled']
            path.unlink(missing_ok=True)
            if path.parent.is_dir() and not any(path.parent.iterdir()):path.parent.rmdir()
        elif args[1] == 'enable':
            self.enable('enabled-runtime' if '--runtime' in args else 'enabled')
        elif args[1] in {'start', 'stop'}:
            self.active = 'active' if args[1] == 'start' else 'inactive'
        return ''


class HttpRC2Tests(unittest.TestCase):
    @unittest.skipUnless(Path('/proc/net/tcp').exists(),'Linux sockets')
    def test_owned_ipv4_wildcard_cannot_prove_ipv6_listener(self):
        from nodepilot_pkg.services import listening
        with socket.socket() as sock:
            sock.bind(('0.0.0.0',0));sock.listen(1)
            self.assertFalse(listening({'listen':'::','port':sock.getsockname()[1],'transport':'tcp'},os.getpid(),strict_family=True))
    def test_certbot_dual_stack_rejects_before_issuance_and_checks_both(self):
        n=fixture('anytls');n['tls']['mode']='acme'
        with patch('nodepilot_pkg.certificates.dns_matches',return_value={'8.8.8.8','2001:4860:4860::8888'}), \
             patch('nodepilot_pkg.certificates.public_ips'),patch('nodepilot_pkg.certificates.port_free') as bind, \
             patch('nodepilot_pkg.certificates.run') as command, self.assertRaisesRegex(PilotError,'DNS-01'):
            certificates.acme(n)
        self.assertEqual([c.args[0] for c in bind.call_args_list],['0.0.0.0','::'])
        self.assertTrue(all(c.kwargs['v6only'] for c in bind.call_args_list))
        command.assert_not_called()

    def test_caddy_explicit_families_never_collapse_to_ipv6_wildcard(self):
        from nodepilot_pkg.caddy import configuration
        from test_caddy import self_node
        n=self_node();n['caddy'].update(certificate_mode='acme',http01_listen=['tcp4/0.0.0.0:80','tcp6/[::]:80'])
        self.assertEqual(configuration([n],'/test')['apps']['http']['servers']['http01']['listen'],n['caddy']['http01_listen'])

    @unittest.skipUnless(os.name=='posix','Linux family binds')
    def test_v6only_socket_does_not_hide_ipv4_busy_port(self):
        from nodepilot_pkg.system import port_free
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as ipv4:
            ipv4.bind(('0.0.0.0',0));ipv4.listen(1);port=ipv4.getsockname()[1]
            with self.assertRaises(PilotError):port_free('0.0.0.0',port,'tcp',v6only=True)
            try:
                port_free('::',port,'tcp',v6only=True)
            except PilotError:
                self.skipTest('IPv6 unavailable or busy in this environment')
            with self.assertRaises(PilotError):port_free('::',port,'tcp',v6only=False)

    def test_disabled_ipv6_fails_without_system_changes(self):
        from nodepilot_pkg.system import port_free
        with patch('nodepilot_pkg.system.socket.socket',side_effect=OSError('disabled')),self.assertRaises(PilotError):
            port_free('::',80,'tcp',v6only=True)


@unittest.skipUnless(os.name=='posix','Linux ownership and durable transactions')
class CredentialLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.manager=Manager(self.root/'state',FakeCores(),FakeServices(self.root/'units'))
        self.manager.initialize();self.state=self.manager.state
        self.token='test_only_'+'a'*30
        self.settings={'zone_id':'0'*32,'propagation_seconds':60}

    def tearDown(self):self.tmp.cleanup()

    def staged(self):
        with patch('nodepilot_pkg.dns_credentials.verify_zone'):
            return dns.stage_token(self.state,self.token,self.settings,'example.com')

    def test_invalid_zone_and_token_never_write_credentials(self):
        for settings,token in (({**self.settings,'zone_id':'bad'},self.token),(self.settings,'bad')):
            with self.assertRaises(PilotError):dns.stage_token(self.state,token,settings,'example.com')
        self.assertFalse(list(self.state.rglob('*.ini')))

    def test_zone_failure_and_interrupt_clean_temp_token(self):
        for error in (PilotError('API failed'),KeyboardInterrupt()):
            with patch('nodepilot_pkg.dns_credentials.verify_zone',side_effect=error),self.assertRaises(type(error)):
                dns.stage_token(self.state,self.token,self.settings,'example.com')
            self.assertFalse(list(self.state.rglob('*.ini')))

    def test_cancel_only_removes_owned_uncommitted_profile(self):
        staged=self.staged();existing=dns.store_token(self.state,self.token)
        dns.recover_pending(self.state,owned=[staged['credential_id']])
        self.assertFalse((self.state/'credentials/pending'/(staged['credential_id']+'.ini')).exists())
        self.assertTrue(dns.credential_path(self.state,existing,pending=False).exists())

    def test_dead_process_recovery_and_live_other_menu_preserved(self):
        staged=self.staged();path=dns.credential_path(self.state,staged['credential_id'])
        dns.recover_pending(self.state)
        self.assertTrue(path.exists())
        with patch('nodepilot_pkg.dns_credentials.owner_alive',return_value=False):dns.recover_pending(self.state)
        self.assertFalse(path.exists())

    def test_validation_preflight_and_issuance_failure_clean_uncommitted(self):
        for mode in ('validation','preflight','issuance','reload'):
            settings=self.staged();n=fixture('anytls');n['tls'].update(mode='acme',challenge='dns-cloudflare',dns=settings)
            if mode=='validation':n['port']=0
            if mode=='reload':self.manager.services.fail_next=True
            def prepare(node, revision, *args):
                if mode=='issuance':raise PilotError('issuance failed')
                node['tls'].update(certificate=str(revision/'certificate.pem'),key=str(revision/'key.pem'))
            with patch('nodepilot_pkg.manager.port_free',side_effect=PilotError('busy') if mode=='preflight' else None), \
                 patch('nodepilot_pkg.certificates.prepare',side_effect=prepare),self.assertRaises(PilotError):
                self.manager.apply(n,probe=False)
            self.assertFalse(list((self.state/'credentials/pending').glob('*.ini')))
            self.assertFalse(list((self.state/'credentials').glob('*.ini')))

    def test_success_registers_only_after_service_is_healthy_and_reuses_shared(self):
        settings=self.staged();profile=settings['credential_id'];n=fixture('anytls')
        n['tls'].update(mode='acme',challenge='dns-cloudflare',dns=settings)
        def prepare(node,revision,*args):
            node['tls'].update(certificate=str(revision/'certificate.pem'),key=str(revision/'key.pem'))
        original=self.manager.services.restart
        def restart(node):
            self.assertFalse((self.state/'credentials'/(profile+'.ini')).exists())
            original(node)
        with patch('nodepilot_pkg.manager.port_free'),patch('nodepilot_pkg.certificates.prepare',side_effect=prepare), \
             patch.object(self.manager.services,'restart',side_effect=restart):
            saved=self.manager.apply(n,probe=False)
        self.assertTrue(dns.credential_path(self.state,profile,pending=False).exists())
        self.assertFalse(dns.audit(self.state)[0]['orphan'])
        with self.assertRaises(PilotError):dns.cleanup_orphans(self.state,[profile],confirmed=True)
        self.assertNotIn(self.token,json.dumps(saved))

    def test_old_revision_backup_adoption_and_transaction_pin_shared_token(self):
        profile=dns.store_token(self.state,self.token)
        for directory in ('backups','adoptions','nodes/test/revisions/r-test'):
            path=self.state/directory;path.mkdir(parents=True,exist_ok=True)
            entry=path/'record.json';atomic_json(entry,{'dns':{'credential_id':profile}})
            with self.assertRaises(PilotError):dns.cleanup_orphans(self.state,[profile],confirmed=True)
            entry.unlink()
        atomic_json(self.state/'uninstall.json',{'dns':{'credential_id':profile}})
        with self.assertRaises(PilotError):dns.cleanup_orphans(self.state,[profile],confirmed=True)

    def test_orphan_audit_readonly_cleanup_confirmed_and_corruption_refuses(self):
        profile=dns.store_token(self.state,self.token);path=dns.credential_path(self.state,profile)
        self.assertTrue(dns.audit(self.state)[0]['orphan']);self.assertTrue(path.exists())
        with self.assertRaises(PilotError):dns.cleanup_orphans(self.state,[profile])
        broken=self.state/'backups/broken.json';broken.write_text('{')
        with self.assertRaises(PilotError):dns.cleanup_orphans(self.state,[profile],confirmed=True)
        self.assertTrue(path.exists());broken.unlink()
        atomic_json(broken,{'schema':99,'future_credential_reference':profile})
        with self.assertRaises(PilotError):dns.cleanup_orphans(self.state,[profile],confirmed=True)
        broken.unlink()
        dns.cleanup_orphans(self.state,[profile],confirmed=True);self.assertFalse(path.exists())

    def test_current_transaction_blocks_pending_cleanup_even_if_owner_dead(self):
        settings=self.staged();node=self.state/'nodes/test';node.mkdir()
        atomic_json(node/'pending.json',{'dns':settings})
        with patch('nodepilot_pkg.dns_credentials.owner_alive',return_value=False),self.assertRaises(PilotError):
            dns.recover_pending(self.state)
        self.assertTrue(dns.credential_path(self.state,settings['credential_id']).exists())

    def test_legacy_owner_migrates_and_future_owner_is_refused(self):
        (self.state/'.owner').write_text(LEGACY_OWNER);self.manager.initialize()
        self.assertEqual((self.state/'.owner').read_text(),OWNER)
        (self.state/'.owner').write_text('NodePilot schema=99\n')
        with self.assertRaises(PilotError):self.manager.initialize()


class TargetRC2Tests(unittest.TestCase):
    def test_server_and_peer_output_only_accept_exact_classical_group(self):
        from nodepilot_pkg.targets import tls_handshake
        for label in ('Server','Peer'):
            for group in ('X25519','X25519MLKEM768','X25519-unknown','unknown'):
                output=f'TLSv1.3\n{label} Temp Key: {group}, 253 bits\nALPN protocol: h2\nVerify return code: 0 (ok)'.encode()
                with patch('nodepilot_pkg.targets.run_openssl',return_value=CompletedProcess([],0,output,b'')):
                    if group=='X25519':self.assertEqual(tls_handshake('8.8.8.8',443,'example.com')['group'],'X25519')
                    else:
                        with self.assertRaisesRegex(PilotError,'未验证'):tls_handshake('8.8.8.8',443,'example.com')

    def test_manual_fixed_ip_failure_and_dns_drift_never_change_node(self):
        from nodepilot_pkg.targets import recheck_target,target_status
        n=fixture();n['reality']['target']='8.8.8.8:443';original=copy.deepcopy(n)
        with tempfile.TemporaryDirectory() as d, patch('nodepilot_pkg.targets.resolve',return_value=(['8.8.4.4'],set())), \
             patch('nodepilot_pkg.targets.cloudflare_networks',return_value=[]), \
             patch('nodepilot_pkg.targets.inspect_target',side_effect=PilotError('TLS failed')):
            result=recheck_target(Path(d),n)
            self.assertTrue(result['dns_drift']);self.assertIn('检测失败',target_status(Path(d),n))
            self.assertEqual(n,original)
            n['reality']['target']='8.8.4.4:443'
            self.assertIn('尚未检测',target_status(Path(d),n))


class DnsProcessCleanupTests(unittest.TestCase):
    def test_timeout_requests_plugin_cleanup_then_escalates_without_output(self):
        import signal
        import subprocess
        from nodepilot_pkg.common import run_with_cleanup
        for delayed in (False,True):
            process=MagicMock()
            process.wait.side_effect=[subprocess.TimeoutExpired('certbot',1),0] if not delayed else [
                subprocess.TimeoutExpired('certbot',1),subprocess.TimeoutExpired('certbot',20),0]
            with patch('nodepilot_pkg.common.subprocess.Popen',return_value=process),self.assertRaisesRegex(PilotError,'TXT'):
                run_with_cleanup(['python3','redacting_runner'],1)
            process.send_signal.assert_called_once_with(signal.SIGTERM)
            self.assertEqual(process.kill.called,delayed)

    def test_interrupt_requests_cleanup_and_preserves_interrupt(self):
        from nodepilot_pkg.common import run_with_cleanup
        process=MagicMock();process.wait.side_effect=[KeyboardInterrupt(),0]
        with patch('nodepilot_pkg.common.subprocess.Popen',return_value=process),self.assertRaises(KeyboardInterrupt):
            run_with_cleanup(['python3','redacting_runner'],10)
        process.send_signal.assert_called_once()


@unittest.skipUnless(os.name=='posix' and __import__('shutil').which('openssl'),'Linux/OpenSSL certificates')
class CertificateRecoveryRC2Tests(unittest.TestCase):
    def test_renewal_install_reload_failure_and_restore_rebind(self):
        from nodepilot_pkg.common import run
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);m=Manager(root/'state',FakeCores(),FakeServices(root/'units'));m.initialize()
            def pair(name,days):
                key=root/(name+'.key');cert=root/(name+'.pem')
                run(['openssl','req','-x509','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256','-nodes',
                    '-days',str(days),'-subj','/CN=example.com','-addext','subjectAltName=DNS:example.com','-keyout',key,'-out',cert])
                return cert,key
            cert,key=pair('original',1);newcert,newkey=pair('new',90)
            n=fixture('anytls');n['tls'].update(mode='import',certificate=str(cert),key=str(key))
            with patch('nodepilot_pkg.manager.port_free'):
                old=m.apply(n,probe=False);oldrevision=m.revision(n['id'])
                profile=dns.store_token(m.state,'test_only_'+'z'*30)
                changed=copy.deepcopy(old);changed['tls'].update(mode='acme',challenge='dns-cloudflare',
                    dns={'credential_id':profile,'zone_id':'0'*32,'propagation_seconds':60})
                self.assertTrue(certificates.due(changed))
                self.assertEqual(certificates.information(old,m.state)['status'],'临期')
                m.services.fail_next=True
                with patch('nodepilot_pkg.certificates.acme',return_value=(newcert,newkey)),self.assertRaises(PilotError):
                    m.apply(changed,probe=False,renew=True)
                self.assertEqual(m.get(n['id']),old);self.assertEqual(m.revision(n['id']),oldrevision)
                self.assertEqual(Path(old['tls']['certificate']).read_bytes(),cert.read_bytes())
                self.assertTrue(m.services.active(old))
                self.assertIn('失败',read_json(m.state/'certificate-status'/(n['id']+'.json'))['status'])
                with patch('nodepilot_pkg.certificates.acme',return_value=(newcert,newkey)):
                    installed=m.apply(changed,probe=False,renew=True)
                self.assertFalse(certificates.due(installed));self.assertTrue(m.services.active(installed))
                self.assertEqual(Path(installed['tls']['certificate']).read_bytes(),newcert.read_bytes())
                expired=root/'expired.pem'
                run(['openssl','x509','-in',newcert,'-signkey',newkey,'-days','0','-out',expired])
                expired_node=copy.deepcopy(installed);expired_node['tls'].update(certificate=str(expired),key=str(newkey))
                self.assertEqual(certificates.information(expired_node,m.state)['status'],'已过期')
                self.assertTrue(certificates.due(expired_node))
                with patch('nodepilot_pkg.certificates.acme',return_value=(newcert,newkey)):
                    installed=m.apply(expired_node,probe=False)
                self.assertFalse(certificates.due(installed))
                backup=m.backup()
                other=Manager(root/'other',FakeCores(),FakeServices(root/'other-units'));other.initialize()
                with patch('nodepilot_pkg.manager.probe_reality'):
                    restored=other.restore(backup)
                recovered=other.get(restored[0]);self.assertEqual(recovered['tls']['mode'],'import')
                self.assertNotIn('dns',recovered['tls']);self.assertNotIn('challenge',recovered['tls'])
                self.assertFalse(certificates.due(recovered))
            with self.assertRaises(PilotError):certificates.validate_pair(cert,newkey,'example.com')
            with self.assertRaises(PilotError):certificates.validate_pair(cert,key,'other.example.com')
            cert.write_text('broken')
            with self.assertRaises(PilotError):certificates.validate_pair(cert,key,'example.com')


class ProgramRecoveryTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='posix','Linux program transaction')
    def test_failed_upgrade_restores_legacy_owners_and_runtime_timer_enablement(self):
        from nodepilot_pkg.deploy import deploy
        from nodepilot_pkg.installation import LAUNCHER,RENEW_SERVICE,LEGACY_RENEW_TIMER
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);root=base/'program';app=root/'app';old=app/('a'*64);old.mkdir(parents=True)
            (old/'nodepilot').write_text('old');atomic_json(old/'files.json',{'schema':1,'files':['nodepilot']})
            (app/'current').symlink_to(old.name,target_is_directory=True);(root/'.owner').write_text(LEGACY_OWNER)
            units=base/'units';units.mkdir();launcher=base/'nodepilot';launcher.write_text(LAUNCHER)
            for name,text in (('nodepilot-renew.service',RENEW_SERVICE),('nodepilot-renew.timer',LEGACY_RENEW_TIMER)):
                (units/name).write_text(text)
            service_units=base/'service-units';m=Manager(base/'state',FakeCores(),FakeServices(service_units),migrate_owner=False)
            m.initialize();(m.state/'.owner').write_text(LEGACY_OWNER)
            source=base/'payload';candidate_at(source)
            systemd=TimerSystemd(base,units,'enabled-runtime','active')
            calls=systemd.calls;failed=False
            def command(args,**kwargs):
                nonlocal failed
                args=list(map(str,args))
                if args[0]=='python3' and ('b'*64) in args[1] and (app/'current').resolve().name=='b'*64 and not failed:
                    # Exercise convergence from a partially changed enablement.
                    for path in systemd.links.values():path.unlink(missing_ok=True)
                    systemd.enable('enabled')
                    failed=True;raise PilotError('injected installed self-check failure')
                return systemd(args,**kwargs)
            with patch('nodepilot_pkg.deploy.supported'),patch('nodepilot_pkg.deploy.run',side_effect=command), \
                 patch('nodepilot_pkg.installation.RUNTIME_UNITS',systemd.links['enabled-runtime'].parent.parent),self.assertRaises(PilotError):
                deploy(source,'b'*64,root=root,launcher=launcher,units=units,manager=m)
            self.assertEqual((app/'current').resolve(),old)
            self.assertEqual((root/'.owner').read_text(),LEGACY_OWNER)
            self.assertEqual((m.state/'.owner').read_text(),LEGACY_OWNER)
            self.assertEqual((units/'nodepilot-renew.timer').read_text(),LEGACY_RENEW_TIMER)
            self.assertIn(['systemctl','enable','--runtime','nodepilot-renew.timer'],calls)
            self.assertEqual(systemd.state(),{'unit_file_state':'enabled-runtime','active_state':'active'})
            self.assertFalse((root/'program-pending.json').exists())

    def test_durable_upgrade_finishes_after_each_interruption_and_is_idempotent(self):
        from nodepilot_pkg.deploy import recover_program
        from nodepilot_pkg.installation import LAUNCHER,RENEW_TIMER
        for crash in ('switch','launcher','timer'):
            with tempfile.TemporaryDirectory() as d:
                base=Path(d);root=base/'program';app=root/'app';app.mkdir(parents=True)
                units=base/'units';units.mkdir();launcher=base/'nodepilot'
                (root/'.owner').write_text(LEGACY_OWNER)
                old=app/('a'*64);old.mkdir();(old/'nodepilot').write_text('old')
                (old/'files.json').write_text('{"schema":1,"files":["nodepilot"]}')
                new=app/('b'*64);hashes=candidate_at(new)
                atomic_json(new/'files.json',{'schema':1,'recovery_schema':2,'files':list(hashes),'hashes':hashes})
                current=app/'current';current.symlink_to(old.name,target_is_directory=True)
                record={'schema':2,'phase':'installing','root':str(root),'launcher':str(launcher),'units':str(units),
                        'destination':new.name,'old':old.name,'repository':''}
                atomic_json(root/'program-pending.json',record)
                from nodepilot_pkg import deploy
                original=deploy.atomic_bytes
                def write(path,data,**kwargs):
                    if (crash=='launcher' and path==launcher) or (crash=='timer' and path.name.endswith('.timer')):
                        raise SystemExit('simulated power loss')
                    return original(path,data,**kwargs)
                with patch('nodepilot_pkg.deploy.run',side_effect=TimerSystemd(base,units)):
                    if crash=='switch':
                        with patch('nodepilot_pkg.deploy.os.replace',side_effect=SystemExit('power loss')),self.assertRaises(SystemExit):
                            recover_program(root,launcher,units)
                    else:
                        with patch('nodepilot_pkg.deploy.atomic_bytes',side_effect=write),self.assertRaises(SystemExit):
                            recover_program(root,launcher,units)
                    self.assertTrue(recover_program(root,launcher,units))
                    self.assertFalse(recover_program(root,launcher,units))
                self.assertEqual(current.resolve(),new)
                self.assertEqual(launcher.read_text(),LAUNCHER)
                self.assertIn('OnBootSec=2min',RENEW_TIMER)
                self.assertEqual((root/'.owner').read_text(),OWNER)
                self.assertTrue(old.is_dir())

    def test_tampered_candidate_is_never_executed(self):
        from nodepilot_pkg.deploy import recover_program
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);root=base/'program';new=root/'app'/('b'*64);new.mkdir(parents=True)
            units=base/'units';units.mkdir();launcher=base/'nodepilot';(root/'.owner').write_text(OWNER)
            (new/'nodepilot').write_text('tampered')
            atomic_json(new/'files.json',{'schema':1,'recovery_schema':2,'files':['nodepilot'],'hashes':{'nodepilot':'0'*64}})
            atomic_json(root/'program-pending.json',{'schema':2,'phase':'installing','root':str(root),'launcher':str(launcher),
                'units':str(units),'destination':new.name,'old':None,'repository':''})
            with patch('nodepilot_pkg.deploy.run',return_value='') as command,self.assertRaisesRegex(PilotError,'校验失败'):
                recover_program(root,launcher,units)
            self.assertFalse(any(c.args[0][0]=='python3' for c in command.call_args_list))
            self.assertFalse(launcher.exists());self.assertTrue((root/'program-pending.json').exists())

    def test_unknown_schema_and_paths_refuse_before_mutating(self):
        from nodepilot_pkg.deploy import recover_program
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'.owner').write_text(OWNER)
            atomic_json(root/'program-pending.json',{'schema':99})
            with patch('nodepilot_pkg.deploy.run',return_value=''),self.assertRaises(PilotError):
                recover_program(root,root/'nodepilot',root)
            self.assertTrue((root/'program-pending.json').exists())


@unittest.skipUnless(os.name == 'posix', 'Linux upgrade transactions')
class ProgramRC3Tests(unittest.TestCase):
    states = [(e, a) for e in ('enabled', 'enabled-runtime', 'disabled') for a in ('active', 'inactive')]

    def environment(self, base, enabled='disabled', active='inactive', first=False):
        from nodepilot_pkg.installation import LAUNCHER, RENEW_SERVICE, LEGACY_RENEW_TIMER
        root=base/'program'; app=root/'app'; app.mkdir(parents=True)
        (root/'.owner').write_text(LEGACY_OWNER)
        units=base/'units'; units.mkdir(); launcher=base/'nodepilot'
        old=app/('a'*64)
        if not first:
            old.mkdir(); (old/'nodepilot').write_text('old')
            atomic_json(old/'files.json',{'schema':1,'files':['nodepilot']})
            (app/'current').symlink_to(old.name,target_is_directory=True)
            launcher.write_text(LAUNCHER)
            (units/'nodepilot-renew.service').write_text(RENEW_SERVICE)
            (units/'nodepilot-renew.timer').write_text(LEGACY_RENEW_TIMER)
        m=Manager(base/'state',FakeCores(),FakeServices(base/'service-units'),migrate_owner=False)
        m.initialize(); (m.state/'.owner').write_text(LEGACY_OWNER)
        sentinel=m.state/'user-config';sentinel.write_text('preserved')
        source=base/'source';candidate_at(source)
        sd=TimerSystemd(base,units,enabled,active)
        return root,launcher,units,m,source,sd

    def install(self, env):
        from nodepilot_pkg.deploy import deploy
        root,launcher,units,m,source,sd=env
        with patch('nodepilot_pkg.deploy.supported'),patch('nodepilot_pkg.deploy.run',side_effect=sd), \
             patch('nodepilot_pkg.installation.RUNTIME_UNITS',sd.links['enabled-runtime'].parent.parent):
            deploy(source,'b'*64,root=root,launcher=launcher,units=units,manager=m)

    def recover(self, env):
        from nodepilot_pkg.deploy import recover_program
        root,launcher,units,m,source,sd=env
        with m.locked(migrate_owner=False),patch('nodepilot_pkg.deploy.run',side_effect=sd), \
             patch('nodepilot_pkg.installation.RUNTIME_UNITS',sd.links['enabled-runtime'].parent.parent):
            result=recover_program(root,launcher,units,m)
            self.assertFalse(recover_program(root,launcher,units,m))
        return result

    def assert_original(self,env,first=False):
        from nodepilot_pkg.installation import LEGACY_RENEW_TIMER, LAUNCHER, RENEW_SERVICE
        root,launcher,units,m,source,sd=env
        self.assertEqual((m.state/'user-config').read_text(),'preserved')
        self.assertEqual((root/'.owner').read_text(),LEGACY_OWNER)
        self.assertEqual((m.state/'.owner').read_text(),LEGACY_OWNER)
        self.assertFalse((root/'program-pending.json').exists())
        if first:
            self.assertFalse((root/'app/current').is_symlink())
            self.assertFalse(launcher.exists());self.assertFalse(list(units.iterdir()))
            self.assertFalse(any(p.is_symlink() for p in sd.links.values()))
        else:
            self.assertEqual((root/'app/current').resolve().name,'a'*64)
            self.assertEqual(launcher.read_text(),LAUNCHER)
            self.assertEqual((units/'nodepilot-renew.timer').read_text(),LEGACY_RENEW_TIMER)
            self.assertEqual((units/'nodepilot-renew.service').read_text(),RENEW_SERVICE)

    def test_success_all_six_timer_states_and_first_install(self):
        for enabled,active in self.states+[(None,None)]:
            with self.subTest(enabled=enabled,active=active),tempfile.TemporaryDirectory() as d:
                first=enabled is None
                env=self.environment(Path(d),enabled or 'disabled',active or 'inactive',first)
                self.install(env)
                self.assertEqual(env[-1].state(),{'unit_file_state':'enabled' if first else enabled,
                                                 'active_state':'active' if first else active})
                self.assertEqual((env[0]/'app/current').resolve().name,'b'*64)
                self.assertEqual((env[3].state/'user-config').read_text(),'preserved')
                self.assertFalse(self.recover(env))

    def test_failed_self_check_restores_all_states_files_owners_and_symlinks(self):
        for enabled,active in self.states+[(None,None)]:
            with self.subTest(enabled=enabled,active=active),tempfile.TemporaryDirectory() as d:
                first=enabled is None
                env=self.environment(Path(d),enabled or 'disabled',active or 'inactive',first)
                root,launcher,units,m,source,sd=env
                original=sd.__call__
                def failure(args,**kwargs):
                    if str(args[0])=='python3' and (root/'app/current').is_symlink() and (root/'app/current').resolve().name=='b'*64:
                        raise PilotError('installed self-check failed')
                    return original(args,**kwargs)
                with patch.object(TimerSystemd,'__call__',side_effect=failure),self.assertRaises(PilotError):
                    self.install(env)
                self.assertEqual(sd.state(),{'unit_file_state':enabled or 'disabled','active_state':active or 'inactive'})
                self.assert_original(env,first)

    def test_interrupt_every_durable_step_all_six_states_and_first_install(self):
        from nodepilot_pkg import deploy
        for enabled,active in self.states+[(None,None)]:
            for step in ('intent','candidate','current','owners','launcher','units','reloaded','timer','checked'):
                with self.subTest(enabled=enabled,active=active,step=step),tempfile.TemporaryDirectory() as d:
                    first=enabled is None
                    env=self.environment(Path(d),enabled or 'disabled',active or 'inactive',first)
                    root,launcher,units,m,source,sd=env
                    original=deploy.atomic_json
                    def crash(path,record):
                        original(path,record)
                        if path.name=='program-pending.json' and record.get('step')==step:
                            raise SystemExit('simulated process death')
                    with patch('nodepilot_pkg.deploy.atomic_json',side_effect=crash),self.assertRaises(SystemExit):
                        self.install(env)
                    record=read_json(root/'program-pending.json')
                    self.assertEqual(record['previous']['timer'],{'unit_file_state':enabled or 'disabled','active_state':active or 'inactive'})
                    self.assertIn('nodepilot',record['hashes'])
                    self.assertIn('old_version',record);self.assertIn('target_version',record)
                    self.assertTrue(self.recover(env))
                    if step=='intent':
                        self.assert_original(env,first)
                    else:
                        self.assertEqual((root/'app/current').resolve().name,'b'*64)
                        self.assertEqual(sd.state(),{'unit_file_state':'enabled' if first else enabled,'active_state':'active' if first else active})
                    self.assertEqual((m.state/'user-config').read_text(),'preserved')

    def test_interrupt_during_copy_and_after_destination_write(self):
        from nodepilot_pkg import deploy
        for boundary in ('copy','renamed'):
            with self.subTest(boundary=boundary),tempfile.TemporaryDirectory() as d:
                env=self.environment(Path(d));root=env[0]
                if boundary=='copy':
                    target='nodepilot_pkg.deploy.shutil.copytree'
                    def crash(*args,**kwargs):raise SystemExit('copy interrupted')
                else:
                    target='nodepilot_pkg.deploy.os.replace';original=deploy.os.replace
                    def crash(src,dst):
                        original(src,dst)
                        if Path(dst).name=='b'*64:raise SystemExit('rename interrupted')
                with patch(target,side_effect=crash),self.assertRaises(SystemExit):self.install(env)
                self.assertTrue(self.recover(env));self.assert_original(env)

    def test_rollback_failure_keeps_journal_and_retry_finishes_rollback(self):
        for crash in (PilotError('systemd unavailable'),SystemExit('power loss during rollback')):
            with self.subTest(crash=type(crash)),tempfile.TemporaryDirectory() as d:
                env=self.environment(Path(d),'disabled','active');root,launcher,units,m,source,sd=env
                original=sd.__call__
                def failure(args,**kwargs):
                    if str(args[0])=='python3' and (root/'app/current').resolve().name=='b'*64:
                        raise PilotError('self-check failed')
                    journal=root/'program-pending.json'
                    if list(args)==['systemctl','daemon-reload'] and journal.exists() and read_json(journal)['phase']=='rolling_back':
                        raise crash
                    return original(args,**kwargs)
                with patch.object(TimerSystemd,'__call__',side_effect=failure),self.assertRaises(type(crash)):
                    self.install(env)
                self.assertEqual(read_json(root/'program-pending.json')['phase'],'rolling_back')
                self.assertTrue(self.recover(env));self.assert_original(env)
                self.assertEqual(sd.state(),{'unit_file_state':'disabled','active_state':'active'})

    def test_interrupt_inside_timer_enable_and_start(self):
        for operation in ('enable','start'):
            with self.subTest(operation=operation),tempfile.TemporaryDirectory() as d:
                env=self.environment(Path(d),first=True);sd=env[-1];original=sd.__call__
                def crash(args,**kwargs):
                    result=original(args,**kwargs)
                    if list(args)[:2]==['systemctl',operation]:raise SystemExit('timer state interrupted')
                    return result
                with patch.object(TimerSystemd,'__call__',side_effect=crash),self.assertRaises(SystemExit):self.install(env)
                self.assertTrue((env[0]/'program-pending.json').exists())
                self.assertTrue(self.recover(env))
                self.assertEqual(sd.state(),{'unit_file_state':'enabled','active_state':'active'})

    def test_lock_contention_cannot_migrate_legacy_owner(self):
        import fcntl
        with tempfile.TemporaryDirectory() as d:
            env=self.environment(Path(d));m=env[3]
            competitor=Manager(m.state,FakeCores(),FakeServices(Path(d)/'other-units'))
            with (m.state/'.lock').open('a') as held:
                fcntl.flock(held,fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaises(PilotError):
                    with competitor.locked():pass
                self.assertEqual((m.state/'.owner').read_text(),LEGACY_OWNER)
                # No marker write at all is allowed before lock acquisition.
                with patch('nodepilot_pkg.manager.atomic_bytes',side_effect=AssertionError('write before lock')):
                    with self.assertRaises(PilotError):
                        with competitor.locked():pass

    def test_existing_candidate_must_match_trusted_source_before_execution(self):
        with tempfile.TemporaryDirectory() as d:
            env=self.environment(Path(d));root=env[0];sd=env[-1]
            new=root/'app'/('b'*64);new.mkdir();(new/'nodepilot').write_text('modified')
            atomic_json(new/'files.json',{'schema':1,'recovery_schema':2,'files':['nodepilot'],
                                        'hashes':{'nodepilot':hashlib.sha256(b'modified').hexdigest()}})
            with self.assertRaises(PilotError):self.install(env)
            self.assertFalse(any(c[0]=='python3' for c in sd.calls));self.assert_original(env)

    def test_masked_unknown_transient_dropin_and_external_unit_refuse(self):
        for mode in ('masked','unknown','activating','dropin','external'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                env=self.environment(Path(d));root,launcher,units,m,source,sd=env
                original=sd.__call__
                def unsafe(args,**kwargs):
                    if args[:2]==['systemctl','show']:
                        if mode in ('masked','unknown') and 'UnitFileState' in args:return mode
                        if mode=='activating' and 'ActiveState' in args:return mode
                        if mode=='dropin' and 'DropInPaths' in args:return '/etc/foreign.conf'
                        if mode=='external' and 'FragmentPath' in args:return '/usr/lib/systemd/system/foreign.timer'
                    return original(args,**kwargs)
                with patch.object(TimerSystemd,'__call__',side_effect=unsafe),self.assertRaises(PilotError):self.install(env)
                self.assert_original(env)

    def test_legacy_rc2_log_preserves_observed_disabled_policy_and_evidence(self):
        from nodepilot_pkg import deploy
        with tempfile.TemporaryDirectory() as d:
            env=self.environment(Path(d),'disabled','active');root=env[0]
            original=deploy.checkpoint
            def crash(journal,record,step):
                original(journal,record,step)
                if step=='candidate':raise SystemExit('power loss')
            with patch('nodepilot_pkg.deploy.checkpoint',side_effect=crash),self.assertRaises(SystemExit):self.install(env)
            record=read_json(root/'program-pending.json')
            legacy={k:v for k,v in record.items() if k not in {'transaction_version','previous','hashes','old_version','target_version','step'}}
            atomic_json(root/'program-pending.json',legacy)
            self.assertTrue(self.recover(env))
            self.assertEqual(read_json(root/'program-pending-rc2.json'),legacy)
            self.assertEqual(env[-1].state(),{'unit_file_state':'disabled','active_state':'active'})

    def test_corrupt_snapshot_and_manifest_reject_without_execution_or_cleanup(self):
        from nodepilot_pkg import deploy
        for mode in ('snapshot','hash','extra_module','schema','state_path','missing_candidate'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                env=self.environment(Path(d));root,launcher,units,m,source,sd=env
                original=deploy.checkpoint
                def crash(journal,record,step):
                    original(journal,record,step)
                    if step=='candidate':raise SystemExit('power loss')
                with patch('nodepilot_pkg.deploy.checkpoint',side_effect=crash),self.assertRaises(SystemExit):self.install(env)
                journal=root/'program-pending.json';record=read_json(journal)
                if mode=='snapshot':record['previous']['timer']['active_state']='unknown'
                elif mode=='hash':record['hashes']['nodepilot']='0'*64
                elif mode=='schema':record['transaction_version']=99
                elif mode=='state_path':record['previous']['state']='/external/state'
                elif mode=='extra_module':(root/'app'/('b'*64)/'foreign.py').write_text('unverified')
                else:(root/'app'/('b'*64)).rename(root/'app'/'missing')
                atomic_json(journal,record);before=journal.read_bytes();sd.calls.clear()
                with self.assertRaises(PilotError):self.recover(env)
                self.assertEqual(journal.read_bytes(),before)
                self.assertFalse(any(c[0]=='python3' for c in sd.calls))
                self.assertEqual((root/'app/current').resolve().name,'a'*64)


@unittest.skipUnless(os.name=='posix','complete transaction finalization')
class CompleteProgramTests(unittest.TestCase):
    environment=ProgramRC3Tests.environment
    install=ProgramRC3Tests.install
    recover=ProgramRC3Tests.recover

    def completed(self,base,enabled='disabled',active='active'):
        from nodepilot_pkg import deploy
        env=self.environment(base,enabled,active);original=deploy.checkpoint
        def crash(journal,record,step):
            original(journal,record,step)
            if step=='checked':raise SystemExit('complete persisted, unlink not done')
        with patch('nodepilot_pkg.deploy.checkpoint',side_effect=crash),self.assertRaises(SystemExit):self.install(env)
        return env

    def files(self,env):
        root,launcher,units,m,source,sd=env
        paths=[root/'.owner',m.state/'.owner',launcher,*[units/n for n in ('nodepilot-renew.service','nodepilot-renew.timer')],root/'source.json']
        return {p:(p.read_bytes(),p.stat().st_ino,p.stat().st_mtime_ns) for p in paths}

    def test_complete_only_unlinks_intent_all_six_states_and_repeat_is_noop(self):
        for enabled,active in ProgramRC3Tests.states:
            with self.subTest(enabled=enabled,active=active),tempfile.TemporaryDirectory() as d:
                env=self.completed(Path(d),enabled,active);root,launcher,units,m,source,sd=env
                history=root/'program-pending-rc2.json';history.write_text('audit evidence')
                before=self.files(env);link=(root/'app/current').lstat();backups=list((m.state/'backups').iterdir())
                sd.calls.clear()
                with patch('nodepilot_pkg.deploy.atomic_bytes',side_effect=AssertionError('complete wrote file')), \
                     patch('nodepilot_pkg.deploy.atomic_json',side_effect=AssertionError('complete wrote journal')):
                    self.assertTrue(self.recover(env))
                self.assertEqual(self.files(env),before)
                self.assertEqual((root/'app/current').lstat().st_ino,link.st_ino)
                self.assertEqual(list((m.state/'backups').iterdir()),backups)
                self.assertEqual(history.read_text(),'audit evidence')
                self.assertTrue(all(c[:2]==['systemctl','show'] for c in sd.calls))

    def test_complete_mismatch_never_repairs_or_discards_evidence(self):
        for mode in ('current','hash','manifest','launcher','service','timer_unit','active','enabled','root_owner','state_owner','source','source_link'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                env=self.completed(Path(d));root,launcher,units,m,source,sd=env
                if mode=='current':
                    (root/'app/current').unlink();(root/'app/current').symlink_to('a'*64,target_is_directory=True)
                elif mode=='hash':(root/'app'/('b'*64)/'nodepilot').write_text('tampered')
                elif mode=='manifest':(root/'app'/('b'*64)/'nodepilot_pkg/cli.py').unlink()
                elif mode=='launcher':launcher.write_text('external launcher')
                elif mode in ('service','timer_unit'):(units/('nodepilot-renew.service' if mode=='service' else 'nodepilot-renew.timer')).write_text('external unit')
                elif mode=='active':sd.active='inactive'
                elif mode=='enabled':sd.enable('enabled')
                elif mode=='root_owner':(root/'.owner').write_text(LEGACY_OWNER)
                elif mode=='state_owner':(m.state/'.owner').write_text(LEGACY_OWNER)
                elif mode=='source':atomic_json(root/'source.json',{'repository':'other/project'})
                else:
                    (root/'source.json').unlink();(root/'source.json').symlink_to(source/'nodepilot')
                journal=root/'program-pending.json';before=journal.read_bytes();sd.calls.clear()
                with patch('nodepilot_pkg.deploy.atomic_bytes',side_effect=AssertionError('unexpected repair')),self.assertRaises(PilotError):self.recover(env)
                self.assertEqual(journal.read_bytes(),before)
                self.assertTrue(all(c[:2]==['systemctl','show'] for c in sd.calls))

    def test_complete_unlink_io_error_keeps_evidence_and_retry_finalizes(self):
        with tempfile.TemporaryDirectory() as d:
            env=self.completed(Path(d));journal=env[0]/'program-pending.json';before=journal.read_bytes();files=self.files(env)
            original=Path.unlink
            def fail(path,*args,**kwargs):
                if path==journal:raise OSError('injected unlink I/O failure')
                return original(path,*args,**kwargs)
            env[-1].calls.clear()
            with patch.object(Path,'unlink',fail),self.assertRaises(OSError):self.recover(env)
            self.assertEqual(journal.read_bytes(),before);self.assertEqual(self.files(env),files)
            self.assertTrue(self.recover(env));self.assertEqual(self.files(env),files)

    def test_complete_legacy_record_uses_legacy_source_and_never_reinstalls(self):
        with tempfile.TemporaryDirectory() as d:
            env=self.completed(Path(d));root=env[0];journal=root/'program-pending.json';record=read_json(journal)
            del record['source_after'];record['transaction_version']=1;record['repository']='example/project'
            atomic_json(root/'source.json',{'repository':'example/project'});atomic_json(journal,record)
            env[-1].calls.clear();self.assertTrue(self.recover(env))
            self.assertTrue(all(c[:2]==['systemctl','show'] for c in env[-1].calls))


@unittest.skipUnless(os.name=='posix','boot-aware complete transactions')
class RebootCompleteTests(unittest.TestCase):
    environment=ProgramRC3Tests.environment
    install=ProgramRC3Tests.install
    recover=ProgramRC3Tests.recover
    completed=CompleteProgramTests.completed
    files=CompleteProgramTests.files
    boot_before='11111111-1111-4111-8111-111111111111'
    boot_after='22222222-2222-4222-8222-222222222222'

    def setup_complete(self, base, enabled='disabled', active='active'):
        with patch('nodepilot_pkg.installation.boot_id',return_value=self.boot_before):
            return self.completed(base,enabled,active)

    def reboot(self, env):
        sd=env[-1]
        sd.links['enabled-runtime'].unlink(missing_ok=True)
        enabled=sd.links['enabled'].is_symlink()
        sd.active='active' if enabled else 'inactive'
        for unit,enter in (('nodepilot-renew.timer',10 if enabled else 0),('timers.target',20)):
            sd.stamps[(unit,'ActiveEnterTimestampMonotonic')]=enter
            sd.stamps[(unit,'InactiveEnterTimestampMonotonic')]=0
        sd.calls.clear()

    def resume(self, env, boot=None):
        with patch('nodepilot_pkg.installation.boot_id',return_value=boot or self.boot_after):
            return self.recover(env)

    def assert_read_only(self,env):
        self.assertTrue(all(c[:2]==['systemctl','show'] for c in env[-1].calls))

    def test_reboot_six_states_only_unlinks_and_repeat_noop(self):
        for enabled,active in ProgramRC3Tests.states:
            with self.subTest(enabled=enabled,active=active),tempfile.TemporaryDirectory() as d:
                env=self.setup_complete(Path(d),enabled,active);self.reboot(env)
                before=self.files(env);backups=list((env[3].state/'backups').iterdir())
                with patch('nodepilot_pkg.deploy.atomic_bytes',side_effect=AssertionError('write')), \
                     patch('nodepilot_pkg.deploy.atomic_json',side_effect=AssertionError('write')):
                    self.assertTrue(self.resume(env))
                self.assertEqual(self.files(env),before)
                self.assertEqual(list((env[3].state/'backups').iterdir()),backups)
                self.assertEqual(env[-1].state(),{'unit_file_state':'enabled' if enabled=='enabled' else 'disabled',
                    'active_state':'active' if enabled=='enabled' else 'inactive'})
                self.assert_read_only(env)

    def test_reboot_does_not_relax_non_timer_security_checks(self):
        for mode in ('unit','source','current','hash','owner','launcher','dropin'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                env=self.setup_complete(Path(d));root,launcher,units,m,source,sd=env;self.reboot(env)
                if mode=='unit':(units/'nodepilot-renew.timer').write_text('foreign')
                elif mode=='source':atomic_json(root/'source.json',{'repository':'foreign/project'})
                elif mode=='current':
                    (root/'app/current').unlink();(root/'app/current').symlink_to('a'*64)
                elif mode=='hash':(root/'app'/('b'*64)/'nodepilot').write_text('corrupt')
                elif mode=='owner':(m.state/'.owner').write_text(LEGACY_OWNER)
                elif mode=='launcher':launcher.write_text('foreign')
                journal=root/'program-pending.json';before=journal.read_bytes()
                original=sd.__call__
                def altered(args,**kw):
                    if mode=='dropin' and 'DropInPaths' in args:return '/foreign.conf'
                    return original(args,**kw)
                with patch.object(TimerSystemd,'__call__',side_effect=altered),self.assertRaises(PilotError):self.resume(env)
                self.assertEqual(journal.read_bytes(),before);self.assert_read_only(env)

    def test_reboot_user_policy_stop_restart_and_enable_link_edits_refuse(self):
        for mode in ('disabled','stopped','restart','reenabled','runtime','target_restart','missing_time'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                env=self.setup_complete(Path(d),'enabled','active');sd=env[-1];self.reboot(env)
                if mode=='disabled':sd.links['enabled'].unlink();sd.active='inactive'
                elif mode=='stopped':sd.active='inactive'
                elif mode=='restart':sd.stamps[('nodepilot-renew.timer','ActiveEnterTimestampMonotonic')]=30
                elif mode=='reenabled':
                    sd.links['enabled'].unlink();sd.enable('enabled')
                elif mode=='runtime':sd.enable('enabled-runtime')
                elif mode=='target_restart':sd.stamps[('timers.target','InactiveEnterTimestampMonotonic')]=15
                else:sd.stamps.clear()
                journal=env[0]/'program-pending.json';before=journal.read_bytes()
                with self.assertRaises(PilotError):self.resume(env)
                self.assertEqual(journal.read_bytes(),before);self.assert_read_only(env)
        with tempfile.TemporaryDirectory() as d:
            env=self.setup_complete(Path(d));self.reboot(env)
            env[-1].stamps[('nodepilot-renew.timer','InactiveEnterTimestampMonotonic')]=30
            with self.assertRaises(PilotError):self.resume(env)  # User start then stop, now same inactive state.
            self.assert_read_only(env)

    def test_same_boot_state_and_link_changes_remain_strict(self):
        for mode in ('state','links'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                env=self.setup_complete(Path(d),'enabled','active');sd=env[-1];sd.calls.clear()
                if mode=='state':sd.active='inactive'
                else:sd.links['enabled'].unlink();sd.enable('enabled')
                with self.assertRaises(PilotError):self.resume(env,self.boot_before)
                self.assertTrue((env[0]/'program-pending.json').exists());self.assert_read_only(env)

    def test_old_v1_v2_and_unreadable_boot_use_strict_fallback(self):
        from nodepilot_pkg import installation
        for version in (1,2,3):
            for changed in (False,True):
                with self.subTest(version=version,changed=changed),tempfile.TemporaryDirectory() as d:
                    env=self.setup_complete(Path(d));journal=env[0]/'program-pending.json';record=read_json(journal)
                    if version<3:
                        record['transaction_version']=version;record.pop('commit');record['previous'].pop('boot_id')
                    atomic_json(journal,record)
                    if changed:env[-1].active='inactive'
                    with patch('nodepilot_pkg.installation.BOOT_ID',Path(d)/'missing-boot-id'):
                        self.assertIsNone(installation.boot_id())
                        if changed:
                            with self.assertRaises(PilotError):self.recover(env)
                            self.assertTrue(journal.exists())
                        else:self.assertTrue(self.recover(env))
        with patch.object(Path,'read_text',side_effect=PermissionError('denied')):
            self.assertIsNone(installation.boot_id())

    def test_corrupt_commit_or_future_schema_never_bypass(self):
        for mode in ('missing','boot','links','state','step','version'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                env=self.setup_complete(Path(d));journal=env[0]/'program-pending.json';record=read_json(journal)
                if mode=='missing':record.pop('commit')
                elif mode=='boot':record['commit']['boot_id']='invalid'
                elif mode=='links':record['commit']['links']={}
                elif mode=='state':record['commit']['timer']['active_state']='inactive'
                elif mode=='step':record['step']='timer'
                else:record['transaction_version']=99
                atomic_json(journal,record);before=journal.read_bytes()
                with self.assertRaises(PilotError):self.resume(env)
                self.assertEqual(journal.read_bytes(),before)

    def test_reboot_unlink_failure_keeps_journal_and_retry_is_readonly(self):
        with tempfile.TemporaryDirectory() as d:
            env=self.setup_complete(Path(d));self.reboot(env);journal=env[0]/'program-pending.json';before=journal.read_bytes()
            original=Path.unlink
            def fail(path,*args,**kwargs):
                if path==journal:raise PermissionError('unlink blocked')
                return original(path,*args,**kwargs)
            with patch.object(Path,'unlink',fail),self.assertRaises(OSError):self.resume(env)
            self.assertEqual(journal.read_bytes(),before);self.assertTrue(self.resume(env));self.assert_read_only(env)

    def test_error_after_durable_complete_never_rolls_back(self):
        from nodepilot_pkg import deploy
        with tempfile.TemporaryDirectory() as d:
            env=self.environment(Path(d));root=env[0];original=deploy.checkpoint
            def io_failure(journal,record,step):
                original(journal,record,step)
                if step=='checked':raise OSError('completion fsync/report failure')
            with patch('nodepilot_pkg.deploy.checkpoint',side_effect=io_failure), \
                 patch('nodepilot_pkg.deploy.rollback_program',side_effect=AssertionError('committed rollback')), \
                 self.assertRaisesRegex(PilotError,'complete'):
                self.install(env)
            self.assertEqual(read_json(root/'program-pending.json')['phase'],'complete')
            self.assertEqual((root/'app/current').resolve().name,'b'*64)
            env[-1].calls.clear();self.assertTrue(self.recover(env));self.assert_read_only(env)

    def test_journal_specific_manual_timer_review_never_bypasses_program_or_phase(self):
        from nodepilot_pkg.deploy import review_complete
        for mode in ('valid','hash','source','phase','stale','schema'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                env=self.setup_complete(Path(d));root,launcher,units,m,source,sd=env
                sd.active='inactive';journal=root/'program-pending.json';r=read_json(journal)
                if mode=='hash':(root/'app'/('b'*64)/'nodepilot').write_text('bad')
                elif mode=='source':atomic_json(root/'source.json',{'repository':'other/project'})
                elif mode=='phase':r['phase']='installing';atomic_json(journal,r)
                elif mode=='schema':r['transaction_version']=99;atomic_json(journal,r)
                digest=hashlib.sha256(journal.read_bytes()).hexdigest() if mode!='stale' else '0'*64
                before=journal.read_bytes();files=self.files(env);sd.calls.clear()
                with patch('nodepilot_pkg.deploy.run',side_effect=sd):
                    if mode=='valid':review_complete(m,digest,root,launcher,units)
                    else:
                        with self.assertRaises(PilotError):review_complete(m,digest,root,launcher,units)
                        self.assertEqual(journal.read_bytes(),before)
                self.assertEqual(self.files(env),files);self.assert_read_only(env)

    def test_manual_review_never_initializes_missing_state_or_auxiliary_directories(self):
        from nodepilot_pkg.deploy import review_complete
        for missing_state in (False,True):
            with self.subTest(missing_state=missing_state),tempfile.TemporaryDirectory() as d:
                env=self.setup_complete(Path(d));root,launcher,units,m,source,sd=env
                journal=root/'program-pending.json';digest=hashlib.sha256(journal.read_bytes()).hexdigest()
                if missing_state:m.state.rename(Path(d)/'saved-state')
                else:(m.state/'acme').rmdir()
                with patch('nodepilot_pkg.deploy.run',side_effect=sd):
                    if missing_state:
                        with self.assertRaises(PilotError):review_complete(m,digest,root,launcher,units)
                        self.assertFalse(m.state.exists());self.assertTrue(journal.exists())
                    else:
                        review_complete(m,digest,root,launcher,units)
                        self.assertFalse((m.state/'acme').exists());self.assertFalse(journal.exists())


@unittest.skipUnless(os.name=='posix','non-complete boot recovery')
class NonCompleteBootTests(unittest.TestCase):
    environment=ProgramRC3Tests.environment
    recover=ProgramRC3Tests.recover
    reboot=RebootCompleteTests.reboot
    boot_before=RebootCompleteTests.boot_before
    boot_after=RebootCompleteTests.boot_after
    stages=('intent','candidate','current','owners','launcher','units','reloaded',
            'timer-intent','timer','rollback','rollback-current','rollback-launcher',
            'rollback-units','rollback-reloaded','rollback-timer','checked')

    def install(self,env):
        from nodepilot_pkg.deploy import deploy
        root,launcher,units,m,source,sd=env
        with patch('nodepilot_pkg.deploy.supported'),patch('nodepilot_pkg.deploy.run',side_effect=sd), \
             patch('nodepilot_pkg.installation.RUNTIME_UNITS',sd.links['enabled-runtime'].parent.parent):
            deploy(source,'b'*64,root=root,launcher=launcher,units=units,manager=m,confirm_source_change=lambda old,new:True)

    def interrupted(self, base, enabled='disabled', active='active', step='candidate', first=False, source_before=None):
        from nodepilot_pkg import deploy
        env=self.environment(base,enabled,active,first);root,launcher,units,m,source,sd=env
        if source_before is not None:(root/'source.json').write_bytes(source_before)
        writer=deploy.atomic_json;command=sd.__call__
        def crash(path,record):
            writer(path,record)
            if path.name=='program-pending.json' and record.get('step')==step:
                raise SystemExit('durable boundary interruption')
        def fail(args,**kwargs):
            if step.startswith('rollback') and str(args[0])=='python3' and (root/'app/current').is_symlink() and (root/'app/current').resolve().name=='b'*64:
                raise PilotError('force rollback')
            return command(args,**kwargs)
        with patch('nodepilot_pkg.installation.boot_id',return_value=self.boot_before), \
             patch('nodepilot_pkg.deploy.atomic_json',side_effect=crash), \
             patch.object(TimerSystemd,'__call__',side_effect=fail),self.assertRaises(SystemExit):
            self.install(env)
        self.assertTrue((root/'program-pending.json').exists())
        sd.calls.clear()
        return env

    def resume(self,env,boot=None,reviewed=None):
        from nodepilot_pkg.deploy import recover_program
        root,launcher,units,m,source,sd=env
        with m.locked(migrate_owner=False),patch('nodepilot_pkg.installation.boot_id',return_value=boot), \
             patch('nodepilot_pkg.installation.RUNTIME_UNITS',sd.links['enabled-runtime'].parent.parent), \
             patch('nodepilot_pkg.deploy.run',side_effect=sd):
            result=recover_program(root,launcher,units,m,reviewed_timer=reviewed)
            self.assertFalse(recover_program(root,launcher,units,m))
            return result

    def assert_no_timer_mutation(self,env):
        self.assertFalse([c for c in env[-1].calls if c[:2] in [
            ['systemctl',x] for x in ('start','stop','enable','disable','restart')]])

    def test_same_and_changed_boot_all_stages_all_six_states(self):
        for rebooted in (False,True):
            for enabled,active in ProgramRC3Tests.states:
                for step in self.stages:
                    with self.subTest(rebooted=rebooted,enabled=enabled,active=active,step=step),tempfile.TemporaryDirectory() as d:
                        env=self.interrupted(Path(d),enabled,active,step);root,launcher,units,m,source,sd=env
                        if rebooted:self.reboot(env)
                        expected=sd.state();sd.calls.clear()
                        self.assertTrue(self.resume(env,self.boot_after if rebooted else self.boot_before))
                        self.assertEqual(sd.state(),expected)
                        self.assert_no_timer_mutation(env)
                        rolled=step=='intent' or step.startswith('rollback')
                        self.assertEqual((root/'app/current').resolve().name,('a' if rolled else 'b')*64)
                        self.assertEqual((m.state/'user-config').read_text(),'preserved')
                        self.assertFalse((root/'program-pending.json').exists())
                        if rolled:
                            self.assertEqual((root/'.owner').read_text(),LEGACY_OWNER)
                            self.assertEqual((m.state/'.owner').read_text(),LEGACY_OWNER)
                            self.assertEqual(launcher.read_text(),__import__('nodepilot_pkg.installation',fromlist=['LAUNCHER']).LAUNCHER)
                            self.assertFalse((root/'source.json').exists())
                        if step=='intent':self.assertFalse(any(c[0]=='python3' for c in sd.calls))

    def test_unknown_boot_and_corrupt_records_leave_original_bytes_every_stage(self):
        for step in self.stages[:-1]:
            for mode in ('unreadable','missing','context','step'):
                with self.subTest(step=step,mode=mode),tempfile.TemporaryDirectory() as d:
                    env=self.interrupted(Path(d),step=step);journal=env[0]/'program-pending.json';r=read_json(journal)
                    if mode=='missing':r['timer_context']['boot_id']=None
                    elif mode=='context':r['timer_context']['links']={}
                    elif mode=='step':r['step']='unknown'
                    atomic_json(journal,r);before=journal.read_bytes()
                    with self.assertRaises(PilotError):self.resume(env,None if mode=='unreadable' else self.boot_after)
                    self.assertEqual(journal.read_bytes(),before);self.assert_no_timer_mutation(env)

    def test_external_post_boot_timer_changes_never_reconciled_at_any_stage(self):
        for step in self.stages[:-1]:
            for mode in ('stop','links','masked','transition','time'):
                with self.subTest(step=step,mode=mode),tempfile.TemporaryDirectory() as d:
                    env=self.interrupted(Path(d),'enabled','active',step);self.reboot(env);sd=env[-1]
                    if mode=='stop':sd.active='inactive'
                    elif mode=='links':sd.links['enabled'].unlink();sd.enable('enabled')
                    elif mode=='time':sd.stamps[('nodepilot-renew.timer','ActiveEnterTimestampMonotonic')]=30
                    original=sd.__call__
                    def masked(args,**kw):
                        if mode=='masked' and 'UnitFileState' in args:return 'masked'
                        if mode=='transition' and 'ActiveState' in args:return 'activating'
                        return original(args,**kw)
                    journal=env[0]/'program-pending.json';before=journal.read_bytes()
                    with patch.object(TimerSystemd,'__call__',side_effect=masked),self.assertRaises(PilotError):self.resume(env,self.boot_after)
                    self.assertEqual(journal.read_bytes(),before);self.assert_no_timer_mutation(env)

    def test_cross_boot_forward_failure_rolls_back_files_but_preserves_this_boot_timer(self):
        from nodepilot_pkg import deploy
        for enabled,active in ProgramRC3Tests.states:
            with self.subTest(enabled=enabled,active=active),tempfile.TemporaryDirectory() as d:
                env=self.interrupted(Path(d),enabled,active,'candidate');root,launcher,units,m,source,sd=env;self.reboot(env)
                original=sd.__call__;expected=sd.state()
                def failure(args,**kw):
                    if str(args[0])=='python3' and (root/'app/current').resolve().name=='b'*64:raise PilotError('new self-check failed')
                    return original(args,**kw)
                with patch.object(TimerSystemd,'__call__',side_effect=failure),self.assertRaises(PilotError):self.resume(env,self.boot_after)
                self.assertEqual((root/'app/current').resolve().name,'a'*64)
                self.assertEqual((root/'.owner').read_text(),LEGACY_OWNER)
                self.assertEqual((m.state/'.owner').read_text(),LEGACY_OWNER)
                self.assertEqual(sd.state(),expected);self.assert_no_timer_mutation(env)
                self.assertFalse((root/'program-pending.json').exists())
                self.assertFalse((root/'source.json').exists())

    def test_repeat_reboot_rebases_again_and_rollback_failure_retains_phase(self):
        from nodepilot_pkg import deploy
        with tempfile.TemporaryDirectory() as d:
            env=self.interrupted(Path(d),'enabled-runtime','active','candidate');self.reboot(env)
            original=deploy.checkpoint
            def crash(path,r,step):
                original(path,r,step)
                if step=='current':raise SystemExit('second recovery interrupted')
            with patch('nodepilot_pkg.deploy.checkpoint',side_effect=crash),self.assertRaises(SystemExit):self.resume(env,self.boot_after)
            r=read_json(env[0]/'program-pending.json');self.assertEqual(r['timer_context']['mode'],'preserve')
            self.reboot(env)
            self.assertTrue(self.resume(env,'33333333-3333-4333-8333-333333333333'));self.assert_no_timer_mutation(env)
        with tempfile.TemporaryDirectory() as d:
            env=self.interrupted(Path(d),step='rollback-current');self.reboot(env);journal=env[0]/'program-pending.json'
            with patch('nodepilot_pkg.deploy.restore_file',side_effect=OSError('disk failed')),self.assertRaises(OSError):self.resume(env,self.boot_after)
            self.assertEqual(read_json(journal)['phase'],'rolling_back')
            self.assertTrue(self.resume(env,self.boot_after));self.assert_no_timer_mutation(env)

    def test_first_install_before_and_after_timer_commit_reboot_and_cleanup(self):
        for step in ('intent','candidate','current','owners','launcher','units','reloaded','timer-intent','timer','checked'):
            with self.subTest(step=step),tempfile.TemporaryDirectory() as d:
                env=self.interrupted(Path(d),active='inactive',step=step,first=True);self.reboot(env)
                self.assertTrue(self.resume(env,self.boot_after))
                if step=='intent':
                    self.assertFalse((env[0]/'app/current').exists());self.assertFalse(any(p.is_symlink() for p in env[-1].links.values()))
                else:self.assertEqual(env[-1].state(),{'unit_file_state':'enabled','active_state':'active'})

    def test_old_v1_v2_v3_cross_boot_requires_exact_manual_ack_without_timer_writes(self):
        for version in (1,2,3):
            for stage in ('intent','candidate','rollback-current'):
                with self.subTest(version=version,stage=stage),tempfile.TemporaryDirectory() as d:
                    env=self.interrupted(Path(d),'enabled-runtime','active',stage);journal=env[0]/'program-pending.json';r=read_json(journal)
                    r['transaction_version']=version;r.pop('timer_context')
                    if version<3:r['previous'].pop('boot_id')
                    atomic_json(journal,r);self.reboot(env);before=journal.read_bytes()
                    with self.assertRaises(PilotError):self.resume(env,self.boot_after)
                    self.assertEqual(journal.read_bytes(),before)
                    with self.assertRaises(PilotError):self.resume(env,self.boot_after,reviewed='0'*64)
                    self.assertEqual(journal.read_bytes(),before)
                    digest=hashlib.sha256(before).hexdigest()
                    self.assertTrue(self.resume(env,self.boot_after,reviewed=digest))
                    self.assertEqual(env[-1].state(),{'unit_file_state':'disabled','active_state':'inactive'})
                    self.assert_no_timer_mutation(env)

    def test_manual_ack_cannot_bypass_candidate_source_units_or_step_corruption(self):
        for mode in ('hash','source','unit','current','step','alias'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                env=self.interrupted(Path(d),step='units');root,launcher,units,m,source,sd=env;self.reboot(env)
                if mode=='hash':(root/'app'/('b'*64)/'nodepilot').write_text('corrupt')
                elif mode=='source':atomic_json(root/'source.json',{'repository':'foreign/project'})
                elif mode=='unit':(units/'nodepilot-renew.timer').write_text('foreign')
                elif mode=='current':(root/'app/current').unlink();(root/'app/current').symlink_to('a'*64)
                elif mode=='alias':
                    p=units/'other.target.wants/nodepilot-renew.timer';p.parent.mkdir();p.symlink_to(units/'nodepilot-renew.timer')
                else:
                    r=read_json(root/'program-pending.json');r['step']='unknown';atomic_json(root/'program-pending.json',r)
                journal=root/'program-pending.json';before=journal.read_bytes();digest=hashlib.sha256(before).hexdigest()
                with self.assertRaises(PilotError):self.resume(env,self.boot_after,reviewed=digest)
                self.assertEqual(journal.read_bytes(),before);self.assert_no_timer_mutation(env)

    def test_first_install_cross_boot_failure_and_rollback_interruption_remove_only_owned_timer(self):
        for step in ('candidate','units','timer','rollback','rollback-current','rollback-launcher',
                     'rollback-timer-cleanup','rollback-units','rollback-reloaded','rollback-timer'):
            with self.subTest(step=step),tempfile.TemporaryDirectory() as d:
                env=self.interrupted(Path(d),active='inactive',step=step,first=True);root,launcher,units,m,source,sd=env
                self.reboot(env);command=sd.__call__
                def fail(args,**kw):
                    if not step.startswith('rollback') and str(args[0])=='python3' and (root/'app/current').is_symlink():
                        raise PilotError('first install failed after reboot')
                    return command(args,**kw)
                if step.startswith('rollback'):self.assertTrue(self.resume(env,self.boot_after))
                else:
                    with patch.object(TimerSystemd,'__call__',side_effect=fail),self.assertRaises(PilotError):self.resume(env,self.boot_after)
                self.assertFalse((root/'app/current').is_symlink());self.assertFalse(launcher.exists())
                self.assertFalse((root/'program-pending.json').exists())
                self.assertFalse(any(p.is_symlink() for p in sd.links.values()))
                self.assertFalse((units/'nodepilot-renew.timer').exists())
                self.assertEqual((m.state/'user-config').read_text(),'preserved')

    def test_uncheckpointed_first_enable_after_reboot_requires_manual_review(self):
        with tempfile.TemporaryDirectory() as d:
            env=self.interrupted(Path(d),active='inactive',step='timer-intent',first=True);sd=env[-1]
            sd.enable('enabled');sd.active='active'  # enable returned, checkpoint never persisted.
            self.reboot(env);journal=env[0]/'program-pending.json';before=journal.read_bytes()
            with self.assertRaises(PilotError):self.resume(env,self.boot_after)
            self.assertEqual(journal.read_bytes(),before);self.assert_no_timer_mutation(env)
            self.assertTrue(self.resume(env,self.boot_after,reviewed=hashlib.sha256(before).hexdigest()))
            self.assert_no_timer_mutation(env)

    def test_manual_unknown_boot_failure_can_roll_back_in_the_same_authorized_call(self):
        with tempfile.TemporaryDirectory() as d:
            env=self.interrupted(Path(d),step='candidate');root,launcher,units,m,source,sd=env
            journal=root/'program-pending.json';r=read_json(journal);r['timer_context']['boot_id']=None;atomic_json(journal,r)
            digest=hashlib.sha256(journal.read_bytes()).hexdigest();command=sd.__call__
            def fail(args,**kw):
                if str(args[0])=='python3' and (root/'app/current').resolve().name=='b'*64:raise PilotError('final check failed')
                return command(args,**kw)
            with patch.object(TimerSystemd,'__call__',side_effect=fail),self.assertRaises(PilotError):self.resume(env,None,reviewed=digest)
            self.assertFalse(journal.exists());self.assertEqual((root/'app/current').resolve().name,'a'*64)
            self.assert_no_timer_mutation(env)

    def test_checkpoint_claims_cannot_override_valid_but_wrong_owned_files(self):
        from nodepilot_pkg.installation import LEGACY_RENEW_TIMER
        for mode in ('preparing','owner','launcher','unit','rollback'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                stage={'preparing':'intent','owner':'owners','launcher':'launcher','unit':'units','rollback':'rollback-current'}[mode]
                env=self.interrupted(Path(d),step=stage);root,launcher,units,m,source,sd=env
                if mode=='preparing':(root/'app/current').unlink();(root/'app/current').symlink_to('b'*64)
                elif mode=='owner':(m.state/'.owner').write_text(LEGACY_OWNER)
                elif mode=='launcher':launcher.unlink()
                elif mode=='unit':(units/'nodepilot-renew.timer').write_text(LEGACY_RENEW_TIMER)
                else:(root/'app/current').unlink();(root/'app/current').symlink_to('b'*64)
                journal=root/'program-pending.json';before=journal.read_bytes()
                with self.assertRaises(PilotError):self.resume(env,self.boot_after)
                self.assertEqual(journal.read_bytes(),before);self.assert_no_timer_mutation(env)

    def test_recovery_fails_again_at_each_stage_keeps_rollback_intent_until_retry(self):
        from nodepilot_pkg import deploy
        for step in self.stages[:-1]:
            with self.subTest(step=step),tempfile.TemporaryDirectory() as d:
                env=self.interrupted(Path(d),step=step);self.reboot(env);journal=env[0]/'program-pending.json'
                writer=deploy.checkpoint
                def error(path,record,boundary):
                    writer(path,record,boundary)
                    raise OSError('recovery checkpoint I/O error')
                with patch('nodepilot_pkg.deploy.checkpoint',side_effect=error),self.assertRaises((PilotError,OSError)):
                    self.resume(env,self.boot_after)
                self.assertEqual(read_json(journal)['phase'],'rolling_back')
                self.assertEqual((env[3].state/'user-config').read_text(),'preserved')
                self.assert_no_timer_mutation(env)
                self.assertTrue(self.resume(env,self.boot_after))
                self.assertEqual((env[0]/'app/current').resolve().name,'a'*64)
                self.assert_no_timer_mutation(env)

    def test_cross_boot_rollback_restores_exact_legacy_source_bytes(self):
        before=b'{ "repository" : "old/project" }\n'
        for step in ('intent','rollback-launcher','rollback-reloaded'):
            with self.subTest(step=step),tempfile.TemporaryDirectory() as d:
                env=self.interrupted(Path(d),step=step,source_before=before);self.reboot(env)
                self.assertTrue(self.resume(env,self.boot_after))
                self.assertEqual((env[0]/'source.json').read_bytes(),before)
                self.assert_no_timer_mutation(env)


@unittest.skipUnless(os.name=='posix','durable node revisions')
class CaddyRollbackRC2Tests(unittest.TestCase):
    def test_partial_node_delete_survives_loss_of_inner_pending_file(self):
        import shutil
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);m=Manager(root/'state',FakeCores(),FakeServices(root/'units'));m.initialize()
            with patch('nodepilot_pkg.manager.port_free'):
                first=m.apply(fixture(),probe=False);second=m.apply(fixture(port=8443),probe=False)
            original=shutil.rmtree;path=m.node_dir(first['id'])
            def partial(target,*args,**kwargs):
                if Path(target)==path:
                    (path/'pending.json').unlink(missing_ok=True)
                    (path/'current').unlink(missing_ok=True)
                    raise OSError('simulated partial delete')
                return original(target,*args,**kwargs)
            with patch('nodepilot_pkg.manager.shutil.rmtree',side_effect=partial),self.assertRaises(OSError):
                m.delete(first['id'])
            self.assertTrue((m.state/'deletions'/(first['id']+'.json')).exists())
            m.recover();m.recover()
            self.assertFalse(path.exists());self.assertEqual(m.nodes(),[second]);self.assertTrue(m.services.active(second))

    def test_core_recovery_restores_exact_previous_caddy_bytes(self):
        import base64
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);cores=FakeCores();cores.root=root/'cores';cores.switch=MagicMock()
            old=cores.root/'caddy/old';old.mkdir(parents=True)
            m=Manager(root/'state',cores,FakeServices(root/'units'));m.initialize();m.caddy=MagicMock()
            data=b'{"legacy":"unchanged"}'
            atomic_json(m.state/'core-pending.json',{'schema':2,'core':'caddy','old':str(old),
                'caddy_previous':base64.b64encode(data).decode()})
            m.recover_core();m.recover_core()
            m.caddy.restore_configuration.assert_called_once_with(data)

    def test_power_loss_restores_exact_legacy_caddy_configuration(self):
        from test_caddy import self_node
        from nodepilot_pkg.common import private_dir
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);services=FakeServices(root/'units');m=Manager(root/'state',FakeCores(),services);m.initialize()
            m.caddy=MagicMock()
            with patch('nodepilot_pkg.manager.port_free'):
                original=m.apply(self_node(),probe=False)
                config=private_dir(m.state/'caddy')/'config.json';old_data=b'{"legacy":"exact configuration bytes"}'
                config.write_bytes(old_data);(services.units/'nodepilot-caddy.service').write_text('owned fixture')
                changed=copy.deepcopy(original);changed['port']=18443
                with patch.object(services,'restart',side_effect=SystemExit('power loss')),self.assertRaises(SystemExit):
                    m.apply(changed,probe=False)
                self.assertTrue((m.node_dir(original['id'])/'pending.json').exists())
                m.recover()
                self.assertEqual(m.get(original['id']),original)
                m.caddy.restore_configuration.assert_called_once_with(old_data)
                self.assertFalse((m.node_dir(original['id'])/'pending.json').exists())


if __name__=='__main__':unittest.main()
