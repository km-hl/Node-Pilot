import unittest

from tools.mihomo_check import asset_name


class MihomoAssetTests(unittest.TestCase):
    def test_amd64_uses_baseline_cpu_build_and_arm64_is_unchanged(self):
        self.assertEqual(asset_name('amd64','v1.19.32'),'mihomo-linux-amd64-v1-v1.19.32.gz')
        self.assertEqual(asset_name('arm64','v1.19.32'),'mihomo-linux-arm64-v1.19.32.gz')
