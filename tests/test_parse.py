"""Tests for Stormglass payload normalisation."""

from datetime import datetime, timedelta, timezone

import pytest
from surf_forecast_pure import parse

UTC = timezone.utc
NOON = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def _weather(*hours):
    return {"hours": list(hours), "meta": {"dailyQuota": 10, "requestCount": 3}}


class TestParseTimestamp:
    def test_iso_with_t_separator(self):
        assert parse.parse_timestamp("2018-01-19T17:00:00+00:00") == datetime(
            2018, 1, 19, 17, 0, tzinfo=UTC
        )

    def test_iso_with_space_separator(self):
        """The tide endpoint uses a space where the weather endpoint uses T."""
        assert parse.parse_timestamp("2019-03-15 03:40:44+00:00") == datetime(
            2019, 3, 15, 3, 40, 44, tzinfo=UTC
        )

    def test_zulu_suffix(self):
        assert parse.parse_timestamp("2018-01-19T17:00:00Z") == datetime(
            2018, 1, 19, 17, 0, tzinfo=UTC
        )

    def test_offset_is_converted_to_utc(self):
        assert parse.parse_timestamp("2018-01-19T17:00:00+02:00") == datetime(
            2018, 1, 19, 15, 0, tzinfo=UTC
        )

    def test_naive_timestamp_is_assumed_utc(self):
        assert parse.parse_timestamp("2018-01-19T17:00:00") == datetime(
            2018, 1, 19, 17, 0, tzinfo=UTC
        )

    @pytest.mark.parametrize("value", [None, "", "not a date", 12345, {}])
    def test_junk_returns_none(self, value):
        assert parse.parse_timestamp(value) is None


class TestPickValue:
    def test_prefers_stormglass_blended_source(self):
        assert parse.pick_value({"noaa": 2.1, "sg": 2.5, "meteo": 3.0}) == 2.5

    def test_falls_back_through_known_sources_in_order(self):
        assert parse.pick_value({"meteo": 3.0, "noaa": 2.1}) == 2.1

    def test_falls_back_to_any_value_for_an_unknown_source(self):
        assert parse.pick_value({"someNewProvider": 1.7}) == 1.7

    def test_accepts_the_strings_the_api_sometimes_returns(self):
        assert parse.pick_value({"smhi": "-2.6"}) == -2.6

    def test_accepts_a_bare_number(self):
        assert parse.pick_value(4.2) == 4.2

    def test_skips_unparseable_values(self):
        assert parse.pick_value({"sg": None, "noaa": 1.5}) == 1.5

    @pytest.mark.parametrize("value", [None, {}, "text", []])
    def test_returns_none_when_there_is_nothing_usable(self, value):
        assert parse.pick_value(value) is None

    def test_booleans_are_not_treated_as_numbers(self):
        assert parse.pick_value({"sg": True}) is None


class TestNormalizeHours:
    def test_flattens_source_maps_into_plain_values(self):
        payload = _weather(
            {
                "time": "2026-09-02T12:00:00+00:00",
                "waveHeight": {"sg": 1.5, "noaa": 1.4},
                "windSpeed": {"noaa": 3.2},
            }
        )
        hours = parse.normalize_hours(payload)
        assert hours == [
            {"time": NOON, "waveHeight": 1.5, "windSpeed": 3.2}
        ]

    def test_sorts_by_time(self):
        payload = _weather(
            {"time": "2026-09-02T15:00:00+00:00", "waveHeight": {"sg": 3}},
            {"time": "2026-09-02T12:00:00+00:00", "waveHeight": {"sg": 1}},
            {"time": "2026-09-02T13:00:00+00:00", "waveHeight": {"sg": 2}},
        )
        assert [h["waveHeight"] for h in parse.normalize_hours(payload)] == [1, 2, 3]

    def test_drops_entries_without_a_usable_time(self):
        payload = _weather(
            {"waveHeight": {"sg": 1.5}},
            {"time": "2026-09-02T12:00:00+00:00", "waveHeight": {"sg": 2.0}},
        )
        assert len(parse.normalize_hours(payload)) == 1

    def test_omits_parameters_with_no_usable_value(self):
        payload = _weather(
            {"time": "2026-09-02T12:00:00+00:00", "waveHeight": {}, "gust": {"sg": 5}}
        )
        hour = parse.normalize_hours(payload)[0]
        assert "waveHeight" not in hour
        assert hour["gust"] == 5

    @pytest.mark.parametrize("payload", [None, {}, {"hours": None}, "nope"])
    def test_empty_and_malformed_payloads_yield_nothing(self, payload):
        assert parse.normalize_hours(payload) == []


class TestNormalizeTides:
    def test_parses_high_and_low_events(self):
        payload = {
            "data": [
                {"height": 1.18, "time": "2026-09-02 03:40:44+00:00", "type": "high"},
                {"height": 0.60, "time": "2026-09-02 09:53:54+00:00", "type": "low"},
            ]
        }
        tides = parse.normalize_tides(payload)
        assert [t["type"] for t in tides] == ["high", "low"]
        assert tides[0]["height"] == 1.18

    def test_ignores_events_of_an_unknown_type(self):
        payload = {
            "data": [
                {"height": 1.0, "time": "2026-09-02 03:40:44+00:00", "type": "slack"}
            ]
        }
        assert parse.normalize_tides(payload) == []

    def test_sorts_by_time(self):
        payload = {
            "data": [
                {"height": 0.6, "time": "2026-09-02 09:00:00+00:00", "type": "low"},
                {"height": 1.2, "time": "2026-09-02 03:00:00+00:00", "type": "high"},
            ]
        }
        assert [t["type"] for t in parse.normalize_tides(payload)] == ["high", "low"]

    @pytest.mark.parametrize("payload", [None, {}, {"data": None}])
    def test_empty_payloads_yield_nothing(self, payload):
        assert parse.normalize_tides(payload) == []


class TestNormalizeAstronomy:
    PAYLOAD = {
        "data": [
            {
                "time": "2026-09-02T00:00:00+00:00",
                "sunrise": "2026-09-02T06:56:32+00:00",
                "sunset": "2026-09-02T19:16:06+00:00",
                "moonrise": None,
                "moonFraction": 0.977,
                "moonPhase": {
                    "current": {"text": "Waxing gibbous", "value": 0.45},
                    "closest": {"text": "Full moon", "value": 0.5},
                },
            }
        ]
    }

    def test_extracts_sun_times(self):
        day = parse.normalize_astronomy(self.PAYLOAD)[0]
        assert day["sunrise"] == datetime(2026, 9, 2, 6, 56, 32, tzinfo=UTC)
        assert day["sunset"] == datetime(2026, 9, 2, 19, 16, 6, tzinfo=UTC)

    def test_uses_the_current_moon_phase_not_the_closest(self):
        day = parse.normalize_astronomy(self.PAYLOAD)[0]
        assert day["moonPhase"] == "Waxing gibbous"
        assert day["moonPhaseValue"] == 0.45

    def test_null_events_are_preserved_as_none(self):
        """Stormglass returns null when a moonrise does not occur that day."""
        day = parse.normalize_astronomy(self.PAYLOAD)[0]
        assert day["moonrise"] is None

    def test_moon_fraction_is_kept(self):
        assert parse.normalize_astronomy(self.PAYLOAD)[0]["moonFraction"] == 0.977


class TestNearestHour:
    HOURS = [
        {"time": NOON + timedelta(hours=offset), "waveHeight": offset}
        for offset in range(6)
    ]

    def test_picks_the_closest_entry(self):
        found = parse.nearest_hour(self.HOURS, NOON + timedelta(hours=2, minutes=20))
        assert found["waveHeight"] == 2

    def test_rounds_to_the_nearer_side(self):
        found = parse.nearest_hour(self.HOURS, NOON + timedelta(hours=2, minutes=40))
        assert found["waveHeight"] == 3

    def test_returns_none_when_nothing_is_close_enough(self):
        assert parse.nearest_hour(self.HOURS, NOON + timedelta(days=2)) is None

    def test_empty_forecast_returns_none(self):
        assert parse.nearest_hour([], NOON) is None


class TestCovers:
    HOURS = [{"time": NOON}, {"time": NOON + timedelta(hours=5)}]

    def test_inside_the_range(self):
        assert parse.covers(self.HOURS, NOON + timedelta(hours=2)) is True

    def test_on_the_boundaries(self):
        assert parse.covers(self.HOURS, NOON) is True
        assert parse.covers(self.HOURS, NOON + timedelta(hours=5)) is True

    def test_outside_the_range(self):
        assert parse.covers(self.HOURS, NOON - timedelta(hours=1)) is False
        assert parse.covers(self.HOURS, NOON + timedelta(hours=6)) is False

    def test_no_forecast_covers_nothing(self):
        assert parse.covers([], NOON) is False


class TestTideContext:
    TIDES = [
        {"time": NOON - timedelta(hours=3), "height": 1.2, "type": "high"},
        {"time": NOON + timedelta(hours=3), "height": 0.2, "type": "low"},
        {"time": NOON + timedelta(hours=9), "height": 1.3, "type": "high"},
    ]

    def test_falling_towards_a_low(self):
        context = parse.tide_context(self.TIDES, NOON)
        assert context["state"] == parse.TIDE_FALLING
        assert context["next"]["type"] == "low"

    def test_rising_towards_a_high(self):
        context = parse.tide_context(self.TIDES, NOON + timedelta(hours=4))
        assert context["state"] == parse.TIDE_RISING

    def test_reports_the_next_high_and_low_separately(self):
        context = parse.tide_context(self.TIDES, NOON)
        assert context["next_low"]["time"] == NOON + timedelta(hours=3)
        assert context["next_high"]["time"] == NOON + timedelta(hours=9)

    def test_reports_the_most_recent_past_extreme(self):
        context = parse.tide_context(self.TIDES, NOON)
        assert context["previous"]["time"] == NOON - timedelta(hours=3)

    def test_no_data_gives_an_unknown_state(self):
        context = parse.tide_context([], NOON)
        assert context["state"] is None
        assert context["next_high"] is None

    def test_exhausted_data_gives_an_unknown_state(self):
        context = parse.tide_context(self.TIDES, NOON + timedelta(days=5))
        assert context["state"] is None
        assert context["previous"]["type"] == "high"


class TestAstronomyForDay:
    DAYS = [
        {"time": NOON.replace(hour=0) + timedelta(days=offset), "sunrise": offset}
        for offset in range(3)
    ]

    def test_finds_the_matching_day(self):
        assert parse.astronomy_for_day(self.DAYS, NOON + timedelta(days=1))["sunrise"] == 1

    def test_matches_regardless_of_the_time_of_day(self):
        late = NOON.replace(hour=23, minute=59)
        assert parse.astronomy_for_day(self.DAYS, late)["sunrise"] == 0

    def test_falls_forward_when_the_exact_day_is_missing(self):
        earlier = NOON - timedelta(days=5)
        assert parse.astronomy_for_day(self.DAYS, earlier)["sunrise"] == 0

    def test_falls_back_to_the_last_known_day_when_data_runs_out(self):
        later = NOON + timedelta(days=10)
        assert parse.astronomy_for_day(self.DAYS, later)["sunrise"] == 2

    def test_no_data_returns_none(self):
        assert parse.astronomy_for_day([], NOON) is None


class TestHorizonEnd:
    def test_returns_the_last_timestamp(self):
        entries = [{"time": NOON}, {"time": NOON + timedelta(hours=4)}]
        assert parse.horizon_end(entries) == NOON + timedelta(hours=4)

    def test_empty_returns_none(self):
        assert parse.horizon_end([]) is None
