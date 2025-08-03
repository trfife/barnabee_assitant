"""Config flow for Barnabee Assistant integration."""
from __future__ import annotations

import logging
from typing import Any
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN, DEFAULT_NAME, CONF_NODERED_URL, DEFAULT_NODERED_URL

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema({
    vol.Optional("name", default=DEFAULT_NAME): str,
    vol.Optional(CONF_NODERED_URL, default=DEFAULT_NODERED_URL): str,
})


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Barnabee Assistant."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA
            )

        # Simple validation - just check if Node-RED URL is provided
        if not user_input.get(CONF_NODERED_URL):
            return self.async_show_form(
                step_id="user", 
                data_schema=STEP_USER_DATA_SCHEMA,
                errors={CONF_NODERED_URL: "Node-RED URL is required"}
            )

        return self.async_create_entry(
            title=user_input.get("name", DEFAULT_NAME), 
            data=user_input
        )