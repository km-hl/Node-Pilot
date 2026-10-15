"""Source provenance is an authorization boundary, independent of archive integrity."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nodepilot_pkg.common import PilotError, atomic_json, read_json
from nodepilot_pkg.manager import OWNER
from nodepilot_pkg import project_update as update
import test_release_candidate as rc3

URL='https://github.com/example/project/releases/download/v0.4.0/nodepilot-0.4.0.tar.gz'


def release():
    return {'tag_name':'v0.4.0','assets':[{'name':name,'digest':'sha256:'+'a'*64,
        'browser_download_url':'https://github.com/example/project/releases/download/v0.4.0/'+name}
        for name in ('install.sh','nodepilot-0.4.0.tar.gz')]}


class SourceRecordTests(unittest.TestCase):
    def root(self,d):
        root=Path(d);(root/'.owner').write_text(OWNER);return root

    def test_missing_and_local_sources_block_online_discovery_without_network(self):
        for value in (None,{'schema':1,'provenance':'local-source'},
                      {'schema':1,'provenance':'local-source','previous_binding':{'repository':'example/project'}}):
            with self.subTest(value=value),tempfile.TemporaryDirectory() as d:
                root=self.root(d)
                if value:atomic_json(root/'source.json',value)
                with patch.object(update,'official_release') as network,self.assertRaisesRegex(PilotError,'未绑定在线更新源'):
                    update.check_update(root)
                network.assert_not_called()

    def test_existing_v03_rc_and_explicit_records_keep_authorized_repository(self):
        for record in ({'repository':'example/project'},
                       {'schema':1,'provenance':'official-release','repository':'example/project'},
                       {'schema':1,'provenance':'explicit-user-bound','repository':'example/project'}):
            with self.subTest(record=record),tempfile.TemporaryDirectory() as d:
                root=self.root(d);atomic_json(root/'source.json',record);before=(root/'source.json').read_bytes()
                self.assertEqual(update.bound_repository(root),'example/project')
                self.assertEqual((root/'source.json').read_bytes(),before)

    def test_abnormal_sources_never_continue_update_or_overwrite_evidence(self):
        records=[[],{}, {'repository':None},{'repository':'../project'},
                 {'schema':99,'provenance':'official-release','repository':'example/project'},
                 {'schema':True,'provenance':'official-release','repository':'example/project'},
                 {'schema':1,'provenance':[],'repository':'example/project'},
                 {'schema':1,'provenance':'local-source','repository':'example/project'}]
        for record in records:
            with self.subTest(record=record),tempfile.TemporaryDirectory() as d:
                root=self.root(d);atomic_json(root/'source.json',record);before=(root/'source.json').read_bytes()
                with patch.object(update,'official_release') as network,self.assertRaises(PilotError):update.check_update(root)
                network.assert_not_called();self.assertEqual((root/'source.json').read_bytes(),before)
        with tempfile.TemporaryDirectory() as d:
            root=self.root(d);path=root/'source.json';path.write_text('{')
            with self.assertRaises(PilotError):update.bound_repository(root)
            path.unlink();path.symlink_to(root/'.owner')
            with self.assertRaises(PilotError):update.bound_repository(root)

    def test_release_source_url_strictness_and_fork_local_never_infer_upstream(self):
        self.assertEqual(update.planned_source(None,URL)['repository'],'example/project')
        self.assertEqual(update.planned_source(None,''),{'schema':1,'provenance':'local-source'})
        for url in (URL.replace('https:','http:'),URL+'?repo=other',URL.replace('github.com','github.com.evil'),URL.replace('github.com','user@github.com')):
            with self.subTest(url=url),self.assertRaises(PilotError):update.planned_source(None,url)

    def test_binding_requires_confirmation_valid_release_assets_and_no_transaction(self):
        with tempfile.TemporaryDirectory() as d:
            root=self.root(d);record={'schema':1,'provenance':'local-source'};atomic_json(root/'source.json',record)
            with patch.object(update,'official_release',return_value=release()) as network:
                with self.assertRaises(PilotError):update.bind_repository('example/project',root=root)
                network.assert_not_called()
                (root/'program-pending.json').write_text('{}')
                with self.assertRaises(PilotError):update.bind_repository('example/project',True,root)
                network.assert_not_called();(root/'program-pending.json').unlink()
                update.bind_repository('example/project',True,root)
            self.assertEqual(read_json(root/'source.json'),{'schema':1,'provenance':'explicit-user-bound','repository':'example/project'})
            self.assertEqual(update.bound_repository(root),'example/project')
            bad=release();bad['assets'][0]['digest']=''
            with patch.object(update,'official_release',return_value=bad),self.assertRaises(PilotError):update.bind_repository('example/project',True,root)
            self.assertEqual(update.bound_repository(root),'example/project')


@unittest.skipUnless(os.name=='posix','Linux deployment provenance')
class SourceDeployTests(unittest.TestCase):
    environment=rc3.ProgramRC3Tests.environment

    def install(self,env,url='',confirm=None):
        from nodepilot_pkg.deploy import deploy
        root,launcher,units,m,source,sd=env
        with patch('nodepilot_pkg.deploy.supported'),patch('nodepilot_pkg.deploy.run',side_effect=sd):
            deploy(source,'b'*64,url,root,launcher,units,m,confirm_source_change=confirm)

    def test_first_official_and_local_install_provenance(self):
        for url,kind in ((URL,'official-release'),('','local-source')):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as d:
                env=self.environment(Path(d),first=True);self.install(env,url)
                record=read_json(env[0]/'source.json');self.assertEqual(record['provenance'],kind)
                self.assertEqual(update.active_repository(record),'example/project' if url else None)

    def test_official_to_local_requires_consent_preserves_inactive_old_binding(self):
        for legacy in (True,False):
            with self.subTest(legacy=legacy),tempfile.TemporaryDirectory() as d:
                env=self.environment(Path(d));root=env[0]
                prior={'repository':'example/project'} if legacy else {'schema':1,'provenance':'official-release','repository':'example/project'}
                atomic_json(root/'source.json',prior);before=(root/'source.json').read_bytes()
                for confirm in (None,lambda old,new:False):
                    with self.assertRaises(PilotError):self.install(env,confirm=confirm)
                    self.assertEqual((root/'source.json').read_bytes(),before)
                    self.assertEqual(list((env[3].state/'backups').iterdir()),[])
                self.install(env,confirm=lambda old,new:True)
                self.assertEqual(read_json(root/'source.json'),{'schema':1,'provenance':'local-source','previous_binding':{'repository':'example/project'}})
                with self.assertRaises(PilotError):update.bound_repository(root)

    def test_local_to_official_and_repository_switch_require_authorization(self):
        with tempfile.TemporaryDirectory() as d:
            env=self.environment(Path(d));self.install(env);root=env[0]
            with self.assertRaises(PilotError):self.install(env,URL)
            self.install(env,URL,lambda old,new:True)
            self.assertEqual(update.bound_repository(root),'example/project')
            other=URL.replace('example/project','fork/project')
            with self.assertRaises(PilotError):self.install(env,other)
            self.install(env,other,lambda old,new:True)
            self.assertEqual(update.bound_repository(root),'fork/project')
            before=list((env[3].state/'backups').iterdir());self.install(env,other)
            self.assertEqual(list((env[3].state/'backups').iterdir()),before)

    def test_local_upgrade_keeps_local_history_and_failure_restores_exact_legacy_source(self):
        from nodepilot_pkg import deploy
        with tempfile.TemporaryDirectory() as d:
            env=self.environment(Path(d));root=env[0];data=b'{"repository": "example/project"}\n'
            (root/'source.json').write_bytes(data);original=deploy.checkpoint
            def fail(journal,record,step):
                original(journal,record,step)
                if step=='timer':raise PilotError('injected upgrade failure')
            with patch('nodepilot_pkg.deploy.checkpoint',side_effect=fail),self.assertRaises(PilotError):self.install(env,confirm=lambda old,new:True)
            self.assertEqual((root/'source.json').read_bytes(),data)
            self.install(env,confirm=lambda old,new:True);before=(root/'source.json').read_bytes()
            self.install(env);self.assertEqual((root/'source.json').read_bytes(),before)

    def test_source_intents_require_v2_so_old_managers_refuse_instead_of_ignoring_policy(self):
        from nodepilot_pkg import deploy
        with tempfile.TemporaryDirectory() as d:
            env=self.environment(Path(d));original=deploy.checkpoint
            def crash(journal,record,step):
                original(journal,record,step)
                if step=='candidate':raise SystemExit('source-aware intent persisted')
            with patch('nodepilot_pkg.deploy.checkpoint',side_effect=crash),self.assertRaises(SystemExit):self.install(env)
            root,launcher,units,m,source,sd=env;journal=root/'program-pending.json';record=read_json(journal)
            self.assertEqual(record['transaction_version'],2);self.assertIn('source_after',record)
            del record['source_after'];atomic_json(journal,record);before=journal.read_bytes()
            with patch('nodepilot_pkg.deploy.run',side_effect=sd),self.assertRaises(PilotError):deploy.recover_program(root,launcher,units,m)
            self.assertEqual(journal.read_bytes(),before)
            # A damaged old v1 intent must use the same repository validator.
            record['transaction_version']=1;record['repository']='../project';atomic_json(journal,record);before=journal.read_bytes();sd.calls.clear()
            with patch('nodepilot_pkg.deploy.run',side_effect=sd),self.assertRaises(PilotError):deploy.recover_program(root,launcher,units,m)
            self.assertEqual(journal.read_bytes(),before)
            self.assertFalse(any(c[0]=='python3' for c in sd.calls))
