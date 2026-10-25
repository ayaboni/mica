"""System-admin endpoints for staff subclasses and staff accounts.

Used by the "Roles & Permissions" and "User Accounts" tabs of the admin dashboard.
"""
import json
import logging

from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.crypto import get_random_string
from django.views.decorators.http import require_POST

from .models import SERVICES, StaffProfile, StaffSubclass, GuidanceThread, StudentFlag
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