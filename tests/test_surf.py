"""Tests for the surf scoring model."""

import pytest
from surf_forecast_pure import surf

# A west-facing beach: it looks out to sea on bearing 270, so an offshore
# wind blows from the land at bearing 90.
WEST_FACING = 270.0


class TestGeometry:
    def test_angle_difference_wraps_around_north(self):
        assert surf.angle_difference(350, 10) == pytest.approx(20)
        assert surf.angle_difference(10, 350) == pytest.approx(20)

    def test_angle_difference_is_never_more_than_180(self):
        for a in range(0, 360, 7):
            for b in range(0, 360, 11):
                assert 0 <= surf.angle_difference(a, b) <= 180

    def test_offshore_offset_is_zero_for_a_wind_off_the_land(self):
        assert surf.offshore_offset(90, WEST_FACING) == pytest.approx(0)

    def test_offshore_offset_is_180_for_a_wind_off_the_sea(self):
        assert surf.offshore_offset(270, WEST_FACING) == pytest.approx(180)

    @pytest.mark.parametrize(
        "bearing,expected",
        [(0, "N"), (45, "NE"), (90, "E"), (180, "S"), (270, "W"), (350, "N"), (360, "N")],
    )
    def test_compass_point(self, bearing, expected):
        assert surf.compass_point(bearing) == expected

    def test_compass_point_of_none_is_none(self):
        assert surf.compass_point(None) is None


class TestWindRelation:
    @pytest.mark.parametrize(
        "wind_from,expected",
        [
            (90, surf.WIND_OFFSHORE),
            (135, surf.WIND_CROSS_OFFSHORE),
            (180, surf.WIND_CROSS_SHORE),
            (225, surf.WIND_CROSS_ONSHORE),
            (270, surf.WIND_ONSHORE),
        ],
    )
    def test_bands(self, wind_from, expected):
        assert surf.wind_relation(wind_from, WEST_FACING, 8.0) == expected

    def test_calm_air_is_glassy_whatever_its_direction(self):
        assert surf.wind_relation(270, WEST_FACING, 0.5) == surf.WIND_GLASSY

    def test_unknown_direction_is_unknown(self):
        assert surf.wind_relation(None, WEST_FACING, 5.0) is None


class TestHeightScore:
    def test_flat_ocean_scores_zero(self):
        assert surf.height_score(0.1, 1.0, 2.5) == 0.0
        assert surf.height_score(None, 1.0, 2.5) == 0.0

    def test_ideal_band_scores_one(self):
        assert surf.height_score(1.0, 1.0, 2.5) == 1.0
        assert surf.height_score(1.8, 1.0, 2.5) == 1.0
        assert surf.height_score(2.5, 1.0, 2.5) == 1.0

    def test_score_rises_through_the_small_range(self):
        small = surf.height_score(0.5, 1.0, 2.5)
        bigger = surf.height_score(0.8, 1.0, 2.5)
        assert 0.0 < small < bigger < 1.0

    def test_oversized_surf_decays_but_never_to_zero(self):
        assert surf.height_score(3.5, 1.0, 2.5) < 1.0
        assert surf.height_score(8.0, 1.0, 2.5) == pytest.approx(surf.OVERSIZED_FLOOR)
        assert surf.height_score(8.0, 1.0, 2.5) > 0.0

    def test_score_never_leaves_the_unit_range(self):
        for height in [x / 10 for x in range(0, 150)]:
            assert 0.0 <= surf.height_score(height, 1.0, 2.5) <= 1.0


class TestPeriodScore:
    def test_short_wind_slop_scores_low(self):
        assert surf.period_score(4.0) == pytest.approx(surf.MIN_PERIOD_SCORE)

    def test_long_groundswell_scores_full(self):
        assert surf.period_score(16.0) == 1.0

    def test_score_increases_with_period(self):
        scores = [surf.period_score(p) for p in (6, 8, 10, 12, 14)]
        assert scores == sorted(scores)
        assert len(set(scores)) == len(scores)

    def test_missing_period_scores_zero(self):
        assert surf.period_score(None) == 0.0


class TestWindScore:
    def test_glassy_is_perfect_even_pointing_onshore(self):
        assert surf.wind_score(0.5, 270, WEST_FACING) == 1.0

    def test_offshore_beats_onshore_at_the_same_speed(self):
        offshore = surf.wind_score(7.0, 90, WEST_FACING)
        onshore = surf.wind_score(7.0, 270, WEST_FACING)
        assert offshore > onshore

    def test_onshore_gets_worse_as_it_builds(self):
        light = surf.wind_score(3.0, 270, WEST_FACING)
        strong = surf.wind_score(9.0, 270, WEST_FACING)
        assert light > strong

    def test_a_howling_offshore_is_penalised(self):
        moderate = surf.wind_score(6.0, 90, WEST_FACING)
        howling = surf.wind_score(18.0, 90, WEST_FACING)
        assert howling < moderate

    def test_unknown_wind_is_neutral(self):
        assert surf.wind_score(None, 90, WEST_FACING) == 0.5
        assert surf.wind_score(6.0, None, WEST_FACING) == 0.5


class TestSurfRating:
    def test_flat_is_zero_however_perfect_everything_else_is(self):
        result = surf.surf_rating(0.1, 15, 1.0, 90, WEST_FACING, 1.0, 2.5)
        assert result["rating"] == 0.0

    def test_clean_groundswell_rates_highly(self):
        result = surf.surf_rating(1.8, 14, 2.0, 90, WEST_FACING, 1.0, 2.5)
        assert result["rating"] >= 8.5

    def test_onshore_slop_rates_poorly(self):
        result = surf.surf_rating(1.8, 6, 12.0, 270, WEST_FACING, 1.0, 2.5)
        assert result["rating"] <= 3.0

    def test_same_swell_is_better_offshore_than_onshore(self):
        offshore = surf.surf_rating(1.5, 11, 7.0, 90, WEST_FACING, 1.0, 2.5)
        onshore = surf.surf_rating(1.5, 11, 7.0, 270, WEST_FACING, 1.0, 2.5)
        assert offshore["rating"] > onshore["rating"]

    def test_longer_period_beats_shorter_all_else_equal(self):
        long_swell = surf.surf_rating(1.5, 14, 3.0, 90, WEST_FACING, 1.0, 2.5)
        short_swell = surf.surf_rating(1.5, 7, 3.0, 90, WEST_FACING, 1.0, 2.5)
        assert long_swell["rating"] > short_swell["rating"]

    def test_components_are_reported(self):
        result = surf.surf_rating(1.5, 12, 3.0, 90, WEST_FACING, 1.0, 2.5)
        assert set(result) == {"rating", "height_score", "period_score", "wind_score"}
        for key in ("height_score", "period_score", "wind_score"):
            assert 0.0 <= result[key] <= 1.0

    def test_rating_always_within_zero_to_ten(self):
        for height in (0.0, 0.5, 1.5, 3.0, 7.0):
            for period in (3, 8, 20):
                for speed in (0.0, 5.0, 25.0):
                    for direction in (0, 90, 180, 270):
                        rating = surf.surf_rating(
                            height, period, speed, direction, WEST_FACING, 1.0, 2.5
                        )["rating"]
                        assert 0.0 <= rating <= 10.0

    def test_missing_data_does_not_raise(self):
        result = surf.surf_rating(None, None, None, None, WEST_FACING, 1.0, 2.5)
        assert result["rating"] == 0.0


class TestConditionsText:
    @pytest.mark.parametrize(
        "rating,expected",
        [
            (0.0, surf.CONDITION_FLAT),
            (0.9, surf.CONDITION_FLAT),
            (2.0, surf.CONDITION_POOR),
            (4.0, surf.CONDITION_FAIR),
            (6.0, surf.CONDITION_GOOD),
            (8.0, surf.CONDITION_VERY_GOOD),
            (9.5, surf.CONDITION_EPIC),
            (10.0, surf.CONDITION_EPIC),
        ],
    )
    def test_bands(self, rating, expected):
        assert surf.conditions_text(rating) == expected

    def test_unknown_rating_has_no_text(self):
        assert surf.conditions_text(None) is None
