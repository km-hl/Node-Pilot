"""Optional local REALITY client + verified HTTPS; never claims public reachability."""
import hashlib
import http.client
import json
import socket
import ssl
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .common import PilotError, atomic_json, private_dir, read_json


def fingerprint(n):
    return hashlib.sha256(json.dumps(n,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def latest(manager,n):
    path = manager.state/'probes'/(n['id']+'.json')
    if path.is_file():
        record = read_json(path)
        if record.get('fingerprint')==fingerprint(n):
            return record
    return {'status':'当前修订未测试；公网未验证'}


def client_config(n,port):
    r = n['reality']
    address = '::1' if n['listen']=='::' else '127.0.0.1' if n['listen']=='0.0.0.0' else n['listen']
    return {'log':{'loglevel':'warning'},'inbounds':[{'listen':'127.0.0.1','port':port,'protocol':'http','settings':{}}],
            'outbounds':[{'protocol':'vless','settings':{'vnext':[{'address':address,'port':n['port'],
                         'users':[{'id':r['uuid'],'encryption':'none','flow':'xtls-rprx-vision'}]}]},
                         'streamSettings':{'network':'tcp','security':'reality','realitySettings':{
                         'serverName':n['sni'],'fingerprint':r['fingerprint'],'password':r['public_key'],'shortId':r['short_id']}}}]}


def classify(error,log):
    if isinstance(error,(TimeoutError,socket.timeout,subprocess.TimeoutExpired)):
        return 'timeout'
    if isinstance(error,ssl.SSLError):
        return 'tls'
    lower=log.lower()
    if 'failed to verify certificate' in lower or 'invalid user' in lower or 'authentication' in lower:
        return 'authentication_or_reality_handshake'
    return 'connection_or_handshake'  # Core logs do not always distinguish bad auth from target failure.


def check(manager,n):
    if n['protocol']!='reality':
        raise PilotError('此增强检测只用于 Reality；AnyTLS/HY2 请用真实客户端验证。')
    manager.services.assert_owned(n)
    if not manager.services.active(n):
        raise PilotError('节点服务未运行。')
    record={'fingerprint':fingerprint(n),'at':datetime.now(timezone.utc).isoformat(),'public_verified':False,'scope':'local'}
    directory=private_dir(manager.state/'probes')
    with tempfile.TemporaryDirectory(prefix='client-',dir=directory) as d:
        tmp=Path(d)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        atomic_json(tmp/'client.json',client_config(n,port))
        manager.cores.check(n,tmp/'client.json')
        with (tmp/'client.log').open('wb') as log:
            process=subprocess.Popen([str(manager.cores.binary('xray')),'run','-config',str(tmp/'client.json')],stdout=log,stderr=log,
                                     env={**__import__('os').environ,'GOMAXPROCS':'1','GOMEMLIMIT':'32MiB','GOGC':'50'})
            try:
                for _ in range(30):
                    if process.poll() is not None:
                        raise PilotError('测试客户端启动失败')
                    try:
                        with socket.create_connection(('127.0.0.1',port),timeout=.2):
                            break
                    except OSError:
                        time.sleep(.1)
                connection=http.client.HTTPSConnection('127.0.0.1',port,timeout=15,context=ssl.create_default_context())
                try:
                    connection.set_tunnel('example.com',443)
                    connection.request('GET','/',headers={'Host':'example.com'})
                    response=connection.getresponse(); data=response.read(16384)
                    if response.status!=200 or not data:
                        raise PilotError('代理 HTTPS 没有返回有效数据')
                    record.update(status='本机 Reality 握手和代理 HTTPS 数据通过；公网未验证',kind='passed',bytes=len(data))
                finally:
                    connection.close()
            except (OSError,PilotError,http.client.HTTPException) as error:
                log.flush()
                text=(tmp/'client.log').read_text(errors='replace')[-8192:]
                kind=classify(error,text)
                record.update(status=f'本机 Reality 测试未通过：{kind}；公网未验证',kind=kind)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()
    atomic_json(directory/(n['id']+'.json'),record)
    return record
