import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from nodepilot_pkg.common import PilotError
from tools.vps_smoke import setup


class VpsPreparationTests(unittest.TestCase):
    def test_setup_uses_screened_pinned_target_and_independent_sni(self):
        manager=MagicMock()
        manager.cores.keypair.return_value=('A'*43,'B'*43)
        manager.apply.side_effect=lambda n:n
        selected={'sni':'sni.example.com','target':'8.8.4.4:443',
                  'requested_target':'origin.example.com:443','target_mode':'pinned_ip'}
        with tempfile.TemporaryDirectory() as d,patch('tools.vps_smoke.Manager',return_value=manager), \
             patch('tools.vps_smoke.root_required'),patch('tools.vps_smoke.available',side_effect=lambda port,*args:port), \
             patch.dict('os.environ',{'SSH_CONNECTION':'127.0.0.1 1234 8.8.8.8 22'}), \
             patch('tools.vps_smoke.rank_targets',return_value=[selected]) as rank,patch('builtins.print'):
            setup(Path(d))
        self.assertEqual(rank.call_args.args,())
        reality=manager.apply.call_args_list[0].args[0]
        self.assertEqual(reality['sni'],selected['sni'])
        self.assertEqual(reality['reality']['target'],selected['target'])
        self.assertEqual(reality['reality']['selection'],selected)

    def test_setup_without_screened_candidate_never_creates_nodes(self):
        manager=MagicMock()
        with tempfile.TemporaryDirectory() as d,patch('tools.vps_smoke.Manager',return_value=manager), \
             patch('tools.vps_smoke.root_required'),patch.dict('os.environ',{'SSH_CONNECTION':'127.0.0.1 1234 8.8.8.8 22'}), \
             patch('tools.vps_smoke.rank_targets',return_value=[]),self.assertRaises(PilotError):
            setup(Path(d))
        manager.apply.assert_not_called();manager.update_core.assert_not_called()
