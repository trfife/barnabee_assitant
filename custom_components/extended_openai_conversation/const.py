"""Constants for the Barnabee Assistant integration."""

DOMAIN = "barnabee_assistant"
DEFAULT_NAME = "Barnabee Assistant"

# Configuration keys
CONF_NODERED_URL = "nodered_url"
DEFAULT_NODERED_URL = "http://192.168.86.50:1880"

# Events
EVENT_CONVERSATION_FINISHED = "barnabee_assistant.conversation.finished"
EVENT_RESPONSE = "barnabee_assistant.response"

# Services
SERVICE_BARNABEE_VOICE_PROCESS = "voice_process"

# Data keys
DATA_AGENT = "agent"