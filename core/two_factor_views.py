"""Pages for System Admin two-factor sign-in (see core/twofactor.py for how it fits together).

  /login/two-factor/             enter the authenticator code (or a recovery code) after the password
  /account/two-factor/setup/     first-time (or replacement) setup: key/QR, confirm a code, save recovery codes
  /account/two-factor/           status, new recovery codes, switch to a new authenticator
"""
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from . import totp, twofactor
from .models import AuditEvent, LoginThrottle
from .net import client_ip
from .views import (_ip_lockout_seconds_left, _lockout_seconds_left, _log_sign_in_failure, _plural,
                    _record_failed_ip, _record_failed_login)

logger = logging.getLogger(__name__)

BROKEN_KEY_MESSAGE = ("Two-factor sign-in can't be checked right now (the stored secret no longer matches this "
                      "server's key). Ask whoever runs the server to run: python manage.py reset_admin_2fa "
                      "--username %s")


def _audit(request, user, detail):
    AuditEvent.objects.create(actor=user, event="admin_action", detail=str(detail)[:300], ip=client_ip(request))


def _wait_seconds(request, user):
    """Seconds the admin must wait before another code attempt (this account's lock, or the address's)."""
    return max(_lockout_seconds_left(twofactor.throttle_key(user)), _ip_lockout_seconds_left(request))


def _register_wrong_code(request, user):
    """Count one wrong code. Returns (tries_left, lockout_seconds)."""
    left, lock = _record_failed_login(twofactor.throttle_key(user), twofactor.TOTP_MAX_ATTEMPTS,
                                      twofactor.TOTP_LOCKOUT_SECONDS)
    _log_sign_in_failure(request, "totp", user=user)
    return left, max(lock, _record_failed_ip(request))


def _wrong_code_message(left, lockout):
    if lockout:
        return "Too many wrong codes. Try again in %s." % _plural(lockout, "second")
    return "That code isn't right. %d %s left." % (left, "try" if left == 1 else "tries")


# -- 1. the code page at sign-in --------------------------------------------------------------

def _totp_page(request, status=200, **extra):
    return render(request, "core/login-totp.html", extra, status=status)


@never_cache
@require_http_methods(["GET", "POST"])
def login_totp(request):
    if request.user.is_authenticated:
        return redirect("login")
    user = twofactor.pending_user(request)
    if user is None:
        messages.error(request, "Your sign-in session expired. Please sign in again.")
        return redirect("login")

    if request.method == "GET":
        return _totp_page(request)

    wait = _wait_seconds(request, user)
    if wait:
        return _totp_page(request, status=429, lockout_seconds=wait)

    try:
        result = twofactor.check_second_factor(user, request.POST.get("code"))
    except ValueError:
        logger.exception("Could not read the authenticator secret of user %s", user.pk)
        messages.error(request, BROKEN_KEY_MESSAGE % user.username)
        return redirect("login")

    if result is None:
        left, lockout = _register_wrong_code(request, user)
        if lockout:
            return _totp_page(request, status=429, lockout_seconds=lockout)
        messages.error(request, _wrong_code_message(left, lockout))
        return _totp_page(request)

    LoginThrottle.objects.filter(username_key=twofactor.throttle_key(user)).delete()
    auth_login(request, user)
    request.session.set_expiry(0)               # admin sessions end with the browser
    twofactor.mark_verified(request)

    if result == twofactor.RECOVERY:
        left = twofactor.recovery_codes_left(user)
        _audit(request, user, "System Admin @%s signed in with a recovery code (%d left)" % (user.username, left))
        messages.warning(request, "You signed in with a recovery code, and it can't be used again. "
                                  "You have %s left. If you lost your phone, switch to a new authenticator below."
                         % _plural(left, "recovery code"))
        return redirect("two_factor_manage")
    return redirect("admin_dashboard")


# -- 2. setup ---------------------------------------------------------------------------------

def _recovery_codes_page(request, codes, *, first_time):
    return render(request, "core/two-factor-codes.html", {"codes": codes, "first_time": first_time})


@never_cache
@login_required(login_url="login")
@require_http_methods(["GET", "POST"])
def two_factor_setup(request):
    user = request.user
    if not user.is_superuser:
        return redirect("login")
    if not twofactor.may_set_up(request):
        return redirect("two_factor_manage" if twofactor.is_verified(request) else "login")
    if twofactor.has_confirmed_device(user):
        twofactor.clear_session(request)        # already set up: they must sign in with it
        messages.error(request, "Two-factor sign-in is already set up. Please sign in again.")
        return redirect("login")

    try:
        secret = twofactor.device_secret(twofactor.pending_device(user))
    except ValueError:
        # A half-finished setup from before the key changed: start it over.
        twofactor.remove_device(user)
        secret = twofactor.device_secret(twofactor.pending_device(user))

    if request.method == "POST":
        wait = _wait_seconds(request, user)
        if wait:
            messages.error(request, "Too many wrong codes. Try again in %s." % _plural(wait, "second"))
        elif twofactor.confirm_device(user, request.POST.get("code")):
            LoginThrottle.objects.filter(username_key=twofactor.throttle_key(user)).delete()
            codes = twofactor.new_recovery_codes(user)
            twofactor.mark_verified(request)
            _audit(request, user, "System Admin @%s turned on two-factor sign-in" % user.username)
            return _recovery_codes_page(request, codes, first_time=True)
        else:
            left, lockout = _register_wrong_code(request, user)
            messages.error(request, _wrong_code_message(left, lockout))

    uri = totp.provisioning_uri(secret, user.username, getattr(settings, "TOTP_ISSUER", "MICA"))
    return render(request, "core/two-factor-setup.html", {
        "secret_display": totp.format_secret(secret),
        "otpauth_uri": uri,
        "qr_svg": twofactor.qr_svg(uri),
        "step_seconds": totp.PERIOD,
    })


# -- 3. manage --------------------------------------------------------------------------------

@never_cache
@login_required(login_url="login")
@require_http_methods(["GET", "POST"])
def two_factor_manage(request):
    user = request.user
    if not user.is_superuser:
        return redirect("login")
    device = twofactor.confirmed_device(user)

    if request.method == "POST" and device is not None:
        action = request.POST.get("action")
        wait = _wait_seconds(request, user)
        if action not in ("regenerate", "replace"):
            messages.error(request, "Please choose an action.")
        elif wait:
            messages.error(request, "Too many wrong codes. Try again in %s." % _plural(wait, "second"))
        else:
            try:
                result = twofactor.check_second_factor(user, request.POST.get("code"))
            except ValueError:
                logger.exception("Could not read the authenticator secret of user %s", user.pk)
                messages.error(request, BROKEN_KEY_MESSAGE % user.username)
                return redirect("two_factor_manage")
            if result is None:
                left, lockout = _register_wrong_code(request, user)
                messages.error(request, _wrong_code_message(left, lockout))
            else:
                LoginThrottle.objects.filter(username_key=twofactor.throttle_key(user)).delete()
                if action == "regenerate":
                    codes = twofactor.new_recovery_codes(user)
                    _audit(request, user, "System Admin @%s made new recovery codes" % user.username)
                    return _recovery_codes_page(request, codes, first_time=False)
                twofactor.remove_device(user)
                twofactor.allow_setup(request)       # the code just proved who they are
                _audit(request, user, "System Admin @%s is switching to a new authenticator" % user.username)
                return redirect("two_factor_setup")
        return redirect("two_factor_manage")

    left = twofactor.recovery_codes_left(user) if device else 0
    return render(request, "core/two-factor-manage.html", {
        "enabled": device is not None,
        "enabled_since": device.confirmed_at if device else None,
        "codes_left": left,
        "codes_low": device is not None and left <= 2,
        "required": twofactor.required(),
        "step_seconds": totp.PERIOD,
    })
