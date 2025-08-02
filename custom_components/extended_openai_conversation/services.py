import base64
import json
import logging
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

from openai import AsyncOpenAI
from openai._exceptions import OpenAIError
from openai.types.chat.chat_completion_content_part_image_param import (
    ChatCompletionContentPartImageParam,
)
import voluptuous as vol

from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, selector
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, SERVICE_QUERY_IMAGE, SERVICE_BARNABEE_VOICE_PROCESS

QUERY_IMAGE_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry"): selector.ConfigEntrySelector(
            {
                "integration": DOMAIN,
            }
        ),
        vol.Required("model", default="gpt-4-vision-preview"): cv.string,
        vol.Required("prompt"): cv.string,
        vol.Required("images"): vol.All(cv.ensure_list, [{"url": cv.string}]),
        vol.Optional("max_tokens", default=300): cv.positive_int,
    }
)

BARNABEE_VOICE_PROCESS_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry"): selector.ConfigEntrySelector(
            {
                "integration": DOMAIN,
            }
        ),
        vol.Required("text"): cv.string,
        vol.Optional("user_id"): cv.string,
        vol.Optional("session_id"): cv.string,
        vol.Optional("conversation_id"): cv.string,
        vol.Optional("source", default="barnabee_voice"): cv.string,
    }
)

_LOGGER = logging.getLogger(__package__)


async def async_setup_services(hass: HomeAssistant, config: ConfigType) -> None:
    """Set up services for the Barnabee Assistant component."""

    async def query_image(call: ServiceCall) -> ServiceResponse:
        """Query an image."""
        try:
            model = call.data["model"]
            images = [
                {"type": "image_url", "image_url": to_image_param(hass, image)}
                for image in call.data["images"]
            ]

            messages = [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": call.data["prompt"]}] + images,
                }
            ]
            _LOGGER.info("Prompt for %s: %s", model, messages)

            response = await AsyncOpenAI(
                api_key=hass.data[DOMAIN][call.data["config_entry"]]["api_key"]
            ).chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=call.data["max_tokens"],
            )
            response_dict = response.model_dump()
            _LOGGER.info("Response %s", response_dict)
        except OpenAIError as err:
            raise HomeAssistantError(f"Error generating image: {err}") from err

        return response_dict

    async def barnabee_voice_process(call: ServiceCall) -> ServiceResponse:
        """Process voice input from Node-RED pipeline or external systems."""
        try:
            text = call.data["text"]
            user_id = call.data.get("user_id", "unknown")
            session_id = call.data.get("session_id", f"barnabee-{hash(text)}")
            conversation_id = call.data.get("conversation_id")
            source = call.data.get("source", "barnabee_voice")
            
            _LOGGER.info("Barnabee voice processing: %s (user: %s, session: %s)", text, user_id, session_id)
            
            # Get the agent for this config entry
            from homeassistant.components import conversation
            
            config_entry_id = call.data["config_entry"]
            agent = hass.data[DOMAIN][config_entry_id]["agent"]
            
            # Create conversation input
            conv_input = conversation.ConversationInput(
                text=text,
                conversation_id=conversation_id,
                device_id=None,
                language="en",
                context=conversation.ConversationContext(user_id=user_id)
            )
            
            # Process through Barnabee
            result = await agent.async_process(conv_input)
            
            # Fire Barnabee-specific event
            hass.bus.async_fire(
                "barnabee_assistant.voice.processed",
                {
                    "text": text,
                    "response": result.response.speech["plain"]["speech"] if result.response.speech else "",
                    "user_id": user_id,
                    "session_id": session_id,
                    "source": source,
                    "conversation_id": result.conversation_id,
                }
            )
            
            response_data = {
                "response": result.response.speech["plain"]["speech"] if result.response.speech else "No response generated",
                "conversation_id": result.conversation_id,
                "success": True,
                "processed_by": "barnabee_assistant",
                "session_id": session_id,
                "source": source
            }
            
            _LOGGER.info("Barnabee response: %s", response_data["response"])
            return response_data
            
        except Exception as err:
            _LOGGER.error("Error in Barnabee voice processing: %s", err)
            return {
                "response": "Sorry, I encountered an error processing your request.",
                "success": False,
                "error": str(err),
                "processed_by": "barnabee_assistant"
            }

    hass.services.async_register(
        DOMAIN,
        SERVICE_QUERY_IMAGE,
        query_image,
        schema=QUERY_IMAGE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    
    hass.services.async_register(
        DOMAIN,
        SERVICE_BARNABEE_VOICE_PROCESS,
        barnabee_voice_process,
        schema=BARNABEE_VOICE_PROCESS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )


def to_image_param(hass: HomeAssistant, image) -> ChatCompletionContentPartImageParam:
    """Convert url to base64 encoded image if local."""
    url = image["url"]

    if urlparse(url).scheme in cv.EXTERNAL_URL_PROTOCOL_SCHEMA_LIST:
        return image

    if not hass.config.is_allowed_path(url):
        raise HomeAssistantError(
            f"Cannot read `{url}`, no access to path; "
            "`allowlist_external_dirs` may need to be adjusted in "
            "`configuration.yaml`"
        )
    if not Path(url).exists():
        raise HomeAssistantError(f"`{url}` does not exist")
    mime_type, _ = mimetypes.guess_type(url)
    if mime_type is None or not mime_type.startswith("image"):
        raise HomeAssistantError(f"`{url}` is not an image")

    image["url"] = f"data:{mime_type};base64,{encode_image(url)}"
    return image


def encode_image(image_path):
    """Convert to base64 encoded image."""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")