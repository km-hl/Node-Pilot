"""Mihomo proxy entries, JSON-quoted YAML without third-party runtime dependencies."""
import json
from .model import validate


def mihomo_proxy(n):
    validate(n)
    p={'name':n['name']+' ['+n['id']+']','type':'vless' if n['protocol']=='reality' else n['protocol'],
       'server':n['server'],'port':n['port'],'udp':True,'skip-cert-verify':False}
    if n['protocol']=='reality':
        r=n['reality']
        p.update(uuid=r['uuid'],flow='xtls-rprx-vision',network='tcp',tls=True,servername=n['sni'],
                 **{'client-fingerprint':r['fingerprint'],'reality-opts':{'public-key':r['public_key'],'short-id':r['short_id']}})
    else:
        p.update(password=n['password'],sni=n['sni'])
        if n['protocol']=='anytls':
            p['client-fingerprint']='chrome'
        if n['protocol']=='hysteria2' and n.get('obfs'):
            p.update(obfs='salamander',**{'obfs-password':n['obfs']['password']})
    return p


def mihomo_yaml(nodes):
    # JSON flow mappings are valid YAML 1.2 and prevent credential/name injection.
    lines=['# PRIVATE: contains credentials. Merge proxies into a recent Mihomo configuration.',
           '# Self-signed/imported private CA: manually install certificate trust on the client.',
           '# skip-cert-verify remains false. GUI/version compatibility requires testing.','proxies:']
    if not nodes:
        return '\n'.join(lines[:-1]+['proxies: []'])+'\n'
    for n in nodes:
        lines.append('  - '+json.dumps(mihomo_proxy(n),ensure_ascii=False))
    return '\n'.join(lines)+'\n'
