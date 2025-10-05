"""The Barnabee Assistant integration."""

from __future__ import annotations

import logging
from typing import Literal
import aiohttp

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent, area_registry, entity_registry
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import ulid
import homeassistant.util.dt as dt_util

from .const import DOMAIN, DATA_AGENT, CONF_NODERED_URL, DEFAULT_NODERED_URL
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Barnabee Assistant."""
    await async_setup_services(hass, config)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Barnabee Assistant from a config entry."""
    agent = BarnabeeAgent(hass, entry)

    data = hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})
    data[DATA_AGENT] = agent

    conversation.async_set_agent(hass, entry, agent)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Barnabee Assistant."""
    hass.data[DOMAIN].pop(entry.entry_id)
    conversation.async_unset_agent(hass, entry)
    return True


class BarnabeeAgent(conversation.AbstractConversationAgent):
    """Barnabee conversation agent - routes to Node-RED with HA context."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the agent."""
        self.hass = hass
        self.entry = entry
        
        # Node-RED endpoint 
        self.nodered_url = entry.data.get(CONF_NODERED_URL, DEFAULT_NODERED_URL)
        if not self.nodered_url.endswith('/voice-input'):
            self.nodered_url = f"{self.nodered_url}/voice-input"
        
        _LOGGER.info("Barnabee Agent initialized - routing to Node-RED at %s", self.nodered_url)

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return a list of supported languages."""
        return MATCH_ALL

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        """Route to Node-RED with full HA context."""
        
        conversation_id = user_input.conversation_id or ulid.ulid()
        text = user_input.text.strip()
        
        # Call Node-RED with HA context
        try:
            response_text = await self._call_nodered(text, user_input)
            
            if response_text:
                intent_response = intent.IntentResponse(language=user_input.language)
                intent_response.async_set_speech(response_text)
                return conversation.ConversationResult(
                    response=intent_response, conversation_id=conversation_id
                )
            
        except Exception as e:
            _LOGGER.error("Node-RED call failed: %s", e)
        
        # Fallback
        intent_response = intent.IntentResponse(language=user_input.language)
        intent_response.async_set_speech("I'm sorry, I couldn't process that request.")
        return conversation.ConversationResult(
            response=intent_response, conversation_id=conversation_id
        )

    async def _call_nodered(self, text: str, user_input: conversation.ConversationInput) -> str | None:
        """Call Node-RED with full HA context including exposed entities."""
        
        # Get exposed entities
        exposed_entities = await self._get_exposed_entities()
        
        # Get area info if device is specified
        area_name = None
        if user_input.device_id:
            area_reg = area_registry.async_get(self.hass)
            ent_reg = entity_registry.async_get(self.hass)
            
            # Try to find entity for this device
            entities = entity_registry.async_entries_for_device(ent_reg, user_input.device_id)
            if entities:
                area_id = entities[0].area_id
                if area_id:
                    area = area_reg.async_get_area(area_id)
                    if area:
                        area_name = area.name
        
        payload = {
            "text": text,
            "originalText": text,
            "hasWakeWord": True,
            "wakeWord": "assistant",
            "sessionId": user_input.conversation_id,
            "userId": user_input.context.user_id or "ha_user",
            "confidence": 1.0,
            "source": "home_assistant",
            "context": {
                "current_time": dt_util.now().isoformat(),
                "area_name": area_name,
                "device_id": user_input.device_id,
                "exposed_entities": exposed_entities
            }
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.nodered_url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get("reply", "No response from Barnabee")
                else:
                    _LOGGER.error("Node-RED returned status %s", response.status)
                    return None

    async def _get_exposed_entities(self) -> list[dict]:
        """Get all entities exposed to conversation."""
        exposed_entities = []
        
        area_reg = area_registry.async_get(self.hass)
        ent_reg = entity_registry.async_get(self.hass)
        
        for state in self.hass.states.async_all():
            entity_id = state.entity_id
            
            # Check if exposed to conversation
            if not conversation.async_should_expose(self.hass, "conversation", entity_id):
                continue
            
            # Get entity registry entry for additional info
            entity_entry = ent_reg.async_get(entity_id)
            
            # Get area
            area_id = None
            if entity_entry and entity_entry.area_id:
                area_id = entity_entry.area_id
            elif entity_entry and entity_entry.device_id:
                # Try to get area from device
                area_id = area_reg.async_get_area_id_for_device_id(entity_entry.device_id)
            
            # Build entity info
            entity_info = {
                "entity_id": entity_id,
                "name": state.name or entity_id,
                "state": state.state,
                "area_id": area_id,
                "domain": entity_id.split(".")[0],
                "aliases": list(entity_entry.aliases) if entity_entry and entity_entry.aliases else []
            }
            
            exposed_entities.append(entity_info)
        
        _LOGGER.debug("Exposed %d entities to Barnabee", len(exposed_entities))
        return exposed_entities