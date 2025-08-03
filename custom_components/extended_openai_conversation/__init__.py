"""The Barnabee Assistant integration - Simple Node-RED Bridge."""

from __future__ import annotations

import logging
from typing import Literal
import aiohttp

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import ulid
import homeassistant.util.dt as dt_util

from .const import DOMAIN, DATA_AGENT, CONF_NODERED_URL, DEFAULT_NODERED_URL
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

# hass.data key for agent.
DATA_AGENT = "agent"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Barnabee Assistant."""
    await async_setup_services(hass, config)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Barnabee Assistant from a config entry."""
    agent = SimpleBarnabeeAgent(hass, entry)

    data = hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})
    data[DATA_AGENT] = agent
    data["agent"] = agent

    conversation.async_set_agent(hass, entry, agent)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Barnabee Assistant."""
    hass.data[DOMAIN].pop(entry.entry_id)
    conversation.async_unset_agent(hass, entry)
    return True


class SimpleBarnabeeAgent(conversation.AbstractConversationAgent):
    """Simple Barnabee conversation agent - routes everything to Node-RED brain."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the agent."""
        self.hass = hass
        self.entry = entry
        
        # Node-RED endpoint 
        self.nodered_url = entry.data.get(CONF_NODERED_URL, DEFAULT_NODERED_URL)
        if not self.nodered_url.endswith('/voice-input'):
            self.nodered_url = f"{self.nodered_url}/voice-input"
        
        _LOGGER.info("Simple Barnabee Agent initialized - routing to Node-RED brain")

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return a list of supported languages."""
        return MATCH_ALL

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        """Route everything directly to Node-RED brain."""
        
        conversation_id = user_input.conversation_id or ulid.ulid()
        user_input.conversation_id = conversation_id
        
        text = user_input.text.strip()
        _LOGGER.info(f"[BARNABEE] Routing to Node-RED: '{text}'")
        
        # Call Node-RED brain
        try:
            nodered_response = await self._call_nodered(text, user_input)
            
            if nodered_response:
                _LOGGER.info(f"[BARNABEE] Node-RED response: {nodered_response}")
                
                # Fire success event
                self.hass.bus.async_fire(
                    "barnabee_assistant.response",
                    {
                        "text": text,
                        "response": nodered_response,
                        "conversation_id": conversation_id,
                        "timestamp": dt_util.utcnow().isoformat(),
                    }
                )
                
                intent_response = intent.IntentResponse(language=user_input.language)
                intent_response.async_set_speech(nodered_response)
                return conversation.ConversationResult(
                    response=intent_response, conversation_id=conversation_id
                )
            
        except Exception as e:
            _LOGGER.error(f"[BARNABEE] Node-RED call failed: {e}")
        
        # Fallback
        _LOGGER.warning("[BARNABEE] Node-RED failed, using fallback response")
        
        intent_response = intent.IntentResponse(language=user_input.language)
        intent_response.async_set_speech("I'm sorry, I couldn't process that request.")
        return conversation.ConversationResult(
            response=intent_response, conversation_id=conversation_id
        )

    async def _call_nodered(self, text: str, user_input: conversation.ConversationInput) -> str | None:
        """Call Node-RED brain for processing."""
        
        payload = {
            "originalText": text,
            "command": text,
            "hasWakeWord": True,
            "wakeWord": "assistant",
            "sessionId": user_input.conversation_id,
            "userId": user_input.context.user_id or "ha_user",
            "confidence": 1.0,
            "source": "home_assistant"
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.nodered_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        return result.get("reply", "No response from Barnabee brain")
                    else:
                        _LOGGER.error(f"Node-RED returned status {response.status}")
                        return None
                        
        except aiohttp.ClientError as e:
            _LOGGER.error(f"Failed to call Node-RED: {e}")
            return None
        except Exception as e:
            _LOGGER.error(f"Unexpected error calling Node-RED: {e}")
            return None
