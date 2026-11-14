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
                 **{'packet-encoding':'xudp','client-fingerprint':r['fingerprint'],
                    'reality-opts':{'public-key':r['public_key'],'short-id':r['short_id'],
                                    'support-x25519mlkem768':True}})
    else:
        p.update(password=n['password'],sni=n['sni'])
        if n['protocol']=='anytls':
            p['client-fingerprint']='chrome'
        if n['protocol']=='hysteria2':
            if n.get('obfs'):
                p.update(obfs='salamander',**{'obfs-password':n['obfs']['password']})
            if n.get('mport'):
                p['ports']=n['mport']
                p['hop-interval']=n.get('hop_interval',30)
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


def mihomo_full_yaml(nodes, mixed_port=7890):
    lines = [
        '# PRIVATE: contains credentials. Full Mihomo client profile.',
        '# Self-signed/imported private CA: manually install certificate trust on the client.',
        '# skip-cert-verify remains false. Validate with mihomo -t before use.',
        f'mixed-port: {mixed_port}',
        'allow-lan: false',
        'mode: rule',
        'log-level: warning',
        'ipv6: true',
        'unified-delay: true',
        'tcp-concurrent: true',
        'proxies:',
    ]
    if not nodes:
        lines[-1] = 'proxies: []'
        names = []
    else:
        names = []
        for n in nodes:
            p = mihomo_proxy(n)
            names.append(p['name'])
            lines.append('  - ' + json.dumps(p, ensure_ascii=False))
    proxy_list = [json.dumps(name, ensure_ascii=False) for name in names] + ['DIRECT']
    lines.extend([
        'proxy-groups:',
        f'  - {{"name": "NodePilot", "type": "select", "proxies": [{", ".join(proxy_list)}]}}',
        'rules:',
        '  - "IP-CIDR,127.0.0.0/8,DIRECT,no-resolve"',
        '  - "IP-CIDR,10.0.0.0/8,DIRECT,no-resolve"',
        '  - "IP-CIDR,172.16.0.0/12,DIRECT,no-resolve"',
        '  - "IP-CIDR,192.168.0.0/16,DIRECT,no-resolve"',
        '  - "IP-CIDR6,fc00::/7,DIRECT,no-resolve"',
        '  - "MATCH,NodePilot"',
    ])
    return '\n'.join(lines) + '\n'
