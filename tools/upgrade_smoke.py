#!/usr/bin/env python3
"""Authorized isolated v0.3 -> candidate state/unit migration, never production."""
import argparse
import copy
import hashlib
import secrets
import shutil
import socket
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from nodepilot_pkg.common import PilotError,private_dir,read_json,root_required,run
from nodepilot_pkg.cores import Cores
from nodepilot_pkg.manager import OWNER
from nodepilot_pkg.share import share

LEGACY_SCRIPT=r'''
import json,secrets,socket,sys,uuid
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from nodepilot_pkg.manager import Manager
from nodepilot_pkg.cores import Cores
from nodepilot_pkg.services import Services
from nodepilot_pkg.model import new_node
from nodepilot_pkg.common import atomic_json
base=Path(sys.argv[2]);cores=Cores(base/'cores')
m=Manager(base/'state',cores,Services(base/'state',Path('/run/systemd/system'),cores.root))
def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]
with m.locked():
    nodes=[]
    for protocol in ('reality','anytls','hysteria2'):
        n=new_node(protocol,'legacy compatibility test','8.8.8.8',port(),'127.0.0.1','example.com')
        if protocol=='reality':
            private,public=cores.keypair()
            n['reality']=dict(private_key=private,public_key=public,uuid=str(uuid.uuid4()),
                target='example.com:443',server_names=['example.com'],short_id=secrets.token_hex(8),fingerprint='chrome')
        else:n['tls']=dict(mode='selfsigned',certificate='/nonexistent',key='/nonexistent')
        nodes.append(m.apply(n,probe=False))
    atomic_json(base/'legacy-nodes.json',nodes)
print('legacy native services created')
'''


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--legacy-app',type=Path,required=True);args=parser.parse_args()
    root_required()
    if run([sys.executable,args.legacy_app/'nodepilot','--version'])!='0.3.0':
        raise PilotError('需要已校验、可信的 v0.3.0 程序目录。')
    base=Path('/var/lib')/('nodepilot-compatibility-test-'+secrets.token_hex(6))
    private_dir(base);(base/'.test-owner').write_text('NodePilot disposable compatibility\n')
    from nodepilot_pkg.services import Services
    from nodepilot_pkg.manager import Manager
    cores=Cores(base/'cores');m=Manager(base/'state',cores,Services(base/'state',Path('/run/systemd/system'),cores.root))
    try:
        source=Cores(ROOT/'.test-tools/cores')
        for name in ('xray','sing-box'):
            binary=source.binary(name);version=read_json(binary.parent/'verified.json')['version']
            destination=private_dir(base/'cores'/name/(version+'-'+secrets.token_hex(4)))
            shutil.copy2(binary,destination/name);shutil.copy2(binary.parent/'verified.json',destination/'verified.json')
            assert hashlib.sha256(binary.read_bytes()).digest()==hashlib.sha256((destination/name).read_bytes()).digest()
            cores.switch(name,destination)
        run([sys.executable,'-c',LEGACY_SCRIPT,args.legacy_app,base],timeout=90)
        old=read_json(base/'legacy-nodes.json');links={n['id']:share(n) for n in old}
        assert (m.state/'.owner').read_text()=='NodePilot schema=1\n'
        with m.locked():
            for node in old:
                assert m.get(node['id'])==node and share(m.get(node['id']))==links[node['id']]
                cores.check(node,m.revision(node['id'])/'config.json')
                assert m.services.runtime(node)['healthy']
            node=next(n for n in old if n['protocol']=='anytls');changed=copy.deepcopy(node)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0));changed['port']=sock.getsockname()[1]
            m.apply(changed,probe=False);m.apply(node,probe=False)
            assert m.services.runtime(node)['healthy'] and share(m.get(node['id']))==links[node['id']]
        assert (m.state/'.owner').read_text()==OWNER
        script="import sys;sys.path.insert(0,sys.argv[1]);from nodepilot_pkg.manager import Manager;Manager(sys.argv[2]).initialize()"
        import subprocess
        result=subprocess.run([sys.executable,'-c',script,str(args.legacy_app),str(m.state)],capture_output=True)
        assert result.returncode!=0,'old manager must refuse schema=2'
        print('v0.3 three nodes: metadata/URI/native checks/PID listeners preserved; edit+rollback and old manager schema refusal OK')
    finally:
        if (m.state/'.owner').exists():
            with m.locked():
                m.recover()
                for node in m.nodes():m.delete(node['id'])
        assert base.resolve()==base and base.parent==Path('/var/lib') and base.name.startswith('nodepilot-compatibility-test-')
        assert (base/'.test-owner').read_text()=='NodePilot disposable compatibility\n'
        shutil.rmtree(base)
        print('isolated legacy services/state cleaned; no production path changed')


if __name__=='__main__':main()
