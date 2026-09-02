"""Tests for the config and options flows."""

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.surf_forecast.const import (
    CONF_API_KEY,
    CONF_ENABLE_ASTRONOMY,
    CONF_ENABLE_TIDE,
    CONF_FORECAST_DAYS,
    CONF_GOOD_SURF_THRESHOLD,
    CONF_IDEAL_MAX_HEIGHT,
    CONF_IDEAL_MIN_HEIGHT,
    CONF_LATITUDE,
    CONF_LOCATION,
    CONF_LONGITUDE,
    CONF_SHORE_DIRECTION,
    CONF_TIDE_DATUM,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
    ENDPOINT_WEATHER,
)

USER_INPUT = {
    CONF_NAME: "Test Beach",
    CONF_API_KEY: "test-key",
    CONF_LOCATION: {CONF_LATITUDE: 33.0, CONF_LONGITUDE: -117.0},
    CONF_SHORE_DIRECTION: 270.0,
}


async def _start(hass):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_user_flow_creates_an_entry(hass, aioclient_mock):
    """A valid key and a location are all that is needed."""
    aioclient_mock.get(ENDPOINT_WEATHER, json={"hours": [], "meta": {}})

    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Beach"
    assert result["data"] == {
        CONF_NAME: "Test Beach",
        CONF_API_KEY: "test-key",
        CONF_LATITUDE: 33.0,
        CONF_LONGITUDE: -117.0,
        CONF_SHORE_DIRECTION: 270.0,
    }


async def test_rejected_key_shows_an_error_and_recovers(hass, aioclient_mock):
    """A bad key is reported inline, and the form can be resubmitted."""
    aioclient_mock.get(ENDPOINT_WEATHER, status=403)

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(ENDPOINT_WEATHER, json={"hours": [], "meta": {}})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_unreachable_api_shows_an_error(hass, aioclient_mock):
    """A server side failure is not treated as a bad key."""
    aioclient_mock.get(ENDPOINT_WEATHER, status=503)

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_an_exhausted_quota_still_accepts_the_key(hass, aioclient_mock):
    """Being out of requests proves the key works, so setup should continue."""
    aioclient_mock.get(ENDPOINT_WEATHER, status=402)

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_the_same_spot_cannot_be_added_twice(hass, aioclient_mock):
    """The coordinates form the unique id."""
    MockConfigEntry(
        domain=DOMAIN, unique_id="33.0000,-117.0000", data={}
    ).add_to_hass(hass)
    aioclient_mock.get(ENDPOINT_WEATHER, json={"hours": [], "meta": {}})

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow_saves(
    hass, aioclient_mock, weather_payload, tide_payload, astronomy_payload
):
    """Options round trip into the entry."""
    aioclient_mock.get(ENDPOINT_WEATHER, json=weather_payload)
    aioclient_mock.get(
        "https://api.stormglass.io/v2/tide/extremes/point", json=tide_payload
    )
    aioclient_mock.get(
        "https://api.stormglass.io/v2/astronomy/point", json=astronomy_payload
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Beach",
        unique_id="33.0000,-117.0000",
        data={
            CONF_NAME: "Test Beach",
            CONF_API_KEY: "test-key",
            CONF_LATITUDE: 33.0,
            CONF_LONGITUDE: -117.0,
            CONF_SHORE_DIRECTION: 270.0,
        },
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    options = {
        CONF_UPDATE_INTERVAL: 8,
        CONF_FORECAST_DAYS: 3,
        CONF_SHORE_DIRECTION: 180.0,
        CONF_IDEAL_MIN_HEIGHT: 1.5,
        CONF_IDEAL_MAX_HEIGHT: 3.0,
        CONF_GOOD_SURF_THRESHOLD: 6.0,
        CONF_TIDE_DATUM: "MLLW",
        CONF_ENABLE_TIDE: True,
        CONF_ENABLE_ASTRONOMY: False,
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], options
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_UPDATE_INTERVAL] == 8
    assert entry.options[CONF_TIDE_DATUM] == "MLLW"
    assert entry.options[CONF_ENABLE_ASTRONOMY] is False


async def test_options_flow_rejects_an_inverted_ideal_range(
    hass, aioclient_mock, weather_payload, tide_payload, astronomy_payload
):
    """The smallest good wave must be smaller than the largest."""
    aioclient_mock.get(ENDPOINT_WEATHER, json=weather_payload)
    aioclient_mock.get(
        "https://api.stormglass.io/v2/tide/extremes/point", json=tide_payload
    )
    aioclient_mock.get(
        "https://api.stormglass.io/v2/astronomy/point", json=astronomy_payload
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Beach",
        unique_id="33.0000,-117.0000",
        data={
            CONF_NAME: "Test Beach",
            CONF_API_KEY: "test-key",
            CONF_LATITUDE: 33.0,
            CONF_LONGITUDE: -117.0,
            CONF_SHORE_DIRECTION: 270.0,
        },
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_UPDATE_INTERVAL: 6,
            CONF_FORECAST_DAYS: 5,
            CONF_SHORE_DIRECTION: 270.0,
            CONF_IDEAL_MIN_HEIGHT: 3.0,
            CONF_IDEAL_MAX_HEIGHT: 1.0,
            CONF_GOOD_SURF_THRESHOLD: 5.0,
            CONF_TIDE_DATUM: "MSL",
            CONF_ENABLE_TIDE: True,
            CONF_ENABLE_ASTRONOMY: True,
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_IDEAL_MIN_HEIGHT: "ideal_range_invalid"}


async def test_reauth_replaces_the_stored_key(hass, aioclient_mock):
    """A new key is written back to the existing entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Beach",
        unique_id="33.0000,-117.0000",
        data={
            CONF_NAME: "Test Beach",
            CONF_API_KEY: "stale-key",
            CONF_LATITUDE: 33.0,
            CONF_LONGITUDE: -117.0,
            CONF_SHORE_DIRECTION: 270.0,
        },
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    aioclient_mock.get(ENDPOINT_WEATHER, json={"hours": [], "meta": {}})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "fresh-key"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_KEY] == "fresh-key"
