"""Diagnostics support for the Surf Forecast integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import SurfConfigEntry
from .const import CONF_API_KEY, CONF_LATITUDE, CONF_LONGITUDE

TO_REDACT = {CONF_API_KEY, CONF_LATITUDE, CONF_LONGITUDE}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SurfConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "quota": {
            "daily_quota": data.daily_quota,
            "requests_remaining": data.requests_remaining,
            "exhausted": data.quota_blocked,
        },
        "cache": {
            "last_weather_fetch": (
                data.last_weather_fetch.isoformat()
                if data.last_weather_fetch
                else None
            ),
            "forecast_hours": len(data.hours),
            "tide_events": len(data.tides),
            "astronomy_days": len(data.astronomy),
        },
        "derived": {
            "current": {
                key: (value.isoformat() if hasattr(value, "isoformat") else value)
                for key, value in (data.current or {}).items()
            },
            "rating": data.rating,
            "conditions": data.conditions,
            "wind_relation": data.wind_relation,
            "tide_state": data.tide.get("state"),
        },
    }
