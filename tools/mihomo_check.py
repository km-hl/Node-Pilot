#!/usr/bin/env python3
"""Optional verified official Mihomo YAML parser check; no GUI compatibility claim."""
import argparse
import gzip
import json
import secrets
import sys
import tempfile
import uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from nodepilot_pkg.common import PilotError, atomic_bytes, atomic_json, private_dir, read_json, run
from nodepilot_pkg.cores import verify_elf
from nodepilot_pkg.download import fetch
from nodepilot_pkg.exporters import mihomo_proxy, mihomo_yaml
from nodepilot_pkg.model import new_node
from nodepilot_pkg.releases import release
from nodepilot_pkg.system import architecture


def asset_name(arch, tag):
    # The unqualified amd64 build requires x86-64-v3; older VPS CPUs need v1.
    target = 'amd64-v1' if arch == 'amd64' else arch
    return f'mihomo-linux-{target}-{tag}.gz'


def main():
    p=argparse.ArgumentParser();p.add_argument('--download',action='store_true');p.add_argument('--private-nodes',type=Path);args=p.parse_args()
    root=private_dir(ROOT/'.test-tools/mihomo');binary=root/'mihomo'
    if args.download:
        record=release('MetaCubeX/mihomo');tag=record['tag_name'];arch=architecture()
        name=asset_name(arch,tag)
        assets=[a for a in record['assets'] if a['name']==name]
        if len(assets)!=1:raise PilotError('未找到官方对应架构的标准 Mihomo 附件。')
        with tempfile.TemporaryDirectory(prefix='download-',dir=root) as d:
            import re
            asset=assets[0];url=asset.get('browser_download_url','');digest=asset.get('digest','') or ''
            if not url.startswith('https://github.com/MetaCubeX/mihomo/releases/download/'+tag+'/') or not re.fullmatch(r'sha256:[0-9a-fA-F]{64}',digest):
                raise PilotError('Mihomo 官方来源或附件 SHA-256 不可验证。')
            archive=Path(d)/'mihomo.gz';fetch(url,archive,digest[7:],limit=100*1024*1024)
            candidate=Path(d)/'mihomo';total=0
            with gzip.open(archive,'rb') as source,candidate.open('wb') as target:
                while True:
                    data=source.read(256*1024)
                    if not data:break
                    total+=len(data)
                    if total>180*1024*1024:raise PilotError('Mihomo 内核大小超限。')
                    target.write(data)
            verify_elf(candidate,arch);candidate.chmod(0o700)
            if tag.lstrip('v') not in run([candidate,'-v']):raise PilotError('Mihomo 报告版本不匹配。')
            import shutil
            shutil.copy2(candidate,binary);atomic_json(root/'verified.json',{'tag':tag,'digest':assets[0]['digest']})
    if not binary.is_file() or not (root/'verified.json').is_file():raise PilotError('请先使用 --download 校验官方 Mihomo。')
    if args.private_nodes:nodes=read_json(args.private_nodes)['nodes']
    else:
        nodes=[]
        for protocol in ('reality','anytls','hysteria2'):
            n=new_node(protocol,'YAML " 编码 # : '+protocol,'2001:4860:4860::8888',14443,sni='example.com')
            if protocol=='reality':
                from nodepilot_pkg.cores import Cores
                private,public=Cores(ROOT/'.test-tools/cores').keypair()
                n['reality']=dict(private_key=private,public_key=public,uuid=str(uuid.uuid4()),target='example.com:443',
                                  server_names=[n['sni']],short_id=secrets.token_hex(8),fingerprint='chrome')
            else:
                n['tls']=dict(mode='selfsigned',certificate='/nonexistent',key='/nonexistent')
                if protocol=='hysteria2':n['obfs']={'type':'salamander','password':secrets.token_urlsafe(32)}
            nodes.append(n)
    with tempfile.TemporaryDirectory(prefix='config-',dir=root) as d:
        path=Path(d)/'config.yaml'
        names=[mihomo_proxy(n)['name'] for n in nodes]
        text='mixed-port: 18980\nmode: rule\n'+mihomo_yaml(nodes)+'proxy-groups:\n  - '+json.dumps({'name':'NodePilot-test','type':'select','proxies':names},ensure_ascii=False)+'\nrules: ["MATCH,NodePilot-test"]\n'
        atomic_bytes(path,text.encode())
        run([binary,'-t','-d',d,'-f',path])
    print('Mihomo '+read_json(root/'verified.json')['tag']+f' official parser: {len(nodes)} exported proxies OK; GUI/public connectivity not claimed')


if __name__=='__main__':main()
