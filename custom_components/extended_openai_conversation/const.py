"""Constants for the Barnabee Assistant integration."""

DOMAIN = "barnabee_assistant"
DEFAULT_NAME = "Barnabee Assistant"
CONF_ORGANIZATION = "organization"
CONF_BASE_URL = "base_url"
DEFAULT_CONF_BASE_URL = "https://api.openai.com/v1"
CONF_API_VERSION = "api_version"
CONF_SKIP_AUTHENTICATION = "skip_authentication"
DEFAULT_SKIP_AUTHENTICATION = False

# Barnabee-specific events
EVENT_AUTOMATION_REGISTERED = "automation_registered_via_barnabee_assistant"
EVENT_CONVERSATION_FINISHED = "barnabee_assistant.conversation.finished"
EVENT_MEMORY_LOGGED = "barnabee_assistant.memory.logged"
EVENT_PATTERN_LEARNED = "barnabee_assistant.pattern.learned"

CONF_PROMPT = "prompt"
DEFAULT_PROMPT = """I am Barnabee, your smart home assistant for Home Assistant.
I provide quick, helpful responses and can control your devices efficiently.
I learn from our conversations to get better at helping you.

Current Time: {{now()}}

Available Devices:
```csv
entity_id,name,state,aliases
{% for entity in exposed_entities -%}
{{ entity.entity_id }},{{ entity.name }},{{ entity.state }},{{entity.aliases | join('/')}}
{% endfor -%}
```

I respond conversationally and execute requested actions promptly.
I don't repeat what you said - I just do what you need and confirm briefly.
Use execute_services function only for requested actions, not for checking current states.
"""

CONF_CHAT_MODEL = "chat_model"
DEFAULT_CHAT_MODEL = "gpt-4o-mini"
CONF_MAX_TOKENS = "max_tokens"
DEFAULT_MAX_TOKENS = 150
CONF_TOP_P = "top_p"
DEFAULT_TOP_P = 1
CONF_TEMPERATURE = "temperature"
DEFAULT_TEMPERATURE = 0.5
CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION = "max_function_calls_per_conversation"
DEFAULT_MAX_FUNCTION_CALLS_PER_CONVERSATION = 1

# Barnabee-specific configuration
CONF_BARNABEE_PERSONALITY = "barnabee_personality"
DEFAULT_BARNABEE_PERSONALITY = "helpful"
CONF_MEMORY_INTEGRATION = "memory_integration"
DEFAULT_MEMORY_INTEGRATION = True
CONF_VOICE_RESPONSE_STYLE = "voice_response_style"
DEFAULT_VOICE_RESPONSE_STYLE = "concise"
CONF_LEARNING_ENABLED = "learning_enabled"
DEFAULT_LEARNING_ENABLED = True

CONF_FUNCTIONS = "functions"
DEFAULT_CONF_FUNCTIONS = [
    {
        "spec": {
            "name": "execute_services",
            "description": "Use this function to execute service of devices in Home Assistant.",
            "parameters": {
                "type": "object",
                "properties": {
                    "list": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "domain": {
                                    "type": "string",
                                    "description": "The domain of the service",
                                },
                                "service": {
                                    "type": "string",
                                    "description": "The service to be called",
                                },
                                "service_data": {
                                    "type": "object",
                                    "description": "The service data object to indicate what to control.",
                                    "properties": {
                                        "entity_id": {
                                            "type": "string",
                                            "description": "The entity_id retrieved from available devices. It must start with domain, followed by dot character.",
                                        }
                                    },
                                    "required": ["entity_id"],
                                },
                            },
                            "required": ["domain", "service", "service_data"],
                        },
                    }
                },
            },
        },
        "function": {"type": "native", "name": "execute_service"},
    },
    {
        "spec": {
            "name": "get_device_status",
            "description": "Get current status of specific devices with natural language responses",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_query": {
                        "type": "string",
                        "description": "Natural language query about device status (e.g., 'is the bedroom light on?', 'what's the temperature?')"
                    }
                },
                "required": ["device_query"]
            }
        },
        "function": {"type": "native", "name": "get_device_status"},
    },
    {
        "spec": {
            "name": "barnabee_memory_log",
            "description": "Log important information for future reference",
            "parameters": {
                "type": "object", 
                "properties": {
                    "information": {
                        "type": "string",
                        "description": "Information to remember"
                    },
                    "category": {
                        "type": "string",
                        "description": "Category of information (preference, routine, context, etc.)"
                    }
                },
                "required": ["information", "category"]
            }
        },
        "function": {"type": "native", "name": "barnabee_memory_log"},
    }
]

CONF_ATTACH_USERNAME = "attach_username"
DEFAULT_ATTACH_USERNAME = False
CONF_USE_TOOLS = "use_tools"
DEFAULT_USE_TOOLS = False
CONF_CONTEXT_THRESHOLD = "context_threshold"
DEFAULT_CONTEXT_THRESHOLD = 13000
CONTEXT_TRUNCATE_STRATEGIES = [{"key": "clear", "label": "Clear All Messages"}]
CONF_CONTEXT_TRUNCATE_STRATEGY = "context_truncate_strategy"
DEFAULT_CONTEXT_TRUNCATE_STRATEGY = CONTEXT_TRUNCATE_STRATEGIES[0]["key"]

SERVICE_QUERY_IMAGE = "query_image"
SERVICE_BARNABEE_VOICE_PROCESS = "voice_process"

CONF_PAYLOAD_TEMPLATE = "payload_template"