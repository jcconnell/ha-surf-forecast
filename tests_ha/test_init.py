"""End to end tests: HTTP layer, coordinator, and entities together."""

from datetime import timedelta

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
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
                "waveDirection": {"sg": direction},
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
    """The real Kewalos mistake: a south-facing break configured as west-facing.

    Waves cannot cross land to reach a break, so a swell persistently arriving
    from behind the beach proves the shore direction is wrong.
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
