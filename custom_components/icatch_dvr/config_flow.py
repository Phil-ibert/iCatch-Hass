"""Config flow: discovered by the add-on (or entered by hand), then choose the cameras."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import CannotConnect, Go2rtcApi, InvalidAuth, find_addon
from .const import CONF_CAMERAS, CONF_RTSP_PORT, DEFAULT_API_PORT, DEFAULT_RTSP_PORT, DOMAIN

if TYPE_CHECKING:
    from homeassistant.helpers.service_info.hassio import HassioServiceInfo

_PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _camera_selector(available: list[int]) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=[SelectOptionDict(value=str(c), label=f"Caméra {c}") for c in available],
            multiple=True,
            mode=SelectSelectorMode.LIST,
        )
    )


class ICatchConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up the iCatch DVR integration."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._available: list[int] = []
        self._addon_name = "iCatch DVR"

    def _api(self, data: dict[str, Any]) -> Go2rtcApi:
        return Go2rtcApi(
            async_get_clientsession(self.hass),
            data[CONF_HOST],
            data[CONF_PORT],
            data[CONF_RTSP_PORT],
            data.get(CONF_USERNAME) or None,
            data.get(CONF_PASSWORD) or None,
        )

    async def _fetch_cameras(self, data: dict[str, Any]) -> str | None:
        """Load the add-on's camera list into self._available; return an error key."""
        try:
            self._available = await self._api(data).cameras()
        except InvalidAuth:
            return "invalid_auth"
        except CannotConnect:
            return "cannot_connect"
        return None if self._available else "no_cameras"

    # --- discovery: the add-on sends host, ports and generated credentials ---

    async def async_step_hassio(self, discovery_info: HassioServiceInfo) -> ConfigFlowResult:
        cfg = discovery_info.config
        self._data = {
            CONF_HOST: cfg[CONF_HOST],
            CONF_PORT: int(cfg.get(CONF_PORT, DEFAULT_API_PORT)),
            CONF_RTSP_PORT: int(cfg.get(CONF_RTSP_PORT, DEFAULT_RTSP_PORT)),
            CONF_USERNAME: cfg.get(CONF_USERNAME, ""),
            CONF_PASSWORD: cfg.get(CONF_PASSWORD, ""),
        }
        await self.async_set_unique_id(f"{self._data[CONF_HOST]}:{self._data[CONF_PORT]}")
        # Already set up: refresh host/credentials (they may have been regenerated).
        self._abort_if_unique_id_configured(updates=self._data)
        self._addon_name = discovery_info.name or self._addon_name
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if (error := await self._fetch_cameras(self._data)) is None:
                return await self.async_step_cameras()
            errors["base"] = error
        self._set_confirm_only()
        return self.async_show_form(
            step_id="hassio_confirm",
            errors=errors,
            description_placeholders={"addon": self._addon_name},
        )

    # --- manual setup ---

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if (error := await self._fetch_cameras(user_input)) is None:
                await self.async_set_unique_id(f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}")
                self._abort_if_unique_id_configured()
                self._data = user_input
                return await self.async_step_cameras()
            errors["base"] = error

        defaults = user_input or {}
        if not defaults:
            detected = await find_addon(async_get_clientsession(self.hass))
            defaults = {CONF_HOST: detected or ""}
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
                vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_API_PORT)): int,
                vol.Required(
                    CONF_RTSP_PORT, default=defaults.get(CONF_RTSP_PORT, DEFAULT_RTSP_PORT)
                ): int,
                vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "admin")): str,
                vol.Required(CONF_PASSWORD): _PASSWORD_SELECTOR,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    # --- camera choice (both paths) ---

    async def async_step_cameras(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            chosen = sorted(int(c) for c in user_input[CONF_CAMERAS])
            if chosen:
                return self.async_create_entry(
                    title=f"DVR iCatch ({self._data[CONF_HOST]})",
                    data=self._data,
                    options={CONF_CAMERAS: chosen},
                )
            errors["base"] = "no_selection"
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_CAMERAS, default=[str(c) for c in self._available]
                ): _camera_selector(self._available)
            }
        )
        return self.async_show_form(
            step_id="cameras",
            data_schema=schema,
            errors=errors,
            description_placeholders={"count": str(len(self._available))},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ICatchOptionsFlow()


class ICatchOptionsFlow(OptionsFlow):
    """Change which cameras get entities."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current = [int(c) for c in self.config_entry.options.get(CONF_CAMERAS, [])]
        errors: dict[str, str] = {}
        if user_input is not None:
            chosen = sorted(int(c) for c in user_input[CONF_CAMERAS])
            if chosen:
                return self.async_create_entry(data={CONF_CAMERAS: chosen})
            errors["base"] = "no_selection"

        available = set(current)
        try:
            available |= set(await self.config_entry.runtime_data.api.cameras())
        except (CannotConnect, InvalidAuth, AttributeError):
            pass
        options = sorted(available)
        schema = vol.Schema(
            {
                vol.Required(CONF_CAMERAS, default=[str(c) for c in current]): _camera_selector(options)
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
