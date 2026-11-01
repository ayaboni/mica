"""Two-factor sign-in for System Admin (superuser) accounts.

Why only admins: they can read every student's records and manage every staff account, so a
stolen or guessed password must not be enough. The flow is:

  * Admin has an authenticator set up:   password  ->  6-digit code (or a recovery code)  ->  in.
  * Admin has none yet (first sign-in):  password  ->  e-mailed code (proves they own the
    mailbox, so a password alone can't enrol an attacker's phone)  ->  forced to set up the
    authenticator and save recovery codes  ->  in.

The session records how far the person got (MFA_OK_KEY / MFA_SETUP_KEY). The middleware in
core/middleware.py refuses every other page until the admin is fully through, and that also
closes the Django /admin/ login, which would otherwise accept the password alone.

Nothing here imports core.views (views imports this module), so there is no import cycle.
"""
import base64
import hashlib
import hmac
import re
import secrets
import time

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from . import totp
from .models import RecoveryCode, TotpDevice

MFA_OK_KEY = "mfa_ok"                      # signed in AND passed the second factor
MFA_SETUP_KEY = "mfa_setup_allowed"        # may enrol an authenticator (e-mail code or fresh code proved it)
MFA_PENDING_KEY = "pending_mfa_user_id"    # password was right; waiting for the authenticator code
MFA_PENDING_AT_KEY = "pending_mfa_at"
PENDING_MAX_SECONDS = 300                  # the code page must be finished within 5 minutes

RECOVERY_CODE_COUNT = 10
RECOVERY_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"    # no 0/O or 1/I/L, like the e-mailed codes
RECOVERY_HALF = 5                                         # shown as XXXXX-XXXXX
RECOVERY_LENGTH = RECOVERY_HALF * 2

# Separate throttle keys inside LoginThrottle. Real usernames can't contain ":" (see
# name_validation.USERNAME_PATTERN), so these can never collide with a student's or staff's row.
TOTP_MAX_ATTEMPTS = 5
TOTP_LOCKOUT_SECONDS = 300


def required():
    """Admin two-factor is on unless a test run switches it off (see settings.py)."""
    return bool(getattr(settings, "ADMIN_TWO_FACTOR_REQUIRED", True))


def throttle_key(user):
    return "totp:%s" % user.pk


# -- secret storage ---------------------------------------------------------------------------

def _fernet():
    base = (getattr(settings, "TOTP_ENCRYPTION_KEY", "") or settings.SECRET_KEY).encode()
    key = hmac.new(base, b"mica.totp.secret.v1", hashlib.sha256).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(secret):
    return _fernet().encrypt(secret.encode()).decode()


def decrypt_secret(token):
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        # SECRET_KEY / TOTP_ENCRYPTION_KEY changed since this was saved. Fail closed, and tell
        # the operator how to recover (python manage.py reset_admin_2fa).
        raise ValueError("The stored authenticator secret can't be decrypted with the current key.")


# -- devices ----------------------------------------------------------------------------------

def confirmed_device(user):
    return TotpDevice.objects.filter(user=user, confirmed_at__isnull=False).first()


def has_confirmed_device(user):
    return TotpDevice.objects.filter(user=user, confirmed_at__isnull=False).exists()


def pending_device(user):
    """The not-yet-confirmed device for `user`, made on first use and then kept, so refreshing the
    setup page doesn't change the secret halfway through scanning it."""
    device = TotpDevice.objects.filter(user=user, confirmed_at__isnull=True).first()
    if device is None:
        device = TotpDevice.objects.create(user=user, secret_enc=encrypt_secret(totp.generate_secret()))
    return device


def device_secret(device):
    return decrypt_secret(device.secret_enc)


def confirm_device(user, raw_code):
    """Finish setup: `raw_code` must be a current code from the app. True on success."""
    with transaction.atomic():
        device = TotpDevice.objects.select_for_update().filter(user=user, confirmed_at__isnull=True).first()
        if device is None:
            return False
        step = totp.verify(device_secret(device), raw_code)
        if step is None:
            return False
        device.confirmed_at = timezone.now()
        device.last_used_step = step
        device.save(update_fields=["confirmed_at", "last_used_step"])
        return True


def remove_device(user):
    """Forget the authenticator and every recovery code (used when replacing it, or by the
    reset_admin_2fa command)."""
    TotpDevice.objects.filter(user=user).delete()
    RecoveryCode.objects.filter(user=user).delete()


# -- recovery codes ---------------------------------------------------------------------------

def _normalize_recovery(raw):
    return re.sub(r"[^A-Za-z0-9]", "", raw or "").upper()


def _hash_recovery(user, code):
    msg = ("recovery-code:%s:%s" % (user.pk, code)).encode()
    return hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()


def new_recovery_codes(user):
    """Replace this admin's recovery codes with a fresh set and return them as shown to the person
    (XXXXX-XXXXX). They exist in readable form only in this return value."""
    codes = ["".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(RECOVERY_LENGTH))
             for _ in range(RECOVERY_CODE_COUNT)]
    with transaction.atomic():
        RecoveryCode.objects.filter(user=user).delete()
        RecoveryCode.objects.bulk_create(
            [RecoveryCode(user=user, code_hash=_hash_recovery(user, c)) for c in codes])
    return ["%s-%s" % (c[:RECOVERY_HALF], c[RECOVERY_HALF:]) for c in codes]


def recovery_codes_left(user):
    return RecoveryCode.objects.filter(user=user, used_at__isnull=True).count()


def _use_recovery_code(user, raw):
    code = _normalize_recovery(raw)
    if len(code) != RECOVERY_LENGTH:
        return False
    # One UPDATE claims the code, so two simultaneous requests can't both use it.
    claimed = (RecoveryCode.objects
               .filter(user=user, code_hash=_hash_recovery(user, code), used_at__isnull=True)
               .update(used_at=timezone.now()))
    return claimed == 1


# -- checking a typed code --------------------------------------------------------------------

TOTP, RECOVERY = "totp", "recovery"


def check_second_factor(user, raw):
    """What was typed on the code page: an authenticator code, or a recovery code.

    Returns TOTP, RECOVERY, or None when it is wrong. The caller does the throttling and the
    logging, because those need the request.
    """
    text = (raw or "").strip()
    digits = re.sub(r"\s", "", text)
    if digits.isascii() and digits.isdigit() and len(digits) == totp.DIGITS:
        with transaction.atomic():
            device = TotpDevice.objects.select_for_update().filter(user=user, confirmed_at__isnull=False).first()
            if device is None:
                return None
            step = totp.verify(device_secret(device), digits, after_step=device.last_used_step)
            if step is None:
                return None
            device.last_used_step = step
            device.save(update_fields=["last_used_step"])
            return TOTP
    if _use_recovery_code(user, text):
        return RECOVERY
    return None


# -- the part of the sign-in that lives in the session -----------------------------------------

def begin_pending(request, user):
    """Password was right and the admin has an authenticator: wait for the code."""
    clear_session(request)
    request.session[MFA_PENDING_KEY] = user.pk
    request.session[MFA_PENDING_AT_KEY] = int(time.time())


def pending_user(request):
    """The admin waiting on the code page, or None (nobody, or too long ago)."""
    uid = request.session.get(MFA_PENDING_KEY)
    started = request.session.get(MFA_PENDING_AT_KEY) or 0
    if not uid or int(time.time()) - started > PENDING_MAX_SECONDS:
        return None
    user = User.objects.filter(pk=uid, is_active=True, is_superuser=True).first()
    return user if user is not None and has_confirmed_device(user) else None


def clear_session(request):
    for key in (MFA_OK_KEY, MFA_SETUP_KEY, MFA_PENDING_KEY, MFA_PENDING_AT_KEY):
        request.session.pop(key, None)


def mark_verified(request):
    """Fully signed in: password and second factor both proven."""
    for key in (MFA_SETUP_KEY, MFA_PENDING_KEY, MFA_PENDING_AT_KEY):
        request.session.pop(key, None)
    request.session[MFA_OK_KEY] = True


def allow_setup(request):
    """The e-mailed code (or a fresh authenticator/recovery code) proved who this is, so they may
    enrol a new authenticator. They still can't open anything else until they do."""
    for key in (MFA_PENDING_KEY, MFA_PENDING_AT_KEY, MFA_OK_KEY):
        request.session.pop(key, None)
    request.session[MFA_SETUP_KEY] = True


def is_verified(request):
    return bool(request.session.get(MFA_OK_KEY))


def may_set_up(request):
    return bool(request.session.get(MFA_SETUP_KEY))


# -- QR code (optional) -------------------------------------------------------------------------

def qr_svg(uri):
    """An inline SVG QR code for `uri`, or "" when the optional `segno` package isn't installed
    (the setup page then shows the key to type in, which every authenticator app supports)."""
    try:
        import segno
        from django.utils.safestring import mark_safe
        # Safe to mark: the markup is generated by the library from our own otpauth link.
        return mark_safe(segno.make(uri, error="m").svg_inline(scale=5, border=2, dark="#000000", light="#ffffff"))
    except Exception:
        return ""
