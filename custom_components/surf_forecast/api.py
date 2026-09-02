"""Thin async client for the Stormglass v2 API."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import aiohttp

from .const import (
    ENDPOINT_ASTRONOMY,
    ENDPOINT_TIDE_EXTREMES,
    ENDPOINT_WEATHER,
    WEATHER_PARAMS,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)


class StormglassError(Exception):
    """A Stormglass request failed in a way we cannot recover from here."""


class StormglassAuthError(StormglassError):
    """The API key is missing, malformed or rejected."""


class StormglassQuotaError(StormglassError):
    """The account's daily request allowance is spent."""


class StormglassClient:
    """Issue Stormglass requests and surface quota information.

    Every endpoint call costs exactly one request against the daily
    allowance, which is only 10 on the free plan, so callers are expected to
    be deliberate about how often they call.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_key: str,
        latitude: float,
        longitude: float,
    ) -> None:
        """Initialise the client for a single coordinate."""
        self._session = session
        self._api_key = api_key
        self._latitude = latitude
        self._longitude = longitude
        self.daily_quota: int | None = None
        self.request_count: int | None = None

    @property
    def requests_remaining(self) -> int | None:
        """Requests left today, or None while the quota is still unknown."""
        if self.daily_quota is None or self.request_count is None:
            return None
        return max(0, self.daily_quota - self.request_count)

    async def _async_request(
        self,
        url: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Perform one GET request and return the decoded payload."""
        query = {
            "lat": self._latitude,
            "lng": self._longitude,
            **params,
        }
        try:
            async with self._session.get(
                url,
                params=query,
                headers={"Authorization": self._api_key},
                timeout=REQUEST_TIMEOUT,
            ) as response:
                if response.status == 403:
                    raise StormglassAuthError(
                        "Stormglass rejected the API key (HTTP 403)"
                    )
                if response.status == 402:
                    raise StormglassQuotaError(
                        "Stormglass daily request limit reached (HTTP 402)"
                    )
                if response.status != 200:
                    body = await response.text()
                    raise StormglassError(
                        f"Stormglass returned HTTP {response.status}: {body[:200]}"
                    )
                payload = await response.json()
        except asyncio.TimeoutError as err:
            raise StormglassError(f"Timeout contacting Stormglass: {url}") from err
        except aiohttp.ClientError as err:
            raise StormglassError(f"Error contacting Stormglass: {err}") from err

        if not isinstance(payload, dict):
            raise StormglassError("Stormglass returned an unexpected payload")

        self._update_quota(payload.get("meta"))
        return payload

    def _update_quota(self, meta: Any) -> None:
        """Record the quota counters Stormglass returns with every response."""
        if not isinstance(meta, dict):
            return
        quota = meta.get("dailyQuota")
        count = meta.get("requestCount")
        if isinstance(quota, (int, float)):
            self.daily_quota = int(quota)
        if isinstance(count, (int, float)):
            self.request_count = int(count)

    async def async_get_weather(
        self,
        start: datetime,
        end: datetime,
        params: tuple[str, ...] = WEATHER_PARAMS,
    ) -> dict[str, Any]:
        """Fetch the hourly marine forecast. Costs one request."""
        return await self._async_request(
            ENDPOINT_WEATHER,
            {
                "params": ",".join(params),
                "start": int(start.timestamp()),
                "end": int(end.timestamp()),
            },
        )

    async def async_get_tide_extremes(
        self,
        start: datetime,
        end: datetime,
        datum: str,
    ) -> dict[str, Any]:
        """Fetch high and low tide events. Costs one request."""
        return await self._async_request(
            ENDPOINT_TIDE_EXTREMES,
            {
                "start": int(start.timestamp()),
                "end": int(end.timestamp()),
                "datum": datum,
            },
        )

    async def async_get_astronomy(
        self,
        start: datetime,
        end: datetime,
    ) -> dict[str, Any]:
        """Fetch sun and moon times. Costs one request."""
        return await self._async_request(
            ENDPOINT_ASTRONOMY,
            {
                "start": int(start.timestamp()),
                "end": int(end.timestamp()),
            },
        )

    async def async_validate(self) -> dict[str, Any]:
        """Cheapest possible call used to validate credentials at setup time.

        This still costs one request, which matters on the free plan.
        """
        return await self._async_request(
            ENDPOINT_WEATHER,
            {"params": "waveHeight"},
        )
