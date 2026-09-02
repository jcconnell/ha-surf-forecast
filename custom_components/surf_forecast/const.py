"""Constants for the Surf Forecast integration.

This module is intentionally free of Home Assistant imports so the pure
forecast logic can be unit tested without a Home Assistant install.
"""

DOMAIN = "surf_forecast"

# --- Stormglass API ---------------------------------------------------------
API_BASE = "https://api.stormglass.io/v2"
ENDPOINT_WEATHER = f"{API_BASE}/weather/point"
ENDPOINT_TIDE_EXTREMES = f"{API_BASE}/tide/extremes/point"
ENDPOINT_ASTRONOMY = f"{API_BASE}/astronomy/point"

# Every weather parameter this integration knows how to surface. All of them
# come back in a single request, so asking for the full set costs no more
# quota than asking for one.
WEATHER_PARAMS = (
    "waveHeight",
    "wavePeriod",
    "waveDirection",
    "swellHeight",
    "swellPeriod",
    "swellDirection",
    "secondarySwellHeight",
    "secondarySwellPeriod",
    "secondarySwellDirection",
    "windWaveHeight",
    "windWavePeriod",
    "windWaveDirection",
    "windSpeed",
    "windDirection",
    "gust",
    "waterTemperature",
    "airTemperature",
    "currentSpeed",
    "currentDirection",
    "seaLevel",
)

# Stormglass returns each parameter as {source: value}. "sg" is Stormglass's own
# blended best-source model, so prefer it and fall back through the rest.
PREFERRED_SOURCES = (
    "sg",
    "noaa",
    "meteo",
    "dwd",
    "icon",
    "meto",
    "fcoo",
    "fmi",
    "yr",
    "smhi",
)

# --- Configuration ----------------------------------------------------------
CONF_API_KEY = "api_key"
CONF_LOCATION = "location"
CONF_LATITUDE = "latitude"
CONF_LONGITUDE = "longitude"
CONF_SHORE_DIRECTION = "shore_direction"

CONF_UPDATE_INTERVAL = "update_interval_hours"
CONF_FORECAST_DAYS = "forecast_days"
CONF_IDEAL_MIN_HEIGHT = "ideal_min_height"
CONF_IDEAL_MAX_HEIGHT = "ideal_max_height"
CONF_GOOD_SURF_THRESHOLD = "good_surf_threshold"
CONF_TIDE_DATUM = "tide_datum"
CONF_ENABLE_TIDE = "enable_tide"
CONF_ENABLE_ASTRONOMY = "enable_astronomy"

# The free Stormglass plan allows 10 requests per day. A refresh costs at most
# three (weather + tide + astronomy), and tide/astronomy are cached far longer
# than the weather, so a 6 hour interval lands around 6 requests/day.
DEFAULT_UPDATE_INTERVAL = 6
DEFAULT_FORECAST_DAYS = 5
DEFAULT_IDEAL_MIN_HEIGHT = 1.0
DEFAULT_IDEAL_MAX_HEIGHT = 2.5
DEFAULT_GOOD_SURF_THRESHOLD = 5.0
DEFAULT_TIDE_DATUM = "MSL"
DEFAULT_SHORE_DIRECTION = 270.0

TIDE_DATUMS = ("LAT", "MLLW", "MLW", "MSL", "MHW", "MHHW", "HAT")

# Astronomical tide and sun/moon times are deterministic, so a single fetch
# stays valid for its whole horizon. Only refetch when it ages out.
TIDE_CACHE_HOURS = 12
ASTRONOMY_CACHE_HOURS = 12
TIDE_HORIZON_DAYS = 10
ASTRONOMY_HORIZON_DAYS = 5

# Leave this many requests unspent so a manual reload never hits a hard stop.
QUOTA_RESERVE = 1

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.cache"
