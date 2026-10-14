#!/usr/bin/env python3
"""Authorized disposable v0.4 lifecycle checks; never targets production state."""
import argparse
import copy
import hashlib
import os
import secrets
import shutil
import socket
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from nodepilot_pkg.caddy import UNIT
from nodepilot_pkg.common import PilotError, atomic_bytes, atomic_json, private_dir, read_json, root_required, run
from nodepilot_pkg.connectivity import check
from nodepilot_pkg.cores import Cores, verify_elf
from nodepilot_pkg.installation import LAUNCHER, recover_uninstall, uninstall
from nodepilot_pkg.manager import OWNER
from nodepilot_pkg.model import host,new_node
from nodepilot_pkg.share import share
from tools.caddy_smoke import manager_at


def free_port():
    with socket.socket() as sock:
        sock.bind(('0.0.0.0',0));return sock.getsockname()[1]


def digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(256*1024),b''): value.update(block)
    return value.hexdigest()


def state_path():
    state=Path(read_json(ROOT/'.test-tools/stability-state.json')['state'])
    if state.resolve()!=state or state.parent!=Path('/var/lib') or not state.name.startswith('nodepilot-stability-test-') or (state/'.owner').read_text()!=OWNER:
        raise PilotError('拒绝越界测试目录。')
    return state


def setup():
    if run(['systemctl','show',UNIT,'-p','FragmentPath','--value']):
        raise PilotError('已有同名 Caddy 单元；拒绝临时测试。')
    state=Path('/var/lib')/('nodepilot-stability-test-'+secrets.token_hex(6))
    m=manager_at(state);m.initialize()
    atomic_json(ROOT/'.test-tools/stability-state.json',{'state':str(state)})
    source=Cores(ROOT/'.test-tools/cores')
    try:
        for core in ('xray','sing-box','caddy'):
            original=source.binary(core); version=read_json(original.parent/'verified.json')['version']
            destination=private_dir(state/'cores'/core/(version+'-'+secrets.token_hex(4)))
            shutil.copy2(original,destination/core)
            assert digest(original)==digest(destination/core)
            verify_elf(destination/core,'amd64')
            shutil.copy2(original.parent/'verified.json',destination/'verified.json')
            m.cores.switch(core,destination)
        server=host(os.environ['SSH_CONNECTION'].split()[2],True)
        with m.locked():
            nodes=[]
            reality_port=free_port()
            for protocol in ('reality','anytls','hysteria2'):
                port=reality_port if protocol in {'reality','hysteria2'} else free_port()
                n=new_node(protocol,'v04 disposable '+protocol,server,port,'0.0.0.0','stability.example.com')
                if protocol=='reality':
                    private,public=m.cores.keypair(); local=free_port()
                    n['reality']=dict(mode='self',private_key=private,public_key=public,uuid=str(uuid.uuid4()),target=f'127.0.0.1:{local}',
                        server_names=[n['sni']],short_id=secrets.token_hex(8),fingerprint='chrome')
                    n['caddy']=dict(domain=n['sni'],port=local,certificate_mode='selfsigned',certificate='/nonexistent',key='/nonexistent',upstream='')
                else:
                    n['tls']=dict(mode='selfsigned',certificate='/nonexistent',key='/nonexistent')
                    if protocol=='hysteria2': n['obfs']={'type':'salamander','password':secrets.token_urlsafe(32)}
                nodes.append(m.apply(n,probe=False))
            atomic_json(state/'test-nodes.json',{'nodes':nodes})
            assert all(m.services.runtime(n)['healthy'] for n in nodes)
            result=check(m,nodes[0]);assert result['kind']=='passed',result['status']
            print('three native services: owned TCP/UDP listeners + local Reality verified HTTPS OK',flush=True)
            # Real certificate transaction failure, isolated from real ACME providers.
            old=nodes[1];revision=m.revision(old['id']);changed=copy.deepcopy(old)
            changed['tls'].update(mode='acme',domain=old['sni'])
            with patch('nodepilot_pkg.certificates.acme',side_effect=PilotError('injected renewal network failure')):
                try:m.apply(changed,probe=False,renew=True)
                except PilotError:pass
                else:raise AssertionError('renewal failure expected')
            assert m.get(old['id'])==old and m.revision(old['id'])==revision and m.services.runtime(old)['healthy']
            print('failed certificate renewal retains old revision/certificate/live listener OK',flush=True)
            program=private_dir(state/'program');atomic_bytes(program/'.owner',OWNER.encode())
            launcher=state/'test-launcher';atomic_bytes(launcher,LAUNCHER.encode())
            removed=m.services.remove
            calls=0
            def fail_second(node):
                nonlocal calls
                calls+=1
                if calls==2:raise PilotError('injected second service stop failure')
                return removed(node)
            with patch.object(m.services,'remove',side_effect=fail_second):
                try:uninstall(m,program,launcher,m.services.units)
                except PilotError:pass
                else:raise AssertionError('uninstall failure expected')
            assert all(m.services.runtime(n)['healthy'] for n in nodes)
            assert read_json(state/'uninstall.json')['phase']=='rolled_back'
            print('real partial uninstall failure restores all original services/listeners OK',flush=True)
        import subprocess
        port=free_port()
        code="import http.server,sys; data=b'S'*1048576\nclass H(http.server.BaseHTTPRequestHandler):\n def do_GET(self):\n  self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)\n def log_message(self,*args):pass\nhttp.server.ThreadingHTTPServer(('127.0.0.1',int(sys.argv[1])),H).serve_forever()"
        log=(state/'payload.log').open('wb');p=subprocess.Popen([sys.executable,'-c',code,str(port)],stdout=log,stderr=log,start_new_session=True);log.close()
        atomic_json(state/'payload-process.json',{'pid':p.pid})
        atomic_json(state/'payload.json',{'port':port,'bytes':1048576,'sha256':hashlib.sha256(b'S'*1048576).hexdigest()})
        atomic_json(state/'clients.json',{'nodes':[{'protocol':n['protocol'],'uri':share(n)} for n in nodes]})
        for n in nodes:
            if n['protocol']!='reality':atomic_bytes(state/(n['protocol']+'-certificate.pem'),Path(n['tls']['certificate']).read_bytes())
        print('disposable stability state ready: '+str(state),flush=True)
    except BaseException:
        cleanup(state)
        raise


def cleanup(state=None):
    state=state or state_path();m=manager_at(state)
    with m.locked():
        m.recover()
        for n in m.nodes():m.delete(n['id'])
    if (state/'payload-process.json').is_file():
        pid=read_json(state/'payload-process.json')['pid'];p=Path(f'/proc/{pid}/cmdline')
        if p.exists() and b'ThreadingHTTPServer' in p.read_bytes():os.kill(pid,15)
    assert state.resolve()==state and state.parent==Path('/var/lib') and state.name.startswith('nodepilot-stability-test-') and (state/'.owner').read_text()==OWNER
    shutil.rmtree(state)
    print('all disposable stability services, certificates and state cleaned',flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=('setup','cleanup','uninstall'));args=p.parse_args();root_required()
    if args.action=='setup':setup()
    elif args.action=='cleanup':cleanup()
    else:
        state=state_path();m=manager_at(state)
        with m.locked():
            with patch('nodepilot_pkg.installation.remove_program_files',side_effect=PilotError('final cleanup interruption')):
                try:uninstall(m,state/'program',state/'test-launcher',m.services.units)
                except PilotError:pass
                else:raise AssertionError('interruption expected')
            assert read_json(state/'uninstall.json')['phase']=='finalizing'
            assert recover_uninstall(m,state/'program',state/'test-launcher',m.services.units)
            assert not m.nodes()
            assert read_json(state/'uninstall.json')['phase']=='complete'
            print('real final uninstall interruption resumes owned cleanup; private backup retained',flush=True)


if __name__=='__main__':main()
