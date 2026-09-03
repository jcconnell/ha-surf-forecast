"""Tests for deriving shore direction from OSM coastline geometry.

OSM draws natural=coastline with land on the LEFT of the way and water on the
RIGHT, so a coastline drawn west-to-east has water to the south.
"""

import pytest
from surf_forecast_pure import coastline, geo

# Near the equator these keep the arithmetic easy to reason about:
# 0.001 deg of latitude is ~110 m, 0.001 deg of longitude is ~111 m.
DEG = 0.001


def payload(*ways):
    """Build an Overpass-shaped response from lists of (lat, lon) points."""
    elements = []
    node_id = 1
    for points in ways:
        ids = []
        for lat, lon in points:
            elements.append({"type": "node", "id": node_id, "lat": lat, "lon": lon})
            ids.append(node_id)
            node_id += 1
        elements.append({"type": "way", "id": 9000 + len(elements), "nodes": ids})
    return {"elements": elements}


def line(lat, lon, dlat, dlon, count):
    """A run of `count` points stepping by (dlat, dlon)."""
    return [(lat + dlat * i, lon + dlon * i) for i in range(count)]


class TestSeawardNormals:
    def test_water_is_to_the_right_of_the_way(self):
        """Coastline drawn eastward means land north, water south -> normal 180."""
        data = payload(line(0.0, -0.005, 0.0, DEG, 11))
        samples = coastline.seaward_normals(data, 0.0, 0.0, radius_m=2000)
        assert samples
        for bearing, _ in samples:
            assert geo.angle_difference(bearing, 180) < 1.0

    def test_reversing_the_way_flips_the_water_side(self):
        """The same geometry drawn westward puts the water to the north."""
        data = payload(line(0.0, 0.005, 0.0, -DEG, 11))
        samples = coastline.seaward_normals(data, 0.0, 0.0, radius_m=2000)
        assert samples
        for bearing, _ in samples:
            assert geo.angle_difference(bearing, 0) < 1.0

    def test_short_segments_are_ignored(self):
        """Harbour and jetty detail must not outvote the real shoreline."""
        data = payload(line(0.0, -0.0002, 0.0, 0.0001, 5))  # ~11 m steps
        assert coastline.seaward_normals(data, 0.0, 0.0, radius_m=2000) == []

    def test_distant_segments_are_excluded(self):
        data = payload(line(0.5, -0.005, 0.0, DEG, 11))
        assert coastline.seaward_normals(data, 0.0, 0.0, radius_m=500) == []

    def test_segment_length_is_returned_as_the_weight(self):
        data = payload(line(0.0, -0.005, 0.0, DEG, 3))
        samples = coastline.seaward_normals(data, 0.0, 0.0, radius_m=2000)
        for _, weight in samples:
            assert 100 < weight < 125

    @pytest.mark.parametrize(
        "bad",
        [
            {},
            {"elements": []},
            {"elements": [{"type": "way", "id": 1, "nodes": [1, 2]}]},  # nodes missing
            {"elements": [{"type": "node", "id": 1}]},  # no lat/lon
        ],
    )
    def test_malformed_payloads_yield_nothing(self, bad):
        assert coastline.seaward_normals(bad, 0.0, 0.0, radius_m=1000) == []


class TestEstimateFromPayload:
    def test_straight_coast_gives_a_confident_estimate(self):
        data = payload(line(0.0, -0.01, 0.0, DEG, 21))
        est = coastline.estimate_from_payload(data, 0.0, 0.0, radius_m=2000)
        assert est is not None
        assert geo.angle_difference(est.bearing, 180) < 1.0
        assert est.coherence > 0.95
        assert est.confident is True
        assert est.radius_m == 2000

    def test_a_jagged_coast_still_averages_to_its_trend(self):
        """A sawtooth shoreline facing south must still read as south.

        Individual segments alternate either side of the trend, so taking the
        nearest one is a coin flip; the circular mean is what makes this work.
        """
        points = [(0.0 if i % 2 else DEG, -0.006 + DEG * i) for i in range(12)]
        est = coastline.estimate_from_payload(payload(points), 0.0, 0.0, 2000)
        assert est is not None
        assert geo.angle_difference(est.bearing, 180) < 10

    def test_an_enclosed_basin_is_rejected(self):
        """Three sides of a harbour face three ways, so there is no answer.

        This is the real failure mode at somewhere like Kewalo Basin, and it
        must return None rather than a confident-looking wrong bearing.
        """
        data = payload(
            line(0.0, -0.005, 0.0, DEG, 11),        # eastward  -> normal 180
            [(0.0 + DEG * i, 0.005) for i in range(11)],   # northward -> normal 90
            line(0.010, 0.005, 0.0, -DEG, 11),      # westward  -> normal 0
        )
        samples = coastline.seaward_normals(data, 0.005, 0.0, radius_m=3000)
        assert len(samples) > 20, "expected all three walls to be in range"
        assert coastline.estimate_from_payload(data, 0.005, 0.0, 3000) is None

    def test_opposing_coasts_cancel_to_nothing(self):
        data = payload(
            line(0.0, -0.005, 0.0, DEG, 11),   # water south
            line(0.0, 0.005, 0.0, -DEG, 11),   # water north
        )
        assert coastline.estimate_from_payload(data, 0.0, 0.0, 2000) is None

    def test_longer_shoreline_outweighs_a_shorter_spur(self):
        """Length weighting is what rescued the real Kewalo Basin case."""
        data = payload(
            line(0.0, -0.02, 0.0, DEG * 4, 11),        # long, water south
            line(0.0, 0.0006, DEG * 0.6, 0.0, 3),      # short spur, other normal
        )
        est = coastline.estimate_from_payload(data, 0.0, 0.0, 3000)
        assert est is not None
        assert geo.angle_difference(est.bearing, 180) < 20

    def test_empty_payload_returns_none(self):
        assert coastline.estimate_from_payload({"elements": []}, 0.0, 0.0, 1000) is None


class TestShoreEstimate:
    def test_confidence_needs_agreement_and_enough_segments(self):
        assert coastline.ShoreEstimate(180.0, 0.9, 10, 800).confident is True
        assert coastline.ShoreEstimate(180.0, 0.9, 2, 800).confident is False
        assert coastline.ShoreEstimate(180.0, 0.45, 10, 800).confident is False


class TestOverpassQuery:
    def test_asks_for_coastline_around_the_point(self):
        q = coastline.overpass_query(21.29, -157.86, 800)
        assert "natural=coastline" in q
        assert "around:800,21.29,-157.86" in q
        assert q.startswith("[out:json]")
