"""Exercise the release gate against real Git blobs, not working-copy twins."""
import hashlib
import io
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.release_audit import audit_release


class ReleaseAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.archive = self.root / 'nodepilot-0.4.0.tar.gz'
        self.installer = self.root / 'candidate.sh'
        self.files = {'nodepilot': b'# entry\n',
                      'nodepilot_pkg/__init__.py': b'__version__ = "0.4.0"\n'}
        self.pack(self.files)
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        url = 'https://github.com/km-hl/Node-Pilot/releases/download/v0.4.0/' + self.archive.name
        self.installer.write_bytes(f"#!/bin/bash\nPAYLOAD_URL='{url}'\nPAYLOAD_SHA256='{digest}'\n".encode())
        for name, data in {**self.files, 'install.sh': self.installer.read_bytes()}.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
        for args in (['init', '--initial-branch=main'],
                     ['add', '--', 'nodepilot', 'nodepilot_pkg', 'install.sh'],
                     ['-c', 'user.name=Release Test', '-c', 'user.email=test@example.invalid',
                      '-c', 'commit.gpgsign=false', 'commit', '-m', 'fixture']):
            subprocess.run(['git', *args], cwd=self.root, env=env, capture_output=True, check=True)
        self.addCleanup(patch.stopall)
        patch('tools.release_audit.ROOT', self.root).start()

    def pack(self, files):
        with tarfile.open(self.archive, 'w:gz') as archive:
            for name, data in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.archive.with_suffix('.gz.sha256').write_text(digest + '  ' + self.archive.name + '\n')

    def check(self, tag='v0.4.0'):
        audit_release(self.archive, self.installer, 'km-hl/Node-Pilot', tag)

    def test_reviewed_release_passes(self):
        self.check()

    def test_identically_modified_worktree_and_candidate_installer_fail(self):
        changed = self.installer.read_bytes() + b'echo unreviewed\n'
        self.installer.write_bytes(changed)
        (self.root / 'install.sh').write_bytes(changed)
        with self.assertRaisesRegex(SystemExit, 'reviewed installer'):
            self.check()

    def test_source_version_and_tag_must_match(self):
        with self.assertRaisesRegex(SystemExit, 'source version'):
            self.check('v0.5.0')

    def test_archive_filename_must_match_version(self):
        self.archive = self.archive.rename(self.root / 'renamed.tar.gz')
        with self.assertRaisesRegex(SystemExit, 'filename'):
            self.check()

    def test_payload_change_cannot_pass_even_with_updated_checksum(self):
        self.pack({**self.files, 'nodepilot': b'# unreviewed\n'})
        with self.assertRaisesRegex(SystemExit, 'content differs'):
            self.check()

    def test_duplicate_installer_binding_is_rejected(self):
        self.installer.write_bytes(self.installer.read_bytes() + b"PAYLOAD_URL='https://example.invalid/other'\n")
        with self.assertRaisesRegex(SystemExit, 'binding mismatch'):
            self.check()
