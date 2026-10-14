import ipaddress
import unittest
from unittest.mock import MagicMock, patch

from nodepilot_pkg.common import PilotError
from nodepilot_pkg.targets import cf_headers, exclude_cf, inspect_target, rank_targets


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

    def inspected(self, status=200, headers=None, version="TLSv1.3", alpn="h2"):
        context = MagicMock()
        tls = context.wrap_socket.return_value.__enter__.return_value
        tls.version.return_value = version
        tls.selected_alpn_protocol.return_value = alpn
        with patch("nodepilot_pkg.targets.resolve", return_value=(["8.8.8.8"], set())), \
             patch("nodepilot_pkg.targets.ssl.create_default_context", return_value=context), \
             patch("nodepilot_pkg.targets.socket.create_connection"), \
             patch("nodepilot_pkg.targets.time.monotonic", side_effect=[0, .01, 1, 1.03, 2, 2.02]), \
             patch("nodepilot_pkg.targets.http_headers", return_value=(status, headers or {})):
            result = inspect_target("example.com:443", "example.com", [])
        return result, context

    def test_three_handshakes_median_and_certificate_validation_remains_enabled(self):
        result, context = self.inspected()
        self.assertEqual(result["median_ms"], 20)
        self.assertEqual(result["spread_ms"], 20)
        self.assertEqual(context.wrap_socket.call_count, 3)
        self.assertEqual(context.wrap_socket.call_args.kwargs["server_hostname"], "example.com")

    def test_redirect_cf_h1_tls12_are_rejected(self):
        for kwargs in ({"status": 301}, {"headers": {"location": "https://other.example"}},
                       {"headers": {"cf-ray": "x"}}, {"alpn": "http/1.1"}, {"version": "TLSv1.2"}):
            with self.assertRaises(PilotError):
                self.inspected(**kwargs)

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
