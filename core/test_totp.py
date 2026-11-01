"""Tests for core/totp.py. Plain unittest: no database and no Django needed."""
import base64
import unittest
from urllib.parse import parse_qs, urlparse

from . import totp

# RFC 6238 appendix B: the SHA-1 shared secret is the ASCII string 12345678901234567890.
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()


class TotpTests(unittest.TestCase):
    def test_rfc_6238_vectors(self):
        # The RFC lists 8-digit codes; the 6-digit code is the last six of them.
        vectors = {59: "287082", 1111111109: "081804", 1111111111: "050471",
                   1234567890: "005924", 2000000000: "279037", 20000000000: "353130"}
        for when, expected in vectors.items():
            self.assertEqual(totp.code_at(RFC_SECRET, now=when), expected, when)

    def test_verify_accepts_current_and_neighbouring_steps(self):
        now = 1111111111
        for offset in (-totp.PERIOD, 0, totp.PERIOD):
            code = totp.code_at(RFC_SECRET, now=now + offset)
            self.assertIsNotNone(totp.verify(RFC_SECRET, code, now=now), offset)

    def test_verify_rejects_codes_outside_the_window(self):
        now = 1111111111
        old = totp.code_at(RFC_SECRET, now=now - 3 * totp.PERIOD)
        self.assertIsNone(totp.verify(RFC_SECRET, old, now=now))
        future = totp.code_at(RFC_SECRET, now=now + 3 * totp.PERIOD)
        self.assertIsNone(totp.verify(RFC_SECRET, future, now=now))

    def test_verify_rejects_a_replayed_step(self):
        now = 1111111111
        code = totp.code_at(RFC_SECRET, now=now)
        step = totp.verify(RFC_SECRET, code, now=now)
        self.assertIsNotNone(step)
        self.assertIsNone(totp.verify(RFC_SECRET, code, now=now, after_step=step))
        # An older code than the one already used is refused too.
        older = totp.code_at(RFC_SECRET, now=now - totp.PERIOD)
        self.assertIsNone(totp.verify(RFC_SECRET, older, now=now, after_step=step))

    def test_verify_rejects_junk(self):
        now = 1111111111
        for bad in ("", None, "12345", "1234567", "abcdef", "12 345", "٣٤٥٦٧٨", "00000x"):
            self.assertIsNone(totp.verify(RFC_SECRET, bad, now=now), repr(bad))

    def test_verify_ignores_spaces_in_a_typed_code(self):
        now = 1111111111
        code = totp.code_at(RFC_SECRET, now=now)
        spaced = code[:3] + " " + code[3:]
        self.assertIsNotNone(totp.verify(RFC_SECRET, spaced, now=now))

    def test_a_different_secret_does_not_verify(self):
        now = 1111111111
        code = totp.code_at(RFC_SECRET, now=now)
        self.assertIsNone(totp.verify(totp.generate_secret(), code, now=now))

    def test_generated_secrets_are_random_base32_of_the_right_size(self):
        a, b = totp.generate_secret(), totp.generate_secret()
        self.assertNotEqual(a, b)
        self.assertEqual(len(base64.b32decode(a)), totp.SECRET_BYTES)

    def test_lower_case_and_spaced_secret_still_works(self):
        spaced = totp.format_secret(RFC_SECRET).lower()
        self.assertEqual(totp.code_at(spaced, now=59), "287082")

    def test_provisioning_uri(self):
        uri = totp.provisioning_uri("JBSWY3DPEHPK3PXP", "ana.reyes", "MICA")
        parsed = urlparse(uri)
        self.assertEqual((parsed.scheme, parsed.netloc), ("otpauth", "totp"))
        self.assertEqual(parsed.path, "/MICA%3Aana.reyes")
        query = parse_qs(parsed.query)
        self.assertEqual(query["secret"], ["JBSWY3DPEHPK3PXP"])
        self.assertEqual(query["issuer"], ["MICA"])
        self.assertEqual(query["digits"], ["6"])
        self.assertEqual(query["period"], ["30"])

    def test_format_secret_groups_of_four(self):
        self.assertEqual(totp.format_secret("abcdefghij"), "ABCD EFGH IJ")


if __name__ == "__main__":
    unittest.main()
