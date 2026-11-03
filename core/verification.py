"""Unverified accounts: who Guidance may see, and how staff approve or deactivate the rest.

A student who registers with a *shared* term code gets an account straight away, but it is
"unverified": they can use MICA (mood, breathing, Mica chat on a small daily allowance...), and
nothing about them reaches the Guidance dashboard until a staff member approves them. Staff do
that in bulk from one list (permission: verify_students), or deactivate accounts that are junk.

Everything staff-facing that lists or opens a student goes through `visible_students()`.
"""
import json
from datetime import datetime, time, timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import AuditEvent, ChatMessage, EnrolledStudent, UserProfile
from .net import client_ip
from .permissions import require_perm

UNVERIFIED = "unverified"
REJECTED = "rejected"
HIDDEN_FROM_GUIDANCE = (UNVERIFIED, REJECTED)
BULK_LIMIT = 500


# -- Who Guidance may see ------------------------------------------------------------------------

def all_students():
    return User.objects.filter(is_staff=False, is_superuser=False)


def visible_students():
    """Students Guidance staff may see. Unverified and rejected accounts are left out.

    A student with no profile row (very old accounts) counts as verified."""
    return all_students().exclude(profile__verification_status__in=HIDDEN_FROM_GUIDANCE)


def is_unverified(user):
    profile = getattr(user, "profile", None)
    return bool(profile and profile.verification_status == UNVERIFIED)


def can_set_avatar(user):
    """Only verified students may add or change a profile photo (staff and admins always may).

    Unverified and rejected students can still *remove* their own photo. A student with no profile
    row (very old accounts) counts as verified, like everywhere else."""
    if user.is_staff or user.is_superuser:
        return True
    profile = getattr(user, "profile", None)
    return not profile or profile.verification_status == "verified"


AVATAR_LOCKED_MESSAGE = ("Profile photos unlock once the Guidance Office has verified your account. "
                         "Until then your initials are shown.")


def pending_students():
    return all_students().filter(profile__verification_status=UNVERIFIED, is_active=True)


# -- Mica's daily message cap ----------------------------------------------------------------------

def _day_start():
    today = timezone.localdate()
    return timezone.make_aware(datetime.combine(today, time.min))


def _limits():
    return (getattr(settings, "MICA_DAILY_LIMIT", 100),
            getattr(settings, "MICA_DAILY_LIMIT_UNVERIFIED", 15),
            getattr(settings, "MICA_DAILY_LIMIT_UNVERIFIED_TOTAL", 300))


def mica_cap_reached(user):
    """None if `user` may send Mica another message today, otherwise the reason it is refused:
    "personal" (their own allowance is used up) or "pool" (all unverified accounts together are)."""
    verified_cap, unverified_cap, pool_cap = _limits()
    since = _day_start()
    mine = ChatMessage.objects.filter(conversation__user=user, sender="user", created_at__gte=since).count()
    if not is_unverified(user):
        return "personal" if verified_cap and mine >= verified_cap else None
    if unverified_cap and mine >= unverified_cap:
        return "personal"
    if pool_cap:
        pooled = ChatMessage.objects.filter(
            sender="user", created_at__gte=since,
            conversation__user__profile__verification_status=UNVERIFIED).count()
        if pooled >= pool_cap:
            return "pool"
    return None


CAP_MESSAGES = {
    "personal": ("You've reached Mica's chat limit for today. Your other tools, like mood check-ins "
                 "and breathing, still work, and Mica will be back tomorrow."),
    "pool": ("Mica is taking a rest for new accounts today. It'll be back tomorrow, and it opens up "
             "fully once the Guidance Office confirms your account."),
}


# -- Staff API ---------------------------------------------------------------------------------------

def _staff(view):
    view = user_passes_test(lambda u: u.is_staff or u.is_superuser, login_url="login")(view)
    return login_required(login_url="login")(view)


def _audit(request, event, detail):
    AuditEvent.objects.create(actor=request.user, event=event, detail=detail[:300],
                              ip=client_ip(request))


def _name_key(value):
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _on_list(profile, user, listed):
    """True/False: does the student's number + last name match the enrollment list? None if no list is loaded."""
    if not listed:
        return None
    row = EnrolledStudent.objects.filter(student_number__iexact=(profile.student_number or "").strip()).first()
    return bool(row and _name_key(row.last_name) and _name_key(row.last_name) == _name_key(user.last_name))


def _pending_json(u, listed):
    profile = u.profile
    code = profile.registered_code
    return {
        "id": u.id,
        "name": u.get_full_name() or u.username,
        "username": u.username,
        "email": u.email,
        "student_number": profile.student_number or "",
        "course": profile.course or "",
        "year": profile.year_level or "",
        "joined": u.date_joined.isoformat(),
        "code_label": (code.label or "Shared code") if code else "",
        "on_enrollment_list": _on_list(profile, u, listed),
        "profile_incomplete": profile.needs_profile_setup,
    }


@_staff
@require_perm("verify_students")
@require_GET
def pending_list(request):
    listed = EnrolledStudent.objects.exists()
    users = (pending_students().select_related("profile", "profile__registered_code")
             .order_by("-date_joined"))
    return JsonResponse({
        "accounts": [_pending_json(u, listed) for u in users],
        "enrollment_list_loaded": listed,
    })


def _read_ids(request):
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return None, "Invalid request."
    raw = body.get("ids") if isinstance(body, dict) else None
    if not isinstance(raw, list) or not raw:
        return None, "Choose at least one account."
    ids = []
    for v in raw[:BULK_LIMIT]:
        try:
            ids.append(int(v))
        except (TypeError, ValueError):
            return None, "Invalid request."
    return ids, None


@_staff
@require_perm("verify_students")
@require_POST
def pending_approve(request):
    ids, error = _read_ids(request)
    if error:
        return JsonResponse({"error": error}, status=400)
    now = timezone.now()
    with transaction.atomic():
        # Only accounts still waiting; anything already handled by a colleague is skipped.
        profile_ids = list(UserProfile.objects.select_for_update()
                           .filter(user_id__in=ids, user__is_staff=False, user__is_superuser=False,
                                   user__is_active=True, verification_status=UNVERIFIED)
                           .values_list("id", flat=True))
        UserProfile.objects.filter(id__in=profile_ids).update(
            verification_status="verified", verified_by=request.user, verified_at=now)
    if profile_ids:
        _audit(request, "accounts_verified", "Approved %d unverified student account%s"
               % (len(profile_ids), "" if len(profile_ids) == 1 else "s"))
    return JsonResponse({"approved": len(profile_ids), "skipped": len(set(ids)) - len(profile_ids)})


@_staff
@require_perm("verify_students")
@require_POST
def pending_deactivate(request):
    ids, error = _read_ids(request)
    if error:
        return JsonResponse({"error": error}, status=400)
    now = timezone.now()
    with transaction.atomic():
        profiles = list(UserProfile.objects.select_for_update()
                        .filter(user_id__in=ids, user__is_staff=False, user__is_superuser=False,
                                verification_status=UNVERIFIED))
        user_ids = [p.user_id for p in profiles]
        UserProfile.objects.filter(id__in=[p.id for p in profiles]).update(
            verification_status=REJECTED, verified_by=request.user, verified_at=now)
        User.objects.filter(id__in=user_ids).update(is_active=False)
    if profiles:
        _audit(request, "accounts_deactivated", "Deactivated %d unverified student account%s"
               % (len(profiles), "" if len(profiles) == 1 else "s"))
    return JsonResponse({"deactivated": len(profiles), "skipped": len(set(ids)) - len(profiles)})
