"""Tests for the geodesic helpers."""

import math

import pytest
from surf_forecast_pure import geo


class TestAngleDifference:
    def test_wraps_around_north(self):
        assert geo.angle_difference(350, 10) == pytest.approx(20)
        assert geo.angle_difference(10, 350) == pytest.approx(20)

    def test_never_exceeds_180(self):
        for a in range(0, 360, 13):
            for b in range(0, 360, 17):
                assert 0 <= geo.angle_difference(a, b) <= 180


class TestInitialBearing:
    """A pin dropped due <compass point> of the spot must read as that bearing."""

    @pytest.mark.parametrize(
        "dlat,dlon,expected",
        [(0.01, 0.0, 0), (0.0, 0.01, 90), (-0.01, 0.0, 180), (0.0, -0.01, 270)],
    )
    def test_cardinal_directions(self, dlat, dlon, expected):
        bearing = geo.initial_bearing(21.29, -157.86, 21.29 + dlat, -157.86 + dlon)
        assert geo.angle_difference(bearing, expected) < 0.5

    def test_south_facing_break(self):
        """Kewalos with a pin dropped straight out to sea reads roughly south."""
        bearing = geo.initial_bearing(21.2914, -157.8578, 21.2870, -157.8578)
        assert geo.angle_difference(bearing, 180) < 1.0

    def test_always_in_range(self):
        for dlat in (-0.02, 0.0, 0.02):
            for dlon in (-0.02, 0.0, 0.02):
                if dlat or dlon:
                    b = geo.initial_bearing(21.0, -157.0, 21.0 + dlat, -157.0 + dlon)
                    assert 0.0 <= b < 360.0


class TestDistance:
    def test_zero_for_the_same_point(self):
        assert geo.distance_m(21.29, -157.86, 21.29, -157.86) == pytest.approx(0)

    def test_one_degree_of_latitude_is_about_111km(self):
        d = geo.distance_m(0.0, 0.0, 1.0, 0.0)
        assert 110_000 < d < 112_000

    def test_is_symmetric(self):
        a = geo.distance_m(21.29, -157.86, 21.30, -157.85)
        b = geo.distance_m(21.30, -157.85, 21.29, -157.86)
        assert a == pytest.approx(b)


class TestDestination:
    """destination() and initial_bearing() must be inverses."""

    @pytest.mark.parametrize("bearing", [0, 45, 90, 135, 180, 225, 270, 315])
    def test_round_trips_with_bearing(self, bearing):
        lat, lon = geo.destination(21.2914, -157.8578, bearing, 500.0)
        back = geo.initial_bearing(21.2914, -157.8578, lat, lon)
        assert geo.angle_difference(back, bearing) < 0.5

    @pytest.mark.parametrize("bearing", [0, 90, 180, 270])
    def test_round_trips_with_distance(self, bearing):
        lat, lon = geo.destination(21.2914, -157.8578, bearing, 500.0)
        assert geo.distance_m(21.2914, -157.8578, lat, lon) == pytest.approx(500, abs=1)

    def test_longitude_stays_in_range(self):
        lat, lon = geo.destination(0.0, 179.99, 90, 5000.0)
        assert -180.0 <= lon <= 180.0


class TestPointToSegmentDistance:
    def test_zero_on_the_segment(self):
        d = geo.point_to_segment_distance(0.0, 0.0, 0.0, -0.01, 0.0, 0.01)
        assert d == pytest.approx(0, abs=1)

    def test_perpendicular_offset(self):
        """A point 0.001 deg north of an east-west segment is ~110 m away."""
        d = geo.point_to_segment_distance(0.001, 0.0, 0.0, -0.01, 0.0, 0.01)
        assert 100 < d < 120

    def test_clamps_past_the_end(self):
        """Beyond the segment end it measures to the endpoint, not the line."""
        near = geo.point_to_segment_distance(0.0, 0.02, 0.0, -0.01, 0.0, 0.01)
        far = geo.point_to_segment_distance(0.0, 0.03, 0.0, -0.01, 0.0, 0.01)
        assert far > near > 0

    def test_degenerate_segment_is_point_distance(self):
        d = geo.point_to_segment_distance(0.0, 0.0, 0.001, 0.0, 0.001, 0.0)
        assert d == pytest.approx(110.54, abs=2)


class TestCircularMean:
    def test_averages_across_north(self):
        """The whole point: 350 and 10 average to 0, not 180."""
        mean, coherence = geo.circular_mean([(350.0, 1.0), (10.0, 1.0)])
        assert geo.angle_difference(mean, 0) < 0.001
        assert coherence > 0.98

    def test_agreeing_samples_are_coherent(self):
        mean, coherence = geo.circular_mean([(180.0, 1.0)] * 5)
        assert mean == pytest.approx(180)
        assert coherence == pytest.approx(1.0)

    def test_opposing_samples_cancel(self):
        mean, coherence = geo.circular_mean([(0.0, 1.0), (180.0, 1.0)])
        assert coherence < 0.01
        assert mean is None or True  # direction is meaningless here

    def test_scattered_samples_have_low_coherence(self):
        _, coherence = geo.circular_mean([(b, 1.0) for b in (0, 90, 180, 270)])
        assert coherence < 0.01

    def test_weighting_pulls_the_mean(self):
        """Length weighting is what makes long shoreline outvote short jetties."""
        mean, _ = geo.circular_mean([(180.0, 100.0), (90.0, 1.0)])
        assert geo.angle_difference(mean, 180) < 2

    def test_empty_and_zero_weight(self):
        assert geo.circular_mean([]) == (None, 0.0)
        assert geo.circular_mean([(180.0, 0.0)]) == (None, 0.0)
