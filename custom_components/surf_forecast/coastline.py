"""Estimate which way a beach faces from OpenStreetMap coastline geometry.

Pure functions only, with no Home Assistant or network dependencies, so the
geometry can be exercised against captured Overpass payloads in tests. The
HTTP call lives in ``overpass.py``.

OSM draws ``natural=coastline`` ways with the land on the LEFT and the water
on the RIGHT, so the seaward normal of a segment is its bearing plus 90
degrees. That rule is exact; the difficulty is picking which segments to
trust.

Surf spots cluster around harbours, jetties and points, where the nearest
coastline is often a few metres of harbour wall pointing an arbitrary way. So
rather than taking the closest segment, this takes a length-weighted circular
mean of the seaward normals nearby, ignoring very short segments, and reports
a coherence value so a poor estimate can be recognised as poor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .geo import (
    circular_mean,
    distance_m,
    initial_bearing,
    point_to_segment_distance,
)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_TIMEOUT_S = 25

# Radii tried in order. Too small and harbour detail dominates; too large and
# the estimate follows the coast curving away from the spot.
SEARCH_RADII_M = (800, 1500)
# Segments shorter than this are harbour and jetty detail, not shoreline.
MIN_SEGMENT_M = 50.0
# Below this the normals disagree too much to mean anything.
MIN_COHERENCE = 0.4


@dataclass(frozen=True)
class ShoreEstimate:
    """A shore-direction estimate and how much to trust it."""

    bearing: float
    coherence: float
    segments: int
    radius_m: int

    @property
    def confident(self) -> bool:
        """Whether the segments agreed well enough to prefill without a caveat."""
        return self.coherence >= 0.55 and self.segments >= 5


def seaward_normals(
    payload: dict[str, Any],
    lat: float,
    lon: float,
    radius_m: float,
    min_segment_m: float = MIN_SEGMENT_M,
) -> list[tuple[float, float]]:
    """Turn an Overpass response into (seaward bearing, length) samples."""
    elements = payload.get("elements") or []
    nodes = {
        element["id"]: (element["lat"], element["lon"])
        for element in elements
        if element.get("type") == "node"
        and "lat" in element
        and "lon" in element
    }

    samples: list[tuple[float, float]] = []
    for element in elements:
        if element.get("type") != "way":
            continue
        node_ids = element.get("nodes") or []
        for first, second in zip(node_ids, node_ids[1:]):
            if first not in nodes or second not in nodes:
                continue
            alat, alon = nodes[first]
            blat, blon = nodes[second]
            if point_to_segment_distance(lat, lon, alat, alon, blat, blon) > radius_m:
                continue
            length = distance_m(alat, alon, blat, blon)
            if length < min_segment_m:
                continue
            # Land is on the left of the way, so the water is 90 deg clockwise.
            samples.append(((initial_bearing(alat, alon, blat, blon) + 90.0) % 360.0, length))
    return samples


def estimate_from_payload(
    payload: dict[str, Any],
    lat: float,
    lon: float,
    radius_m: int,
) -> ShoreEstimate | None:
    """Compute an estimate from one Overpass response, or None if unusable."""
    samples = seaward_normals(payload, lat, lon, radius_m)
    if not samples:
        return None
    mean, coherence = circular_mean(samples)
    if mean is None or coherence < MIN_COHERENCE:
        return None
    return ShoreEstimate(
        bearing=round(mean, 1),
        coherence=round(coherence, 2),
        segments=len(samples),
        radius_m=radius_m,
    )


def overpass_query(lat: float, lon: float, radius: int) -> str:
    """Overpass QL asking for coastline ways near a coordinate."""
    return (
        f"[out:json][timeout:{OVERPASS_TIMEOUT_S}];"
        f"way(around:{radius},{lat},{lon})[natural=coastline];"
        "(._;>;);out body;"
    )
