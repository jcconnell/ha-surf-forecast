"""Tests for the config and options flows."""

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.surf_forecast.coastline import OVERPASS_URL
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
    CONF_SEA_LOCATION,
    CONF_SHORE_DIRECTION,
    CONF_TIDE_DATUM,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
    ENDPOINT_WEATHER,
)

SPOT_LAT, SPOT_LON = 33.0, -117.0

USER_INPUT = {
    CONF_NAME: "Test Beach",
    CONF_API_KEY: "test-key",
    CONF_LOCATION: {CONF_LATITUDE: SPOT_LAT, CONF_LONGITUDE: SPOT_LON},
}

ENTRY_DATA = {
    CONF_NAME: "Test Beach",
    CONF_API_KEY: "test-key",
    CONF_LATITUDE: SPOT_LAT,
    CONF_LONGITUDE: SPOT_LON,
    CONF_SHORE_DIRECTION: 270.0,
}


def _suggested(schema, key):
    """Read back the value a form field is prefilled with."""
    for field in schema.schema:
        if field == key:
            return field.description["suggested_value"]
    raise AssertionError(f"{key} not in schema")


def _coastline_payload():
    """A straight coast drawn west to east, so the water lies to the south."""
    nodes, ids = [], []
    for index in range(21):
        node_id = index + 1
        nodes.append(
            {
                "type": "node",
                "id": node_id,
                "lat": SPOT_LAT,
                "lon": SPOT_LON - 0.01 + 0.001 * index,
            }
        )
        ids.append(node_id)
    return {"elements": [*nodes, {"type": "way", "id": 500, "nodes": ids}]}


def _mock_overpass(aioclient_mock, payload=None):
    aioclient_mock.post(OVERPASS_URL, json=payload or _coastline_payload())


def _mock_key_ok(aioclient_mock):
    aioclient_mock.get(ENDPOINT_WEATHER, json={"hours": [], "meta": {}})


async def _to_shore_menu(hass, aioclient_mock):
    """Run the first step and land on the shore-direction menu."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    return result


async def test_user_step_leads_to_the_shore_menu(hass, aioclient_mock):
    """The break's location is asked for first, then how it faces."""
    _mock_key_ok(aioclient_mock)
    _mock_overpass(aioclient_mock)

    result = await _to_shore_menu(hass, aioclient_mock)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "shore"
    assert set(result["menu_options"]) == {"shore_map", "shore_manual"}


async def test_map_step_starts_the_pin_on_the_break(hass, aioclient_mock):
    """The selector shows one marker, so it must start somewhere meaningful.

    Starting it on the break makes the gesture "drag this into the water",
    which needs no second marker to be understood.
    """
    _mock_key_ok(aioclient_mock)
    _mock_overpass(aioclient_mock)

    result = await _to_shore_menu(hass, aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "shore_map"}
    )
    assert result["step_id"] == "shore_map"

    suggested = _suggested(result["data_schema"], CONF_SEA_LOCATION)
    assert suggested[CONF_LATITUDE] == pytest.approx(SPOT_LAT)
    assert suggested[CONF_LONGITUDE] == pytest.approx(SPOT_LON)


async def test_map_path_derives_the_bearing_then_asks_to_confirm(hass, aioclient_mock):
    """Dragging the pin due south gives 180, shown for confirmation first."""
    _mock_key_ok(aioclient_mock)
    _mock_overpass(aioclient_mock)

    result = await _to_shore_menu(hass, aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "shore_map"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_SEA_LOCATION: {CONF_LATITUDE: SPOT_LAT - 0.01, CONF_LONGITUDE: SPOT_LON}},
    )

    # Nothing is saved yet: the derived bearing comes back for a look.
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "shore_manual"
    assert _suggested(result["data_schema"], CONF_SHORE_DIRECTION) == pytest.approx(
        180.0, abs=0.5
    )
    assert "1" in result["description_placeholders"]["estimate"]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SHORE_DIRECTION: 180}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SHORE_DIRECTION] == pytest.approx(180.0, abs=0.5)
    assert result["data"][CONF_LATITUDE] == SPOT_LAT


async def test_the_derived_bearing_can_be_overridden_on_confirmation(
    hass, aioclient_mock
):
    """The confirm step is a real field, not a read-only summary."""
    _mock_key_ok(aioclient_mock)
    _mock_overpass(aioclient_mock)

    result = await _to_shore_menu(hass, aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "shore_map"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_SEA_LOCATION: {CONF_LATITUDE: SPOT_LAT - 0.01, CONF_LONGITUDE: SPOT_LON}},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SHORE_DIRECTION: 195}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SHORE_DIRECTION] == 195.0


async def test_map_path_rejects_a_pin_on_top_of_the_break(hass, aioclient_mock):
    """Two coincident points have no meaningful bearing between them."""
    _mock_key_ok(aioclient_mock)
    _mock_overpass(aioclient_mock)

    result = await _to_shore_menu(hass, aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "shore_map"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_SEA_LOCATION: {CONF_LATITUDE: SPOT_LAT, CONF_LONGITUDE: SPOT_LON}},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_SEA_LOCATION: "sea_point_too_close"}


async def test_manual_path_takes_a_bearing(hass, aioclient_mock):
    _mock_key_ok(aioclient_mock)
    _mock_overpass(aioclient_mock)

    result = await _to_shore_menu(hass, aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "shore_manual"}
    )
    assert result["step_id"] == "shore_manual"
    # Prefilled from the coastline estimate: a south-facing coast.
    assert _suggested(result["data_schema"], CONF_SHORE_DIRECTION) == pytest.approx(
        180.0, abs=2
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SHORE_DIRECTION: 185}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SHORE_DIRECTION] == 185.0


async def test_setup_still_works_when_overpass_is_unavailable(hass, aioclient_mock):
    """The coastline estimate is a convenience, never a requirement."""
    _mock_key_ok(aioclient_mock)
    aioclient_mock.post(OVERPASS_URL, status=504)

    result = await _to_shore_menu(hass, aioclient_mock)
    assert result["type"] is FlowResultType.MENU

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "shore_manual"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SHORE_DIRECTION: 200}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SHORE_DIRECTION] == 200.0


async def test_rejected_key_shows_an_error_and_recovers(hass, aioclient_mock):
    """A bad key is reported inline, and the form can be resubmitted."""
    aioclient_mock.get(ENDPOINT_WEATHER, status=403)

    result = await _to_shore_menu(hass, aioclient_mock)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    aioclient_mock.clear_requests()
    _mock_key_ok(aioclient_mock)
    _mock_overpass(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.MENU


async def test_unreachable_api_shows_an_error(hass, aioclient_mock):
    """A server side failure is not treated as a bad key."""
    aioclient_mock.get(ENDPOINT_WEATHER, status=503)

    result = await _to_shore_menu(hass, aioclient_mock)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_an_exhausted_quota_still_accepts_the_key(hass, aioclient_mock):
    """Being out of requests proves the key works, so setup should continue."""
    aioclient_mock.get(ENDPOINT_WEATHER, status=402)
    _mock_overpass(aioclient_mock)

    result = await _to_shore_menu(hass, aioclient_mock)
    assert result["type"] is FlowResultType.MENU


async def test_the_same_spot_cannot_be_added_twice(hass, aioclient_mock):
    """The coordinates form the unique id."""
    MockConfigEntry(
        domain=DOMAIN, unique_id="33.0000,-117.0000", data={}
    ).add_to_hass(hass)

    result = await _to_shore_menu(hass, aioclient_mock)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def _setup_entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Beach",
        unique_id="33.0000,-117.0000",
        data=ENTRY_DATA,
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


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
    entry = await _setup_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_UPDATE_INTERVAL: 8,
            CONF_FORECAST_DAYS: 3,
            CONF_SHORE_DIRECTION: 185.0,
            CONF_IDEAL_MIN_HEIGHT: 1.5,
            CONF_IDEAL_MAX_HEIGHT: 3.0,
            CONF_GOOD_SURF_THRESHOLD: 6.0,
            CONF_TIDE_DATUM: "MLLW",
            CONF_ENABLE_TIDE: True,
            CONF_ENABLE_ASTRONOMY: False,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_UPDATE_INTERVAL] == 8
    assert entry.options[CONF_SHORE_DIRECTION] == 185.0
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
    entry = await _setup_entry(hass)

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
        data={**ENTRY_DATA, CONF_API_KEY: "stale-key"},
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    _mock_key_ok(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "fresh-key"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_KEY] == "fresh-key"
