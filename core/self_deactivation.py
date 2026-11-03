"""A student deactivating their own account, and signing back in to undo it.

The rules, in one place:

* Only an ACTIVE student can deactivate. The account becomes "inactive" (shown to staff as
  archived) with status_reason "self_deactivated" and the self_deactivated flag set.
* The same safeguards as a staff archive apply: it is refused while the student has an
  unresolved risk alert (Guidance must handle that first), upcoming appointments are
  cancelled, and the change is written to the StudentStatusLog.
* Signing in again reactivates the account ONLY when the self_deactivated flag is still set.
  Staff archive/reactivate (views.staff_student_status) clears the flag, and a Graduated
  account never has it, so a staff archive is never undone by signing in.
"""
from django.contrib.auth.signals import user_logged_in
from django.db import transaction
from django.dispatch import receiver
from django.utils import timezone

OK = "ok"
NOT_ACTIVE = "not_active"
OPEN_ALERT = "open_alert"


def is_self_deactivated(profile):
    return bool(profile) and profile.account_status == "inactive" and profile.self_deactivated


def blocks_sign_in(profile):
    """True when this student's archive must stop them signing in (archived or graduated by
    staff). A self-deactivated account is let through, signing in is what reactivates it."""
    return bool(profile) and profile.account_status != "active" and not is_self_deactivated(profile)


def deactivate(user):
    """Archive the student's own account. Returns OK, NOT_ACTIVE or OPEN_ALERT."""
    from .models import Appointment, StudentFlag, StudentStatusLog, UserProfile

    with transaction.atomic():
        profile = UserProfile.objects.select_for_update().filter(user=user).first()
        if profile is None or profile.account_status != "active":
            return NOT_ACTIVE
        # Same rule as a staff archive: unresolved risk alerts are handled by Guidance first.
        if StudentFlag.objects.filter(student=user).exclude(status="resolved").exists():
            return OPEN_ALERT
        now = timezone.now()
        Appointment.objects.filter(
            student=user, status__in=["pending", "confirmed"], scheduled_time__gte=now,
        ).update(status="cancelled")
        profile.account_status = "inactive"
        profile.status_reason = "self_deactivated"
        profile.status_changed_at = now
        profile.self_deactivated = True
        profile.save(update_fields=["account_status", "status_reason", "status_changed_at",
                                    "self_deactivated"])
        StudentStatusLog.objects.create(
            student=user, changed_by=user, old_status="active", new_status="inactive",
            reason="self_deactivated", note="The student deactivated their own account")
    return OK


@receiver(user_logged_in)
def reactivate_on_sign_in(sender, request, user, **kwargs):
    """Every completed sign-in (password, e-mailed code, Google) passes through here."""
    if user.is_staff or user.is_superuser:
        return
    from .models import StudentStatusLog, UserProfile

    with transaction.atomic():
        # One conditional UPDATE: if staff archived the account in the meantime the flag is
        # already cleared, nothing matches, and the account stays archived.
        changed = UserProfile.objects.filter(
            user=user, account_status="inactive", self_deactivated=True,
        ).update(account_status="active", status_reason="", self_deactivated=False,
                 status_changed_at=timezone.now())
        if changed:
            StudentStatusLog.objects.create(
                student=user, changed_by=user, old_status="inactive", new_status="active",
                reason="", note="The student signed in again, which reactivated their account")
