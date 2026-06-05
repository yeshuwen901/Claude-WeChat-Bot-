"""Configuration from environment variables."""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def _get_app_dir() -> str:
    """Directory containing the application (exe or script)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.getcwd()


APP_DIR = _get_app_dir()

# Load .env from app directory (next to exe or in CWD)
_env_path = os.path.join(APP_DIR, ".env")
if os.path.isfile(_env_path):
    load_dotenv(_env_path)
else:
    load_dotenv()

# Model prefix → Anthropic-compatible API base URL
# DeepSeek provides an official Anthropic-compatible endpoint:
#   https://api.deepseek.com/anthropic
# Leave value empty to use the default Anthropic API endpoint.
MODEL_BASE_URL_MAP: dict[str, str] = {
    "deepseek": "https://api.deepseek.com/anthropic",
    "claude": "",  # default Anthropic API
}


def get_base_url_for_model(model: str) -> str:
    """Return the base URL for a given model, or empty for default."""
    env_override = os.getenv("ANTHROPIC_BASE_URL", "")
    if env_override:
        return env_override
    model_lower = model.lower()
    for prefix, url in MODEL_BASE_URL_MAP.items():
        if model_lower.startswith(prefix):
            return url
    return ""


@dataclass
class Config:
    anthropic_api_key: str = field(
        default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", "")
    )
    anthropic_base_url: str = field(
        default_factory=lambda: os.getenv("ANTHROPIC_BASE_URL", "")
    )
    anthropic_model: str = field(
        default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
    )
    anthropic_max_tokens: int = field(
        default_factory=lambda: int(os.getenv("ANTHROPIC_MAX_TOKENS", "4096"))
    )
    anthropic_temperature: float = field(
        default_factory=lambda: float(os.getenv("ANTHROPIC_TEMPERATURE", "0.7"))
    )

    wechat_protocol: str = field(
        default_factory=lambda: os.getenv("WECHAT_PROTOCOL", "itchat")
    )

    allowed_users: str = field(
        default_factory=lambda: os.getenv("ALLOWED_USERS", "*")
    )
    allowed_rooms: str = field(
        default_factory=lambda: os.getenv("ALLOWED_ROOMS", "*")
    )
    group_mention_only: bool = field(
        default_factory=lambda: os.getenv("GROUP_MENTION_ONLY", "true").lower() == "true"
    )

    auto_reply_enabled: bool = field(
        default_factory=lambda: os.getenv("AUTO_REPLY_ENABLED", "true").lower() == "true"
    )
    auto_reply_cooldown: int = field(
        default_factory=lambda: int(os.getenv("AUTO_REPLY_COOLDOWN", "5"))
    )

    conversation_max_turns: int = field(
        default_factory=lambda: int(os.getenv("CONVERSATION_MAX_TURNS", "20"))
    )
    conversation_ttl_minutes: int = field(
        default_factory=lambda: int(os.getenv("CONVERSATION_TTL_MINUTES", "60"))
    )

    bot_name: str = field(
        default_factory=lambda: os.getenv("BOT_NAME", "Claude")
    )
    admin_user: str = field(
        default_factory=lambda: os.getenv("ADMIN_USER", "")
    )

    log_level: str = field(
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO")
    )
    data_dir: str = field(
        default_factory=lambda: os.getenv("DATA_DIR", os.path.join(APP_DIR, "data"))
    )

    def validate(self) -> bool:
        if not self.anthropic_api_key:
            print("WARNING: ANTHROPIC_API_KEY not set. Configure it via admin panel after login.")
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        return True


config = Config()
