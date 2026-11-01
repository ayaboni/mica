"""Time-based one-time passwords (RFC 6238) using only the standard library.

Works with Google Authenticator, Microsoft Authenticator, Authy, 1Password, Bitwarden, Aegis
and every other authenticator app (SHA-1, 6 digits, 30-second steps: the universal defaults).

No Django imports on purpose, so it can be unit-tested on its own (see test_totp.py).
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

DIGITS = 6
PERIOD = 30          # seconds per code
WINDOW = 1           # also accept the step before and after, for a phone clock that is slightly off
SECRET_BYTES = 20    # 160 bits, the size RFC 4226 recommends


def generate_secret():
    """A new random shared secret, as the base32 text authenticator apps expect."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii")


def _key(secret):
    text = "".join(str(secret).split()).upper()
    return base64.b32decode(text + "=" * (-len(text) % 8))


def _code_for_step(secret, step, digits=DIGITS):
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % (10 ** digits)).zfill(digits)


def current_step(now=None):
    return int((time.time() if now is None else now) // PERIOD)


def code_at(secret, now=None):
    """The code an authenticator app shows at time `now` (used by tests)."""
    return _code_for_step(secret, current_step(now))


def verify(secret, code, now=None, after_step=0):
    """Check a typed code. Returns the matching time step, or None.

    `after_step` is the last step already used for this secret: a code from that step or an
    earlier one is refused, so a code that was just used (or sniffed) can't be replayed.
    """
    code = "".join(str(code or "").split())
    if len(code) != DIGITS or not code.isascii() or not code.isdigit():
        return None
    now_step = current_step(now)
    matched = None
    for step in range(now_step - WINDOW, now_step + WINDOW + 1):
        # Every step is compared (no early exit) so timing doesn't reveal which one was close.
        if hmac.compare_digest(_code_for_step(secret, step), code) and step > after_step:
            matched = step
    return matched


def provisioning_uri(secret, account, issuer):
    """The otpauth:// link that an authenticator app turns into an entry (QR code or tap)."""
    label = quote("%s:%s" % (issuer, account), safe="")
    query = urlencode({"secret": secret, "issuer": issuer, "algorithm": "SHA1",
                       "digits": DIGITS, "period": PERIOD}, quote_via=quote)
    return "otpauth://totp/%s?%s" % (label, query)


def format_secret(secret):
    """ABCD EFGH ... in groups of four, easier to read and type."""
    text = "".join(str(secret).split()).upper()
    return " ".join(text[i:i + 4] for i in range(0, len(text), 4))
