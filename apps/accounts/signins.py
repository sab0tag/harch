"""Sign-in log for the admin («Журнал подій → Входи в систему»), built on django-axes records.

Successful sign-ins come from axes' AccessLog, failed ones from AccessFailureLog (both kept 90 days,
deploy/crontab). A sign-in is flagged «нова адреса» when that login has not signed in from that IP in the
previous 90 days, and «поза мережею лікарні» when HOSPITAL_NETWORKS is set and the IP is outside it.
No geolocation: that would send staff addresses to an outside service.
"""
import ipaddress
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.dispatch import receiver
from django.urls import reverse
from django.utils import timezone

WINDOW = timedelta(days=90)

OS_PATTERNS = (("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"), ("Windows", "Windows"),
               ("Mac OS X", "Mac"), ("Macintosh", "Mac"), ("Linux", "Linux"))
BROWSER_PATTERNS = (("Edg/", "Edge"), ("OPR/", "Opera"), ("YaBrowser", "Яндекс"), ("Firefox/", "Firefox"),
                    ("Chrome/", "Chrome"), ("Safari/", "Safari"))


def device(user_agent):
    ua = "" if (user_agent or "").strip() in ("", "<unknown>") else user_agent
    os_name = next((name for needle, name in OS_PATTERNS if needle in ua), "")
    browser = next((name for needle, name in BROWSER_PATTERNS if needle in ua), "")
    return " · ".join(x for x in (browser, os_name) if x) or (ua[:40] or "—")


def networks():
    result = []
    for item in getattr(settings, "HOSPITAL_NETWORKS", []) or []:
        try:
            result.append(ipaddress.ip_network(item.strip(), strict=False))
        except ValueError:
            continue
    return result


def outside_hospital(ip, nets=None):
    nets = networks() if nets is None else nets
    if not nets or not ip:
        return False
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not any(address in n for n in nets)


def rows(limit=300, only_flagged=False, username=""):
    """Newest first: {time, username, user, kind (ok|failed|locked), ip, device, logout, new_address, outside}."""
    from axes.models import AccessFailureLog, AccessLog

    since = timezone.now() - WINDOW
    nets = networks()
    ok = list(AccessLog.objects.filter(attempt_time__gte=since).order_by("attempt_time")
              .values("username", "ip_address", "user_agent", "attempt_time", "logout_time"))
    seen, events = set(), []
    for r in ok:                       # oldest first, so «new» means: not from this address before
        key = (r["username"], r["ip_address"])
        events.append({"time": r["attempt_time"], "username": r["username"], "kind": "ok", "ip": r["ip_address"],
                       "device": device(r["user_agent"]), "logout": r["logout_time"],
                       "new_address": key not in seen, "outside": outside_hospital(r["ip_address"], nets)})
        seen.add(key)
    for r in AccessFailureLog.objects.filter(attempt_time__gte=since).values(
            "username", "ip_address", "user_agent", "attempt_time", "locked_out"):
        events.append({"time": r["attempt_time"], "username": r["username"],
                       "kind": "locked" if r["locked_out"] else "failed", "ip": r["ip_address"],
                       "device": device(r["user_agent"]), "logout": None, "new_address": False,
                       "outside": outside_hospital(r["ip_address"], nets)})
    if username:
        events = [e for e in events if username.lower() in (e["username"] or "").lower()]
    if only_flagged:
        events = [e for e in events if e["new_address"] or e["outside"] or e["kind"] == "locked"]
    events.sort(key=lambda e: e["time"], reverse=True)
    events = events[:limit]
    users = {u.username: u for u in get_user_model().objects.filter(username__in={e["username"] for e in events})}
    for e in events:
        e["user"] = users.get(e["username"])
    return events


def _on_login(sender, request, user, **kwargs):
    """Tell the admins about a sign-in from a new address outside the hospital (any new address for an admin).
    «Earlier» = this login's sign-ins other than the current one (axes may already have logged it)."""
    from axes.helpers import get_client_ip_address, get_client_session_hash
    from axes.models import AccessLog

    from apps.accounts.models import Role
    from apps.workflow import notify as n

    ip = get_client_ip_address(request)
    earlier = AccessLog.objects.filter(username=user.username, attempt_time__gte=timezone.now() - WINDOW) \
        .exclude(session_hash=get_client_session_hash(request))
    if not ip or not earlier.exists() or earlier.filter(ip_address=ip).exists():
        return                          # someone's first sign-in, or a known address: not news
    outside = outside_hospital(ip)
    if not (outside or user.role == Role.ADMIN):
        return
    admins = get_user_model().objects.filter(role=Role.ADMIN, is_active=True)
    where = "поза мережею лікарні" if outside else "нова адреса"
    ua = device(request.META.get("HTTP_USER_AGENT", ""))
    n.notify(admins, None, f"Вхід з нової адреси: {user} ({user.username})",
             f"{ip} · {ua} · {where}", url=reverse("workflow:audit") + "?tab=signins")


def connect():
    from django.contrib.auth.signals import user_logged_in

    receiver(user_logged_in, dispatch_uid="harch-signin-alert")(_on_login)

