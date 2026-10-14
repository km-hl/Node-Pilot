#!/usr/bin/env python3
"""Bounded cgroup matrix on the explicitly owned disposable stability state."""
import argparse
import secrets
import subprocess
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from nodepilot_pkg.common import PilotError, atomic_json, read_json, root_required, run
from tools.stability_smoke import state_path
from tools.caddy_smoke import manager_at


def main():
    p=argparse.ArgumentParser();p.add_argument('--duration',type=int,default=24);args=p.parse_args();root_required()
    if not 18<=args.duration<=60:p.error('duration 18..60')
    state=state_path();m=manager_at(state);nodes=read_json(state/'test-nodes.json')['nodes']
    results=[];report=state/'resource-matrix.json'
    with m.locked():
        for n in nodes:
            m.services.assert_owned(n);run(['systemctl','stop',n['service']])
        m.caddy.assert_owned();run(['systemctl','stop','nodepilot-caddy.service'])
    try:
        for limit in (256,512,1024):
            for protocol in ('reality','anytls','hysteria2','all'):
                unit='nodepilot-resource-v04-'+secrets.token_hex(6)
                worker=[sys.executable,str(ROOT/'tools/resource_probe.py'),'--private-dir',str(state),'--state-dir',str(state),
                        '--cores-root',str(state/'cores'),'--duration',str(args.duration),'--cold-binaries','--lifecycle']
                if protocol!='all':worker+=['--protocol',protocol]
                if protocol in {'all','reality'}:worker+=['--with-caddy']
                if limit==256 and protocol=='all':worker+=['--update-core','sing-box']
                (state/'resource-report.json').unlink(missing_ok=True)
                if run(['systemctl','show',unit+'.service','-p','FragmentPath','--value']):raise RuntimeError('unit collision')
                run(['systemd-run','--unit',unit,'--collect','-p',f'MemoryMax={limit}M','-p','MemorySwapMax=0',
                     '-p','CPUQuota=100%','-p','TasksMax=128',*worker])
                try:
                    deadline=time.monotonic()+180
                    while time.monotonic()<deadline:
                        if (state/'resource-report.json').is_file() and read_json(state/'resource-report.json').get('ready'):break
                        time.sleep(.25)
                    else:raise RuntimeError('resource readiness timeout')
                    for proto in (('reality','anytls','hysteria2') if protocol=='all' else (protocol,)):
                        run([sys.executable,ROOT/'tools/client_probe.py','--private-dir',state,'--protocol',proto,'--requests','2','--parallel','2','--https-check'],timeout=40)
                    while time.monotonic()<deadline:
                        stats=read_json(state/'resource-report.json')
                        if stats.get('completed') or stats.get('failed'):break
                        time.sleep(.5)
                    else:raise RuntimeError('resource completion timeout')
                    if not stats.get('completed') or stats.get('failed'):raise RuntimeError('resource worker failed')
                    entry={'limit_mib':limit,'protocol':protocol,'encrypted_transfers':'2MiB per protocol + HTTPS; same-host client outside cgroup',**stats}
                    results.append(entry);atomic_json(report,{'cases':results,'complete':False,'scope':'cgroup, not complete physical small-memory OS'})
                    print(f"{limit}MiB {protocol}: passed; peak={stats['kernel_memory_peak_bytes']} rss={stats['peak_sum_rss_kib']} swap={stats['peak_swap_current_bytes']}",flush=True)
                finally:
                    if run(['systemctl','show',unit+'.service','-p','FragmentPath','--value']):
                        try:
                            run(['systemctl','stop',unit+'.service'])
                        except PilotError:
                            if run(['systemctl','show',unit+'.service','-p','FragmentPath','--value']):raise
        atomic_json(report,{'cases':results,'complete':True,'scope':'cgroup, not complete physical small-memory OS'})
    finally:
        with m.locked():
            m.caddy.sync(m.nodes())
            for n in m.nodes():m.services.restart(n)


if __name__=='__main__':main()
