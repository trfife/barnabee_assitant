"""Complete services for the Barnabee Assistant component."""

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
from homeassistant.components import conversation

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

# Multi-entry point service schemas
VOICE_PROCESS_SCHEMA = vol.Schema({
    vol.Required("config_entry"): selector.ConfigEntrySelector({"integration": DOMAIN}),
    vol.Required("text"): cv.string,
    vol.Optional("source", default="unknown"): cv.string,
    vol.Optional("user_id"): cv.string,
    vol.Optional("session_id"): cv.string,
    vol.Optional("conversation_id"): cv.string,
    vol.Optional("priority", default="normal"): vol.In(["low", "normal", "high", "urgent"]),
})

EMAIL_PROCESS_SCHEMA = vol.Schema({
    vol.Required("config_entry"): selector.ConfigEntrySelector({"integration": DOMAIN}),
    vol.Required("subject"): cv.string,
    vol.Required("body"): cv.string,
    vol.Required("sender"): cv.string,
    vol.Optional("user_id"): cv.string,
    vol.Optional("priority", default="normal"): vol.In(["low", "normal", "high", "urgent"]),
})

GLASSES_PROCESS_SCHEMA = vol.Schema({
    vol.Required("config_entry"): selector.ConfigEntrySelector({"integration": DOMAIN}),
    vol.Required("text"): cv.string,
    vol.Optional("user_id"): cv.string,
    vol.Optional("session_id"): cv.string,
    vol.Optional("ambient_context"): cv.string,
    vol.Optional("location_context"): cv.string,
})

NOTIFICATION_PROCESS_SCHEMA = vol.Schema({
    vol.Required("config_entry"): selector.ConfigEntrySelector({"integration": DOMAIN}),
    vol.Required("message"): cv.string,
    vol.Optional("title", default="Barnabee"): cv.string,
    vol.Optional("priority", default="normal"): vol.In(["low", "normal", "high", "urgent"]),
    vol.Optional("user_id"): cv.string,
    vol.Optional("requires_response", default=False): cv.boolean,
})

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

    async def voice_process(call: ServiceCall) -> ServiceResponse:
        """Process voice input from any source (Node-RED, external apps, etc.)."""
        try:
            config_entry_id = call.data["config_entry"]
            text = call.data["text"]
            source = call.data.get("source", "voice_api")
            user_id = call.data.get("user_id")
            session_id = call.data.get("session_id", f"voice-{hash(text)}")
            conversation_id = call.data.get("conversation_id")
            priority = call.data.get("priority", "normal")
            
            _LOGGER.info(f"[BARNABEE VOICE] Processing: '{text}' from {source}")
            
            # Get the agent
            agent = hass.data[DOMAIN][config_entry_id]["agent"]
            
            # Create conversation input with source context
            conv_input = conversation.ConversationInput(
                text=text,
                conversation_id=conversation_id,
                device_id=None,
                language="en",
                context=conversation.ConversationContext(
                    user_id=user_id,
                )
            )
            
            # Process through Barnabee's smart routing
            result = await agent.async_process(conv_input)
            
            response_text = ""
            if result.response.speech and "plain" in result.response.speech:
                response_text = result.response.speech["plain"]["speech"]
            
            # Fire completion event
            hass.bus.async_fire(
                "barnabee_assistant.voice.completed",
                {
                    "text": text,
                    "response": response_text,
                    "source": source,
                    "user_id": user_id,
                    "session_id": session_id,
                    "conversation_id": result.conversation_id,
                    "priority": priority,
                }
            )
            
            return {
                "response": response_text,
                "conversation_id": result.conversation_id,
                "success": True,
                "processed_by": "barnabee_voice",
                "source": source,
                "session_id": session_id
            }
            
        except Exception as err:
            _LOGGER.error(f"Error in voice processing: {err}")
            return {
                "response": "Sorry, I encountered an error processing your voice request.",
                "success": False,
                "error": str(err),
                "processed_by": "barnabee_voice"
            }

    async def email_process(call: ServiceCall) -> ServiceResponse:
        """Process email content through Barnabee."""
        try:
            config_entry_id = call.data["config_entry"]
            subject = call.data["subject"]
            body = call.data["body"]
            sender = call.data["sender"]
            user_id = call.data.get("user_id")
            priority = call.data.get("priority", "normal")
            
            _LOGGER.info(f"[BARNABEE EMAIL] Processing email from {sender}: {subject}")
            
            # Combine subject and body for processing
            email_text = f"Email from {sender} with subject '{subject}': {body}"
            
            # Get the agent
            agent = hass.data[DOMAIN][config_entry_id]["agent"]
            
            # Create conversation input with email context
            conv_input = conversation.ConversationInput(
                text=email_text,
                conversation_id=f"email-{hash(email_text)}",
                device_id=None,
                language="en",
                context=conversation.ConversationContext(
                    user_id=user_id,
                )
            )
            
            # Process through Barnabee
            result = await agent.async_process(conv_input)
            
            response_text = ""
            if result.response.speech and "plain" in result.response.speech:
                response_text = result.response.speech["plain"]["speech"]
            
            # Fire completion event
            hass.bus.async_fire(
                "barnabee_assistant.email.completed",
                {
                    "subject": subject,
                    "sender": sender,
                    "response": response_text,
                    "user_id": user_id,
                    "conversation_id": result.conversation_id,
                    "priority": priority,
                }
            )
            
            return {
                "response": response_text,
                "conversation_id": result.conversation_id,
                "success": True,
                "processed_by": "barnabee_email",
                "sender": sender,
                "subject": subject
            }
            
        except Exception as err:
            _LOGGER.error(f"Error in email processing: {err}")
            return {
                "response": "Sorry, I encountered an error processing your email.",
                "success": False,
                "error": str(err),
                "processed_by": "barnabee_email"
            }

    async def glasses_process(call: ServiceCall) -> ServiceResponse:
        """Process input specifically from AR glasses with ambient context."""
        try:
            config_entry_id = call.data["config_entry"]
            text = call.data["text"]
            user_id = call.data.get("user_id")
            session_id = call.data.get("session_id", f"glasses-{hash(text)}")
            ambient_context = call.data.get("ambient_context", "")
            location_context = call.data.get("location_context", "")
            
            _LOGGER.info(f"[BARNABEE GLASSES] Processing: '{text}' with context")
            
            # Enhance text with context for better AI understanding
            enhanced_text = text
            if ambient_context or location_context:
                context_parts = []
                if location_context:
                    context_parts.append(f"I'm at {location_context}")
                if ambient_context:
                    context_parts.append(f"Context: {ambient_context}")
                enhanced_text = f"{text}. {'. '.join(context_parts)}"
            
            # Get the agent
            agent = hass.data[DOMAIN][config_entry_id]["agent"]
            
            # Create conversation input with glasses context
            conv_input = conversation.ConversationInput(
                text=enhanced_text,
                conversation_id=session_id,
                device_id=None,
                language="en",
                context=conversation.ConversationContext(
                    user_id=user_id,
                )
            )
            
            # Process through Barnabee
            result = await agent.async_process(conv_input)
            
            response_text = ""
            if result.response.speech and "plain" in result.response.speech:
                response_text = result.response.speech["plain"]["speech"]
            
            # Fire completion event
            hass.bus.async_fire(
                "barnabee_assistant.glasses.completed",
                {
                    "text": text,
                    "enhanced_text": enhanced_text,
                    "response": response_text,
                    "user_id": user_id,
                    "session_id": session_id,
                    "conversation_id": result.conversation_id,
                    "ambient_context": ambient_context,
                    "location_context": location_context,
                }
            )
            
            return {
                "response": response_text,
                "conversation_id": result.conversation_id,
                "success": True,
                "processed_by": "barnabee_glasses",
                "session_id": session_id,
                "context_enhanced": bool(ambient_context or location_context)
            }
            
        except Exception as err:
            _LOGGER.error(f"Error in glasses processing: {err}")
            return {
                "response": "Sorry, I encountered an error processing your glasses request.",
                "success": False,
                "error": str(err),
                "processed_by": "barnabee_glasses"
            }

    async def notification_process(call: ServiceCall) -> ServiceResponse:
        """Process proactive notifications that may require responses."""
        try:
            config_entry_id = call.data["config_entry"]
            message = call.data["message"]
            title = call.data.get("title", "Barnabee")
            priority = call.data.get("priority", "normal")
            user_id = call.data.get("user_id")
            requires_response = call.data.get("requires_response", False)
            
            _LOGGER.info(f"[BARNABEE NOTIFICATION] {title}: {message}")
            
            if requires_response:
                # Process notification as a query to Barnabee
                notification_text = f"Notification: {title} - {message}. How should I respond?"
                
                # Get the agent
                agent = hass.data[DOMAIN][config_entry_id]["agent"]
                
                # Create conversation input
                conv_input = conversation.ConversationInput(
                    text=notification_text,
                    conversation_id=f"notification-{hash(message)}",
                    device_id=None,
                    language="en",
                    context=conversation.ConversationContext(
                        user_id=user_id,
                    )
                )
                
                # Process through Barnabee
                result = await agent.async_process(conv_input)
                
                response_text = ""
                if result.response.speech and "plain" in result.response.speech:
                    response_text = result.response.speech["plain"]["speech"]
                
                # Fire notification event
                hass.bus.async_fire(
                    "barnabee_assistant.notification.processed",
                    {
                        "title": title,
                        "message": message,
                        "response": response_text,
                        "user_id": user_id,
                        "priority": priority,
                        "requires_response": requires_response,
                    }
                )
                
                return {
                    "response": response_text,
                    "success": True,
                    "processed_by": "barnabee_notification",
                    "requires_response": requires_response
                }
            else:
                # Just log the notification
                hass.bus.async_fire(
                    "barnabee_assistant.notification.logged",
                    {
                        "title": title,
                        "message": message,
                        "user_id": user_id,
                        "priority": priority,
                        "requires_response": False,
                    }
                )
                
                return {
                    "response": f"Notification logged: {title}",
                    "success": True,
                    "processed_by": "barnabee_notification",
                    "requires_response": False
                }
            
        except Exception as err:
            _LOGGER.error(f"Error in notification processing: {err}")
            return {
                "response": "Sorry, I encountered an error processing the notification.",
                "success": False,
                "error": str(err),
                "processed_by": "barnabee_notification"
            }

    # Register all the services
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
    
    hass.services.async_register(
        DOMAIN,
        "voice_process",
        voice_process,
        schema=VOICE_PROCESS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    
    hass.services.async_register(
        DOMAIN,
        "email_process", 
        email_process,
        schema=EMAIL_PROCESS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    
    hass.services.async_register(
        DOMAIN,
        "glasses_process",
        glasses_process,
        schema=GLASSES_PROCESS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    
    hass.services.async_register(
        DOMAIN,
        "notification_process",
        notification_process,
        schema=NOTIFICATION_PROCESS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    _LOGGER.info("[BARNABEE] All services registered successfully")


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