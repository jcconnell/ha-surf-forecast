"""End to end tests: HTTP layer, coordinator, and entities together."""

from datetime import datetime, timedelta

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.surf_forecast.const import (
    CONF_API_KEY,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_SHORE_DIRECTION,
    DOMAIN,
    ENDPOINT_ASTRONOMY,
    ENDPOINT_TIDE_EXTREMES,
    ENDPOINT_WEATHER,
)
from custom_components.surf_forecast.sensor import SurfForecastSensor

ENTRY_DATA = {
    CONF_NAME: "Test Beach",
    CONF_API_KEY: "test-key",
    CONF_LATITUDE: 33.0,
    CONF_LONGITUDE: -117.0,
    # A west facing beach, so a wind from 90 degrees is dead offshore.
    CONF_SHORE_DIRECTION: 270.0,
}


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test Beach",
        data=ENTRY_DATA,
        unique_id="33.0000,-117.0000",
    )


def _mock_all(aioclient_mock, weather_payload, tide_payload, astronomy_payload):
    aioclient_mock.get(ENDPOINT_WEATHER, json=weather_payload)
    aioclient_mock.get(ENDPOINT_TIDE_EXTREMES, json=tide_payload)
    aioclient_mock.get(ENDPOINT_ASTRONOMY, json=astronomy_payload)


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = _entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_succeeds_and_creates_entities(
    hass, aioclient_mock, weather_payload, tide_payload, astronomy_payload
):
    """The integration loads and registers its entities."""
    _mock_all(aioclient_mock, weather_payload, tide_payload, astronomy_payload)
    entry = await _setup(hass)

    assert entry.state is ConfigEntryState.LOADED
    # One request to each of the three endpoints, and no more.
    assert len(aioclient_mock.mock_calls) == 3

    states = [
        state
        for state in hass.states.async_all()
        if state.entity_id.startswith(("sensor.test_beach", "binary_sensor.test_beach"))
    ]
    assert len(states) >= 20


async def test_authorization_header_is_sent(
    hass, aioclient_mock, weather_payload, tide_payload, astronomy_payload
):
    """Stormglass takes the key in a bare Authorization header."""
    _mock_all(aioclient_mock, weather_payload, tide_payload, astronomy_payload)
    await _setup(hass)

    _method, _url, _data, headers = aioclient_mock.mock_calls[0]
    assert headers["Authorization"] == "test-key"


async def test_rating_reflects_clean_offshore_conditions(
    hass, aioclient_mock, weather_payload, tide_payload, astronomy_payload
):
    """A 1.8 m, 14 second swell with a light offshore should rate near perfect."""
    _mock_all(aioclient_mock, weather_payload, tide_payload, astronomy_payload)
    await _setup(hass)

    rating = hass.states.get("sensor.test_beach_surf_rating")
    assert rating is not None
    assert float(rating.state) >= 9.0
    assert rating.attributes["conditions"] == "Epic"
    assert rating.attributes["height_score"] == 1.0

    assert hass.states.get("sensor.test_beach_surf_conditions").state == "Epic"
    assert (
        hass.states.get(
            "sensor.test_beach_wind_direction_relative_to_shore"
        ).state
        == "Offshore"
    )
    assert hass.states.get("binary_sensor.test_beach_good_surf").state == "on"
    assert hass.states.get("binary_sensor.test_beach_clean_wind").state == "on"


async def test_marine_values_are_unwrapped_from_source_maps(
    hass, aioclient_mock, weather_payload, tide_payload, astronomy_payload
):
    """The 'sg' source is preferred over the others."""
    _mock_all(aioclient_mock, weather_payload, tide_payload, astronomy_payload)
    await _setup(hass)

    # waveHeight had sg=1.8 and noaa=1.7.
    assert float(hass.states.get("sensor.test_beach_wave_height").state) == 1.8
    assert float(hass.states.get("sensor.test_beach_swell_period").state) == 14.0
    assert float(hass.states.get("sensor.test_beach_water_temperature").state) == 16.5

    wind_direction = hass.states.get("sensor.test_beach_wind_direction")
    assert float(wind_direction.state) == 90.0
    assert wind_direction.attributes["compass"] == "E"


async def test_tide_sensors(
    hass, aioclient_mock, weather_payload, tide_payload, astronomy_payload
):
    """Tide state and the next extremes come from the extremes endpoint."""
    _mock_all(aioclient_mock, weather_payload, tide_payload, astronomy_payload)
    await _setup(hass)

    tide = hass.states.get("sensor.test_beach_tide")
    assert tide.state == "Rising"
    assert tide.attributes["station"] == "test harbour"

    assert hass.states.get("sensor.test_beach_next_high_tide").state != "unknown"
    assert (
        hass.states.get("sensor.test_beach_next_high_tide").attributes["height_m"] == 1.2
    )
    assert (
        hass.states.get("sensor.test_beach_next_low_tide").attributes["height_m"] == 0.1
    )


async def test_quota_is_reported_from_response_meta(
    hass, aioclient_mock, weather_payload, tide_payload, astronomy_payload
):
    """The remaining allowance is read off the meta object, not guessed."""
    _mock_all(aioclient_mock, weather_payload, tide_payload, astronomy_payload)
    await _setup(hass)

    remaining = hass.states.get("sensor.test_beach_api_requests_remaining")
    # The astronomy payload was fetched last, reporting requestCount 3 of 10.
    assert int(remaining.state) == 7
    assert remaining.attributes["daily_quota"] == 10
    assert hass.states.get("binary_sensor.test_beach_api_quota_exhausted").state == "off"


async def test_a_rejected_key_starts_a_reauth_flow(
    hass, aioclient_mock, weather_payload
):
    """HTTP 403 means the key is wrong, so ask for a new one."""
    aioclient_mock.get(ENDPOINT_WEATHER, status=403)
    entry = await _setup(hass)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow for flow in flows if flow["context"]["source"] == "reauth"]


async def test_exhausted_quota_with_no_cache_fails_setup(hass, aioclient_mock):
    """HTTP 402 with nothing cached leaves nothing to show."""
    aioclient_mock.get(ENDPOINT_WEATHER, status=402)
    aioclient_mock.get(ENDPOINT_TIDE_EXTREMES, status=402)
    aioclient_mock.get(ENDPOINT_ASTRONOMY, status=402)
    entry = await _setup(hass)

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_cached_payloads_survive_a_restart_without_new_requests(
    hass, aioclient_mock, weather_payload, tide_payload, astronomy_payload
):
    """A reload must not spend quota when the cache is still valid."""
    _mock_all(aioclient_mock, weather_payload, tide_payload, astronomy_payload)
    entry = await _setup(hass)
    assert len(aioclient_mock.mock_calls) == 3

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    # Still three: the reload was served entirely from the persisted cache.
    assert len(aioclient_mock.mock_calls) == 3
    assert float(hass.states.get("sensor.test_beach_wave_height").state) == 1.8


async def test_unload(
    hass, aioclient_mock, weather_payload, tide_payload, astronomy_payload
):
    """The entry unloads cleanly."""
    _mock_all(aioclient_mock, weather_payload, tide_payload, astronomy_payload)
    entry = await _setup(hass)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_quota_exhaustion_keeps_serving_the_cached_forecast(
    hass, aioclient_mock, freezer, weather_payload, tide_payload, astronomy_payload
):
    """Running out of requests must not blank the entities.

    The forecast on hand is still valid for hours, so the integration should
    keep publishing it rather than going unavailable.
    """
    _mock_all(aioclient_mock, weather_payload, tide_payload, astronomy_payload)
    entry = await _setup(hass)
    assert float(hass.states.get("sensor.test_beach_wave_height").state) == 1.8

    # Every endpoint now refuses: the daily allowance is spent.
    aioclient_mock.clear_requests()
    aioclient_mock.get(ENDPOINT_WEATHER, status=402)
    aioclient_mock.get(ENDPOINT_TIDE_EXTREMES, status=402)
    aioclient_mock.get(ENDPOINT_ASTRONOMY, status=402)

    freezer.tick(timedelta(hours=7))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    # The cached forecast still covers now, so the values survive.
    assert float(hass.states.get("sensor.test_beach_wave_height").state) == 1.8
    assert hass.states.get("binary_sensor.test_beach_api_quota_exhausted").state == "on"


async def test_a_stale_cache_with_no_quota_goes_unavailable(
    hass, aioclient_mock, freezer, weather_payload, tide_payload, astronomy_payload
):
    """Once the cached forecast no longer covers now, stop pretending."""
    _mock_all(aioclient_mock, weather_payload, tide_payload, astronomy_payload)
    await _setup(hass)

    aioclient_mock.clear_requests()
    aioclient_mock.get(ENDPOINT_WEATHER, status=402)
    aioclient_mock.get(ENDPOINT_TIDE_EXTREMES, status=402)
    aioclient_mock.get(ENDPOINT_ASTRONOMY, status=402)

    # The canned forecast is 48 hours long; step well past the end of it.
    freezer.tick(timedelta(hours=72))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.test_beach_wave_height").state == "unavailable"


async def test_current_conditions_advance_hourly_without_new_requests(
    hass, aioclient_mock, freezer, now, tide_payload, astronomy_payload
):
    """The forecast is fetched rarely but must still track the current hour.

    Refreshing over the network every hour would blow the daily allowance, so
    the coordinator re-derives "now" from the cached forecast instead.
    """
    rising_swell = {
        "hours": [
            {
                "time": (now + timedelta(hours=offset)).isoformat(),
                # A distinct wave height for each hour, so the sensor moving
                # proves it re-read the cache rather than sat still.
                "waveHeight": {"sg": 1.0 + offset},
                "swellPeriod": {"sg": 12.0},
                "windSpeed": {"sg": 2.0},
                "windDirection": {"sg": 90.0},
            }
            for offset in range(48)
        ],
        "meta": {"dailyQuota": 10, "requestCount": 1},
    }
    aioclient_mock.get(ENDPOINT_WEATHER, json=rising_swell)
    aioclient_mock.get(ENDPOINT_TIDE_EXTREMES, json=tide_payload)
    aioclient_mock.get(ENDPOINT_ASTRONOMY, json=astronomy_payload)

    # Pin the clock to the top of the hour. Otherwise which forecast hour is
    # "nearest" depends on what minute the suite happens to run at: at 11:34
    # the nearest hour to now is the 12:00 entry, not the 11:00 one.
    freezer.move_to(now)

    await _setup(hass)
    assert float(hass.states.get("sensor.test_beach_wave_height").state) == 1.0
    assert len(aioclient_mock.mock_calls) == 3

    # Move to the next hour and let the recompute timer fire.
    freezer.move_to(now + timedelta(hours=1, seconds=10))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert float(hass.states.get("sensor.test_beach_wave_height").state) == 2.0
    # Crucially, that cost nothing.
    assert len(aioclient_mock.mock_calls) == 3


def _payload_with_wave_direction(now, direction):
    """A forecast whose swell arrives consistently from one bearing."""
    return {
        "hours": [
            {
                "time": (now + timedelta(hours=offset)).isoformat(),
                "waveHeight": {"sg": 1.5},
                "swellHeight": {"sg": 1.5},
                "swellDirection": {"sg": direction},
                "swellPeriod": {"sg": 12.0},
                "windSpeed": {"sg": 3.0},
                "windDirection": {"sg": 90.0},
            }
            for offset in range(48)
        ],
        "meta": {"dailyQuota": 10, "requestCount": 1},
    }


async def test_swell_arriving_from_inland_raises_a_repair_issue(
    hass, aioclient_mock, freezer, now, tide_payload, astronomy_payload
):
    """Waves cannot cross land to reach a break, so a swell persistently
    arriving from behind the beach proves the shore direction is wrong.
    """
    # Shore says 270 (west), but the swell arrives from 116 (ESE) all week.
    aioclient_mock.get(ENDPOINT_WEATHER, json=_payload_with_wave_direction(now, 116.0))
    aioclient_mock.get(ENDPOINT_TIDE_EXTREMES, json=tide_payload)
    aioclient_mock.get(ENDPOINT_ASTRONOMY, json=astronomy_payload)

    entry = await _setup(hass)

    registry = ir.async_get(hass)
    issue = registry.async_get_issue(
        DOMAIN, f"shore_direction_suspect_{entry.entry_id}"
    )
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_placeholders["configured"] == "270"
    assert issue.translation_placeholders["wave_from"] == "116"

    sensor = hass.states.get("sensor.test_beach_wind_direction_relative_to_shore")
    assert sensor.attributes["shore_direction_suspect"] is True
    assert sensor.attributes["mean_wave_from"] == pytest.approx(116.0, abs=0.5)


async def test_a_plausible_shore_direction_raises_no_issue(
    hass, aioclient_mock, freezer, now, tide_payload, astronomy_payload
):
    """Swell from seaward is normal and must stay silent."""
    # Shore 270 (west), swell arriving from 260 — straight onshore, fine.
    aioclient_mock.get(ENDPOINT_WEATHER, json=_payload_with_wave_direction(now, 260.0))
    aioclient_mock.get(ENDPOINT_TIDE_EXTREMES, json=tide_payload)
    aioclient_mock.get(ENDPOINT_ASTRONOMY, json=astronomy_payload)

    entry = await _setup(hass)

    registry = ir.async_get(hass)
    assert (
        registry.async_get_issue(DOMAIN, f"shore_direction_suspect_{entry.entry_id}")
        is None
    )
    sensor = hass.states.get("sensor.test_beach_wind_direction_relative_to_shore")
    assert sensor.attributes["shore_direction_suspect"] is False


async def test_a_grazing_swell_angle_does_not_raise_a_false_alarm(
    hass, aioclient_mock, freezer, now, tide_payload, astronomy_payload
):
    """Refraction means swell can arrive slightly past 90 degrees legitimately."""
    # 95 degrees off the shore normal — past side-on, but not from inland.
    aioclient_mock.get(ENDPOINT_WEATHER, json=_payload_with_wave_direction(now, 175.0))
    aioclient_mock.get(ENDPOINT_TIDE_EXTREMES, json=tide_payload)
    aioclient_mock.get(ENDPOINT_ASTRONOMY, json=astronomy_payload)

    entry = await _setup(hass)

    registry = ir.async_get(hass)
    assert (
        registry.async_get_issue(DOMAIN, f"shore_direction_suspect_{entry.entry_id}")
        is None
    )


async def test_trade_wind_chop_does_not_condemn_a_south_facing_break(
    hass, aioclient_mock, freezer, now, tide_payload, astronomy_payload
):
    """The real Kewalos case: groundswell from the south, trade chop from ENE.

    Stormglass's combined waveDirection follows the bigger wind sea, so it
    points at land. The swell itself arrives from seaward, and the rating must
    be sized on that, not on chop blowing out to sea behind the break.
    """
    hours = [
        {
            "time": (now + timedelta(hours=offset)).isoformat(),
            "waveHeight": {"sg": 1.45},
            "waveDirection": {"sg": 109.0},
            "swellHeight": {"sg": 0.79},
            "swellPeriod": {"sg": 12.7},
            "swellDirection": {"sg": 191.0},
            "secondarySwellHeight": {"sg": 0.41},
            "secondarySwellPeriod": {"sg": 5.7},
            "secondarySwellDirection": {"sg": 147.0},
            "windWaveHeight": {"sg": 1.11},
            "windWavePeriod": {"sg": 3.9},
            "windWaveDirection": {"sg": 64.0},
            "windSpeed": {"sg": 9.8},
            "windDirection": {"sg": 68.0},
        }
        for offset in range(48)
    ]
    aioclient_mock.get(
        ENDPOINT_WEATHER,
        json={"hours": hours, "meta": {"dailyQuota": 10, "requestCount": 1}},
    )
    aioclient_mock.get(ENDPOINT_TIDE_EXTREMES, json=tide_payload)
    aioclient_mock.get(ENDPOINT_ASTRONOMY, json=astronomy_payload)
    freezer.move_to(now)

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Beach",
        data={**ENTRY_DATA, CONF_SHORE_DIRECTION: 217.0},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registry = ir.async_get(hass)
    assert (
        registry.async_get_issue(DOMAIN, f"shore_direction_suspect_{entry.entry_id}")
        is None
    )
    rating = hass.states.get("sensor.test_beach_surf_rating")
    # Swell and secondary swell only: sqrt(0.79^2 + 0.41^2).
    assert rating.attributes["surf_height_m"] == pytest.approx(0.89, abs=0.01)


async def test_hourly_recompute_does_not_postpone_the_forecast_fetch(
    hass, aioclient_mock, freezer, now, weather_payload, tide_payload, astronomy_payload
):
    """Recomputing every hour must not keep resetting the network refresh timer.

    It did: async_set_updated_data restarts the coordinator's interval, so an
    hourly recompute meant the six hourly fetch never came due and a spot
    served one forecast for days.
    """
    _mock_all(aioclient_mock, weather_payload, tide_payload, astronomy_payload)
    freezer.move_to(now)
    await _setup(hass)

    def weather_calls():
        return sum(
            1
            for call in aioclient_mock.mock_calls
            if str(call[1]).startswith(ENDPOINT_WEATHER)
        )

    assert weather_calls() == 1

    for hour in range(1, 8):
        freezer.move_to(now + timedelta(hours=hour, seconds=10))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    assert weather_calls() == 2


# Honolulu has no daylight saving, so local midnight is always 10:00 UTC.
HONOLULU = "Pacific/Honolulu"


def _day_payload(midnight, now):
    """Three days of hours from local midnight, with every field the view uses.

    The hours already gone today are epic and the rest are small, so a best
    window that looked backwards would be caught out.
    """
    hours = []
    for offset in range(72):
        moment = midnight + timedelta(hours=offset)
        past = moment < now
        hour = {
            "time": moment.isoformat(),
            "waveHeight": {"sg": 1.8 if past else 0.5},
            "wavePeriod": {"sg": 11.0},
            "waveDirection": {"sg": 200.0},
            "swellHeight": {"sg": 1.8 if past else 0.5},
            "swellPeriod": {"sg": 14.0},
            "swellDirection": {"sg": 190.0},
            "windSpeed": {"sg": 2.0},
            "windDirection": {"sg": 45.0},
            "gust": {"sg": 10.2},
            "seaLevel": {"meto": 0.62, "sg": 0.62},
        }
        if offset == 5:
            # A missing value must come through as None, not drop the key.
            del hour["gust"]
        hours.append(hour)
    return {"hours": hours, "meta": {"dailyQuota": 10, "requestCount": 1}}


def _tide_extremes(midnight):
    """Extremes every six hours: a high at 3 pm and a low at 9 pm the day
    before, then a high at 3 am, a low at 9 am, and so on.
    """
    return {
        "data": [
            {
                "time": (midnight + timedelta(hours=-9 + index * 6)).isoformat(),
                "height": 0.7 if index % 2 == 0 else 0.1,
                "type": "high" if index % 2 == 0 else "low",
            }
            for index in range(22)
        ],
        "meta": {"dailyQuota": 10, "requestCount": 2},
    }


def _honolulu_daylight(midnight):
    """Sunrise 6:30 am and sunset 6 pm local, keyed by UTC day as Stormglass does."""
    return {
        "data": [
            {
                "time": datetime(2026, 10, 17, tzinfo=dt_util.UTC).isoformat(),
                "sunrise": (midnight + timedelta(hours=6, minutes=30)).isoformat(),
                "sunset": (midnight + timedelta(hours=18)).isoformat(),
            }
        ],
        "meta": {"dailyQuota": 10, "requestCount": 3},
    }


async def _setup_honolulu_afternoon(
    hass, aioclient_mock, freezer, at=timedelta(hours=13, minutes=30)
):
    """A south-facing break at 1:30 pm local, with the cache from midnight."""
    await hass.config.async_set_time_zone(HONOLULU)
    midnight = datetime(2026, 10, 17, tzinfo=dt_util.get_time_zone(HONOLULU))
    now = midnight + at
    freezer.move_to(now)

    aioclient_mock.get(ENDPOINT_WEATHER, json=_day_payload(midnight, now))
    aioclient_mock.get(ENDPOINT_TIDE_EXTREMES, json=_tide_extremes(midnight))
    aioclient_mock.get(ENDPOINT_ASTRONOMY, json=_honolulu_daylight(midnight))

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Beach",
        data={**ENTRY_DATA, CONF_SHORE_DIRECTION: 180.0},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return midnight, now


async def test_weather_is_fetched_from_local_midnight(
    hass, aioclient_mock, freezer
):
    """Starting at midnight puts all of today in the same single request."""
    midnight, _now = await _setup_honolulu_afternoon(
        hass, aioclient_mock, freezer
    )

    weather_calls = [
        call for call in aioclient_mock.mock_calls
        if str(call[1]).startswith(ENDPOINT_WEATHER)
    ]
    assert len(weather_calls) == 1
    assert int(weather_calls[0][1].query["start"]) == int(midnight.timestamp())


async def test_forecast_covers_all_of_today_with_the_detail_a_view_needs(
    hass, aioclient_mock, freezer
):
    """Hours run from local midnight to a day ahead, each with every field."""
    midnight, now = await _setup_honolulu_afternoon(
        hass, aioclient_mock, freezer
    )

    forecast = hass.states.get("sensor.test_beach_surf_rating").attributes["forecast"]
    times = [dt_util.parse_datetime(hour["time"]) for hour in forecast]
    assert times[0] == midnight
    assert times[-1] < now + timedelta(hours=24)
    # Midnight through 1 pm tomorrow: 14 hours today plus 24.
    assert len(forecast) == 38

    expected = {
        "time", "rating", "conditions", "wave_height", "surf_height",
        "swell_period", "wind_speed", "wind_relation", "wave_period",
        "wave_direction", "swell_height", "swell_direction", "wind_direction",
        "gust", "sea_level",
    }
    assert all(set(hour) == expected for hour in forecast)

    hour = forecast[0]
    assert hour["wave_period"] == 11.0
    assert hour["wave_direction"] == 200.0
    assert hour["swell_height"] == 1.8
    assert hour["swell_direction"] == 190.0
    assert hour["wind_direction"] == 45.0
    # Units are untouched: m/s and metres above mean sea level.
    assert hour["gust"] == 10.2
    assert hour["sea_level"] == 0.62
    assert forecast[5]["gust"] is None


async def test_hours_already_gone_do_not_drive_current_conditions(
    hass, aioclient_mock, freezer
):
    """The morning was epic, but it is over; now and the best window move on."""
    _midnight, now = await _setup_honolulu_afternoon(
        hass, aioclient_mock, freezer
    )

    # 1:30 pm is equidistant from 1 pm (epic) and 2 pm (small); the freezer
    # pins it, so nearest_hour keeps the earlier one, as it always has.
    assert float(hass.states.get("sensor.test_beach_wave_height").state) == 1.8

    best = hass.states.get("sensor.test_beach_best_window_today")
    assert dt_util.parse_datetime(best.state) >= now
    assert best.attributes["wave_height_m"] == 0.5


async def test_tide_extremes_are_fetched_from_noon_the_day_before(
    hass, aioclient_mock, freezer
):
    """A morning fetch must still cover the small hours, at the same span."""
    midnight, _now = await _setup_honolulu_afternoon(
        hass, aioclient_mock, freezer, at=timedelta(hours=8, minutes=27)
    )

    tide_calls = [
        call for call in aioclient_mock.mock_calls
        if str(call[1]).startswith(ENDPOINT_TIDE_EXTREMES)
    ]
    assert len(tide_calls) == 1
    query = tide_calls[0][1].query
    start = midnight - timedelta(hours=12)
    assert int(query["start"]) == int(start.timestamp())
    assert int(query["end"]) == int((start + timedelta(days=10)).timestamp())


async def test_tide_sensor_lists_extremes_from_midnight_to_two_days_out(
    hass, aioclient_mock, freezer
):
    """The list opens on the last extreme before midnight, so a view can draw
    a curve from 12 am, then keeps today's earlier extremes.
    """
    midnight, now = await _setup_honolulu_afternoon(
        hass, aioclient_mock, freezer
    )

    tide = hass.states.get("sensor.test_beach_tide")
    tides = tide.attributes["tides"]
    times = [dt_util.parse_datetime(extreme["time"]) for extreme in tides]

    # The 9 pm low leads; the 3 pm high before it is not needed.
    assert [moment for moment in times if moment < midnight] == [
        midnight - timedelta(hours=3)
    ]
    assert tides[0] == {
        "time": (midnight - timedelta(hours=3)).astimezone(dt_util.UTC).isoformat(),
        "type": "low",
        "height": 0.1,
    }
    assert times[1] == midnight + timedelta(hours=3)
    assert times[-1] <= now + timedelta(hours=48)
    assert times[-1] + timedelta(hours=6) > now + timedelta(hours=48)

    # Past extremes in the payload do not leak into "next": after the 9 am
    # low comes the 3 pm high.
    assert tide.state == "Rising"

    next_high = hass.states.get("sensor.test_beach_next_high_tide")
    upcoming_highs = [
        extreme for extreme, moment in zip(tides, times)
        if moment > now and extreme["type"] == "high"
    ]
    assert dt_util.parse_datetime(next_high.state) == dt_util.parse_datetime(
        upcoming_highs[0]["time"]
    )
    assert next_high.attributes["height_m"] == upcoming_highs[0]["height"]


def test_large_attributes_are_kept_out_of_the_recorder():
    """48 rich hours would blow past the recorder's attribute size limit."""
    assert {"forecast", "tides"} <= SurfForecastSensor._unrecorded_attributes
