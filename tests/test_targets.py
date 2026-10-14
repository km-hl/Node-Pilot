import ipaddress
import unittest
from unittest.mock import MagicMock, patch

from nodepilot_pkg.common import PilotError
from nodepilot_pkg.targets import CANDIDATES, TargetFailure, cf_headers, exclude_cf, inspect_target, rank_targets, tls_handshake


class TargetTests(unittest.TestCase):
    def test_any_cf_address_excludes_host_even_if_other_address_is_fast(self):
        ranges = [ipaddress.ip_network("104.16.0.0/13"), ipaddress.ip_network("2606:4700::/32")]
        for addresses in (["8.8.8.8", "104.16.2.3"], ["2606:4700::123"]):
            with self.assertRaises(PilotError):
                exclude_cf(addresses, set(), ranges)
        exclude_cf(["8.8.8.8"], set(), ranges)

    def test_dns_and_response_cf_markers(self):
        with self.assertRaises(PilotError):
            exclude_cf(["8.8.8.8"], {"foo.cdn.cloudflare.net"}, [])
        for headers in ({"cf-ray": "value"}, {"cf-cache-status": "HIT"}, {"server": "cloudflare"}):
            self.assertTrue(cf_headers(headers))
        self.assertFalse(cf_headers({"server": "nginx"}))

    def inspected(self, status=200, headers=None, error=None):
        samples = [{"elapsed_ms": v, "group": "X25519"} for v in (10, 30, 20)]
        with patch("nodepilot_pkg.targets.resolve", return_value=(["8.8.8.8"], set())), \
             patch("nodepilot_pkg.targets.tls_handshake", side_effect=error or samples) as handshake, \
             patch("nodepilot_pkg.targets.http_headers", return_value=(status, headers or {})):
            result = inspect_target("example.com:443", "example.com", [])
        return result, handshake

    def test_three_handshakes_median_and_certificate_validation_remains_enabled(self):
        result, handshake = self.inspected()
        self.assertEqual(result["median_ms"], 20)
        self.assertEqual(result["spread_ms"], 20)
        self.assertEqual(handshake.call_count, 3)
        self.assertEqual(handshake.call_args.args[2], "example.com")
        self.assertEqual(result["group"], "X25519")

    def test_cf_tls_and_curve_failures_are_rejected(self):
        for kwargs in ({"headers": {"cf-ray": "x"}}, {"error": TargetFailure('alpn', 'h1')},
                       {"error": TargetFailure('tls', 'TLS1.2')}, {"error": TargetFailure('unverified', 'group unknown')}):
            with self.assertRaises(PilotError):
                self.inspected(**kwargs)

    def test_redirect_and_non_success_are_quality_warnings(self):
        for status in (301, 403, 404):
            result, _ = self.inspected(status=status)
            self.assertTrue(result['feasible'])
            self.assertTrue(result['warnings'])

    def test_default_pool_does_not_restrict_manual_domains(self):
        self.assertNotIn('www.apple.com', CANDIDATES)
        self.assertFalse(any('google' in h for h in CANDIDATES))
        with patch('nodepilot_pkg.targets.inspect_target', return_value={'sni':'www.apple.com','median_ms':1,'spread_ms':0}), \
             patch('nodepilot_pkg.targets.cloudflare_networks', return_value=[]):
            self.assertEqual(rank_targets(['www.apple.com'])[0]['sni'], 'www.apple.com')

    def test_first_address_failure_does_not_hide_other_address_or_family(self):
        def handshake(ip, *args):
            if ip == '8.8.8.8':
                raise TargetFailure('timeout', 'timeout')
            return {'elapsed_ms': 10 if ':' in ip else 20, 'group':'X25519'}
        with patch('nodepilot_pkg.targets.resolve', return_value=(['8.8.8.8','8.8.4.4','2001:4860:4860::8888'], set())), \
             patch('nodepilot_pkg.targets.tls_handshake', side_effect=handshake), \
             patch('nodepilot_pkg.targets.http_headers', return_value=(200, {})):
            result = inspect_target('example.com:443', 'example.com', [])
        self.assertEqual({a['family'] for a in result['addresses']}, {4,6})
        self.assertEqual(result['failures'][0]['code'], 'timeout')
        self.assertTrue(result['target'].startswith('['))

    def test_openssl_requires_actual_curve_alpn_and_verified_hostname(self):
        from subprocess import CompletedProcess
        output = b'New, TLSv1.3\nServer Temp Key: X25519, 253 bits\nALPN protocol: h2\nVerify return code: 0 (ok)'
        with patch('nodepilot_pkg.targets.run_openssl', return_value=CompletedProcess([],0,output,b'')) as command:
            tls_handshake('8.8.8.8',443,'example.com')
        args = command.call_args.args[0]
        self.assertIn('-verify_return_error', args)
        self.assertIn('-verify_hostname', args)
        for changed in (output.replace(b'X25519',b'P-256'), output.replace(b'h2',b'http/1.1'), output.replace(b'0 (ok)',b'20 (bad)'), output.replace(b'TLSv1.3',b'TLSv1.2')):
            with patch('nodepilot_pkg.targets.run_openssl', return_value=CompletedProcess([],0,changed,b'')), self.assertRaises(PilotError):
                tls_handshake('8.8.8.8',443,'example.com')

    @unittest.skipUnless(__import__('os').name=='posix','Linux bounded pipe reader')
    def test_openssl_output_is_bounded_before_full_capture(self):
        import sys
        from nodepilot_pkg.targets import run_openssl
        with self.assertRaises(TargetFailure):
            run_openssl([sys.executable,'-c','print("x"*70000)'],2)

    def test_ranking_uses_current_vps_measurement_and_excludes_failed_candidate(self):
        def check(target, sni, networks):
            if sni == "bad.example.com":
                raise PilotError("rejected")
            return {"sni": sni, "median_ms": 90 if sni.startswith("slow") else 10, "spread_ms": 1}
        with patch("nodepilot_pkg.targets.cloudflare_networks", return_value=[]), \
             patch("nodepilot_pkg.targets.inspect_target", side_effect=check):
            ranked = rank_targets(["slow.example.com", "bad.example.com", "fast.example.com"])
        self.assertEqual([r["sni"] for r in ranked], ["fast.example.com", "slow.example.com"])

    def test_cannot_rank_without_current_cf_ranges(self):
        with patch("nodepilot_pkg.targets.cloudflare_networks", side_effect=PilotError("unavailable")), \
             patch("nodepilot_pkg.targets.inspect_target") as check:
            with self.assertRaises(PilotError):
                rank_targets(["example.com"])
        check.assert_not_called()
