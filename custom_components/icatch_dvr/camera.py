"""Camera entities: one HD and one low-definition preview per DVR input."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo

from .api import CannotConnect, Go2rtcApi, InvalidAuth
from .const import CONF_CAMERAS, DOMAIN, QUALITY_HD, QUALITY_SD, SNAPSHOT_CACHE_SECONDS

if TYPE_CHECKING:
    from . import ICatchConfigEntry, ICatchRuntime

_LOGGER = logging.getLogger(__name__)
_DEVICE_INFO_KEYS = DeviceInfo.__required_keys__ | DeviceInfo.__optional_keys__


async def async_setup_entry(hass: HomeAssistant, entry: ICatchConfigEntry, async_add_entities) -> None:
    runtime = entry.runtime_data
    cameras = sorted(int(c) for c in entry.options.get(CONF_CAMERAS, []))
    entities = []
    for channel in cameras:
        entities.append(ICatchCamera(hass, entry, runtime, channel, QUALITY_HD))
        entities.append(ICatchCamera(hass, entry, runtime, channel, QUALITY_SD))
    _remove_deselected(hass, entry, {e.unique_id for e in entities}, cameras)
    async_add_entities(entities)


def _remove_deselected(hass: HomeAssistant, entry: ICatchConfigEntry, keep: set[str], cameras: list[int]) -> None:
    """Drop entities and devices of cameras unticked in the options."""
    ent_reg = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if ent.unique_id not in keep:
            ent_reg.async_remove(ent.entity_id)
    dev_reg = dr.async_get(hass)
    wanted = {(DOMAIN, f"{entry.entry_id}_cam{c}") for c in cameras} | {(DOMAIN, entry.entry_id)}
    for dev in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        if not dev.identifiers & wanted:
            dev_reg.async_remove_device(dev.id)


class ICatchCamera(Camera):
    """A DVR input, either the HD main stream or the low-definition preview."""

    _attr_has_entity_name = True
    _attr_supported_features = CameraEntityFeature.STREAM
    _attr_brand = "iCatch"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ICatchConfigEntry,
        runtime: ICatchRuntime,
        channel: int,
        quality: str,
    ) -> None:
        super().__init__()
        self._api: Go2rtcApi = runtime.api
        self._channel = channel
        self._quality = quality
        self._stream = f"cam{channel}_{quality}"
        # Snapshots always come from the light preview stream.
        self._snapshot_stream = f"cam{channel}_{QUALITY_SD}"
        self._last_image: bytes | None = None
        self._last_image_at = 0.0
        self._attr_unique_id = f"{entry.entry_id}_cam{channel}_{quality}"
        if quality == QUALITY_SD:
            self._attr_translation_key = "preview"
        else:
            self._attr_name = None  # the device name: "Caméra DVR 1"
        french = (hass.config.language or "").startswith("fr")
        info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_cam{channel}")},
            name=f"Caméra DVR {channel}" if french else f"DVR camera {channel}",
            manufacturer="iCatch",
            model=f"Entrée {channel}" if french else f"Input {channel}",
        )
        # Home Assistant 2026.8 replaced via_device (identifier) by via_device_id.
        if "via_device_id" in _DEVICE_INFO_KEYS:
            info["via_device_id"] = runtime.hub_device_id
        else:
            info["via_device"] = (DOMAIN, entry.entry_id)  # type: ignore[typeddict-unknown-key]
        self._attr_device_info = info
        self._attr_extra_state_attributes = {"channel": channel, "quality": quality}

    async def stream_source(self) -> str | None:
        return self._api.rtsp_url(self._stream)

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        now = time.monotonic()
        if self._last_image and now - self._last_image_at < SNAPSHOT_CACHE_SECONDS:
            return self._last_image
        try:
            image = await self._api.snapshot(self._snapshot_stream)
        except (CannotConnect, InvalidAuth) as err:
            _LOGGER.debug("Snapshot of camera %s failed: %s", self._channel, err)
            return self._last_image
        if image:
            self._last_image, self._last_image_at = image, now
        return image or self._last_image
