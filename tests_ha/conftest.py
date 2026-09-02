"""Fixtures for the Home Assistant integration tests."""

from datetime import timedelta

import pytest
from homeassistant.util import dt as dt_util

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load the integration from custom_components."""
    return


@pytest.fixture
def now():
    """The current hour, which the canned payloads are built around."""
    return dt_util.utcnow().replace(minute=0, second=0, microsecond=0)


@pytest.fixture
def weather_payload(now):
    """A clean, head-high, offshore forecast for a west facing beach."""
    return {
        "hours": [
            {
                "time": (now + timedelta(hours=offset)).isoformat(),
                "waveHeight": {"sg": 1.8, "noaa": 1.7},
                "wavePeriod": {"sg": 13.0},
                "waveDirection": {"sg": 270.0},
                "swellHeight": {"sg": 1.6},
                "swellPeriod": {"sg": 14.0},
                "swellDirection": {"sg": 265.0},
                "windSpeed": {"sg": 2.0},
                "windDirection": {"sg": 90.0},
                "gust": {"sg": 3.5},
                "waterTemperature": {"sg": 16.5},
                "airTemperature": {"sg": 19.0},
            }
            for offset in range(0, 48)
        ],
        "meta": {"dailyQuota": 10, "requestCount": 1, "lat": 33.0, "lng": -117.0},
    }


@pytest.fixture
def tide_payload(now):
    """Ten days of alternating extremes, as a real 10 day request returns.

    The first high is three hours out and the first low six hours after it.
    """
    return {
        "data": [
            {
                "height": 1.2 if index % 2 == 0 else 0.1,
                "time": (now + timedelta(hours=3 + index * 6)).isoformat(),
                "type": "high" if index % 2 == 0 else "low",
            }
            for index in range(40)
        ],
        "meta": {
            "dailyQuota": 10,
            "requestCount": 2,
            "station": {
                "distance": 12,
                "lat": 33.1,
                "lng": -117.1,
                "name": "test harbour",
                "source": "noaa",
            },
        },
    }


@pytest.fixture
def astronomy_payload(now):
    """Sunrise and sunset bracketing the whole canned forecast."""
    midnight = now.replace(hour=0)
    return {
        "data": [
            {
                "time": (midnight + timedelta(days=day)).isoformat(),
                "sunrise": (midnight + timedelta(days=day, hours=6)).isoformat(),
                "sunset": (midnight + timedelta(days=day, hours=23)).isoformat(),
                "moonrise": None,
                "moonset": (midnight + timedelta(days=day, hours=5)).isoformat(),
                "moonFraction": 0.5,
                "moonPhase": {
                    "current": {"text": "First quarter", "value": 0.25},
                    "closest": {"text": "Full moon", "value": 0.5},
                },
            }
            for day in range(0, 5)
        ],
        "meta": {"dailyQuota": 10, "requestCount": 3},
    }
