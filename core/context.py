from .hotlines import get_hotlines


def site_info(request):
    return {"hotlines": get_hotlines()}