"""Registration codes: staff hand a 6-character code to a student, who needs it to register.

* Staff generate codes (at most one a minute each) from Operations > Registration Codes.
* A code works once and expires after REG_CODE_TTL.
* The student enters it on /register/; it is remembered in their session until the
  account (local or Google) is created, which is when the code is used up.
"""
import secrets
from datetime import timedelta

from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import AuditEvent, RegistrationCode
from .permissions import require_perm

REG_CODE_LENGTH = 6
# No 0/O, 1/I/L: the code is read out and copied by hand.
REG_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
REG_CODE_TTL = timedelta(hours=24)
REG_CODE_COOLDOWN = timedelta(minutes=1)      # per staff member
REG_CODE_HISTORY = 15                          # codes shown in the staff list

SESSION_KEY = "registration_code_id"

# Wrong guesses allowed per IP before entry is paused (the code space is ~10^9, this is belt and braces).
ENTRY_MAX_FAILURES = 10
ENTRY_LOCKOUT_SECONDS = 15 * 60


# -- Helpers shared with the student-facing views --------------------------------------------

def normalize_code(raw):
    """'ab3-k9z ' -> 'AB3K9Z'. Anything that isn't a letter or digit is dropped."""
    return "".join(ch for ch in str(raw or "").upper() if ch.isalnum())


def _usable(qs):
    return qs.filter(used_at__isnull=True, expires_at__gt=timezone.now())


def find_usable_code(raw):
    code = normalize_code(raw)
    if len(code) != REG_CODE_LENGTH:
        return None
    return _usable(RegistrationCode.objects.filter(code=code)).first()


def session_code(request, lock=False):
    """The still-usable code this browser entered, or None. lock=True must run inside a transaction."""
    code_id = request.session.get(SESSION_KEY)
    if not code_id:
        return None
    qs = RegistrationCode.objects
    if lock:
        qs = qs.select_for_update()
    code = _usable(qs.filter(pk=code_id)).first()
    if code is None:
        request.session.pop(SESSION_KEY, None)   # used or expired since it was entered
    return code


def use_code(request, code, user):
    """Mark `code` used by `user` (call in the same transaction that creates the account)."""
    code.used_by = user
    code.used_at = timezone.now()
    code.save(update_fields=["used_by", "used_at"])
    request.session.pop(SESSION_KEY, None)


def _client_ip(request):
    return request.META.get("REMOTE_ADDR") or "unknown"


def _fail_key(request):
    return "regcode-fail:%s" % _client_ip(request)


def entry_locked(request):
    return cache.get(_fail_key(request), 0) >= ENTRY_MAX_FAILURES


def record_entry_failure(request):
    key = _fail_key(request)
    cache.add(key, 0, ENTRY_LOCKOUT_SECONDS)
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, ENTRY_LOCKOUT_SECONDS)


def clear_entry_failures(request):
    cache.delete(_fail_key(request))


def new_code_value():
    return "".join(secrets.choice(REG_CODE_ALPHABET) for _ in range(REG_CODE_LENGTH))


# -- Staff API ---------------------------------------------------------------------------------

staff_only = [login_required(login_url="login"),
              user_passes_test(lambda u: u.is_staff or u.is_superuser, login_url="login")]


def _staff(view):
    for deco in reversed(staff_only):
        view = deco(view)
    return view


def _status(code, now):
    if code.used_at:
        return "used"
    return "expired" if code.expires_at <= now else "active"


def _cooldown_left(user, now=None):
    """Seconds until this staff member may generate another code (0 = now)."""
    now = now or timezone.now()
    last = RegistrationCode.objects.filter(created_by=user).order_by("-created_at").first()
    if not last:
        return 0
    left = (last.created_at + REG_CODE_COOLDOWN - now).total_seconds()
    return max(0, int(left) + (1 if left % 1 else 0))


def _code_json(code, now):
    used_by = code.used_by
    return {
        "code": code.code,
        "status": _status(code, now),
        "created_at": code.created_at.isoformat(),
        "expires_at": code.expires_at.isoformat(),
        "used_at": code.used_at.isoformat() if code.used_at else None,
        "used_by": (used_by.get_full_name() or used_by.username) if used_by else "",
    }


def _payload(user):
    now = timezone.now()
    codes = RegistrationCode.objects.filter(created_by=user).select_related("used_by")[:REG_CODE_HISTORY]
    return {
        "cooldown_seconds": _cooldown_left(user, now),
        "cooldown_limit": int(REG_CODE_COOLDOWN.total_seconds()),
        "ttl_hours": int(REG_CODE_TTL.total_seconds() // 3600),
        "codes": [_code_json(c, now) for c in codes],
    }


@_staff
@require_perm("generate_codes")
@require_GET
def codes_list(request):
    return JsonResponse(_payload(request.user))


@_staff
@require_perm("generate_codes")
@require_POST
def code_generate(request):
    with transaction.atomic():
        # Lock this staff member's row so two quick clicks can't both pass the cooldown.
        User.objects.select_for_update().get(pk=request.user.pk)
        wait = _cooldown_left(request.user)
        if wait:
            data = _payload(request.user)
            data["error"] = "Please wait %d second%s before generating another code." % (wait, "" if wait == 1 else "s")
            return JsonResponse(data, status=429)

        code = None
        for _ in range(20):
            try:
                with transaction.atomic():
                    code = RegistrationCode.objects.create(
                        code=new_code_value(), created_by=request.user,
                        expires_at=timezone.now() + REG_CODE_TTL)
                break
            except IntegrityError:      # that value already exists; draw again
                continue
        if code is None:
            return JsonResponse({"error": "Couldn't make a code. Please try again."}, status=503)

        AuditEvent.objects.create(actor=request.user, event="reg_code_created",
                                  detail="Registration code generated",   # never log the code itself
                                  ip=request.META.get("REMOTE_ADDR") or None)

    data = _payload(request.user)
    data["new_code"] = code.code
    return JsonResponse(data)
