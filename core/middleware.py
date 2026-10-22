from django.http import JsonResponse
from django.shortcuts import redirect

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