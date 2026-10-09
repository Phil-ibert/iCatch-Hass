"""iCatch DVR cameras through the iCatch DVR add-on (go2rtc)."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CannotConnect, Go2rtcApi, InvalidAuth
from .const import CONF_RTSP_PORT, DEFAULT_RTSP_PORT, DOMAIN

PLATFORMS = [Platform.CAMERA]



@dataclass
class ICatchRuntime:
    """Per-entry runtime data."""

    api: Go2rtcApi
    hub_device_id: str


type ICatchConfigEntry = ConfigEntry[ICatchRuntime]


def api_from_entry(hass: HomeAssistant, entry: ConfigEntry) -> Go2rtcApi:
    data = entry.data
    return Go2rtcApi(
        async_get_clientsession(hass),
        data[CONF_HOST],
        data[CONF_PORT],
        data.get(CONF_RTSP_PORT, DEFAULT_RTSP_PORT),
        data.get(CONF_USERNAME) or None,
        data.get(CONF_PASSWORD) or None,
    )


async def async_setup_entry(hass: HomeAssistant, entry: ICatchConfigEntry) -> bool:
    api = api_from_entry(hass, entry)
    try:
        await api.cameras()
    except InvalidAuth as err:
        raise ConfigEntryError("go2rtc refused the API username/password") from err
    except CannotConnect as err:
        raise ConfigEntryNotReady(
            f"iCatch DVR add-on not reachable at {api.base_url}: {err}"
        ) from err
    hub = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer="iCatch",
        model="DVR",
        name="DVR iCatch",
    )
    entry.runtime_data = ICatchRuntime(api, hub.id)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ICatchConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
