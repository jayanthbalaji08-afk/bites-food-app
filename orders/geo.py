from math import radians, sin, cos, asin, sqrt


def distance_km(lat1, lon1, lat2, lon2):
    """Haversine distance between two points in kilometres."""
    lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
    a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * asin(sqrt(a))


def interpolate(lat1, lon1, lat2, lon2, t):
    """Point a fraction t (0..1) of the way from point 1 to point 2."""
    return (lat1 + (lat2 - lat1) * t, lon1 + (lon2 - lon1) * t)
