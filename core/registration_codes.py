"""Registration codes: what a student needs to type on /register/ before they can make an account.

Two kinds (see models.RegistrationCode):

* single-use codes: staff make one at a time, or a batch of up to MAX_BATCH to hand out on slips.
  Each works once. The account it creates is verified straight away: staff handed it over.
* a shared term code: one code for a whole class or term, with a cap on how many accounts it can
  create and an expiry. Accounts made with it are *unverified* until staff approve them
  (core/verification.py), so a leaked code gets an outsider a private wellness app and nothing more.

The student enters the code on /register/; it is remembered in their session until the account
(local or Google) is created, which is when one use of the code is spent.
"""
import csv
import json
import logging
import secrets
import uuid
from datetime import timedelta

from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import AuditEvent, RegistrationCode
from .net import client_ip
from .permissions import require_perm

logger = logging.getLogger(__name__)

REG_CODE_LENGTH = 6                            # single-use codes
SHARED_CODE_LENGTH = 8                         # generated shared codes
CODE_MIN_LENGTH = 6                            # anything a student can type
CODE_MAX_LENGTH = 20
# No 0/O, 1/I/L: the code is read out and copied by hand.
REG_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
REG_CODE_TTL = timedelta(hours=24)             # a single code made one at a time
REG_CODE_COOLDOWN = timedelta(minutes=1)       # per staff member, across every way of making codes
REG_CODE_HISTORY = 15                          # single codes shown in the staff list

MAX_BATCH = 100                                # codes in one batch
BATCH_MAX_OUTSTANDING = 300                    # unused batch codes one staff member may hold at once
BATCH_DEFAULT_DAYS, BATCH_MAX_DAYS = 7, 30
SHARED_DEFAULT_USES, SHARED_MAX_USES = 100, 1000
SHARED_DEFAULT_DAYS, SHARED_MAX_DAYS = 30, 180
BATCHES_SHOWN = 10

SESSION_KEY = "registration_code_id"

# Wrong guesses allowed per IP before entry is paused.
ENTRY_MAX_FAILURES = 10
ENTRY_LOCKOUT_SECONDS = 15 * 60


# -- Helpers shared with the student-facing views --------------------------------------------

def normalize_code(raw):
    """'ab3-k9z ' -> 'AB3K9Z'. Anything that isn't a letter or digit is dropped."""
    return "".join(ch for ch in str(raw or "").upper() if ch.isalnum())


def _usable(qs):
    """Codes that still work: not expired, not revoked, and with a use left."""
    return qs.filter(revoked_at__isnull=True, expires_at__gt=timezone.now(), use_count__lt=F("max_uses"))


def find_usable_code(raw):
    code = normalize_code(raw)
    if not CODE_MIN_LENGTH <= len(code) <= CODE_MAX_LENGTH:
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
        request.session.pop(SESSION_KEY, None)   # used up, revoked or expired since it was entered
    return code


def profile_fields(code):
    """UserProfile fields for an account made with `code`: shared-code accounts start unverified."""
    return {
        "verification_status": "unverified" if code.kind == "shared" else "verified",
        "registered_code": code,
    }


def use_code(request, code, user):
    """Spend one use of `code` for `user` (call in the same transaction that creates the account,
    with `code` loaded through session_code(lock=True))."""
    code.use_count += 1
    code.used_at = timezone.now()
    fields = ["use_count", "used_at"]
    if code.kind == "single":
        code.used_by = user
        fields.append("used_by")
    code.save(update_fields=fields)
    request.session.pop(SESSION_KEY, None)


def _client_ip(request):
    return client_ip(request) or "unknown"


def _fail_key(request):
    return "regcode-fail:%s" % _client_ip(request)


def entry_locked(request):
    # The lockout counters live in the cache. If the cache is down or its table is missing, let the
    # student carry on (and log it) rather than blocking every registration with a 500.
    try:
        return cache.get(_fail_key(request), 0) >= ENTRY_MAX_FAILURES
    except Exception:
        logger.exception("Registration lockout cache unavailable; allowing code entry")
        return False


def record_entry_failure(request):
    key = _fail_key(request)
    try:
        cache.add(key, 0, ENTRY_LOCKOUT_SECONDS)
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, ENTRY_LOCKOUT_SECONDS)
    except Exception:
        logger.exception("Registration lockout cache unavailable; failure not recorded")


def clear_entry_failures(request):
    try:
        cache.delete(_fail_key(request))
    except Exception:
        logger.exception("Registration lockout cache unavailable; failures not cleared")


def new_code_value(length=REG_CODE_LENGTH):
    return "".join(secrets.choice(REG_CODE_ALPHABET) for _ in range(length))


# -- Staff API ---------------------------------------------------------------------------------

staff_only = [login_required(login_url="login"),
              user_passes_test(lambda u: u.is_staff or u.is_superuser, login_url="login")]


def _staff(view):
    for deco in reversed(staff_only):
        view = deco(view)
    return view


def _status(code, now):
    if code.revoked_at:
        return "revoked"
    if code.use_count >= code.max_uses:
        return "used" if code.kind == "single" else "full"
    return "expired" if code.expires_at <= now else "active"


def _cooldown_left(user, now=None):
    """Seconds until this staff member may make more codes (0 = now)."""
    now = now or timezone.now()
    last = RegistrationCode.objects.filter(created_by=user).order_by("-created_at").first()
    if not last:
        return 0
    left = (last.created_at + REG_CODE_COOLDOWN - now).total_seconds()
    return max(0, int(left) + (1 if left % 1 else 0))


def _person(u):
    return (u.get_full_name() or u.username) if u else ""


def _code_json(code, now):
    return {
        "id": code.id,
        "code": code.code,
        "status": _status(code, now),
        "created_at": code.created_at.isoformat(),
        "expires_at": code.expires_at.isoformat(),
        "used_at": code.used_at.isoformat() if code.used_at else None,
        "used_by": _person(code.used_by),
    }


def _shared_json(code, now):
    pending = code.registrants.filter(verification_status="unverified").count()
    return {
        "id": code.id,
        "code": code.code,
        "label": code.label,
        "status": _status(code, now),
        "max_uses": code.max_uses,
        "use_count": code.use_count,
        "pending": pending,
        "created_at": code.created_at.isoformat(),
        "expires_at": code.expires_at.isoformat(),
        "created_by": _person(code.created_by),
    }


def _batches_json(user, now):
    rows = (RegistrationCode.objects.filter(created_by=user).exclude(batch="")
            .order_by("-created_at"))
    out, seen = [], {}
    for c in rows:
        b = seen.get(c.batch)
        if b is None:
            if len(seen) >= BATCHES_SHOWN:
                continue
            b = seen[c.batch] = {"batch": c.batch, "label": c.label, "created_at": c.created_at.isoformat(),
                                 "expires_at": c.expires_at.isoformat(), "total": 0, "used": 0, "active": 0}
            out.append(b)
        b["total"] += 1
        state = _status(c, now)
        if state == "used":
            b["used"] += 1
        elif state == "active":
            b["active"] += 1
    return out


def _payload(user):
    now = timezone.now()
    singles = (RegistrationCode.objects.filter(created_by=user, kind="single", batch="")
               .select_related("used_by")[:REG_CODE_HISTORY])
    shared = (RegistrationCode.objects.filter(kind="shared").select_related("created_by")
              .order_by("-created_at")[:BATCHES_SHOWN])
    return {
        "cooldown_seconds": _cooldown_left(user, now),
        "cooldown_limit": int(REG_CODE_COOLDOWN.total_seconds()),
        "ttl_hours": int(REG_CODE_TTL.total_seconds() // 3600),
        "limits": {"max_batch": MAX_BATCH, "batch_days": BATCH_MAX_DAYS, "batch_default_days": BATCH_DEFAULT_DAYS,
                   "shared_max_uses": SHARED_MAX_USES, "shared_default_uses": SHARED_DEFAULT_USES,
                   "shared_days": SHARED_MAX_DAYS, "shared_default_days": SHARED_DEFAULT_DAYS},
        "codes": [_code_json(c, now) for c in singles],
        "batches": _batches_json(user, now),
        "shared": [_shared_json(c, now) for c in shared],
    }


def _audit(request, event, detail):
    AuditEvent.objects.create(actor=request.user, event=event, detail=detail[:300],
                              ip=client_ip(request))


def _cooldown_response(user):
    wait = _cooldown_left(user)
    if not wait:
        return None
    data = _payload(user)
    data["error"] = "Please wait %d second%s before generating more codes." % (wait, "" if wait == 1 else "s")
    return JsonResponse(data, status=429)


def _body(request):
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _int(value, default, low, high):
    """`value` as an int, `default` if it's missing, or None if it isn't a number in [low, high]."""
    if value in (None, ""):
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if low <= n <= high else None


def _label(value):
    return " ".join(str(value or "").split())[:80]


@_staff
@require_perm("generate_codes")
@require_GET
def codes_list(request):
    return JsonResponse(_payload(request.user))


@_staff
@require_perm("generate_codes")
@require_POST
def code_generate(request):
    """One single-use code, good for REG_CODE_TTL."""
    with transaction.atomic():
        # Lock this staff member's row so two quick clicks can't both pass the cooldown.
        User.objects.select_for_update().get(pk=request.user.pk)
        blocked = _cooldown_response(request.user)
        if blocked:
            return blocked

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

        _audit(request, "reg_code_created", "Registration code generated")    # never log the code itself

    data = _payload(request.user)
    data["new_code"] = code.code
    return JsonResponse(data)


@_staff
@require_perm("generate_codes")
@require_POST
def batch_generate(request):
    """A batch of single-use codes to hand out one by one. Each works once; the batch shares an expiry."""
    body = _body(request)
    if body is None:
        return JsonResponse({"error": "Invalid request."}, status=400)
    count = _int(body.get("count"), None, 1, MAX_BATCH)
    if count is None:
        return JsonResponse({"error": "Choose between 1 and %d codes." % MAX_BATCH}, status=400)
    days = _int(body.get("days"), BATCH_DEFAULT_DAYS, 1, BATCH_MAX_DAYS)
    if days is None:
        return JsonResponse({"error": "Codes can last 1 to %d days." % BATCH_MAX_DAYS}, status=400)
    label = _label(body.get("label"))

    with transaction.atomic():
        User.objects.select_for_update().get(pk=request.user.pk)
        blocked = _cooldown_response(request.user)
        if blocked:
            return blocked
        outstanding = _usable(RegistrationCode.objects.filter(created_by=request.user, kind="single")).count()
        if outstanding + count > BATCH_MAX_OUTSTANDING:
            return JsonResponse({"error": "You already hold %d unused codes (limit %d). Use or revoke some first."
                                          % (outstanding, BATCH_MAX_OUTSTANDING)}, status=400)

        batch_id = uuid.uuid4().hex[:12]
        expires = timezone.now() + timedelta(days=days)
        values = None
        for _ in range(5):
            wanted = set()
            while len(wanted) < count:
                wanted.add(new_code_value())
            taken = set(RegistrationCode.objects.filter(code__in=wanted).values_list("code", flat=True))
            wanted -= taken
            if len(wanted) < count:         # a collision: top up on the next pass
                continue
            try:
                with transaction.atomic():
                    RegistrationCode.objects.bulk_create([
                        RegistrationCode(code=v, kind="single", label=label, batch=batch_id,
                                         created_by=request.user, expires_at=expires)
                        for v in sorted(wanted)])
                values = sorted(wanted)
                break
            except IntegrityError:
                continue
        if values is None:
            return JsonResponse({"error": "Couldn't make the codes. Please try again."}, status=503)
        _audit(request, "reg_batch_created", "Batch of %d registration codes generated" % count)

    data = _payload(request.user)
    data["new_batch"] = {"batch": batch_id, "label": label, "codes": values, "expires_at": expires.isoformat()}
    return JsonResponse(data)


@_staff
@require_perm("generate_codes")
@require_GET
def batch_detail(request, batch):
    """Every code in one batch and who used it, for the "View" pop-up. Same access rule as the CSV."""
    qs = (RegistrationCode.objects.filter(batch=batch, kind="single")
          .select_related("used_by", "used_by__profile").order_by("code"))
    if not request.user.is_superuser:
        qs = qs.filter(created_by=request.user)
    rows = list(qs)
    if not rows:
        return JsonResponse({"error": "Batch not found."}, status=404)
    now = timezone.now()
    codes = []
    for c in rows:
        u = c.used_by
        profile = getattr(u, "profile", None) if u else None
        codes.append({
            "id": c.id, "code": c.code, "status": _status(c, now),
            "used_at": c.used_at.isoformat() if c.used_at else None,
            "used_by": _person(u),
            "student_number": profile.student_number if profile else "",
        })
    return JsonResponse({"batch": batch, "label": rows[0].label,
                         "created_at": rows[0].created_at.isoformat(),
                         "expires_at": rows[0].expires_at.isoformat(), "codes": codes})


@_staff
@require_perm("generate_codes")
@require_GET
def batch_download(request, batch):
    """The codes in one batch as a CSV, for printing slips. Only the staff member who made it (or the admin)."""
    qs = RegistrationCode.objects.filter(batch=batch, kind="single").order_by("code")
    if not request.user.is_superuser:
        qs = qs.filter(created_by=request.user)
    rows = list(qs)
    if not rows:
        return JsonResponse({"error": "Batch not found."}, status=404)
    now = timezone.now()
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="registration-codes-%s.csv"' % batch
    writer = csv.writer(response)
    writer.writerow(["Code", "Label", "Status", "Expires"])
    for c in rows:
        writer.writerow([c.code, c.label, _status(c, now), timezone.localtime(c.expires_at).strftime("%Y-%m-%d %H:%M")])
    return response


@_staff
@require_perm("generate_codes")
@require_POST
def shared_create(request):
    """The shared term code: many students, a cap on accounts, an expiry. Its accounts start unverified."""
    body = _body(request)
    if body is None:
        return JsonResponse({"error": "Invalid request."}, status=400)
    max_uses = _int(body.get("max_uses"), SHARED_DEFAULT_USES, 1, SHARED_MAX_USES)
    if max_uses is None:
        return JsonResponse({"error": "Allow between 1 and %d sign-ups." % SHARED_MAX_USES}, status=400)
    days = _int(body.get("days"), SHARED_DEFAULT_DAYS, 1, SHARED_MAX_DAYS)
    if days is None:
        return JsonResponse({"error": "A shared code can last 1 to %d days." % SHARED_MAX_DAYS}, status=400)
    label = _label(body.get("label"))
    if not label:
        return JsonResponse({"error": "Give the code a name, e.g. \u201c1st Sem AY 2026-2027\u201d."}, status=400)

    custom = normalize_code(body.get("code"))
    if custom and not CODE_MIN_LENGTH <= len(custom) <= CODE_MAX_LENGTH:
        return JsonResponse({"error": "A custom code needs %d to %d letters or numbers."
                                      % (CODE_MIN_LENGTH, CODE_MAX_LENGTH)}, status=400)

    with transaction.atomic():
        User.objects.select_for_update().get(pk=request.user.pk)
        blocked = _cooldown_response(request.user)
        if blocked:
            return blocked
        code = None
        for _ in range(20):
            value = custom or new_code_value(SHARED_CODE_LENGTH)
            try:
                with transaction.atomic():
                    code = RegistrationCode.objects.create(
                        code=value, kind="shared", label=label, max_uses=max_uses,
                        created_by=request.user, expires_at=timezone.now() + timedelta(days=days))
                break
            except IntegrityError:
                if custom:
                    return JsonResponse({"error": "That code is already in use. Choose another."}, status=400)
        if code is None:
            return JsonResponse({"error": "Couldn't make a code. Please try again."}, status=503)
        _audit(request, "reg_shared_created",
               "Shared registration code \u201c%s\u201d created (%d sign-ups, %d days)" % (label, max_uses, days))

    data = _payload(request.user)
    data["new_shared"] = code.code
    return JsonResponse(data)


@_staff
@require_perm("generate_codes")
@require_POST
def code_revoke(request):
    """Stop codes working. {"id": n} for one code, or {"batch": "..."} for the unused rest of a batch.

    Staff may revoke their own codes and any shared code; the admin may revoke anything."""
    body = _body(request)
    if body is None:
        return JsonResponse({"error": "Invalid request."}, status=400)
    now = timezone.now()
    if body.get("batch"):
        qs = RegistrationCode.objects.filter(batch=str(body["batch"]), kind="single")
    elif body.get("id") is not None:
        n = _int(body.get("id"), None, 1, 2**62)
        if n is None:
            return JsonResponse({"error": "Invalid request."}, status=400)
        qs = RegistrationCode.objects.filter(pk=n)
    else:
        return JsonResponse({"error": "Invalid request."}, status=400)
    if not request.user.is_superuser:
        qs = qs.filter(Q(created_by=request.user) | Q(kind="shared"))
    qs = qs.filter(revoked_at__isnull=True, use_count__lt=F("max_uses"))
    revoked = qs.update(revoked_at=now, revoked_by=request.user)
    if not revoked:
        return JsonResponse({"error": "Nothing to revoke: it may already be used or revoked."}, status=404)
    _audit(request, "reg_code_revoked", "Revoked %d registration code%s" % (revoked, "" if revoked == 1 else "s"))
    data = _payload(request.user)
    data["revoked"] = revoked
    return JsonResponse(data)
