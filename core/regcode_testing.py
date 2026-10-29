"""Test helper: let a test client past the registration-code step."""
from datetime import timedelta

from django.utils import timezone

from .models import RegistrationCode
from .registration_codes import REG_CODE_TTL, SESSION_KEY, new_code_value


def make_code(created_by=None, **fields):
    fields.setdefault("expires_at", timezone.now() + REG_CODE_TTL)
    return RegistrationCode.objects.create(code=new_code_value(), created_by=created_by, **fields)


def grant_registration_code(client):
    """Give `client` a fresh, valid code in its session (as if the student had typed it in)."""
    code = make_code()
    session = client.session
    session[SESSION_KEY] = code.pk
    session.save()
    return code
