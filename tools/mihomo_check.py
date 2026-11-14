#!/usr/bin/env python3
"""Optional verified official Mihomo YAML parser check; no GUI compatibility claim."""
import argparse
import gzip
import http.server
import json
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from nodepilot_pkg.common import PilotError, atomic_bytes, atomic_json, private_dir, read_json, run
from nodepilot_pkg.config import generate
from nodepilot_pkg.cores import Cores, verify_elf
from nodepilot_pkg.download import fetch
from nodepilot_pkg.exporters import mihomo_full_yaml, mihomo_proxy, mihomo_yaml
from nodepilot_pkg.model import new_node
from nodepilot_pkg.releases import release
from nodepilot_pkg.system import architecture


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def live_reality_check(binary, cores):
    private, public = cores.keypair()
    srv_port, mh_port = _free_port(), _free_port()
    n = new_node('reality', 'mihomo-live', '8.8.8.8', srv_port, listen='127.0.0.1', sni='www.apple.com')
    n['reality'] = dict(private_key=private, public_key=public, uuid=str(uuid.uuid4()),
                        target='www.apple.com:443', server_names=[n['sni']],
                        short_id=secrets.token_hex(4), fingerprint='chrome')

    class EchoHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'NODEPILOT_MIHOMO_OK')
        def log_message(self, *args):
            pass

    echo = http.server.ThreadingHTTPServer(('127.0.0.1', 0), EchoHandler)
    echo_port = echo.server_address[1]
    threading.Thread(target=echo.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='mihomo-live-', dir=binary.parent) as d:
            tmp = Path(d)
            srv_data = generate(n)
            srv_cfg = tmp / 'sb.json'
            atomic_json(srv_cfg, srv_data)
            cores.check(n, srv_cfg)
            srv_data['log'] = {'level': 'info'}
            atomic_json(srv_cfg, srv_data)
            mh_raw = mihomo_full_yaml([n], mixed_port=mh_port)
            mh_stock = tmp / 'mihomo-stock.yaml'
            atomic_bytes(mh_stock, mh_raw.encode())
            run([binary, '-t', '-d', d, '-f', mh_stock])
            live_yaml = mh_raw.replace('"server": "8.8.8.8"', '"server": "127.0.0.1"').replace(
                '  - "IP-CIDR,127.0.0.0/8,DIRECT,no-resolve"\n', ''
            )
            mh_cfg = tmp / 'mihomo.yaml'
            atomic_bytes(mh_cfg, live_yaml.encode())
            run([binary, '-t', '-d', d, '-f', mh_cfg])
            sb_proc = subprocess.Popen([cores.binary('sing-box'), 'run', '-c', str(srv_cfg)],
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            mh_proc = None
            try:
                time.sleep(0.4)
                mh_proc = subprocess.Popen([str(binary), '-d', d, '-f', str(mh_cfg)],
                                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({'http': f'http://127.0.0.1:{mh_port}'}))
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    if mh_proc.poll() is not None:
                        raise PilotError('Mihomo 测试进程提前退出: ' + mh_proc.stdout.read().decode(errors='replace'))
                    try:
                        body = opener.open(f'http://127.0.0.1:{echo_port}/', timeout=2).read()
                        if body == b'NODEPILOT_MIHOMO_OK':
                            if sb_proc.poll() is not None:
                                raise PilotError('sing-box 测试进程意外退出')
                            sb_proc.terminate(); sb_proc.wait(3)
                            sb_log = sb_proc.stdout.read().decode(errors='replace')
                            if f'127.0.0.1:{echo_port}' not in sb_log:
                                raise PilotError('Mihomo 未通过 sing-box Reality 隧道转发流量: ' + sb_log)
                            return
                    except PilotError:
                        raise
                    except Exception:
                        time.sleep(0.2)
                raise PilotError('Mihomo <-> sing-box Reality 本地回环握手超时。')
            finally:
                if mh_proc and mh_proc.poll() is None:
                    mh_proc.terminate(); mh_proc.wait(3)
                if sb_proc.poll() is None:
                    sb_proc.terminate(); sb_proc.wait(3)
    finally:
        echo.shutdown()
        echo.server_close()


def asset_name(arch, tag):
    # The unqualified amd64 build requires x86-64-v3; older VPS CPUs need v1.
    target = 'amd64-v1' if arch == 'amd64' else arch
    return f'mihomo-linux-{target}-{tag}.gz'


def main():
    p=argparse.ArgumentParser();p.add_argument('--download',action='store_true');p.add_argument('--live',action='store_true');p.add_argument('--private-nodes',type=Path);args=p.parse_args()
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
        full_path = Path(d) / 'full.yaml'
        atomic_bytes(full_path, mihomo_full_yaml(nodes, mixed_port=18981).encode())
        run([binary, '-t', '-d', d, '-f', full_path])
    if args.live:
        live_reality_check(binary, Cores(ROOT / '.test-tools/cores'))
        print('Mihomo <-> sing-box Reality live loopback handshake OK')
    print('Mihomo '+read_json(root/'verified.json')['tag']+f' official parser: {len(nodes)} exported proxies OK; GUI/public connectivity not claimed')


if __name__=='__main__':main()
