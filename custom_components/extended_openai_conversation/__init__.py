"""The Barnabee Assistant integration with Smart Routing."""

from __future__ import annotations

import json
import logging
from typing import Literal
import aiohttp

from openai import AsyncAzureOpenAI, AsyncOpenAI
from openai._exceptions import AuthenticationError, OpenAIError
from openai.types.chat.chat_completion import (
    ChatCompletion,
    ChatCompletionMessage,
    Choice,
)
import yaml

from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_NAME, CONF_API_KEY, MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryNotReady,
    HomeAssistantError,
    TemplateError,
)
from homeassistant.helpers import (
    config_validation as cv,
    entity_registry as er,
    intent,
    template,
)
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import ulid
import homeassistant.util.dt as dt_util

from .const import (
    CONF_API_VERSION,
    CONF_ATTACH_USERNAME,
    CONF_BASE_URL,
    CONF_CHAT_MODEL,
    CONF_CONTEXT_THRESHOLD,
    CONF_CONTEXT_TRUNCATE_STRATEGY,
    CONF_FUNCTIONS,
    CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION,
    CONF_MAX_TOKENS,
    CONF_ORGANIZATION,
    CONF_PROMPT,
    CONF_SKIP_AUTHENTICATION,
    CONF_TEMPERATURE,
    CONF_TOP_P,
    CONF_USE_TOOLS,
    CONF_BARNABEE_PERSONALITY,
    CONF_MEMORY_INTEGRATION,
    CONF_VOICE_RESPONSE_STYLE,
    CONF_LEARNING_ENABLED,
    DEFAULT_ATTACH_USERNAME,
    DEFAULT_CHAT_MODEL,
    DEFAULT_CONF_FUNCTIONS,
    DEFAULT_CONTEXT_THRESHOLD,
    DEFAULT_CONTEXT_TRUNCATE_STRATEGY,
    DEFAULT_MAX_FUNCTION_CALLS_PER_CONVERSATION,
    DEFAULT_MAX_TOKENS,
    DEFAULT_PROMPT,
    DEFAULT_SKIP_AUTHENTICATION,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_P,
    DEFAULT_USE_TOOLS,
    DEFAULT_BARNABEE_PERSONALITY,
    DEFAULT_MEMORY_INTEGRATION,
    DEFAULT_VOICE_RESPONSE_STYLE,
    DEFAULT_LEARNING_ENABLED,
    DOMAIN,
    EVENT_CONVERSATION_FINISHED,
)
from .exceptions import (
    FunctionLoadFailed,
    FunctionNotFound,
    InvalidFunction,
    ParseArgumentsFailed,
    TokenLengthExceededError,
)
from .helpers import get_function_executor, is_azure, validate_authentication
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# hass.data key for agent.
DATA_AGENT = "agent"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Barnabee Assistant."""
    await async_setup_services(hass, config)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Barnabee Assistant from a config entry."""

    try:
        await validate_authentication(
            hass=hass,
            api_key=entry.data[CONF_API_KEY],
            base_url=entry.data.get(CONF_BASE_URL),
            api_version=entry.data.get(CONF_API_VERSION),
            organization=entry.data.get(CONF_ORGANIZATION),
            skip_authentication=entry.data.get(
                CONF_SKIP_AUTHENTICATION, DEFAULT_SKIP_AUTHENTICATION
            ),
        )
    except AuthenticationError as err:
        _LOGGER.error("Invalid API key: %s", err)
        return False
    except OpenAIError as err:
        raise ConfigEntryNotReady(err) from err

    agent = SmartBarnabeeAgent(hass, entry)

    data = hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})
    data[CONF_API_KEY] = entry.data[CONF_API_KEY]
    data[DATA_AGENT] = agent
    # Store agent reference for services
    data["agent"] = agent

    conversation.async_set_agent(hass, entry, agent)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Barnabee Assistant."""
    hass.data[DOMAIN].pop(entry.entry_id)
    conversation.async_unset_agent(hass, entry)
    return True


class SmartBarnabeeAgent(conversation.AbstractConversationAgent):
    """Smart Barnabee conversation agent with HA-first routing."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the agent."""
        self.hass = hass
        self.entry = entry
        self.history: dict[str, list[dict]] = {}
        
        # Barnabee-specific configuration
        self.personality = entry.options.get(CONF_BARNABEE_PERSONALITY, DEFAULT_BARNABEE_PERSONALITY)
        self.memory_integration = entry.options.get(CONF_MEMORY_INTEGRATION, DEFAULT_MEMORY_INTEGRATION)
        self.voice_style = entry.options.get(CONF_VOICE_RESPONSE_STYLE, DEFAULT_VOICE_RESPONSE_STYLE)
        self.learning_enabled = entry.options.get(CONF_LEARNING_ENABLED, DEFAULT_LEARNING_ENABLED)
        
        # Node-RED endpoint for fallback
        self.nodered_url = "http://localhost:1880/voice-input"  # Update this to your Node-RED URL
        
        base_url = entry.data.get(CONF_BASE_URL)
        if is_azure(base_url):
            self.client = AsyncAzureOpenAI(
                api_key=entry.data[CONF_API_KEY],
                azure_endpoint=base_url,
                api_version=entry.data.get(CONF_API_VERSION),
                organization=entry.data.get(CONF_ORGANIZATION),
                http_client=get_async_client(hass),
            )
        else:
            self.client = AsyncOpenAI(
                api_key=entry.data[CONF_API_KEY],
                base_url=base_url,
                organization=entry.data.get(CONF_ORGANIZATION),
                http_client=get_async_client(hass),
            )
        
        # Cache current platform data
        _ = hass.async_add_executor_job(self.client.platform_headers)
        
        _LOGGER.info("Smart Barnabee Agent initialized with HA-first routing")

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return a list of supported languages."""
        return MATCH_ALL

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        """Smart routing: Try HA first, then Barnabee instant responses, then Node-RED."""
        
        conversation_id = user_input.conversation_id or ulid.ulid()
        user_input.conversation_id = conversation_id
        
        text = user_input.text.strip()
        _LOGGER.info(f"[BARNABEE] Processing: '{text}'")
        
        # ==========================================
        # STEP 1: TRY HOME ASSISTANT BUILT-IN FIRST
        # ==========================================
        
        try:
            _LOGGER.info("[BARNABEE] Trying Home Assistant built-in first...")
            
            # Call HA's built-in conversation agent
            ha_result = await self.hass.services.async_call(
                "conversation", "process",
                {
                    "text": text,
                    "agent_id": "homeassistant",  # Built-in HA agent
                    "conversation_id": f"ha-{conversation_id}",
                },
                blocking=True,
                return_response=True
            )
            
            # Check if HA handled it successfully
            if (ha_result and 
                ha_result.get("response", {}).get("response_type") != "error"):
                
                _LOGGER.info("[BARNABEE] Home Assistant handled successfully")
                
                # Extract response from HA
                ha_response = ha_result.get("response", {})
                response_text = "Done!"
                
                if (ha_response.get("speech", {}).get("plain", {}).get("speech")):
                    response_text = ha_response["speech"]["plain"]["speech"]
                
                # Fire success event for learning
                self.hass.bus.async_fire(
                    "barnabee_assistant.ha.success",
                    {
                        "text": text,
                        "response": response_text,
                        "conversation_id": conversation_id,
                        "timestamp": dt_util.utcnow().isoformat(),
                    }
                )
                
                # Return HA response
                intent_response = intent.IntentResponse(language=user_input.language)
                intent_response.async_set_speech(response_text)
                return conversation.ConversationResult(
                    response=intent_response, conversation_id=conversation_id
                )
                
        except Exception as e:
            _LOGGER.warning(f"[BARNABEE] Home Assistant failed: {e}")
        
        # ==========================================
        # STEP 2: TRY BARNABEE INSTANT RESPONSES
        # ==========================================
        
        _LOGGER.info("[BARNABEE] HA failed, trying instant responses...")
        
        instant_response = await self._try_instant_response(text)
        if instant_response:
            _LOGGER.info(f"[BARNABEE] Instant response: {instant_response}")
            
            # Fire instant response event
            self.hass.bus.async_fire(
                "barnabee_assistant.instant.response",
                {
                    "text": text,
                    "response": instant_response,
                    "conversation_id": conversation_id,
                    "timestamp": dt_util.utcnow().isoformat(),
                }
            )
            
            intent_response = intent.IntentResponse(language=user_input.language)
            intent_response.async_set_speech(instant_response)
            return conversation.ConversationResult(
                response=intent_response, conversation_id=conversation_id
            )
        
        # ==========================================
        # STEP 3: FALLBACK TO NODE-RED
        # ==========================================
        
        _LOGGER.info("[BARNABEE] No instant response, falling back to Node-RED...")
        
        # Fire fallback event for learning
        self.hass.bus.async_fire(
            "barnabee_assistant.ha.fallback",
            {
                "text": text,
                "conversation_id": conversation_id,
                "timestamp": dt_util.utcnow().isoformat(),
            }
        )
        
        try:
            nodered_response = await self._call_nodered(text, user_input)
            
            if nodered_response:
                _LOGGER.info(f"[BARNABEE] Node-RED response: {nodered_response}")
                
                # Fire Node-RED success event
                self.hass.bus.async_fire(
                    "barnabee_assistant.nodered.success",
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
        
        # ==========================================
        # STEP 4: FINAL FALLBACK
        # ==========================================
        
        _LOGGER.warning("[BARNABEE] All methods failed, using fallback response")
        
        intent_response = intent.IntentResponse(language=user_input.language)
        intent_response.async_set_speech("I'm sorry, I couldn't process that request.")
        return conversation.ConversationResult(
            response=intent_response, conversation_id=conversation_id
        )

    async def _try_instant_response(self, text: str) -> str | None:
        """Try to handle with instant responses."""
        
        text_lower = text.lower()
        
        # Time queries
        if any(phrase in text_lower for phrase in ["what time", "time is it", "current time"]):
            from datetime import datetime
            now = datetime.now()
            return f"It's {now.strftime('%I:%M %p')}"
        
        # Greetings
        if any(phrase in text_lower for phrase in ["hello", "hi", "hey", "good morning", "good afternoon"]):
            greetings = [
                "Hello! I'm Barnabee, ready to help.",
                "Hi there! What can I do for you?",
                "Hey! Barnabee here, how can I assist?",
            ]
            import random
            return random.choice(greetings)
        
        # Jokes
        if any(phrase in text_lower for phrase in ["tell me a joke", "joke", "make me laugh"]):
            jokes = [
                "Why don't scientists trust atoms? Because they make up everything!",
                "Why did the scarecrow win an award? He was outstanding in his field!",
                "What do you call a fake noodle? An impasta!",
            ]
            import random
            return random.choice(jokes)
        
        # Simple math
        if any(phrase in text_lower for phrase in ["what is", "calculate"]) and any(op in text_lower for op in ["plus", "minus", "times", "divided", "+", "-", "*", "/"]):
            try:
                # Very basic math parsing - you can expand this
                import re
                math_expr = re.sub(r'[^\d\+\-\*\/\.\s]', '', text_lower.replace("what is", "").replace("plus", "+").replace("minus", "-").replace("times", "*").replace("divided by", "/"))
                if re.match(r'^[\d\+\-\*\/\.\s]+$', math_expr.strip()):
                    result = eval(math_expr.strip())  # Be careful with eval in production
                    return str(result)
            except:
                pass
        
        return None

    async def _call_nodered(self, text: str, user_input: conversation.ConversationInput) -> str | None:
        """Call Node-RED for processing."""
        
        payload = {
            "originalText": text,
            "command": text,
            "hasWakeWord": True,
            "wakeWord": "barnabee",
            "sessionId": user_input.conversation_id,
            "userId": user_input.context.user_id or "ha_user",
            "confidence": 0.95,
            "source": "barnabee_ha"
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
                        return result.get("reply", "No response from Node-RED")
                    else:
                        _LOGGER.error(f"Node-RED returned status {response.status}")
                        return None
                        
        except aiohttp.ClientError as e:
            _LOGGER.error(f"Failed to call Node-RED: {e}")
            return None
        except Exception as e:
            _LOGGER.error(f"Unexpected error calling Node-RED: {e}")
            return None

    # Keep all your existing methods (get_exposed_entities, get_functions, etc.)
    def get_exposed_entities(self):
        states = [
            state
            for state in self.hass.states.async_all()
            if async_should_expose(self.hass, conversation.DOMAIN, state.entity_id)
        ]
        entity_registry = er.async_get(self.hass)
        exposed_entities = []
        for state in states:
            entity_id = state.entity_id
            entity = entity_registry.async_get(entity_id)

            aliases = []
            if entity and entity.aliases:
                aliases = entity.aliases

            exposed_entities.append(
                {
                    "entity_id": entity_id,
                    "name": state.name,
                    "state": self.hass.states.get(entity_id).state,
                    "aliases": aliases,
                }
            )
        return exposed_entities

    def get_functions(self):
        try:
            function = self.entry.options.get(CONF_FUNCTIONS)
            result = yaml.safe_load(function) if function else DEFAULT_CONF_FUNCTIONS
            if result:
                for setting in result:
                    function_executor = get_function_executor(
                        setting["function"]["type"]
                    )
                    setting["function"] = function_executor.to_arguments(
                        setting["function"]
                    )
            return result
        except (InvalidFunction, FunctionNotFound) as e:
            raise e
        except:
            raise FunctionLoadFailed()

    # Include all other existing methods from your original code...


class BarnabeeQueryResponse:
    """Barnabee query response value object."""

    def __init__(
        self, response: ChatCompletion, message: ChatCompletionMessage
    ) -> None:
        """Initialize Barnabee query response value object."""
        self.response = response
        self.message = message