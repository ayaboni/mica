from django.test import RequestFactory, SimpleTestCase, override_settings

from .net import client_ip


def req(remote="10.0.0.1", xff=None, **extra):
    meta = {"REMOTE_ADDR": remote}
    if xff is not None:
        meta["HTTP_X_FORWARDED_FOR"] = xff
    meta.update(extra)
    return RequestFactory().get("/", **meta)


class ClientIpTests(SimpleTestCase):
    @override_settings(TRUST_PROXY_IP=False)
    def test_ignores_forwarded_header_when_not_behind_a_proxy(self):
        self.assertEqual(client_ip(req("203.0.113.5", "1.2.3.4")), "203.0.113.5")

    @override_settings(TRUST_PROXY_IP=True, TRUSTED_PROXY_COUNT=1)
    def test_one_proxy_uses_last_entry_and_ignores_spoofed_prefix(self):
        self.assertEqual(client_ip(req(xff="6.6.6.6, 198.51.100.7")), "198.51.100.7")

    @override_settings(TRUST_PROXY_IP=True, TRUSTED_PROXY_COUNT=2)
    def test_two_proxies_use_second_from_right(self):
        self.assertEqual(client_ip(req(xff="6.6.6.6, 198.51.100.7, 172.16.0.9")), "198.51.100.7")

    @override_settings(TRUST_PROXY_IP=True, TRUSTED_PROXY_COUNT=2)
    def test_too_few_entries_falls_back_to_remote_addr(self):
        self.assertEqual(client_ip(req("10.0.0.1", "198.51.100.7")), "10.0.0.1")

    @override_settings(TRUST_PROXY_IP=True, TRUSTED_PROXY_COUNT=1)
    def test_garbage_is_never_returned(self):
        self.assertEqual(client_ip(req("10.0.0.1", "not-an-ip")), "10.0.0.1")

    @override_settings(TRUST_PROXY_IP=True, CLIENT_IP_HEADER="CF-Connecting-IP")
    def test_configured_header_wins_when_valid(self):
        r = req(xff="6.6.6.6, 172.16.0.9", HTTP_CF_CONNECTING_IP="198.51.100.7")
        self.assertEqual(client_ip(r), "198.51.100.7")

    @override_settings(TRUST_PROXY_IP=True, CLIENT_IP_HEADER="CF-Connecting-IP")
    def test_invalid_configured_header_falls_back_to_forwarded_for(self):
        r = req(xff="172.16.0.9", HTTP_CF_CONNECTING_IP="junk")
        self.assertEqual(client_ip(r), "172.16.0.9")

    @override_settings(TRUST_PROXY_IP=True)
    def test_ipv6_is_normalised(self):
        self.assertEqual(client_ip(req(xff="2001:DB8::1")), "2001:db8::1")
