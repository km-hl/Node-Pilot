#!/usr/bin/env python3
"""Disposable real PTY/systemd source transitions; HTTPS transport is simulated."""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from nodepilot_pkg.common import root_required
from nodepilot_pkg.project_update import active_repository, read_source
from tools.install_smoke import tty_command
from tools.package import package
from tools.program_smoke import cleanup, require_empty
from tools.bootstrap_smoke import snapshot


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--disposable',action='store_true',required=True);parser.parse_args()
    root_required();require_empty()
    with tempfile.TemporaryDirectory(prefix='nodepilot-source-smoke-') as tmp:
        base=Path(tmp);fork=base/'fork';fork.mkdir()
        shutil.copy2(ROOT/'install.sh',fork/'install.sh')
        shutil.copytree(ROOT/'nodepilot_pkg',fork/'nodepilot_pkg',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        shutil.copy2(ROOT/'nodepilot',fork/'nodepilot');(fork/'tools').mkdir();shutil.copy2(ROOT/'tools/package.py',fork/'tools/package.py')
        # A genuine local modification and a Git remote must not authorize any
        # online updater. The package helper doesn't consult this Git config.
        with (fork/'nodepilot_pkg/__init__.py').open('a') as f:f.write('\n# disposable fork test\n')
        (fork/'.git').mkdir();(fork/'.git/config').write_text('[remote "origin"]\nurl = https://github.com/example/fork.git\n')
        archive=base/'release.tar.gz';digest=package(archive);stub=base/'stub';stub.mkdir()
        curl=stub/'curl';curl.write_text('#!/usr/bin/env bash\nset -eu\nwhile [[ $# -gt 0 ]]; do\n'
            'if [[ $1 == --output ]]; then cp -- '+str(archive)+' "$2"; exit 0; fi\nshift\ndone\nexit 1\n');curl.chmod(0o700)
        release=['env','PATH='+str(stub)+':'+os.environ['PATH'],
                 'NODEPILOT_SOURCE_URL=https://github.com/example/fork/releases/download/v0.4.0/nodepilot-0.4.0.tar.gz',
                 'NODEPILOT_SOURCE_SHA256='+digest,'bash',str(ROOT/'install.sh')]
        root=Path('/opt/nodepilot')
        try:
            tty_command(['bash',str(fork/'install.sh'),'--local'])
            assert read_source(root)=={'schema':1,'provenance':'local-source'}
            Path('/etc/nodepilot/.smoke-sentinel').write_text('preserve')
            before=snapshot();tty_command(['bash',str(fork/'install.sh'),'--local','--upgrade']);assert snapshot()==before
            print('modified Fork local first/repeat: no inferred online binding OK',flush=True)
            tty_command([*release,'--upgrade'],replies=(),expect_code=1,source_replies=('n',));assert snapshot()==before
            tty_command([*release,'--upgrade'],source_replies=('y',))
            assert read_source(root)=={'schema':1,'provenance':'official-release','repository':'example/fork'}
            before=snapshot();tty_command([*release,'--upgrade']);assert snapshot()==before
            print('local -> Release decline/accept and same Release upgrade OK (simulated transport)',flush=True)
            tty_command(['bash',str(fork/'install.sh'),'--local','--upgrade'],replies=(),expect_code=1,source_replies=('n',));assert snapshot()==before
            tty_command(['bash',str(fork/'install.sh'),'--local','--upgrade'],source_replies=('y',))
            source=read_source(root)
            assert source=={'schema':1,'provenance':'local-source','previous_binding':{'repository':'example/fork'}}
            assert active_repository(source) is None
            before=snapshot();tty_command(['bash',str(fork/'install.sh'),'--local','--upgrade']);assert snapshot()==before
            print('Release -> local decline/accept: inactive previous binding retained; stable local upgrade OK',flush=True)
        finally:cleanup()
        try:
            tty_command(release)
            assert read_source(root)=={'schema':1,'provenance':'official-release','repository':'example/fork'}
            print('Release first install provenance OK (simulated transport, real SHA/extraction/systemd)',flush=True)
        finally:cleanup()
    print('source test installations cleaned; no real public RC attachment download claimed',flush=True)


if __name__=='__main__':main()
