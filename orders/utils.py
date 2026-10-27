import time

from .models import Profile, Restaurant


def get_profile(user):
    profile, _ = Profile.objects.get_or_create(user=user)
    return profile


def get_owner_restaurant(user):
    if not user.is_authenticated:
        return None
    return Restaurant.objects.filter(owner=user).first()


def is_owner(user):
    if not user.is_authenticated:
        return False
    return (Restaurant.objects.filter(owner=user).exists()
            or Profile.objects.filter(user=user, is_owner=True).exists())


def throttle(request, name, limit=10, window=60):
    """True if this session made `limit` calls to `name` within `window` seconds.
    Protects your AI credits from being used up by rapid clicking."""
    now = time.time()
    key = f"thr_{name}"
    hits = [t for t in request.session.get(key, []) if now - t < window]
    blocked = len(hits) >= limit
    if not blocked:
        hits.append(now)
    request.session[key] = hits
    return blocked
