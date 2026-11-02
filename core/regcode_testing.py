"""Test helper: let a test client past the registration-code step."""
from datetime import timedelta

from django.utils import timezone

from .models import RegistrationCode
from .registration_codes import REG_CODE_TTL, SESSION_KEY, SHARED_CODE_LENGTH, new_code_value


def make_code(created_by=None, **fields):
    """A single-use code (its accounts are verified)."""
    fields.setdefault("expires_at", timezone.now() + REG_CODE_TTL)
    return RegistrationCode.objects.create(code=new_code_value(), created_by=created_by, **fields)


def make_shared_code(created_by=None, max_uses=50, **fields):
    """A shared term code (its accounts start unverified)."""
    fields.setdefault("expires_at", timezone.now() + timedelta(days=30))
    fields.setdefault("label", "Test term")
    return RegistrationCode.objects.create(
        code=new_code_value(SHARED_CODE_LENGTH), kind="shared", max_uses=max_uses,
        created_by=created_by, **fields)


def put_code_in_session(client, code):
    session = client.session
    session[SESSION_KEY] = code.pk
    session.save()
    return code


def grant_registration_code(client):
    """Give `client` a fresh, valid single-use code in its session (as if the student had typed it in)."""
    return put_code_in_session(client, make_code())


def make_staff_code(subclass, created_by=None, **fields):
    """A single-use staff code for `subclass` (its account gets that staff role)."""
    from .registration_codes import STAFF_CODE_LENGTH
    fields.setdefault("expires_at", timezone.now() + timedelta(days=7))
    return RegistrationCode.objects.create(
        code=new_code_value(STAFF_CODE_LENGTH), audience="staff", subclass=subclass,
        created_by=created_by, **fields)
