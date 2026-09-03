"""Overpass API client for coastline lookups.

Kept apart from :mod:`coastline` so the geometry stays free of network
dependencies, matching the ``parse``/``api`` split used for Stormglass.
"""

from __future__ import annotations

import asyncio
import logging

import aiohttp

from .coastline import (
    OVERPASS_TIMEOUT_S,
    OVERPASS_URL,
    SEARCH_RADII_M,
    ShoreEstimate,
    estimate_from_payload,
    overpass_query,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=OVERPASS_TIMEOUT_S + 5)


async def async_estimate_shore_direction(
    session: aiohttp.ClientSession,
    lat: float,
    lon: float,
) -> ShoreEstimate | None:
    """Best-effort shore-direction estimate. Never raises.

    This only prefills the setup form, so any failure — Overpass down, rate
    limited, an inland coordinate — yields None and the user sets the
    direction themselves.
    """
    for radius in SEARCH_RADII_M:
        try:
            async with session.post(
                OVERPASS_URL,
                data={"data": overpass_query(lat, lon, radius)},
                timeout=REQUEST_TIMEOUT,
            ) as response:
                if response.status != 200:
                    _LOGGER.debug(
                        "Overpass returned HTTP %s at radius %s m",
                        response.status,
                        radius,
                    )
                    continue
                payload = await response.json(content_type=None)
        except (asyncio.TimeoutError, aiohttp.ClientError, ValueError) as err:
            _LOGGER.debug("Overpass lookup failed at radius %s m: %s", radius, err)
            continue

        estimate = estimate_from_payload(payload, lat, lon, radius)
        if estimate is not None:
            _LOGGER.debug("Shore direction estimate: %s", estimate)
            return estimate

    return None
