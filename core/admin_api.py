"""System-admin endpoints for staff subclasses and staff accounts.

Used by the "Roles & Permissions" and "User Accounts" tabs of the admin dashboard.
"""

import time
from datetime import datetime, timedelta
from pathlib import Path
import json
import logging

from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.conf import settings
from django.contrib.sessions.models import Session
from django.db import connection, transaction
from django.db.models import Count, Q
from django.core.paginator import Paginator
from django.utils import timezone
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.crypto import get_random_string
from django.views.decorators.http import require_POST

from .models import (COURSES, SERVICES, StaffProfile, StaffSubclass, GuidanceThread, GuidanceMessage,
                     StudentFlag, StudentNote, Appointment, AccessLog, StudentStatusLog,
                     LoginThrottle, MoodEntry, ChatConversation)
from .permissions import BOOL_FIELDS, PERMISSION_FIELDS, SERVICE_FIELDS, SERVICE_KEYS, subclass_json

logger = logging.getLogger(__name__)

admin_only = [login_required(login_url='login'), user_passes_test(lambda u: u.is_superuser, login_url='login')]


def _admin(view):
    for deco in reversed(admin_only):
        view = deco(view)
    return view


def _body(request):
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _clean_subclass(data, instance=None):
    """Validate a subclass payload. Returns (values, error_message)."""
    name = " ".join(str(data.get("name") or "").split())
    if len(name) < 2 or len(name) > 60:
        return None, "Give the role a name (2\u201360 characters)."
    clash = StaffSubclass.objects.filter(name__iexact=name)
    if instance:
        clash = clash.exclude(pk=instance.pk)
    if clash.exists():
        return None, "A role called \u201c%s\u201d already exists." % name
    values = {"name": name, "description": str(data.get("description") or "").strip()[:200]}
    for f in BOOL_FIELDS:
        values[f] = bool(data.get(f))
    for f in SERVICE_FIELDS:
        raw = data.get(f) or []
        if not isinstance(raw, list):
            return None, "Invalid services."
        values[f] = [s for s in SERVICE_KEYS if s in raw]       # keeps a stable order, drops unknowns
    return values, None


@_admin
def subclasses(request):
    """GET: all staff subclasses + the permission definitions. POST: create one."""
    if request.method == "POST":
        data = _body(request)
        if data is None:
            return JsonResponse({"error": "Invalid request."}, status=400)
        values, error = _clean_subclass(data)
        if error:
            return JsonResponse({"error": error}, status=400)
        sub = StaffSubclass.objects.create(**values)
        return JsonResponse({"subclass": subclass_json(sub, 0)}, status=201)

    return JsonResponse({
        "subclasses": [subclass_json(s) for s in StaffSubclass.objects.all()],
        "services": [{"key": k, "label": l} for k, l in SERVICES],
        "fields": [{"key": k, "label": l, "kind": kind} for k, l, kind in PERMISSION_FIELDS],
    })


@_admin
@require_POST
def subclass_update(request, subclass_id):
    sub = get_object_or_404(StaffSubclass, id=subclass_id)
    data = _body(request)
    if data is None:
        return JsonResponse({"error": "Invalid request."}, status=400)
    values, error = _clean_subclass(data, instance=sub)
    if error:
        return JsonResponse({"error": error}, status=400)
    for k, v in values.items():
        setattr(sub, k, v)
    sub.save()
    return JsonResponse({"subclass": subclass_json(sub)})


@_admin
@require_POST
def subclass_delete(request, subclass_id):
    sub = get_object_or_404(StaffSubclass, id=subclass_id)
    count = sub.members.count()
    if count:
        return JsonResponse({
            "error": "%d staff account%s still use \u201c%s\u201d. Move them to another role first."
                     % (count, "" if count == 1 else "s", sub.name)}, status=409)
    sub.delete()
    return JsonResponse({"status": "ok"})


def _staff_json(user):
    sp = getattr(user, "staff_profile", None)
    sub = sp.subclass if sp and sp.subclass_id else None
    if not user.is_active:
        status = "Suspended"
    elif user.last_login is None:
        status = "Pending"
    else:
        status = "Active"
    return {
        "id": user.id,
        "name": user.get_full_name() or user.username,
        "email": user.email,
        "username": user.username,
        "subclass_id": sub.id if sub else None,
        "subclass": sub.name if sub else (sp.get_role_display() if sp else "Guidance Staff"),
        "status": status,
        "last_login": user.last_login.strftime("%Y-%m-%d %H:%M") if user.last_login else "\u2014",
        "joined": user.date_joined.strftime("%Y-%m-%d"),
    }


@_admin
def staff_accounts(request):
    users = (User.objects.filter(is_staff=True, is_superuser=False)
             .select_related("staff_profile", "staff_profile__subclass").order_by("-date_joined"))
    return JsonResponse({"staff": [_staff_json(u) for u in users]})


def _unique_username(email):
    base = (email.split("@")[0] or "staff")[:30]
    name, n = base, 1
    while User.objects.filter(username__iexact=name).exists():
        n += 1
        name = "%s%d" % (base, n)
    return name


@_admin
@require_POST
def staff_create(request):
    from .views import _send_reset_link        # imported late: views imports a lot

    data = _body(request)
    if data is None:
        return JsonResponse({"error": "Invalid request."}, status=400)
    full_name = " ".join(str(data.get("name") or "").split())[:150]
    email = str(data.get("email") or "").strip().lower()
    if not full_name:
        return JsonResponse({"error": "Please enter the staff member\u2019s name."}, status=400)
    try:
        validate_email(email)
    except ValidationError:
        return JsonResponse({"error": "Please enter a valid email address."}, status=400)
    if User.objects.filter(email__iexact=email).exists():
        return JsonResponse({"error": "An account with that email already exists."}, status=409)
    sub = StaffSubclass.objects.filter(id=data.get("subclass_id")).first()
    if sub is None:
        return JsonResponse({"error": "Choose a staff role for this account."}, status=400)

    first, _, last = full_name.partition(" ")
    with transaction.atomic():
        user = User.objects.create_user(
            username=_unique_username(email), email=email, first_name=first[:150], last_name=last[:150],
            password=get_random_string(32), is_staff=True,
        )
        profile, _ = StaffProfile.objects.get_or_create(user=user, defaults={"role": "other"})
        profile.subclass = sub
        profile.license_number = str(data.get("license_number") or "").strip()[:50]
        profile.save()

    # The random password is never shown; the new staff member sets their own from the e-mailed link.
    email_sent = True
    try:
        _send_reset_link(request, email)
    except Exception:
        logger.exception("Could not e-mail the set-password link to a new staff account.")
        email_sent = False
    return JsonResponse({"staff": _staff_json(user), "email_sent": email_sent}, status=201)


@_admin
@require_POST
def staff_set_subclass(request, user_id):
    user = get_object_or_404(User, id=user_id, is_staff=True, is_superuser=False)
    data = _body(request)
    if data is None:
        return JsonResponse({"error": "Invalid request."}, status=400)
    sub = StaffSubclass.objects.filter(id=data.get("subclass_id")).first()
    if sub is None:
        return JsonResponse({"error": "Choose a staff role."}, status=400)
    profile, _ = StaffProfile.objects.get_or_create(user=user, defaults={"role": "other"})
    profile.subclass = sub
    profile.save(update_fields=["subclass"])
    return JsonResponse({"staff": _staff_json(user)})


@_admin
@require_POST
def staff_set_active(request, user_id):
    user = get_object_or_404(User, id=user_id, is_staff=True, is_superuser=False)
    data = _body(request)
    if data is None:
        return JsonResponse({"error": "Invalid request."}, status=400)
    active = bool(data.get("active"))
    released = 0
    with transaction.atomic():
        user.is_active = active
        user.save(update_fields=["is_active"])
        if not active:
            # Don't leave chats or cases stuck with someone who can't log in.
            released = GuidanceThread.objects.filter(counselor=user).update(counselor=None)
            StudentFlag.objects.filter(assigned_to=user).exclude(status="resolved").update(assigned_to=None, status="new")
    return JsonResponse({"staff": _staff_json(user), "released_threads": released})


@_admin
@require_POST
def staff_reset_password(request, user_id):
    from .views import _send_reset_link
    user = get_object_or_404(User, id=user_id, is_staff=True, is_superuser=False)
    if not user.email:
        return JsonResponse({"error": "This account has no email address."}, status=400)
    try:
        _send_reset_link(request, user.email)
    except Exception:
        logger.exception("Could not send a reset link to staff account %s.", user.pk)
        return JsonResponse({"error": "Could not send the e-mail. Check the e-mail settings."}, status=502)
    return JsonResponse({"status": "ok"})


@_admin
def dashboard_stats(request):
    """Real numbers for the admin dashboard. Counts and names only, nothing a student wrote."""
    import firebase_admin
    from .views import _time_ago, CONSENT_VERSION     # imported late: views imports a lot

    now = timezone.now()
    today = timezone.localdate()
    students = User.objects.filter(is_staff=False, is_superuser=False)
    staff = User.objects.filter(is_staff=True, is_superuser=False)
    n_students = students.count()

    def person(u):
        return (u.get_full_name() or u.username) if u else "Former staff"

    # ---- top cards
    on_consent = students.filter(profile__consent_version=CONSENT_VERSION).count()
    signed_in = set()
    now_ts = int(time.time())
    for s in Session.objects.filter(expire_date__gt=now):
        data = s.get_decoded()
        uid, last = data.get("_auth_user_id"), data.get("last_activity")
        if uid and last and now_ts - last <= 15 * 60:
            signed_in.add(uid)
    locked_now = LoginThrottle.objects.filter(locked_until__gt=now).count()
    failed_24h = LoginThrottle.objects.filter(last_failed_at__gte=now - timedelta(hours=24)).count()

    # ---- platform activity, last 14 days
    days = [today - timedelta(days=i) for i in range(13, -1, -1)]
    since_dt = timezone.make_aware(datetime.combine(days[0], datetime.min.time()))

    def per_day(values):
        counts = {}
        for v in values:
            d = timezone.localtime(v).date() if isinstance(v, datetime) else v
            counts[d] = counts.get(d, 0) + 1
        return [counts.get(d, 0) for d in days]

    checkins = per_day(MoodEntry.objects.filter(date__gte=days[0]).values_list("date", flat=True))
    chats = per_day(ChatConversation.objects.filter(created_at__gte=since_dt).values_list("created_at", flat=True))
    msgs = per_day(GuidanceMessage.objects.filter(created_at__gte=since_dt).values_list("created_at", flat=True))

    # ---- account growth, last 6 weeks
    weeks = 6
    new_students, new_staff = [0] * weeks, [0] * weeks
    for joined, is_staff in User.objects.filter(
            is_superuser=False, date_joined__gte=now - timedelta(days=7 * weeks)
    ).values_list("date_joined", "is_staff"):
        ago = (today - timezone.localtime(joined).date()).days
        if 0 <= ago < 7 * weeks:
            i = weeks - 1 - ago // 7
            (new_staff if is_staff else new_students)[i] += 1
    growth_labels = [(today - timedelta(days=7 * (weeks - 1 - i) + 6)).strftime("%b %d") for i in range(weeks)]

    # ---- recent activity feed
    state = {"active": "Active", "inactive": "Archived", "graduated": "Graduated"}
    what = {"view_profile": "profile", "open_chat": "chat", "view_notes": "notes", "view_assessment": "assessment"}
    feed = []
    for u in students.order_by("-date_joined")[:5]:
        feed.append((u.date_joined, "🫶", "%s registered" % person(u)))
    for u in staff.order_by("-date_joined")[:3]:
        feed.append((u.date_joined, "🧑‍⚕️", "Staff account created: %s" % person(u)))
    for a in AccessLog.objects.select_related("staff", "student").order_by("-created_at")[:5]:
        feed.append((a.created_at, "🔒", "%s opened the %s of %s" % (
            person(a.staff), what.get(a.action, a.action), person(a.student))))
    for l in StudentStatusLog.objects.select_related("changed_by", "student").order_by("-created_at")[:5]:
        feed.append((l.created_at, "🗂️", "%s changed %s from %s to %s" % (
            person(l.changed_by), person(l.student), state.get(l.old_status, l.old_status), state.get(l.new_status, l.new_status))))
    for f in StudentFlag.objects.select_related("student").order_by("-created_at")[:5]:
        feed.append((f.created_at, "⚠️", "%s was flagged for review" % person(f.student)))
    feed.sort(key=lambda x: x[0], reverse=True)

    # ---- service health: quick local checks only, no calls to outside services
    health = []
    t = time.perf_counter()
    try:
        with connection.cursor() as c:
            c.execute("SELECT 1")
        health.append({"name": "Database (PostgreSQL)", "status": "ok",
                       "meta": "Answered in %d ms" % round((time.perf_counter() - t) * 1000)})
    except Exception:
        health.append({"name": "Database (PostgreSQL)", "status": "down", "meta": "Not responding"})
    health.append({"name": "Mica chat (Gemini)", "status": "ok" if settings.GEMINI_API_KEY else "down",
                   "meta": "API key set" if settings.GEMINI_API_KEY else "No API key set"})
    smtp = settings.EMAIL_BACKEND.endswith("smtp.EmailBackend")
    health.append({"name": "Email (sign-in and reset links)", "status": "ok" if smtp else "warn",
                   "meta": "SMTP configured" if smtp else "Console only: emails are printed, not sent"})
    health.append({"name": "Firebase (Google sign-in and reset codes)",
                   "status": "ok" if firebase_admin._apps else "down",
                   "meta": "Initialized" if firebase_admin._apps else "Not initialized"})
    vosk = (Path(settings.VOSK_MODEL_PATH) / "am" / "final.mdl").is_file()
    health.append({"name": "Voice input (Vosk)", "status": "ok" if vosk else "warn",
                   "meta": "Speech model installed" if vosk else "Speech model not found"})

    # ---- needs attention
    waiting = 0
    for th in GuidanceThread.objects.filter(counselor__isnull=True):
        last = th.messages.last()
        if last and last.sender_id == th.student_id:
            waiting += 1
    pending_review = StudentNote.objects.filter(needs_review=True, reviewed_at__isnull=True)
    attention = {
        "flags_unassigned": StudentFlag.objects.filter(status="new").count(),
        "threads_waiting": waiting,
        "notes_review": pending_review.count(),
        "staff_pending": staff.filter(is_active=True, last_login__isnull=True).count(),
    }

    # ---- sessions per staff, last 30 days (same counting rule as Service Reports)
    start = today - timedelta(days=29)
    t0 = timezone.make_aware(datetime.combine(start, datetime.min.time()))
    notes = list(StudentNote.objects.filter(service_delivered=True, session_date__gte=start)
                 .values_list("author_id", "student_id", "service", "session_date"))
    covered = {(sid, svc, d) for _, sid, svc, d in notes}
    per = {}
    for aid, _, _, _ in notes:
        if aid:
            per[aid] = per.get(aid, 0) + 1
    for cid, sid, svc, when in Appointment.objects.filter(
            status="completed", scheduled_time__gte=t0
    ).values_list("counselor_id", "student_id", "service", "scheduled_time"):
        if (sid, svc, timezone.localtime(when).date()) not in covered:
            per[cid] = per.get(cid, 0) + 1
    review_by = dict(pending_review.values_list("author_id").annotate(n=Count("id")))
    staff_rows = sorted(({
        "name": person(u), "role": _staff_json(u)["subclass"],
        "sessions": per.get(u.id, 0), "to_review": review_by.get(u.id, 0),
    } for u in staff.filter(is_active=True).select_related("staff_profile", "staff_profile__subclass")),
        key=lambda r: -r["sessions"])

    return JsonResponse({
        "accounts": {"total": User.objects.count(), "students": n_students, "staff": staff.count(),
                     "new_week": User.objects.filter(is_superuser=False, date_joined__gte=now - timedelta(days=7)).count()},
        "consent": {"on_current": on_consent, "students": n_students,
                    "rate": round(100 * on_consent / n_students) if n_students else None},
        "sessions": {"signed_in": len(signed_in)},
        "security": {"locked_now": locked_now, "failed_24h": failed_24h},
        "activity": {"labels": [d.strftime("%b %d") for d in days],
                     "checkins": checkins, "chats": chats, "messages": msgs},
        "growth": {"labels": growth_labels, "students": new_students, "staff": new_staff},
        "feed": [{"icon": i, "text": txt, "time": _time_ago(dt)} for dt, i, txt in feed[:8]],
        "health": health,
        "attention": attention,
        "staff_activity": staff_rows,
    })


STATUS_LABEL = {"active": "Active", "inactive": "Archived", "graduated": "Graduated"}


@_admin
def students_list(request):
    """One page of students for the admin cards. Account info only, nothing a student wrote."""
    from .views import _time_ago, _throttle_key       # imported late: views imports a lot

    base = User.objects.filter(is_staff=False, is_superuser=False)
    active_q = Q(profile__account_status="active") | Q(profile__isnull=True)
    counts = {
        "all": base.count(),
        "active": base.filter(active_q).count(),
        "inactive": base.filter(profile__account_status="inactive").count(),
        "graduated": base.filter(profile__account_status="graduated").count(),
    }

    qs = base.select_related("profile")
    status = request.GET.get("status", "all")
    if status == "active":
        qs = qs.filter(active_q)
    elif status in ("inactive", "graduated"):
        qs = qs.filter(profile__account_status=status)
    course = request.GET.get("course", "").strip()
    if course:
        qs = qs.filter(profile__course=course)
    for token in request.GET.get("q", "").split()[:5]:      # "juan cruz" must match both words
        qs = qs.filter(Q(username__icontains=token) | Q(first_name__icontains=token)
                       | Q(last_name__icontains=token) | Q(email__icontains=token)
                       | Q(profile__student_number__icontains=token))
    qs = qs.order_by("last_name", "first_name", "username")

    paginator = Paginator(qs, 12)
    page = paginator.get_page(request.GET.get("page"))
    keys = [_throttle_key(u.username) for u in page]
    locked = set(LoginThrottle.objects.filter(
        username_key__in=keys, locked_until__gt=timezone.now()).values_list("username_key", flat=True))
    course_names = dict(COURSES)

    rows = []
    for u in page:
        p = getattr(u, "profile", None)
        state = p.account_status if p else "active"
        rows.append({
            "id": u.id,
            "name": u.get_full_name() or u.username,
            "username": u.username,
            "email": u.email,
            "student_number": p.student_number if p and p.student_number else "No ID",
            "course": p.course if p and p.course else "",
            "course_label": course_names.get(p.course, p.course) if p and p.course else "",
            "year": p.year_level if p else "",
            "status": state,
            "status_label": STATUS_LABEL.get(state, state),
            "reason": p.get_status_reason_display() if p and p.status_reason else "",
            "last_login": _time_ago(u.last_login),
            "joined": u.date_joined.strftime("%Y-%m-%d"),
            "google": bool(p and p.firebase_uid),
            "has_password": u.has_usable_password(),
            "locked": _throttle_key(u.username) in locked,
            "setup_pending": bool(p and p.needs_profile_setup),
        })

    return JsonResponse({
        "students": rows, "counts": counts, "page": page.number,
        "pages": paginator.num_pages, "total": paginator.count,
        "courses": [{"code": c, "label": l} for c, l in COURSES],
    })


@_admin
@require_POST
def student_unlock(request, user_id):
    from .views import _throttle_key
    user = get_object_or_404(User, id=user_id, is_staff=False, is_superuser=False)
    key = _throttle_key(user.username)
    was_locked = LoginThrottle.objects.filter(username_key=key, locked_until__gt=timezone.now()).exists()
    LoginThrottle.objects.filter(username_key=key).delete()      # also clears the failed-attempt counter
    return JsonResponse({"status": "ok", "was_locked": was_locked})


@_admin
@require_POST
def student_reset_password(request, user_id):
    from .views import _send_reset_link
    user = get_object_or_404(User, id=user_id, is_staff=False, is_superuser=False)
    if not user.is_active or not user.email:
        return JsonResponse({"error": "This account is disabled or has no email address."}, status=400)
    if not user.has_usable_password():
        return JsonResponse({"error": "This student signs in with Google, so there is no password to reset."}, status=400)
    try:
        _send_reset_link(request, user.email)
    except Exception:
        logger.exception("Could not send a reset link to student account %s.", user.pk)
        return JsonResponse({"error": "Could not send the email. Check the email settings."}, status=502)
    return JsonResponse({"status": "ok"})