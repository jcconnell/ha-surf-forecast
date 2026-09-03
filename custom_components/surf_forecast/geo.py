"""Geodesic helpers.

Pure functions with no Home Assistant, network or third-party dependencies,
so the geometry behind shore-direction estimation is unit testable on its own.
"""

from __future__ import annotations

import math

EARTH_RADIUS_M = 6371000.0


def angle_difference(a: float, b: float) -> float:
    """Smallest absolute angle between two bearings, in degrees (0-180)."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


def initial_bearing(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Initial great-circle bearing from point 1 to point 2, in degrees.

    This is what turns "drop a pin out in the water" into a shore direction.
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta = math.radians(lon2 - lon1)
    y = math.sin(delta) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(
        delta
    )
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in metres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def destination(
    lat: float, lon: float, bearing: float, distance: float
) -> tuple[float, float]:
    """Point reached by travelling ``distance`` metres along ``bearing``.

    Used to drop the offshore map pin in roughly the right place so the user
    only has to nudge it.
    """
    phi1, lambda1 = math.radians(lat), math.radians(lon)
    theta = math.radians(bearing)
    delta = distance / EARTH_RADIUS_M

    phi2 = math.asin(
        math.sin(phi1) * math.cos(delta)
        + math.cos(phi1) * math.sin(delta) * math.cos(theta)
    )
    lambda2 = lambda1 + math.atan2(
        math.sin(theta) * math.sin(delta) * math.cos(phi1),
        math.cos(delta) - math.sin(phi1) * math.sin(phi2),
    )
    return math.degrees(phi2), (math.degrees(lambda2) + 540.0) % 360.0 - 180.0


def point_to_segment_distance(
    plat: float,
    plon: float,
    alat: float,
    alon: float,
    blat: float,
    blon: float,
) -> float:
    """Approximate distance from a point to a segment, in metres.

    Uses a local equirectangular projection, which is accurate enough over the
    few hundred metres of coastline this is applied to.
    """
    kx = math.cos(math.radians(plat)) * 111320.0
    ky = 110540.0
    px, py = (plon - alon) * kx, (plat - alat) * ky
    bx, by = (blon - alon) * kx, (blat - alat) * ky
    length_sq = bx * bx + by * by
    if length_sq == 0:
        return math.hypot(px, py)
    t = max(0.0, min(1.0, (px * bx + py * by) / length_sq))
    return math.hypot(px - t * bx, py - t * by)


def circular_mean(
    samples: list[tuple[float, float]],
) -> tuple[float | None, float]:
    """Weighted circular mean of bearings.

    Returns ``(mean_bearing, coherence)``. Coherence is the resultant vector
    length: near 1 when the samples agree, near 0 when they cancel out and the
    mean is meaningless. Averaging bearings arithmetically is wrong (350 and 10
    average to 180, not 0), hence the vector form.
    """
    if not samples:
        return None, 0.0
    total = sum(weight for _, weight in samples)
    if total <= 0:
        return None, 0.0
    y = sum(w * math.sin(math.radians(b)) for b, w in samples)
    x = sum(w * math.cos(math.radians(b)) for b, w in samples)
    if x == 0.0 and y == 0.0:
        return None, 0.0
    mean = (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
    return mean, math.hypot(x, y) / total
