import ipaddress
import unittest
from unittest.mock import MagicMock, patch

from nodepilot_pkg.common import PilotError
from nodepilot_pkg.targets import CANDIDATES, TargetFailure, cf_headers, exclude_cf, inspect_target, rank_targets, tls_handshake

RANGES=[ipaddress.ip_network('104.16.0.0/13'),ipaddress.ip_network('2606:4700::/32')]
CHECKS={name:{'status':'passed'} for name in ('ip_ranges','dns_cname','http_headers','tls')}


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
            result = inspect_target("example.com:443", "example.com", RANGES)
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
        for status in (301, 302, 403, 404):
            result, _ = self.inspected(status=status)
            self.assertTrue(result['feasible'])
            self.assertTrue(result['warnings'])
            self.assertTrue(result['cf_screened'])

    def test_default_pool_does_not_restrict_manual_domains(self):
        self.assertNotIn('www.apple.com', CANDIDATES)
        self.assertFalse(any('google' in h for h in CANDIDATES))
        with patch('nodepilot_pkg.targets.inspect_target', return_value={'sni':'www.apple.com','median_ms':1,'spread_ms':0,'cf_screened':True,'checks':CHECKS}), \
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
        for message,code in ((b'Connection refused\nconnect:errno=111','connection_refused'),
                             (b'Network is unreachable','network_unreachable'),(b'certificate verify error','certificate')):
            with patch('nodepilot_pkg.targets.run_openssl',return_value=CompletedProcess([],1,message,b'')),self.assertRaises(TargetFailure) as error:
                tls_handshake('8.8.8.8',443,'example.com')
            self.assertEqual(error.exception.code,code)

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
            return {"sni": sni, "median_ms": 90 if sni.startswith("slow") else 10, "spread_ms": 1,'cf_screened':True,'checks':CHECKS}
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

    def test_http_failure_is_unknown_and_automatic_ranking_refuses_it(self):
        import errno
        import ssl
        cases=[(TimeoutError(),'timeout'),(ConnectionRefusedError(),'connection_refused'),
               (OSError(errno.ENETUNREACH,'unreachable'),'network_unreachable'),
               (ssl.SSLCertVerificationError('bad cert'),'certificate'),(PilotError('bad header'),'http_unverified')]
        for error,code in cases:
            with self.subTest(code=code),patch('nodepilot_pkg.targets.resolve',return_value=(['8.8.8.8'],set())), \
                 patch('nodepilot_pkg.targets.tls_handshake',return_value={'elapsed_ms':10,'group':'X25519'}), \
                 patch('nodepilot_pkg.targets.http_headers',side_effect=error):
                result=inspect_target('origin.example.com:443','sni.example.com',RANGES)
            self.assertTrue(result['feasible']);self.assertFalse(result['cf_screened'])
            self.assertEqual(result['checks']['http_headers']['code'],code)
            self.assertEqual(result['checks']['tls']['status'],'passed')
            with patch('nodepilot_pkg.targets.cloudflare_networks',return_value=RANGES), \
                 patch('nodepilot_pkg.targets.inspect_target',return_value=result):
                self.assertEqual(rank_targets(['origin.example.com']),[])

    def test_fully_checked_address_preferred_over_faster_unknown_and_ipv6_failure_reported(self):
        def handshake(ip,*args):
            if ':' in ip:raise TargetFailure('network_unreachable','IPv6 无路由')
            return {'elapsed_ms':1 if ip=='8.8.8.8' else 20,'group':'X25519'}
        def headers(ip,*args):
            if ip=='8.8.8.8':raise TimeoutError()
            return 403,{}
        with patch('nodepilot_pkg.targets.resolve',return_value=(['8.8.8.8','8.8.4.4','2001:4860:4860::8888'],set())), \
             patch('nodepilot_pkg.targets.tls_handshake',side_effect=handshake),patch('nodepilot_pkg.targets.http_headers',side_effect=headers):
            result=inspect_target('origin.example.com:443','sni.example.com',RANGES)
        self.assertEqual(result['target'],'8.8.4.4:443');self.assertTrue(result['cf_screened'])
        self.assertEqual(result['failures'][0]['code'],'network_unreachable')
        self.assertEqual(result['addresses'][0]['http_check']['status'],'unknown')

    def test_cf_header_on_any_tested_address_rejects_whole_candidate(self):
        with patch('nodepilot_pkg.targets.resolve',return_value=(['8.8.8.8','8.8.4.4'],set())), \
             patch('nodepilot_pkg.targets.tls_handshake',return_value={'elapsed_ms':10,'group':'X25519'}), \
             patch('nodepilot_pkg.targets.http_headers',side_effect=[(200,{}),(404,{'CF-Ray':'marker'})]):
            with self.assertRaisesRegex(PilotError,'Cloudflare'):inspect_target('origin.example.com:443','sni.example.com',RANGES)

    def test_missing_official_ranges_cannot_be_marked_screened(self):
        with patch('nodepilot_pkg.targets.resolve',return_value=(['8.8.8.8'],set())), \
             patch('nodepilot_pkg.targets.tls_handshake',return_value={'elapsed_ms':10,'group':'X25519'}), \
             patch('nodepilot_pkg.targets.http_headers',return_value=(200,{})):
            result=inspect_target('origin.example.com:443','sni.example.com',[])
        self.assertFalse(result['cf_screened']);self.assertEqual(result['checks']['ip_ranges']['status'],'unknown')
        self.assertIn('非完整 CNAME',result['checks']['dns_cname']['scope'])

    def test_single_boolean_cannot_authorize_automatic_recommendation(self):
        with patch('nodepilot_pkg.targets.cloudflare_networks',return_value=RANGES), \
             patch('nodepilot_pkg.targets.inspect_target',return_value={'cf_screened':True}):
            self.assertEqual(rank_targets(['origin.example.com']),[])

    def test_multiple_ipv6_addresses_pin_only_successful_canonical_address(self):
        def handshake(ip,*args):
            if ip.endswith('8888'):raise TargetFailure('timeout','timeout')
            return {'elapsed_ms':10,'group':'X25519'}
        with patch('nodepilot_pkg.targets.resolve',return_value=(['2001:4860:4860::8888','2001:4860:4860::8844'],set())), \
             patch('nodepilot_pkg.targets.tls_handshake',side_effect=handshake), \
             patch('nodepilot_pkg.targets.http_headers',return_value=(200,{})):
            result=inspect_target('origin.example.com:10443','sni.example.com',RANGES)
        self.assertEqual(result['target'],'[2001:4860:4860::8844]:10443')
        self.assertEqual(result['selected_family'],6)
        self.assertEqual(result['target_port'],10443)
        self.assertEqual(result['target_mode'],'pinned_ip')
        self.assertFalse(result['dynamic_dns'])
        self.assertEqual(result['sni'],'sni.example.com')
        self.assertEqual(result['original_host'],'origin.example.com')
        self.assertEqual(result['tested_target'],result['target'])

    def test_http_parser_rejects_partial_or_malformed_response_and_keeps_duplicate_cf_marker(self):
        from nodepilot_pkg.targets import http_headers
        import socket
        for wire,valid in [(b'HTTP/1.1 103 Early Hints\r\n\r\n',False),
                           (b'garbage 200\r\n\r\n',False),(b'HTTP/1.1 200 OK\r\nbroken\r\n\r\n',False),
                           (b'HTTP/1.1 302 Found\r\nServer: cloudflare\r\nServer: nginx\r\n\r\n',True)]:
            context=MagicMock();tls=context.wrap_socket.return_value.__enter__.return_value
            tls.recv.return_value=wire
            with patch('nodepilot_pkg.targets.ssl.create_default_context',return_value=context),patch.object(socket,'create_connection'):
                if valid:
                    status,headers=http_headers('8.8.8.8',443,'sni.example.com')
                    self.assertEqual(status,302);self.assertTrue(cf_headers(headers))
                else:
                    with self.assertRaises(PilotError):http_headers('8.8.8.8',443,'sni.example.com')


class TargetRecheckTests(unittest.TestCase):
    def test_dynamic_domain_has_independent_dns_status_and_never_changes_node(self):
        import copy,tempfile
        from pathlib import Path
        from test_config import fixture
        from nodepilot_pkg.targets import recheck_target,target_status
        n=fixture();n['reality']['target']='origin.example.com:443'
        n['reality']['selection']={'requested_target':n['reality']['target'],'target':n['reality']['target'],
                                  'sni':n['sni'],'target_mode':'dynamic_dns','dynamic_dns':True}
        before=copy.deepcopy(n)
        with tempfile.TemporaryDirectory() as d,patch('nodepilot_pkg.targets.resolve',return_value=(['8.8.4.4'],set())), \
             patch('nodepilot_pkg.targets.cloudflare_networks',return_value=RANGES), \
             patch('nodepilot_pkg.targets.inspect_target',return_value={'cf_screened':True,'checks':CHECKS}) as scan:
            result=recheck_target(Path(d),n)
            self.assertIsNone(result['dns_drift']);self.assertTrue(result['dynamic_dns'])
            self.assertIn('动态域名',result['dns_status']);self.assertIn('运行时 IP 未固定',result['status'])
            scan.assert_called_once_with(n['reality']['target'],n['sni'],RANGES)
            self.assertEqual(n,before)
            n['sni']='changed.example.com'
            self.assertNotIn('选站历史',target_status(Path(d),n))
            self.assertNotIn('最近检测通过',target_status(Path(d),n))

    def recheck(self,original=None,addresses=None,selection=True):
        import copy,tempfile
        from pathlib import Path
        from test_config import fixture
        from nodepilot_pkg.targets import recheck_target
        n=fixture();n['reality']['target']='[2001:4860:4860::8888]:443'
        if selection:n['reality']['selection']={'requested_target':original,'target':n['reality']['target'],'sni':n['sni']}
        before=copy.deepcopy(n)
        with tempfile.TemporaryDirectory() as d,patch('nodepilot_pkg.targets.resolve',return_value=(addresses or ['2001:4860:4860:0:0:0:0:8888','8.8.8.8'],set())) as dns, \
             patch('nodepilot_pkg.targets.cloudflare_networks',return_value=RANGES), \
             patch('nodepilot_pkg.targets.inspect_target',return_value={'cf_screened':True}) as inspect:
            result=recheck_target(Path(d),n)
            self.assertEqual(n,before)
            inspect.assert_called_once_with(n['reality']['target'],n['sni'],RANGES)
        return result,dns

    def test_original_target_domain_not_client_sni_and_multi_family_membership(self):
        result,dns=self.recheck('origin.example.com:443')
        dns.assert_called_once_with('origin.example.com');self.assertFalse(result['dns_drift'])
        result,_=self.recheck('origin.example.com:443',['8.8.4.4'])
        self.assertTrue(result['dns_drift'])

    def test_literal_original_and_legacy_missing_selection_do_not_use_sni_dns(self):
        for original,selection in [('[2001:4860:4860::8888]:443',True),('8.8.8.8:443',True),(None,False)]:
            result,dns=self.recheck(original,selection=selection)
            dns.assert_not_called();self.assertIsNone(result['dns_drift'])
            self.assertIn('不适用' if selection else '未知',result['dns_status'])

    def test_dns_failure_and_stale_selection_preserve_node_and_report_unknown(self):
        from test_config import fixture
        import copy,tempfile
        from pathlib import Path
        from nodepilot_pkg.targets import recheck_target,target_status
        n=fixture();n['reality']['target']='8.8.8.8:443'
        n['reality']['selection']={'target':n['reality']['target'],'requested_target':'origin.example.com:443','sni':n['sni']}
        for stale in (False,True):
            if stale:n['reality']['selection']['target']='8.8.4.4:443'
            before=copy.deepcopy(n)
            with tempfile.TemporaryDirectory() as d,patch('nodepilot_pkg.targets.resolve',side_effect=PilotError('DNS timeout')) as dns, \
                 patch('nodepilot_pkg.targets.cloudflare_networks',return_value=RANGES),patch('nodepilot_pkg.targets.inspect_target',return_value={'cf_screened':False}):
                result=recheck_target(Path(d),n)
                self.assertIsNone(result['dns_drift']);self.assertIn('未知',target_status(Path(d),n))
                if stale:dns.assert_not_called()
                self.assertEqual(n,before)
