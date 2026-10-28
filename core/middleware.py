import time
from django.contrib.auth import logout
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
import logging

logger = logging.getLogger(__name__)


ALLOWED = ("/logout/", "/archived/", "/static/", "/media/")

class ArchivedStudentMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if (user.is_authenticated and not (user.is_staff or user.is_superuser)
                and not request.path.startswith(ALLOWED)):
            profile = getattr(user, "profile", None)
            if profile and profile.account_status != "active":
                if request.path.startswith("/api/"):
                    return JsonResponse({"error": "archived"}, status=403)
                return redirect("archived")
        return self.get_response(request)

STAFF_IDLE_SECONDS = 15 * 60
STUDENT_IDLE_SECONDS = 30 * 60

class IdleTimeoutMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            limit = STAFF_IDLE_SECONDS if (user.is_staff or user.is_superuser) else STUDENT_IDLE_SECONDS
            now = int(time.time())
            last = request.session.get('last_activity')

            if last is not None and now - last > limit:
                logout(request)
                if request.path.startswith('/api/'):
                    return JsonResponse({'error': 'Session expired.', 'expired': True}, status=401)
                return redirect(reverse('login') + '?expired=1')

            # background polling (GET /api/...) must not keep the session alive
            is_poll = request.method == 'GET' and request.path.startswith('/api/')
            if last is None or not is_poll:
                request.session['last_activity'] = now

        return self.get_response(request)

class SuspendedAccountMiddleware:
    """A staff member suspended while signed in is signed out on their next request."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated and '_auth_user_id' in request.session:
            request.session.flush()
            if request.path.startswith('/api/'):
                return JsonResponse({'error': 'Account suspended.', 'suspended': True}, status=401)
            return redirect(reverse('login') + '?suspended=1')
        return self.get_response(request)

SETUP_ALLOWED = ("/staff/setup/", "/logout/", "/static/", "/media/")

class StaffSetupMiddleware:
    """A brand-new staff account (created by the admin, no password yet) can only reach the
    first-time setup page until it has set a password and confirmed its details."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if (user.is_authenticated and user.is_staff and not user.is_superuser
                and not request.path.startswith(SETUP_ALLOWED)):
            from .models import StaffProfile
            profile = StaffProfile.objects.filter(user=user).only("must_complete_setup").first()
            if profile and profile.must_complete_setup:
                if request.path.startswith("/api/"):
                    return JsonResponse({"error": "Finish setting up your account first.", "setup_required": True}, status=403)
                return redirect("staff_setup")
        return self.get_response(request)

MAINTENANCE_ALLOWED = ("/login/", "/logout/", "/static/", "/media/", "/admin/")

class MaintenanceModeMiddleware:
    """While the admin has maintenance mode on, only the admin can use the site. Everyone else sees
    a friendly page (with the crisis hotlines) instead of an error."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith(MAINTENANCE_ALLOWED) or request.user.is_superuser:
            return self.get_response(request)
        try:
            from .models import SiteSettings
            row = SiteSettings.objects.filter(pk=1).only("maintenance_on", "maintenance_message").first()
        except Exception:
            logger.exception("Maintenance check failed; letting the request through.")
            return self.get_response(request)
        if row and row.maintenance_on:
            if request.path.startswith("/api/"):
                return JsonResponse({"error": "maintenance", "maintenance": True}, status=503)
            return render(request, "core/maintenance.html", {"message": row.maintenance_message}, status=503)
        return self.get_response(request)