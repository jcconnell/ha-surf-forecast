"""Config and options flow for the Surf Forecast integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    StormglassAuthError,
    StormglassClient,
    StormglassError,
    StormglassQuotaError,
)
from .const import (
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
    DEFAULT_FORECAST_DAYS,
    DEFAULT_GOOD_SURF_THRESHOLD,
    DEFAULT_IDEAL_MAX_HEIGHT,
    DEFAULT_IDEAL_MIN_HEIGHT,
    DEFAULT_SHORE_DIRECTION,
    DEFAULT_TIDE_DATUM,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    TIDE_DATUMS,
)

_LOGGER = logging.getLogger(__name__)


class SurfForecastConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup of a surf spot."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the API key, the spot's coordinates and its orientation."""
        errors: dict[str, str] = {}

        if user_input is not None:
            location = user_input[CONF_LOCATION]
            latitude = location[CONF_LATITUDE]
            longitude = location[CONF_LONGITUDE]

            await self.async_set_unique_id(f"{latitude:.4f},{longitude:.4f}")
            self._abort_if_unique_id_configured()

            error = await _async_validate_key(
                self.hass, user_input[CONF_API_KEY], latitude, longitude
            )
            if error:
                errors["base"] = error
            else:
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={
                        CONF_NAME: user_input[CONF_NAME],
                        CONF_API_KEY: user_input[CONF_API_KEY],
                        CONF_LATITUDE: latitude,
                        CONF_LONGITUDE: longitude,
                        CONF_SHORE_DIRECTION: user_input[CONF_SHORE_DIRECTION],
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                _user_schema(),
                user_input
                or {
                    CONF_LOCATION: {
                        CONF_LATITUDE: self.hass.config.latitude,
                        CONF_LONGITUDE: self.hass.config.longitude,
                    }
                },
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Start the reauthentication flow after a rejected API key."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect a replacement API key."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            error = await _async_validate_key(
                self.hass,
                user_input[CONF_API_KEY],
                entry.data[CONF_LATITUDE],
                entry.data[CONF_LONGITUDE],
            )
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_API_KEY: user_input[CONF_API_KEY]}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {vol.Required(CONF_API_KEY): selector.TextSelector()}
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow handler."""
        return SurfForecastOptionsFlow()


class SurfForecastOptionsFlow(OptionsFlow):
    """Tune scoring, refresh cadence and which endpoints are worth the quota."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and store the options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if user_input[CONF_IDEAL_MIN_HEIGHT] >= user_input[CONF_IDEAL_MAX_HEIGHT]:
                errors[CONF_IDEAL_MIN_HEIGHT] = "ideal_range_invalid"
            else:
                return self.async_create_entry(data=user_input)

        current = {
            CONF_UPDATE_INTERVAL: DEFAULT_UPDATE_INTERVAL,
            CONF_FORECAST_DAYS: DEFAULT_FORECAST_DAYS,
            CONF_SHORE_DIRECTION: self.config_entry.data.get(
                CONF_SHORE_DIRECTION, DEFAULT_SHORE_DIRECTION
            ),
            CONF_IDEAL_MIN_HEIGHT: DEFAULT_IDEAL_MIN_HEIGHT,
            CONF_IDEAL_MAX_HEIGHT: DEFAULT_IDEAL_MAX_HEIGHT,
            CONF_GOOD_SURF_THRESHOLD: DEFAULT_GOOD_SURF_THRESHOLD,
            CONF_TIDE_DATUM: DEFAULT_TIDE_DATUM,
            CONF_ENABLE_TIDE: True,
            CONF_ENABLE_ASTRONOMY: True,
        }
        current.update(self.config_entry.options)
        if user_input is not None:
            current.update(user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                _options_schema(), current
            ),
            errors=errors,
        )


def _user_schema() -> vol.Schema:
    """Schema for the initial setup step."""
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default="Surf"): selector.TextSelector(),
            vol.Required(CONF_API_KEY): selector.TextSelector(),
            vol.Required(CONF_LOCATION): selector.LocationSelector(),
            vol.Required(
                CONF_SHORE_DIRECTION, default=DEFAULT_SHORE_DIRECTION
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=359,
                    step=1,
                    unit_of_measurement="deg",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
        }
    )


def _options_schema() -> vol.Schema:
    """Schema for the options step."""
    return vol.Schema(
        {
            vol.Required(CONF_UPDATE_INTERVAL): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=24, step=1, unit_of_measurement="h",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(CONF_FORECAST_DAYS): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=10, step=1, unit_of_measurement="d",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(CONF_SHORE_DIRECTION): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=359, step=1, unit_of_measurement="deg",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(CONF_IDEAL_MIN_HEIGHT): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.3, max=10.0, step=0.1, unit_of_measurement="m",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(CONF_IDEAL_MAX_HEIGHT): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.4, max=12.0, step=0.1, unit_of_measurement="m",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(CONF_GOOD_SURF_THRESHOLD): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=10, step=0.5,
                    mode=selector.NumberSelectorMode.SLIDER,
                )
            ),
            vol.Required(CONF_TIDE_DATUM): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(TIDE_DATUMS),
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(CONF_ENABLE_TIDE): selector.BooleanSelector(),
            vol.Required(CONF_ENABLE_ASTRONOMY): selector.BooleanSelector(),
        }
    )


async def _async_validate_key(
    hass, api_key: str, latitude: float, longitude: float
) -> str | None:
    """Check the API key against Stormglass, returning an error key on failure.

    This spends one request, which is worth it to avoid a spot that silently
    never works, but is worth knowing about on the 10-per-day free plan.
    """
    client = StormglassClient(
        async_get_clientsession(hass), api_key, latitude, longitude
    )
    try:
        await client.async_validate()
    except StormglassAuthError:
        return "invalid_auth"
    except StormglassQuotaError:
        # The key is clearly valid, it is just out of requests for today.
        return None
    except StormglassError as err:
        _LOGGER.debug("Stormglass validation failed: %s", err)
        return "cannot_connect"
    return None
