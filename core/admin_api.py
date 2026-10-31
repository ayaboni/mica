import re, io, time
from datetime import date, datetime, timedelta
from pathlib import Path
import json, logging, csv
import firebase_admin

from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.conf import settings
from django.contrib.sessions.models import Session
from django.db import connection, transaction
from django.db.models import Count, Q
from django.db.models.functions import Lower
from django.core.paginator import Paginator
from django.utils import timezone
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from .name_validation import validate_person_names
from .models import (COURSES, SERVICES, STATUS_REASONS, StaffProfile, StaffSubclass, GuidanceThread, GuidanceMessage,
                     StudentFlag, StudentNote, Appointment, AccessLog, StudentStatusLog, LoginThrottle,
                    MoodEntry, ChatConversation, SiteSettings, AuditEvent, Semester, EnrolledStudent,
                    UserProfile, SignInFailure, DataRequest)
from .permissions import BOOL_FIELDS, PERMISSION_FIELDS, SERVICE_FIELDS, SERVICE_KEYS, subclass_json
from .hotlines import DEFAULT_HOTLINES, MAX_HOTLINES, NUMBER_RE, NOTE_RE, get_hotlines, saved_hotlines

logger = logging.getLogger(__name__)

admin_only = [login_required(login_url='login'), user_passes_test(lambda u: u.is_superuser, login_url='login')]


def _admin(view):
    for deco in reversed(admin_only):
        view = deco(view)
    return view

def _log_admin(request, detail):
    AuditEvent.objects.create(actor=request.user, event="admin_action", detail=str(detail)[:300],
                              ip=request.META.get("REMOTE_ADDR") or None)

def _body(request):
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _label(u):
    return "%s (@%s)" % (u.get_full_name() or u.username, u.username)


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
        _log_admin(request, "Roles: created %s" % sub.name)
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
    _log_admin(request, "Roles: updated %s" % sub.name)    
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
    name = sub.name
    sub.delete()
    _log_admin(request, "Roles: deleted %s" % name)
    return JsonResponse({"status": "ok"})


def _site_json(s):
    return {"platform_name": s.platform_name, "institution_name": s.institution_name,
            "support_email": s.support_email, "timezone": settings.TIME_ZONE,
            "registration_open": s.registration_open,
            "maintenance_on": s.maintenance_on, "maintenance_message": s.maintenance_message}

@_admin
def site_settings(request):
    return JsonResponse(_site_json(SiteSettings.get()))


@_admin
@require_POST
def site_settings_save(request):
    data = _body(request)
    if data is None:
        return JsonResponse({"error": "Invalid request."}, status=400)
    platform = " ".join(str(data.get("platform_name") or "").split())
    institution = " ".join(str(data.get("institution_name") or "").split())
    email = str(data.get("support_email") or "").strip()
    if not 2 <= len(platform) <= 80:
        return JsonResponse({"error": "Platform name must be 2\u201380 characters."}, status=400)
    if len(institution) > 120:
        return JsonResponse({"error": "Institution name is too long."}, status=400)
    if email:
        try:
            validate_email(email)
        except ValidationError:
            return JsonResponse({"error": "That support email doesn't look right."}, status=400)

    s = SiteSettings.get()
    changed = [label for label, old, new in (
        ("platform name", s.platform_name, platform),
        ("institution name", s.institution_name, institution),
        ("support email", s.support_email, email),
    ) if old != new]
    s.platform_name, s.institution_name, s.support_email = platform, institution, email
    s.updated_by = request.user
    s.save()
    if changed:
        AuditEvent.objects.create(actor=request.user, event="settings_changed",
                                  detail="General: changed " + ", ".join(changed),
                                  ip=request.META.get("REMOTE_ADDR") or None)
    return JsonResponse(_site_json(s))


@_admin
@require_POST
def registration_set(request):
    data = _body(request)
    if data is None or not isinstance(data.get("open"), bool):
        return JsonResponse({"error": "Invalid request."}, status=400)
    s = SiteSettings.get()
    if s.registration_open != data["open"]:
        s.registration_open = data["open"]
        s.updated_by = request.user
        s.save()
        AuditEvent.objects.create(actor=request.user, event="settings_changed",
                                  detail="General: student registration " + ("opened" if data["open"] else "closed"),
                                  ip=request.META.get("REMOTE_ADDR") or None)
    return JsonResponse(_site_json(s))


def _staff_json(user):
    sp = getattr(user, "staff_profile", None)
    sub = sp.subclass if sp and sp.subclass_id else None
    fresh = bool(sp and sp.must_complete_setup)
    if not user.is_active:
        status = "Suspended"
    elif fresh or user.last_login is None:
        status = "Pending"             # hasn't finished first-time setup / never signed in
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
        "fresh": fresh,
        "last_login": user.last_login.strftime("%Y-%m-%d %H:%M") if user.last_login else "\u2014",
        "joined": user.date_joined.strftime("%Y-%m-%d"),
    }


@_admin
def staff_accounts(request):
    users = (User.objects.filter(is_staff=True, is_superuser=False)
             .select_related("staff_profile", "staff_profile__subclass").order_by("-date_joined"))
    return JsonResponse({"staff": [_staff_json(u) for u in users]})


USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,29}$")


@_admin
@require_POST
def staff_create(request):
    """Create a fresh staff account: names, username, e-mail and role, but no password.

    The staff member signs in with just the username, confirms an e-mailed code, then sets
    their own password (see views.staff_setup)."""
    data = _body(request)
    if data is None:
        return JsonResponse({"error": "Invalid request."}, status=400)
    first = " ".join(str(data.get("first_name") or "").split())[:150]
    last = " ".join(str(data.get("last_name") or "").split())[:150]
    username = str(data.get("username") or "").strip()
    email = str(data.get("email") or "").strip().lower()
    if not first:
        return JsonResponse({"error": "Please enter the staff member\u2019s first name."}, status=400)
    name_error = validate_person_names(first, last, require_last=False)
    if name_error:
        return JsonResponse({"error": name_error.replace("your ", "the staff member\u2019s ").replace("Your ", "The staff member\u2019s ")}, status=400)
    if not last:
        return JsonResponse({"error": "Please enter the staff member\u2019s last name."}, status=400)
    if not USERNAME_RE.match(username):
        return JsonResponse({"error": "Username must be 3\u201330 characters: letters, numbers, dots, dashes or underscores."}, status=400)
    if User.objects.filter(Q(username__iexact=username) | Q(email__iexact=username)).exists():
        return JsonResponse({"error": "That username is already taken."}, status=409)
    try:
        validate_email(email)
    except ValidationError:
        return JsonResponse({"error": "Please enter a valid email address."}, status=400)
    if User.objects.filter(email__iexact=email).exists():
        return JsonResponse({"error": "An account with that email already exists."}, status=409)
    sub = StaffSubclass.objects.filter(id=data.get("subclass_id")).first()
    if sub is None:
        return JsonResponse({"error": "Choose a staff role for this account."}, status=400)

    with transaction.atomic():
        user = User.objects.create_user(          # password=None -> unusable password: nobody can sign in with one
            username=username, email=email, first_name=first, last_name=last, password=None, is_staff=True)
        profile, _ = StaffProfile.objects.get_or_create(user=user, defaults={"role": "other"})
        profile.subclass = sub
        profile.license_number = str(data.get("license_number") or "").strip()[:50]
        profile.must_complete_setup = True
        profile.save()
    user = User.objects.select_related("staff_profile").get(pk=user.pk)   # reload so the new profile is read
    _log_admin(request, "Staff: created account %s (%s)" % (_label(user), sub.name))
    return JsonResponse({"staff": _staff_json(user)}, status=201)


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
    _log_admin(request, "Staff: changed the role of %s to %s" % (_label(user), sub.name))
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
    _log_admin(request, "Staff: %s %s" % ("reactivated" if active else "suspended", _label(user)))
    return JsonResponse({"staff": _staff_json(user), "released_threads": released})


@_admin
@require_POST
def staff_reset_password(request, user_id):
    from .views import _send_reset_link
    user = get_object_or_404(User, id=user_id, is_staff=True, is_superuser=False)
    sp = getattr(user, "staff_profile", None)
    if sp and sp.must_complete_setup:
        return JsonResponse({"error": "This account is new and has no password yet. They set one the first time they sign in."}, status=400)
    if not user.email:
        return JsonResponse({"error": "This account has no email address."}, status=400)
    try:
        _send_reset_link(request, user.email)
    except Exception:
        logger.exception("Could not send a reset link to staff account %s.", user.pk)
        return JsonResponse({"error": "Could not send the e-mail. Check the e-mail settings."}, status=502)
    _log_admin(request, "Staff: sent a password reset link to %s" % _label(user))
    return JsonResponse({"status": "ok"})


def _service_health():
    """Quick local checks only, no calls to outside services."""
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
    return health


@_admin
def dashboard_stats(request):
    """Real numbers for the admin dashboard. Counts and names only, nothing a student wrote."""
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
    health = _service_health()

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
        "unverified": base.filter(profile__verification_status="unverified").count(),
    }

    qs = base.select_related("profile")
    status = request.GET.get("status", "all")
    if status == "active":
        qs = qs.filter(active_q)
    elif status in ("inactive", "graduated"):
        qs = qs.filter(profile__account_status=status)
    elif status == "unverified":
        qs = qs.filter(profile__verification_status="unverified")
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
            "verification": p.verification_status if p else "verified",
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
    _log_admin(request, "Students: unlocked %s%s" % (_label(user), " (was locked)" if was_locked else ""))
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
    _log_admin(request, "Students: sent a password reset link to %s" % _label(user))
    return JsonResponse({"status": "ok"})


# ---------------------------------------------------------------- audit log
AUDIT_ACTIONS = [
    ("view_profile", "Opened student profile"),
    ("open_chat", "Opened guidance chat"),
    ("view_notes", "Viewed student notes"),
    ("view_assessment", "Viewed assessment"),
    ("status_change", "Changed account status"),
]
AUDIT_LABEL = dict(AUDIT_ACTIONS)


def _person_filter(qs, field, text):
    """'juan cruz' must match both words, in username, first name or last name."""
    for token in (text or "").split()[:5]:
        qs = qs.filter(Q(**{field + "__username__icontains": token})
                       | Q(**{field + "__first_name__icontains": token})
                       | Q(**{field + "__last_name__icontains": token}))
    return qs


def _audit_name(u):
    return (u.get_full_name() or u.username) if u else "Former staff"


def _audit_role(u):
    if u is None:
        return "-"
    if u.is_superuser:
        return "System Admin"
    sp = getattr(u, "staff_profile", None)
    return sp.role_label if sp else "Guidance Staff"


def _audit_rows(request, limit):
    """AccessLog, StudentStatusLog and admin changes merged into one list, newest first."""
    g = request.GET
    action = g.get("action", "all")
    d_from = parse_date(g.get("date_from") or "")
    d_to = parse_date(g.get("date_to") or "")

    def narrow(qs):
        qs = _person_filter(qs, "student", g.get("student"))
        if d_from:
            qs = qs.filter(created_at__date__gte=d_from)
        if d_to:
            qs = qs.filter(created_at__date__lte=d_to)
        return qs

    rows = []
    if action != "status_change":
        qs = AccessLog.objects.select_related("staff", "staff__staff_profile__subclass", "student")
        if action != "all":
            qs = qs.filter(action=action)
        qs = _person_filter(narrow(qs), "staff", g.get("staff"))
        for a in qs.order_by("-created_at")[:limit]:
            rows.append({"when": a.created_at, "staff": _audit_name(a.staff), "role": _audit_role(a.staff),
                         "action": AUDIT_LABEL.get(a.action, a.action), "student": _audit_name(a.student),
                         "detail": ""})

    if action in ("all", "status_change"):
        qs = StudentStatusLog.objects.select_related("changed_by", "changed_by__staff_profile__subclass", "student")
        qs = narrow(qs)
        if g.get("staff", "").strip():
            qs = _person_filter(qs, "changed_by", g.get("staff"))
        states = {"active": "Active", "inactive": "Archived", "graduated": "Graduated"}
        reasons = dict(STATUS_REASONS)
        for l in qs.order_by("-created_at")[:limit]:
            bits = ["%s to %s" % (states.get(l.old_status, l.old_status), states.get(l.new_status, l.new_status))]
            if l.reason:
                bits.append(reasons.get(l.reason, l.reason))
            if l.note:
                bits.append(l.note)
            if l.exit_interview_done:
                bits.append("exit interview done")
            if l.evaluation_done:
                bits.append("evaluation done")
            rows.append({"when": l.created_at, "staff": _audit_name(l.changed_by), "role": _audit_role(l.changed_by),
                         "action": AUDIT_LABEL["status_change"], "student": _audit_name(l.student),
                         "detail": " · ".join(bits)})

    if action in ("all", "admin_change") and not g.get("student", "").strip():
        qs = AuditEvent.objects.select_related("actor", "actor__staff_profile__subclass")
        if d_from:
            qs = qs.filter(created_at__date__gte=d_from)
        if d_to:
            qs = qs.filter(created_at__date__lte=d_to)
        if g.get("staff", "").strip():
            qs = _person_filter(qs, "actor", g.get("staff"))
        for e in qs.order_by("-created_at")[:limit]:
            rows.append({"when": e.created_at, "staff": _audit_name(e.actor), "role": _audit_role(e.actor),
                         "action": "Admin change", "student": "-", "detail": e.detail})

    rows.sort(key=lambda r: r["when"], reverse=True)
    return rows[:limit]


def _stamp(dt):
    return timezone.localtime(dt).strftime("%Y-%m-%d %H:%M:%S")


@_admin
def audit_logs(request):
    """One page of the audit log. Who did what to which student, never what a student wrote."""
    rows = _audit_rows(request, 1000)
    page = Paginator(rows, 25).get_page(request.GET.get("page"))
    return JsonResponse({
        "rows": [dict(r, when=_stamp(r["when"])) for r in page],
        "page": page.number, "pages": page.paginator.num_pages, "total": page.paginator.count,
    })


def _csv_safe(value):
    """Stops Excel from running a name like =HYPERLINK(...) as a formula."""
    value = str(value or "")
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value


@_admin
def audit_logs_csv(request):
    rows = _audit_rows(request, 5000)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="mica-audit-log-%s.csv"' % timezone.localdate().isoformat()
    w = csv.writer(response)
    w.writerow(["Timestamp", "Staff", "Role", "Action", "Student", "Details"])
    for r in rows:
        w.writerow([_stamp(r["when"]), _csv_safe(r["staff"]), _csv_safe(r["role"]),
                    r["action"], _csv_safe(r["student"]), _csv_safe(r["detail"])])
    return response


# ---------------------------------------------------------------- crisis hotlines
@_admin
def hotlines_list(request):
    return JsonResponse({"hotlines": get_hotlines(), "defaults": DEFAULT_HOTLINES,
                         "using_defaults": not saved_hotlines(), "max": MAX_HOTLINES})


@_admin
@require_POST
def hotlines_save(request):
    data = _body(request)
    rows = data.get("hotlines") if data else None
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_HOTLINES:
        return JsonResponse({"error": "Keep between 1 and %d hotlines." % MAX_HOTLINES}, status=400)
    clean = []
    for i, r in enumerate(rows, 1):
        r = r if isinstance(r, dict) else {}
        name = " ".join(str(r.get("name") or "").split())
        number = " ".join(str(r.get("number") or "").split())
        note = " ".join(str(r.get("note") or "").split())
        if not 2 <= len(name) <= 60:
            return JsonResponse({"error": "Hotline %d: give it a name (2\u201360 characters)." % i}, status=400)
        if not NUMBER_RE.match(number) or not any(c.isdigit() for c in number):
            return JsonResponse({"error": "Hotline %d: enter a phone number. Digits, spaces and + ( ) - . , / "
                                          "are fine, plus short words like \u201cor\u201d." % i}, status=400)
        if not NOTE_RE.match(note):
            return JsonResponse({"error": "Hotline %d: the note is too long or has unusual characters." % i}, status=400)
        clean.append({"name": name, "number": number, "note": note})

    s = SiteSettings.get()
    s.hotlines = clean
    s.updated_by = request.user
    s.save()
    AuditEvent.objects.create(actor=request.user, event="settings_changed",
                              detail="Crisis hotlines updated (%d listed)" % len(clean),
                              ip=request.META.get("REMOTE_ADDR") or None)
    return JsonResponse({"hotlines": clean})


@_admin
def integrations(request):
    items = _service_health()
    has_backup = bool(getattr(settings, "OPENROUTER_API_KEY", ""))
    items.insert(2, {"name": "Mica backup chat (OpenRouter)",
                     "status": "ok" if has_backup else "warn",
                     "meta": "API key set" if has_backup else "Not set up yet"})
    return JsonResponse({"services": items})


@_admin
def privacy_overview(request):
    from .views import CONSENT_VERSION     # imported late: views imports a lot
    students = User.objects.filter(is_staff=False, is_superuser=False, profile__account_status="active")
    total = students.count()
    current = students.filter(profile__consent_version=CONSENT_VERSION).count()
    older = (students.exclude(profile__consent_version=CONSENT_VERSION)
                     .exclude(profile__consent_version="").count())
    never = total - current - older
    last = (students.exclude(profile__consent_given_at=None)
                    .order_by("-profile__consent_given_at")
                    .values_list("profile__consent_given_at", flat=True).first())
    return JsonResponse({
        "version": CONSENT_VERSION, "active_students": total,
        "on_current": current, "on_older": older, "none": never,
        "rate": round(100 * current / total) if total else None,
        "last_consent": last.isoformat() if last else None,
    })


def _semester_json(s):
    return {"id": s.id, "name": s.name, "start": s.start_date.isoformat(), "end": s.end_date.isoformat()}


def _date(value):
    try:
        return parse_date(str(value or ""))
    except ValueError:
        return None


@_admin
def semesters_list(request):
    return JsonResponse({"semesters": [_semester_json(s) for s in Semester.objects.all()]})


@_admin
@require_POST
def semester_create(request):
    data = _body(request)
    if data is None:
        return JsonResponse({"error": "Invalid request."}, status=400)
    name = " ".join(str(data.get("name") or "").split())
    start, end = _date(data.get("start_date")), _date(data.get("end_date"))
    if not 2 <= len(name) <= 80:
        return JsonResponse({"error": "Name must be 2\u201380 characters."}, status=400)
    if not start or not end:
        return JsonResponse({"error": "Pick a start date and an end date."}, status=400)
    if end < start:
        return JsonResponse({"error": "The end date can't be before the start date."}, status=400)
    if Semester.objects.filter(name__iexact=name).exists():
        return JsonResponse({"error": "A semester with that name already exists."}, status=409)
    s = Semester.objects.create(name=name, start_date=start, end_date=end)
    AuditEvent.objects.create(actor=request.user, event="settings_changed",
                              detail="Semesters: added %s" % name[:200],
                              ip=request.META.get("REMOTE_ADDR") or None)
    return JsonResponse({"semester": _semester_json(s)}, status=201)


@_admin
@require_POST
def semester_delete(request, semester_id):
    s = get_object_or_404(Semester, id=semester_id)
    name = s.name
    s.delete()
    AuditEvent.objects.create(actor=request.user, event="settings_changed",
                              detail="Semesters: deleted %s" % name[:200],
                              ip=request.META.get("REMOTE_ADDR") or None)
    return JsonResponse({"status": "ok"})


MAX_ENROLLED = 20000


def _year_digit(value):
    m = re.search(r"[1-5]", str(value or ""))
    return m.group(0) if m else ""


@_admin
def enrollment_info(request):
    last = (AuditEvent.objects.filter(event="settings_changed", detail__startswith="Enrollment: uploaded")
            .values_list("created_at", flat=True).first())
    return JsonResponse({"count": EnrolledStudent.objects.count(),
                         "uploaded_at": last.isoformat() if last else None})


def _year_changes(rows):
    """Registered students whose year level differs from the list. Does not save anything."""
    wanted = {r.student_number.lower(): r.year_level for r in rows if r.year_level}
    changed = []
    profiles = (UserProfile.objects.filter(user__is_staff=False, user__is_superuser=False)
                .exclude(student_number=""))
    for p in profiles.only("id", "student_number", "year_level"):
        new = wanted.get((p.student_number or "").lower())
        if new and p.year_level != new:
            p.year_level = new
            changed.append(p)
    return changed


def _sync_year_levels(rows):
    changed = _year_changes(rows)
    if changed:
        UserProfile.objects.bulk_update(changed, ["year_level"], batch_size=500)
    return len(changed)


def _missing_students(listed=None):
    """Active students whose student number is not on the list (the saved one, or `listed` if given)."""
    if listed is None:
        if not EnrolledStudent.objects.exists():
            return []
        listed = set(EnrolledStudent.objects.annotate(k=Lower("student_number")).values_list("k", flat=True))
    qs = (UserProfile.objects.filter(account_status="active", needs_profile_setup=False,
                                     user__is_staff=False, user__is_superuser=False)
          .exclude(verification_status="rejected")
          .select_related("user").order_by("user__last_name", "user__first_name"))
    return [p for p in qs if (p.student_number or "").lower() not in listed]


@_admin
@require_POST
def enrollment_upload(request):
    f = request.FILES.get("file")
    if not f:
        return JsonResponse({"error": "Choose a CSV file."}, status=400)
    if f.size > 2 * 1024 * 1024:
        return JsonResponse({"error": "That file is too large (2 MB max)."}, status=400)
    raw = f.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return JsonResponse({"error": "The file is empty."}, status=400)
    cols = {(c or "").strip().lower().replace(" ", "_"): c for c in reader.fieldnames}
    for need in ("student_number", "last_name"):
        if need not in cols:
            return JsonResponse({"error": "Missing the column \u201c%s\u201d. Required columns: student_number, last_name." % need},
                                status=400)

    def cell(row, key):
        c = cols.get(key)
        return " ".join(str(row.get(c) or "").split()) if c else ""

    rows, seen, errors = [], set(), []
    for i, row in enumerate(reader, start=2):
        if not any(cell(row, k) for k in cols):
            continue                                    # blank line
        number, last = cell(row, "student_number"), cell(row, "last_name")
        if not number or not last:
            errors.append("Line %d: student number and last name are both required." % i)
        elif len(number) > 50:
            errors.append("Line %d: student number is too long." % i)
        elif number.lower() in seen:
            errors.append("Line %d: student number %s appears twice." % (i, number))
        else:
            seen.add(number.lower())
            rows.append(EnrolledStudent(
                student_number=number, last_name=last[:150], first_name=cell(row, "first_name")[:150],
                course=cell(row, "course").upper()[:50], year_level=_year_digit(cell(row, "year_level"))))
        if len(errors) >= 10:
            break
    if errors:
        return JsonResponse({"error": "Nothing was saved. Fix these lines and upload again:\n" + "\n".join(errors)}, status=400)
    if not rows:
        return JsonResponse({"error": "No students were found in the file."}, status=400)
    if len(rows) > MAX_ENROLLED:
        return JsonResponse({"error": "Too many rows (max %d)." % MAX_ENROLLED}, status=400)

    if request.POST.get("preview") == "1":
        return JsonResponse({
            "preview": True, "new_count": len(rows),
            "current_count": EnrolledStudent.objects.count(),
            "year_changes": len(_year_changes(rows)),
            "missing": len(_missing_students({r.student_number.lower() for r in rows})),
        })

    with transaction.atomic():
        EnrolledStudent.objects.all().delete()
        EnrolledStudent.objects.bulk_create(rows, batch_size=500)
        updated = _sync_year_levels(rows)
    AuditEvent.objects.create(actor=request.user, event="settings_changed",
                              detail="Enrollment: uploaded a list of %d students, updated %d year levels" % (len(rows), updated),
                              ip=request.META.get("REMOTE_ADDR") or None)
    return JsonResponse({"count": len(rows), "year_updated": updated})


@_admin
@require_POST
def enrollment_clear(request):
    n = EnrolledStudent.objects.count()
    EnrolledStudent.objects.all().delete()
    AuditEvent.objects.create(actor=request.user, event="settings_changed",
                              detail="Enrollment: cleared the list (%d students)" % n,
                              ip=request.META.get("REMOTE_ADDR") or None)
    return JsonResponse({"status": "ok"})


@_admin
def enrollment_missing(request):
    people = _missing_students()
    all_total = len(people)
    for token in request.GET.get("q", "").lower().split()[:5]:
        people = [p for p in people if token in " ".join([
            p.user.get_full_name(), p.user.username, p.student_number or "", p.course or ""]).lower()]
    page = Paginator(people, 15).get_page(request.GET.get("page"))
    return JsonResponse({
        "all_total": all_total,
        "total": len(people),
        "matching": [{"id": p.user_id, "name": p.user.get_full_name() or p.user.username} for p in people],
        "students": [
            {"id": p.user_id, "name": p.user.get_full_name() or p.user.username,
             "student_number": p.student_number, "course": p.course, "year_level": p.year_level}
            for p in page],
        "page": page.number, "pages": page.paginator.num_pages,
    })


@_admin
def enrollment_download(request):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="mica-enrollment-list.csv"'
    response.write("\ufeff")                       # lets Excel show accents correctly
    w = csv.writer(response)
    w.writerow(["student_number", "last_name", "first_name", "course", "year_level"])
    for e in EnrolledStudent.objects.order_by("student_number"):
        w.writerow([_csv_safe(e.student_number), _csv_safe(e.last_name), _csv_safe(e.first_name),
                    _csv_safe(e.course), e.year_level])
    return response


SIGNIN_REASON = {"password": "Wrong password", "code": "Wrong e-mail code"}


@_admin
def signin_failures(request):
    now = timezone.now()
    last_day = SignInFailure.objects.filter(created_at__gte=now - timedelta(hours=24))
    by_ip = (last_day.exclude(ip=None).values("ip")
             .annotate(n=Count("id"), accounts=Count("user", distinct=True)).order_by("-n"))
    watch = [{"ip": r["ip"], "attempts": r["n"], "accounts": r["accounts"]}
             for r in by_ip[:20] if r["n"] >= 10 or r["accounts"] >= 3][:5]

    qs = SignInFailure.objects.select_related("user", "user__profile")
    reason = request.GET.get("reason", "all")
    if reason in SIGNIN_REASON:
        qs = qs.filter(reason=reason)
    page = Paginator(qs.order_by("-created_at"), 25).get_page(request.GET.get("page"))

    def kind(u):
        return "Admin" if u.is_superuser else ("Staff" if u.is_staff else "Student")

    def row(f):
        u = f.user
        profile = getattr(u, "profile", None) if u else None
        return {"when": _stamp(f.created_at),
                "account": (u.get_full_name() or u.username) if u else "No such account",
                "username": u.username if u else "",
                "student_number": profile.student_number if profile else "",
                "email": u.email if u else "",
                "kind": kind(u) if u else "-",
                "reason": SIGNIN_REASON.get(f.reason, f.reason), "ip": f.ip or "-"}

    return JsonResponse({
        "failed_24h": last_day.count(),
        "accounts_hit": last_day.exclude(user=None).values("user").distinct().count(),
        "ips": last_day.exclude(ip=None).values("ip").distinct().count(),
        "locked_now": LoginThrottle.objects.filter(locked_until__gt=now).count(),
        "watch": watch,
        "rows": [row(f) for f in page],
        "page": page.number, "pages": page.paginator.num_pages, "total": page.paginator.count,
    })


@_admin
@require_POST
def maintenance_set(request):
    data = _body(request)
    if data is None or not isinstance(data.get("on"), bool):
        return JsonResponse({"error": "Invalid request."}, status=400)
    s = SiteSettings.get()
    s.maintenance_on = data["on"]
    s.maintenance_message = " ".join(str(data.get("message") or "").split())[:300]
    s.updated_by = request.user
    s.save()
    _log_admin(request, "Maintenance mode turned %s" % ("ON" if s.maintenance_on else "OFF"))
    return JsonResponse(_site_json(s))


def _dr_row(r):
    return {
        "id": r.id,
        "student_name": r.student_name,
        "student_number": r.student_number,
        "student_username": r.student_username,
        "type": r.request_type,
        "type_label": r.get_request_type_display(),
        "request_detail": r.request_detail,
        "status": r.status,
        "status_label": r.get_status_display(),
        "received_on": r.received_on.isoformat(),
        "note": r.note,
        "outcome": r.outcome,
        "handled_at": timezone.localtime(r.handled_at).strftime("%b %d, %Y") if r.handled_at else "",
    }


@_admin
def data_requests(request):
    status = request.GET.get("status", "all")
    qs = DataRequest.objects.all()
    if status in ("open", "done", "declined"):
        qs = qs.filter(status=status)
    try:
        page = int(request.GET.get("page", 1))
    except ValueError:
        page = 1
    pg = Paginator(qs,10).get_page(page)
    return JsonResponse({
        "rows": [_dr_row(r) for r in pg.object_list],
        "page": pg.number,
        "pages": pg.paginator.num_pages,
        "total": pg.paginator.count,
        "open_count": DataRequest.objects.filter(status="open").count(),
    })


@_admin
@require_POST
def data_request_create(request):
    try:
        body = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"error": "Bad request."}, status=400)

    try:
        sid = int(body.get("student_id"))
    except (TypeError, ValueError):
        sid = None
    student = (User.objects.filter(pk=sid, is_staff=False, is_superuser=False)
               .select_related("profile").first()) if sid else None
    if student is None:
        return JsonResponse({"error": "Pick a student from the search results."}, status=400)
    profile = getattr(student, "profile", None)

    rtype = body.get("request_type")
    note = " ".join(str(body.get("note") or "").split())[:200]
    detail = " ".join(str(body.get("request_detail") or "").split())[:120]
    if rtype not in dict(DataRequest.TYPES):
        return JsonResponse({"error": "Pick a request type."}, status=400)
    if rtype == "other" and not detail:
        return JsonResponse({"error": "Say what the request is."}, status=400)
    try:
        received = date.fromisoformat(body.get("received_on") or "")
    except ValueError:
        return JsonResponse({"error": "Pick the date you received it."}, status=400)
    if received > timezone.localdate():
        return JsonResponse({"error": "The date can't be in the future."}, status=400)

    r = DataRequest.objects.create(
        student=student,
        student_name=student.get_full_name() or student.username,
        student_username=student.username,
        student_number=(profile.student_number if profile and profile.student_number else ""),
        request_type=rtype, received_on=received, note=note, logged_by=request.user,
        request_detail=detail if rtype == "other" else "",
    )
    _log_admin(request, f"Logged data request #{r.id} ({r.get_request_type_display()}) for {r.student_name}")
    return JsonResponse({"request": _dr_row(r)})


@_admin
@require_POST
def data_request_close(request, pk):
    try:
        body = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"error": "Bad request."}, status=400)
    try:
        r = DataRequest.objects.get(pk=pk)
    except DataRequest.DoesNotExist:
        return JsonResponse({"error": "Request not found."}, status=404)
    if r.status != "open":
        return JsonResponse({"error": "This request is already closed."}, status=400)
    status = body.get("status")
    if status not in ("done", "declined"):
        return JsonResponse({"error": "Pick done or declined."}, status=400)

    r.status = status
    r.outcome = (body.get("outcome") or "").strip()[:300]
    r.handled_at = timezone.now()
    r.save(update_fields=["status", "outcome", "handled_at"])
    _log_admin(request, f"Marked data request #{r.id} as {r.get_status_display().lower()}")
    return JsonResponse({"request": _dr_row(r)})