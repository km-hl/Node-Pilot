#!/usr/bin/env python3
"""Real Caddy family-specific high-port listeners; no ACME or system changes."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from nodepilot_pkg.cores import Cores
from nodepilot_pkg.services import listening


def main():
    binary=Cores(ROOT/'.test-tools/cores').binary('caddy')
    with tempfile.TemporaryDirectory(prefix='nodepilot-http-families-') as tmp:
        root=Path(tmp);logs=root/'output.log'
        # IPv4-only always runs, IPv6/dual explicitly fail if unavailable.
        for families in ((4,),(6,),(4,6)):
            addresses=[]
            for version in families:
                family=socket.AF_INET if version==4 else socket.AF_INET6
                address='127.0.0.1' if version==4 else '::1'
                with socket.socket(family,socket.SOCK_STREAM) as sock:
                    if version==6:sock.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,1)
                    sock.bind((address,0));port=sock.getsockname()[1]
                addresses.append((version,address,port))
            listeners=[f'tcp4/{ip}:{port}' if v==4 else f'tcp6/[{ip}]:{port}' for v,ip,port in addresses]
            config={'admin':{'disabled':True},'apps':{'http':{'servers':{'family_test':{
                'listen':listeners,'automatic_https':{'disable':True},'protocols':['h1'],
                'routes':[{'handle':[{'handler':'static_response','body':'family-ok'}]}]}}}}}
            path=root/'config.json';path.write_text(json.dumps(config))
            with logs.open('wb') as log:
                process=subprocess.Popen([binary,'run','--config',path],stdout=log,stderr=log,
                    env={**os.environ,'GOMEMLIMIT':'32MiB','GOGC':'50','GOMAXPROCS':'1'})
                try:
                    for version,ip,port in addresses:
                        deadline=time.monotonic()+5
                        while True:
                            if process.poll() is not None:raise AssertionError('Caddy exited before family checks')
                            try:
                                with socket.socket(socket.AF_INET if version==4 else socket.AF_INET6) as client:
                                    client.settimeout(1);client.connect((ip,port))
                                    client.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
                                    response=bytearray()
                                    while len(response)<4096:
                                        block=client.recv(4096-len(response))
                                        if not block:break
                                        response.extend(block)
                                    assert b'family-ok' in response
                                break
                            except OSError:
                                if time.monotonic()>=deadline:raise
                                time.sleep(.05)
                        assert listening({'transport':'tcp','listen':ip,'port':port},process.pid,strict_family=True)
                    print('Caddy real listeners/HTTP and PID ownership OK: '+str(families),flush=True)
                finally:
                    process.terminate();process.wait(timeout=10)
    print('Local high-port validation only; real HTTP-01 issuance, public TCP80 and cloud firewall remain unverified.')


if __name__=='__main__':main()
