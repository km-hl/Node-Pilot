#!/usr/bin/env python3
"""Real loopback TLS/HTTP screening. DNS/ranges are fixtures, no public claims."""
import argparse
import ipaddress
import os
import socket
import ssl
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from nodepilot_pkg.common import PilotError,run
from nodepilot_pkg.targets import inspect_target,tls_handshake,TargetFailure


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--local',action='store_true',required=True);parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='nodepilot-target-smoke-') as tmp:
        cert,key=Path(tmp)/'certificate.pem',Path(tmp)/'key.pem'
        run(['openssl','req','-x509','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256','-nodes','-days','1',
             '-subj','/CN=example.com','-addext','subjectAltName=DNS:example.com','-keyout',key,'-out',cert])
        server=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);server.minimum_version=ssl.TLSVersion.TLSv1_3
        server.set_ecdh_curve('X25519');server.set_alpn_protocols(['h2','http/1.1']);server.load_cert_chain(cert,key)
        client=ssl.create_default_context(cafile=str(cert));response=[b'HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n']
        stop=threading.Event()
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(.2)
            port=listener.getsockname()[1]
            def serve():
                while not stop.is_set():
                    try:sock,_=listener.accept()
                    except socket.timeout:continue
                    with sock:
                        sock.settimeout(2)
                        try:
                            with server.wrap_socket(sock,server_side=True) as tls:
                                if tls.recv(4096).startswith(b'GET '):tls.sendall(response[0])
                        except OSError:pass
            thread=threading.Thread(target=serve);thread.start()
            try:
                with patch.dict(os.environ,{'SSL_CERT_FILE':str(cert)}), \
                     patch('nodepilot_pkg.targets.resolve',return_value=(['127.0.0.1'],{'example.com'})), \
                     patch('nodepilot_pkg.targets.ssl.create_default_context',return_value=client):
                    ranges=[ipaddress.ip_network('104.16.0.0/13'),ipaddress.ip_network('2606:4700::/32')]
                    for status in (200,301,302,403,404):
                        response[0]=f'HTTP/1.1 {status} Test\r\nContent-Length: 0\r\n\r\n'.encode()
                        result=inspect_target(f'example.com:{port}','example.com',ranges)
                        assert result['feasible'] and result['cf_screened'] and result['checks']['http_headers']['http_status']==status
                        print(status,'REAL TLS1.3/X25519/h2/certificate and HTTP headers OK',flush=True)
                    for wire,expected in [(b'HTTP/1.1 403 Forbidden\r\nCF-Ray: test\r\n\r\n','reject'),
                                          (b'not HTTP\r\n\r\n','unknown')]:
                        response[0]=wire
                        if expected=='reject':
                            try:inspect_target(f'example.com:{port}','example.com',ranges)
                            except PilotError as error:assert 'Cloudflare' in str(error)
                            else:raise AssertionError('CF response accepted')
                        else:
                            result=inspect_target(f'example.com:{port}','example.com',ranges)
                            assert result['feasible'] and not result['cf_screened']
                        print('REAL HTTP',expected,'OK',flush=True)
                    try:inspect_target(f'example.com:{port}','wrong.example.com',ranges)
                    except PilotError as error:assert 'certificate' in str(error)
                    else:raise AssertionError('certificate mismatch accepted')
                    print('REAL certificate/SNI mismatch refused OK',flush=True)
                    with socket.socket() as refused:
                        refused.bind(('127.0.0.1',0))  # Reserved but never listening.
                        try:tls_handshake('127.0.0.1',refused.getsockname()[1],'example.com')
                        except TargetFailure as error:assert error.code=='connection_refused'
                        else:raise AssertionError('refused TCP connection accepted')
                    print('REAL TCP refusal classified separately from TLS/certificate OK',flush=True)
            finally:
                stop.set();thread.join(timeout=4);assert not thread.is_alive()
    print('9 loopback transport scenarios cleaned; DNS/official ranges are fixtures; public suitability unverified',flush=True)


if __name__=='__main__':main()
