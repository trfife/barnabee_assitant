"""The Barnabee Assistant integration."""

from __future__ import annotations

import logging
from typing import Literal
import aiohttp

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent, area_registry, entity_registry, device_registry
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
        
        _LOGGER.info("Processing: '%s'", text)
        
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
            _LOGGER.error("Node-RED call failed: %s", e, exc_info=True)
        
        # Fallback
        intent_response = intent.IntentResponse(language=user_input.language)
        intent_response.async_set_speech("I'm sorry, I couldn't process that request.")
        return conversation.ConversationResult(
            response=intent_response, conversation_id=conversation_id
        )

    async def _call_nodered(self, text: str, user_input: conversation.ConversationInput) -> str | None:
        """Call Node-RED with full HA context including entities."""
        
        _LOGGER.debug("Starting Node-RED call for: %s", text)
        
        # Get all entities
        all_entities = await self._get_all_entities()
        _LOGGER.debug("Collected %d entities", len(all_entities))
        
        # Get area info if device is specified
        area_name = None
        area_id = None
        if user_input.device_id:
            dev_reg = device_registry.async_get(self.hass)
            device = dev_reg.async_get(user_input.device_id)
            if device and device.area_id:
                area_reg = area_registry.async_get(self.hass)
                area = area_reg.async_get_area(device.area_id)
                if area:
                    area_name = area.name
                    area_id = device.area_id
        
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
                "area_id": area_id,
                "device_id": user_input.device_id,
                "entities": all_entities
            }
        }
        
        _LOGGER.debug("Calling Node-RED at %s", self.nodered_url)
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.nodered_url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                _LOGGER.debug("Node-RED responded with status %d", response.status)
                if response.status == 200:
                    result = await response.json()
                    reply = result.get("reply", "No response from Barnabee")
                    _LOGGER.info("Node-RED replied: %s", reply)
                    return reply
                else:
                    _LOGGER.error("Node-RED returned status %s", response.status)
                    response_text = await response.text()
                    _LOGGER.error("Response body: %s", response_text)
                    return None

    async def _get_all_entities(self) -> list[dict]:
        """Get all entities (Node-RED will filter by barnabee label)."""
        entities_list = []
        
        area_reg = area_registry.async_get(self.hass)
        ent_reg = entity_registry.async_get(self.hass)
        
        for state in self.hass.states.async_all():
            entity_id = state.entity_id
            
            # Get entity registry entry
            entity_entry = ent_reg.async_get(entity_id)
            
            # Skip disabled entities
            if entity_entry and entity_entry.disabled:
                continue
            
            # Get area
            area_id = None
            if entity_entry and entity_entry.area_id:
                area_id = entity_entry.area_id
            
            # Get labels (including 'barnabee' label for filtering)
            labels = []
            if entity_entry and entity_entry.labels:
                labels = list(entity_entry.labels)
            
            # Get attributes for sensors
            attributes = None
            if entity_id.startswith(('sensor.', 'binary_sensor.', 'weather.', 'climate.')):
                attributes = dict(state.attributes)
            
            # Build entity info
            entity_info = {
                "entity_id": entity_id,
                "name": state.name or entity_id,
                "state": state.state,
                "area_id": area_id,
                "domain": entity_id.split(".")[0],
                "labels": labels,
                "aliases": list(entity_entry.aliases) if entity_entry and entity_entry.aliases else [],
                "attributes": attributes
            }
            
            entities_list.append(entity_info)
        
        return entities_list