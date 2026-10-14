import copy
import unittest
from urllib.parse import parse_qs, unquote, urlsplit

from nodepilot_pkg.common import PilotError
from nodepilot_pkg.config import generate
from nodepilot_pkg.model import new_node, validate
from nodepilot_pkg.share import share


def fixture(protocol="reality", port=443):
    n = new_node(protocol, "节点 / A #1", "8.8.8.8", port, sni="example.com")
    if protocol == "reality":
        n["reality"] = dict(uuid="bb46267d-ecca-46a0-aefd-02b748315e29",
                            private_key="A" * 43, public_key="B" * 43,
                            short_id="1234abcd", server_names=["example.com"],
                            target="example.com:443", fingerprint="chrome")
    else:
        n["tls"] = dict(mode="import", certificate="/tmp/certificate.pem", key="/tmp/key.pem")
    return n


class ConfigTests(unittest.TestCase):
    def test_all_protocols(self):
        for protocol in ("reality", "anytls", "hysteria2"):
            n = fixture(protocol)
            config = generate(n)
            self.assertEqual(len(config["inbounds"]), 1)
            self.assertEqual(n["transport"], "udp" if protocol == "hysteria2" else "tcp")

    def test_vision(self):
        inbound = generate(fixture())["inbounds"][0]
        self.assertEqual(inbound["settings"]["clients"][0]["flow"], "xtls-rprx-vision")
        self.assertIn("target", inbound["streamSettings"]["realitySettings"])

    def test_uri_roundtrip_ipv6_unicode_and_password(self):
        for protocol in ("reality", "anytls", "hysteria2"):
            n = fixture(protocol)
            n["server"] = "2606:4700:4700::1111"
            n["password"] = "!@:/?# &密码" + "a" * 16
            u = urlsplit(share(n))
            self.assertEqual(u.hostname, n["server"])
            self.assertEqual(unquote(u.fragment), n["name"])
            self.assertEqual(parse_qs(u.query)["sni"], [n["sni"]])
            if protocol != "reality":
                self.assertEqual(unquote(u.username), n["password"])

    def test_private_key_never_shared(self):
        n = fixture()
        self.assertNotIn(n["reality"]["private_key"], share(n))

    def test_validation(self):
        for field, value in (("port", 0), ("port", True), ("server", "192.168.1.2"),
                             ("id", "../../etc"), ("service", "caddy.service"),
                             ("listen", "example.com"), ("name", "x\nExecStart=bad")):
            n = fixture()
            n[field] = value
            with self.assertRaises(PilotError):
                validate(n)

    def test_sni_must_be_allowed(self):
        n = fixture()
        n["sni"] = "other.example.com"
        with self.assertRaises(PilotError):
            generate(n)

    def test_no_insecure_default_even_for_test_certificate(self):
        n = fixture("anytls")
        n["tls"]["mode"] = "selfsigned"
        self.assertEqual(parse_qs(urlsplit(share(n)).query)["insecure"], ["0"])
        self.assertNotIn("insecure", generate(n)["inbounds"][0]["tls"])

    def test_obfs_and_bandwidth(self):
        n = fixture("hysteria2")
        n["obfs"] = {"type": "salamander", "password": "a@/#?" * 8}
        n["up_mbps"], n["down_mbps"] = 10, 20
        self.assertEqual(generate(n)["inbounds"][0]["obfs"], n["obfs"])
        q = parse_qs(urlsplit(share(n)).query)
        self.assertEqual(q["obfs-password"], [n["obfs"]["password"]])
        self.assertNotIn("up_mbps", q)

    def test_random_credentials(self):
        a, b = new_node("anytls", "A", "8.8.8.8"), new_node("anytls", "B", "8.8.8.8")
        self.assertNotEqual(a["id"], b["id"])
        self.assertNotEqual(a["password"], b["password"])


if __name__ == "__main__":
    unittest.main()
