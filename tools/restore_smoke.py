#!/usr/bin/env python3
"""Disposable real systemd restore/preflight/credential-policy acceptance."""
import argparse
import copy
import secrets
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from nodepilot_pkg import certificates
from nodepilot_pkg.common import PilotError,atomic_json,read_json,root_required,run
from nodepilot_pkg.cores import Cores
from nodepilot_pkg.dns_credentials import store_token,credential_path,profiles
from nodepilot_pkg.manager import Manager,OWNER
from nodepilot_pkg.model import new_node
from nodepilot_pkg.services import Services
from nodepilot_pkg.share import share
from tools.service_smoke import free_port


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--disposable',action='store_true',required=True);parser.parse_args();root_required()
    state=Path('/run')/('nodepilot-restore-test-'+secrets.token_hex(6))
    cores=Cores(ROOT/'.test-tools/cores');services=Services(state,Path('/run/systemd/system'),cores.root)
    manager=Manager(state,cores,services)
    try:
        with manager.locked():
            nodes=[]
            for protocol in ('anytls','hysteria2'):
                n=new_node(protocol,'restore local smoke','8.8.8.8',free_port(),'127.0.0.1','example.com')
                n['tls']={'mode':'selfsigned','certificate':'/nonexistent','key':'/nonexistent'}
                nodes.append(manager.apply(n))
            backup=manager.backup();bundle=read_json(backup)
            for n in nodes:manager.delete(n['id'])
            profile=store_token(state,'test_only_'+'r'*30);token=credential_path(state,profile).read_bytes()
            for entry in bundle['nodes']:
                entry['node']['tls'].update(mode='acme',challenge='dns-cloudflare',dns={'credential_id':profile,'zone_id':'0'*32})
            atomic_json(backup,bundle)
            native=cores.check
            for failure in ('certificate','native'):
                damaged=copy.deepcopy(bundle)
                if failure=='certificate':damaged['nodes'][1]['files']['key.pem']='invalid!'
                bad=state/'damaged.json';atomic_json(bad,damaged)
                with patch.object(services,'restart') as restart, \
                     patch.object(cores,'check',side_effect=PilotError('incompatible core fixture') if failure=='native' else native):
                    try:manager.restore(bad)
                    except PilotError:pass
                    else:raise AssertionError('bad bundle accepted')
                    restart.assert_not_called()
                assert not manager.nodes()
                print('whole-bundle',failure,'preflight refuses before any service restart OK',flush=True)
            preview=manager.preview_restore(backup)
            assert all('自动续期已关闭' in n['notice'] for n in preview['nodes'])
            manager.restore(backup,expected_digest=preview['sha256'])
            for n in manager.nodes():
                assert n['tls']['mode']=='import' and not profiles(n) and not certificates.due(n)
                assert services.runtime(n)['healthy']
                original=next(entry['node'] for entry in bundle['nodes'] if entry['node']['id']==n['id'])
                assert share(n)==share(original)
            assert token==credential_path(state,profile).read_bytes()
            print('two REAL services/listeners, URI preserved; DNS ACME -> import notice and token unchanged OK (no issuance)',flush=True)
            before={n['id']:(run(['systemctl','show',n['service'],'-p','MainPID','--value']),manager.revision(n['id'])) for n in manager.nodes()}
            try:manager.restore(backup)
            except PilotError:pass
            else:raise AssertionError('existing nodes overwritten')
            assert before=={n['id']:(run(['systemctl','show',n['service'],'-p','MainPID','--value']),manager.revision(n['id'])) for n in manager.nodes()}
            print('existing nodes refusal preserves REAL PIDs/revisions OK',flush=True)
    finally:
        if state.exists():
            assert state.resolve()==state and state.parent==Path('/run') and (state/'.owner').read_text()==OWNER
            with manager.locked():
                manager.recover()
                for n in manager.nodes():manager.delete(n['id'])
            shutil.rmtree(state)
    print('owned restore state and services cleaned; no public ACME/VPS/reboot claimed',flush=True)


if __name__=='__main__':main()
