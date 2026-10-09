"""Constants for the iCatch DVR integration."""

DOMAIN = "icatch_dvr"

CONF_RTSP_PORT = "rtsp_port"
CONF_CAMERAS = "cameras"

DEFAULT_API_PORT = 1986
DEFAULT_RTSP_PORT = 8586

ADDON_SLUG = "icatch_dvr"
REPOSITORY_URL = "https://github.com/Phil-ibert/iCatch-Hass"

QUALITY_HD = "hd"
QUALITY_SD = "sd"

# A snapshot younger than this is reused instead of asking go2rtc again.
SNAPSHOT_CACHE_SECONDS = 5
