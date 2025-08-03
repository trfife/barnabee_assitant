"""Simple services for the Barnabee Assistant component."""

import logging
import voluptuous as vol

from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.helpers import config_validation as cv, selector
from homeassistant.helpers.typing import ConfigType
from homeassistant.components import conversation

from .const import DOMAIN, SERVICE_BARNABEE_VOICE_PROCESS, DATA_AGENT

_LOGGER = logging.getLogger(__package__)

VOICE_PROCESS_SCHEMA = vol.Schema({
    vol.Required("config_entry"): selector.ConfigEntrySelector({"integration": DOMAIN}),
    vol.Required("text"): cv.string,
    vol.Optional("user_id", default="service_user"): cv.string,
    vol.Optional("session_id"): cv.string,
    vol.Optional("source", default="barnabee_service"): cv.string,
})


async def async_setup_services(hass: HomeAssistant, config: ConfigType) -> None:
    """Set up services for the Barnabee Assistant component."""

    async def voice_process(call: ServiceCall) -> ServiceResponse:
        """Process voice input through Node-RED brain."""
        try:
            text = call.data["text"]
            user_id = call.data["user_id"]
            session_id = call.data.get("session_id", f"service-{hash(text)}")
            
            # Get the agent
            config_entry_id = call.data["config_entry"]
            agent = hass.data[DOMAIN][config_entry_id][DATA_AGENT]
            
            # Create conversation input
            conv_input = conversation.ConversationInput(
                text=text,
                conversation_id=session_id,
                device_id=None,
                language="en",
                context=conversation.ConversationContext(user_id=user_id)
            )
            
            # Process through Barnabee (routes to Node-RED)
            result = await agent.async_process(conv_input)
            
            response_text = ""
            if result.response.speech and "plain" in result.response.speech:
                response_text = result.response.speech["plain"]["speech"]
            
            return {
                "response": response_text,
                "conversation_id": result.conversation_id,
                "success": True,
                "source": call.data["source"]
            }
            
        except Exception as err:
            _LOGGER.error("Barnabee service error: %s", err)
            return {
                "response": "Sorry, I encountered an error.",
                "success": False,
                "error": str(err)
            }

    hass.services.async_register(
        DOMAIN,
        SERVICE_BARNABEE_VOICE_PROCESS,
        voice_process,
        schema=VOICE_PROCESS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    