"""Portable restore preflight, safe credential policy and rollback failures."""
import base64
import copy
import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from nodepilot_pkg.common import PilotError,atomic_json,read_json,run
from nodepilot_pkg import certificates
from nodepilot_pkg.dns_credentials import profiles,store_token,credential_path
from test_config import fixture
import test_manager as manager_tests
from test_caddy import self_node,FakeCaddy


@unittest.skipUnless(os.name=='posix' and shutil.which('openssl'),'Linux/OpenSSL restore')
class RestoreTests(unittest.TestCase):
    setUp=manager_tests.ManagerTests.setUp
    tearDown=manager_tests.ManagerTests.tearDown
    create=manager_tests.ManagerTests.create

    def pair(self):
        root=Path(self.tmp.name);cert=root/'cert.pem';key=root/'key.pem'
        if not cert.exists():
            run(['openssl','req','-x509','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256','-nodes',
                 '-days','30','-subj','/CN=example.com','-addext','subjectAltName=DNS:example.com',
                 '-out',cert,'-keyout',key])
        return {name:base64.b64encode(path.read_bytes()).decode() for name,path in [('certificate.pem',cert),('key.pem',key)]}

    def bundle(self,nodes):
        path=Path(self.tmp.name)/'backup.json'
        atomic_json(path,{'schema':1,'nodes':[{'node':n,'files':{} if n['protocol']=='reality' and n['reality'].get('mode')!='self' else self.pair()} for n in nodes]})
        return path

    def test_whole_bundle_bad_late_certificate_native_config_metadata_and_duplicate_fail_before_apply(self):
        for fault in ('cert','base64','key','expired','sni','native','metadata','duplicate','port','adoption','missing','schema'):
            with self.subTest(fault=fault):
                a,b=fixture('anytls',port=4443),fixture('hysteria2',port=4444)
                backup=self.bundle([a,b]);r=read_json(backup)
                if fault=='cert':r['nodes'][1]['files']['certificate.pem']=base64.b64encode(b'broken').decode()
                elif fault=='base64':r['nodes'][1]['files']['key.pem']='!bad!'
                elif fault=='key':r['nodes'][1]['files']['key.pem']=r['nodes'][1]['files']['certificate.pem']
                elif fault=='expired':
                    root=Path(self.tmp.name);expired=root/'expired.pem'
                    run(['openssl','x509','-in',root/'cert.pem','-signkey',root/'key.pem','-days','0','-out',expired])
                    r['nodes'][1]['files']['certificate.pem']=base64.b64encode(expired.read_bytes()).decode()
                elif fault=='sni':r['nodes'][1]['node']['sni']='wrong.example.com'
                elif fault=='metadata':r['nodes'][1]['node']['port']='bad'
                elif fault=='duplicate':r['nodes'][1]=copy.deepcopy(r['nodes'][0])
                elif fault=='port':r['nodes'][1]['node']=fixture('anytls',port=4443)
                elif fault=='adoption':r['nodes'][1]['node']['adoption']='a-'+'0'*16
                elif fault=='missing':r['nodes'][1]['files'].pop('key.pem')
                elif fault=='schema':r['schema']=99
                atomic_json(backup,r)
                with patch.object(self.manager,'apply') as apply,patch.object(self.cores,'check',side_effect=PilotError('missing/incompatible kernel') if fault=='native' else None):
                    with self.assertRaises(PilotError),self.manager.locked():self.manager.restore(backup)
                apply.assert_not_called();self.assertFalse(self.manager.nodes());self.assertFalse(list(self.services.units.iterdir()))

    def test_acme_dns01_import_notice_persists_and_token_not_restored_or_rebound(self):
        profile=store_token(self.manager.state,'test_only_'+'k'*30)
        token=credential_path(self.manager.state,profile).read_bytes()
        a,b=fixture('anytls',port=4443),fixture('hysteria2',port=4444)
        for n in (a,b):n['tls'].update(mode='acme',challenge='dns-cloudflare',dns={'credential_id':profile,'zone_id':'0'*32})
        backup=self.bundle([a,b]);original=backup.read_bytes()
        with self.manager.locked():
            preview=self.manager.preview_restore(backup)
            self.assertEqual(len(preview['nodes']),2)
            self.assertTrue(all('自动续期已关闭，需要重新配置' in n['notice'] for n in preview['nodes']))
            ids=self.manager.restore(backup,expected_digest=preview['sha256'])
        self.assertEqual(len(ids),2)
        for n in self.manager.nodes():
            self.assertEqual(n['tls']['mode'],'import');self.assertFalse(profiles(n))
            self.assertFalse(certificates.due(n))
            info=certificates.information(n,self.manager.state)
            self.assertFalse(info['automatic']);self.assertFalse(info['automatic_enabled'])
            self.assertIn('自动续期已关闭',info['renewal_notice'])
        self.assertEqual(token,credential_path(self.manager.state,profile).read_bytes());self.assertEqual(backup.read_bytes(),original)

    def test_caddy_dns01_and_http01_preview_convert_to_import_without_credentials(self):
        self.manager.caddy=FakeCaddy()
        for mode in ('dns01','acme'):
            n=self_node();n['caddy'].update(certificate_mode=mode)
            if mode=='dns01':n['caddy']['dns']={'credential_id':'cf-'+'0'*16,'zone_id':'0'*32}
            backup=self.bundle([n])
            with self.manager.locked():
                preview=self.manager.preview_restore(backup)
            self.assertEqual(preview['nodes'][0]['certificate_mode'],mode)
            self.assertEqual(preview['nodes'][0]['restored_mode'],'import')
            self.assertIn('自动续期已关闭',preview['nodes'][0]['notice'])
            self.assertFalse(self.manager.nodes())

    def test_preview_digest_change_rejected_and_existing_nodes_remain_untouched(self):
        backup=self.bundle([fixture('anytls')])
        with self.manager.locked():preview=self.manager.preview_restore(backup)
        backup.write_bytes(backup.read_bytes()+b'\n')
        with patch.object(self.manager,'apply') as apply,self.assertRaisesRegex(PilotError,'预览后已改变'),self.manager.locked():
            self.manager.restore(backup,expected_digest=preview['sha256'])
        apply.assert_not_called()
        original=self.create();revision=self.manager.revision(original['id'])
        with self.assertRaises(PilotError),self.manager.locked():self.manager.restore(backup)
        self.assertEqual(self.manager.get(original['id']),original);self.assertEqual(self.manager.revision(original['id']),revision)
        self.assertTrue(self.services.active(original));self.assertFalse(self.services.stopped)

    def test_legacy_bundle_without_adoptions_restores_and_foreign_unit_fails_before_any_apply(self):
        backup=self.bundle([fixture('anytls',port=4443),fixture('hysteria2',port=4444)])
        r=read_json(backup);foreign=self.services.units/r['nodes'][1]['node']['service'];foreign.write_text('foreign')
        with patch.object(self.manager,'apply') as apply,self.assertRaises(PilotError),self.manager.locked():self.manager.restore(backup)
        apply.assert_not_called();self.assertEqual(foreign.read_text(),'foreign');self.assertFalse(self.services.stopped)
        foreign.unlink()
        with self.manager.locked():self.assertEqual(len(self.manager.restore(backup)),2)

    def test_restart_and_rollback_failure_report_partial_nodes_and_preserve_external_service(self):
        for cleanup_fail in (False,True):
            with self.subTest(cleanup_fail=cleanup_fail):
                backup=self.bundle([fixture('anytls',port=4443),fixture('hysteria2',port=4444)])
                entries=read_json(backup)['nodes'];first,second=[e['node']['id'] for e in entries]
                before=backup.read_bytes();self.services.running.add('external-service')
                restart=self.services.restart;delete=self.manager.delete
                def fail(n):
                    if n['id']==second:raise PilotError('restart failed')
                    return restart(n)
                with patch.object(self.services,'restart',side_effect=fail), \
                     patch.object(self.manager,'delete',side_effect=PilotError('cleanup disk failure') if cleanup_fail else delete), \
                     self.assertRaises(PilotError) as error,self.manager.locked():self.manager.restore(backup)
                self.assertIn('external-service',self.services.running);self.assertEqual(backup.read_bytes(),before)
                if cleanup_fail:
                    self.assertIn('清理未完成',str(error.exception));self.assertEqual([n['id'] for n in self.manager.nodes()],[first])
                    with self.manager.locked():delete(first)
                else:self.assertFalse(self.manager.nodes())

    def test_active_or_corrupt_transaction_blocks_restore_without_recovery_side_effects(self):
        backup=self.bundle([fixture('anytls')]);pending=self.manager.state/'core-pending.json'
        pending.write_text('{broken');before=pending.read_bytes()
        with patch.object(self.manager,'recover_core') as recover,patch.object(self.manager,'apply') as apply, \
             self.assertRaises(PilotError),self.manager.locked():self.manager.restore(backup)
        recover.assert_not_called();apply.assert_not_called();self.assertEqual(pending.read_bytes(),before)

    def test_restore_configuration_write_failure_cleans_only_new_node(self):
        backup=self.bundle([fixture('anytls')]);writer=__import__('nodepilot_pkg.manager',fromlist=['atomic_json']).atomic_json
        def fail(path,value):
            if path.name=='config.json' and 'revisions' in path.parts:raise OSError('disk failure')
            return writer(path,value)
        with patch('nodepilot_pkg.manager.atomic_json',side_effect=fail),self.assertRaises(PilotError),self.manager.locked():self.manager.restore(backup)
        self.assertFalse(self.manager.nodes());self.assertFalse(list(self.services.units.iterdir()))
