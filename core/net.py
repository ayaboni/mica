"""One place that decides "what is this visitor's IP address?".

Behind a proxy (Render, Cloudflare...) REMOTE_ADDR is the proxy's address, so every visitor
looks identical and per-IP limits (registration-code lockout, password-reset limit, audit
records) hit everybody at once. The real address arrives in X-Forwarded-For, but a visitor can
put anything they like at the *front* of that header; proxies only *append* to it. So the only
entries that can be trusted are the ones added by your own proxies, counted from the END.

Settings (all read from the environment, see settings.py):
  TRUST_PROXY_IP        False (default) -> always use REMOTE_ADDR. Turn on only when deployed
                        behind a proxy, and never when the app is reachable directly.
  TRUSTED_PROXY_COUNT   How many proxies sit between the internet and the app (default 1).
                        The visitor's IP is then the Nth entry from the right of
                        X-Forwarded-For.
  CLIENT_IP_HEADER      Optional. A header your proxy sets AND overwrites, e.g. "CF-Connecting-IP"
                        when Cloudflare is in front. Used first when present and valid.
"""
import ipaddress

from django.conf import settings


def _valid_ip(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def client_ip(request):
    """The visitor's IP as a string, or None. Always a valid address (safe for IPAddressField)."""
    remote = _valid_ip(request.META.get("REMOTE_ADDR"))
    if not getattr(settings, "TRUST_PROXY_IP", False):
        return remote

    header = (getattr(settings, "CLIENT_IP_HEADER", "") or "").strip()
    if header:
        meta_key = "HTTP_" + header.upper().replace("-", "_")
        ip = _valid_ip(request.META.get(meta_key))
        if ip:
            return ip

    try:
        hops = max(1, int(getattr(settings, "TRUSTED_PROXY_COUNT", 1)))
    except (TypeError, ValueError):
        hops = 1
    parts = [p.strip() for p in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if p.strip()]
    if len(parts) >= hops:
        ip = _valid_ip(parts[-hops])
        if ip:
            return ip
    return remote
