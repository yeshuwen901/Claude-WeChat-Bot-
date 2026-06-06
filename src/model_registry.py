"""Model registry — supported models, vision capability, and config management."""

import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── Built-in model catalog ────────────────────────────────────────────────

@dataclass
class ModelDef:
    id: str          # litellm model id: e.g. "deepseek/deepseek-chat"
    name: str        # display name: e.g. "DeepSeek V3"
    provider: str    # provider label: e.g. "DeepSeek"
    supports_vision: bool = False
    supports_tools: bool = True


BUILTIN_MODELS: list[ModelDef] = [
    # DeepSeek
    ModelDef("deepseek/deepseek-v4-pro",   "DeepSeek V4 Pro",   "DeepSeek",    supports_vision=False),
    ModelDef("deepseek/deepseek-v4-flash", "DeepSeek V4 Flash", "DeepSeek",    supports_vision=False),
    ModelDef("deepseek/deepseek-chat",     "DeepSeek V3",       "DeepSeek",    supports_vision=False),
    ModelDef("deepseek/deepseek-reasoner", "DeepSeek R1",       "DeepSeek",    supports_vision=False),
    # OpenAI
    ModelDef("openai/gpt-4.1",             "GPT-4.1",           "OpenAI",      supports_vision=True),
    ModelDef("openai/gpt-4.1-mini",        "GPT-4.1 Mini",      "OpenAI",      supports_vision=True),
    ModelDef("openai/gpt-4.1-nano",        "GPT-4.1 Nano",      "OpenAI",      supports_vision=True),
    ModelDef("openai/gpt-4o",              "GPT-4o",            "OpenAI",      supports_vision=True),
    ModelDef("openai/gpt-4o-mini",         "GPT-4o Mini",       "OpenAI",      supports_vision=True),
    ModelDef("openai/o4-mini",             "o4 Mini",           "OpenAI",      supports_vision=True),
    ModelDef("openai/o3-mini",             "o3 Mini",           "OpenAI",      supports_vision=False),
    # Anthropic
    ModelDef("anthropic/claude-opus-4-20250514",   "Claude Opus 4",   "Anthropic", supports_vision=True),
    ModelDef("anthropic/claude-sonnet-4-20250514", "Claude Sonnet 4", "Anthropic", supports_vision=True),
    ModelDef("anthropic/claude-3-5-haiku-20241022", "Claude Haiku 3.5", "Anthropic", supports_vision=True),
    # Google
    ModelDef("gemini/gemini-2.5-pro",       "Gemini 2.5 Pro",       "Google",      supports_vision=True),
    ModelDef("gemini/gemini-2.5-flash",     "Gemini 2.5 Flash",     "Google",      supports_vision=True),
    ModelDef("gemini/gemini-2.5-flash-lite","Gemini 2.5 Flash Lite","Google",      supports_vision=True),
    ModelDef("gemini/gemini-2.0-flash",     "Gemini 2.0 Flash",     "Google",      supports_vision=True),
    # 通义千问
    ModelDef("openai/qwen3-235b-a22b",     "Qwen3 235B",        "阿里云",      supports_vision=True),
    ModelDef("openai/qwen-max",            "通义千问 Max",      "阿里云",      supports_vision=True),
    ModelDef("openai/qwen-plus",           "通义千问 Plus",     "阿里云",      supports_vision=True),
    ModelDef("openai/qwen-turbo",          "通义千问 Turbo",    "阿里云",      supports_vision=False),
    ModelDef("openai/qwen3-8b",            "Qwen3 8B",          "阿里云",      supports_vision=False),
    # MiniMax
    ModelDef("openai/minimax-m1",          "MiniMax M1",        "MiniMax",     supports_vision=True),
    # 智谱 GLM
    ModelDef("openai/glm-4-plus",          "GLM-4 Plus",         "智谱",        supports_vision=True),
    ModelDef("openai/glm-4-flash",         "GLM-4 Flash",        "智谱",        supports_vision=True),
    ModelDef("openai/glm-4-air",           "GLM-4 Air",          "智谱",        supports_vision=False),
    # Moonshot
    ModelDef("openai/moonshot-v1-8k",      "Moonshot v1 8K",     "月之暗面",    supports_vision=False),
    ModelDef("openai/moonshot-v1-32k",     "Moonshot v1 32K",    "月之暗面",    supports_vision=False),
    ModelDef("openai/moonshot-v1-128k",    "Moonshot v1 128K",   "月之暗面",    supports_vision=False),
]


# ── Per-model user config persisted as JSON in bot_config ─────────────────

@dataclass
class ModelConfig:
    model_id: str
    api_key: str = ""        # user-provided key (empty = not configured)
    enabled: bool = True     # user can toggle off


DEFAULT_MODEL_CONFIGS: dict[str, ModelConfig] = {
    m.id: ModelConfig(model_id=m.id)
    for m in BUILTIN_MODELS
}


def load_model_configs(raw: str | None) -> dict[str, ModelConfig]:
    """Parse persisted model_configs JSON into dict."""
    if not raw:
        return dict(DEFAULT_MODEL_CONFIGS)
    try:
        data = json.loads(raw)
        result: dict[str, ModelConfig] = {}
        for item in data:
            mc = ModelConfig(
                model_id=item.get("model_id", ""),
                api_key=item.get("api_key", ""),
                enabled=item.get("enabled", True),
            )
            result[mc.model_id] = mc
        # Fill in missing built-in models
        for m in BUILTIN_MODELS:
            if m.id not in result:
                result[m.id] = ModelConfig(model_id=m.id)
        return result
    except (json.JSONDecodeError, TypeError):
        return dict(DEFAULT_MODEL_CONFIGS)


def dump_model_configs(configs: dict[str, ModelConfig]) -> str:
    """Serialize model configs to JSON string."""
    return json.dumps(
        [{"model_id": mc.model_id, "api_key": mc.api_key, "enabled": mc.enabled}
         for mc in configs.values()],
        ensure_ascii=False,
    )


def get_enabled_models(configs: dict[str, ModelConfig]) -> list[ModelConfig]:
    """Return only models that are enabled AND have an API key set."""
    return [mc for mc in configs.values() if mc.enabled and mc.api_key]


def get_model_def(model_id: str) -> ModelDef | None:
    """Look up a built-in model definition by id."""
    for m in BUILTIN_MODELS:
        if m.id == model_id:
            return m
    return None


def get_api_key_for_model(model_id: str, configs: dict[str, ModelConfig]) -> str:
    """Get the API key for a model from user config, with fallback to default key."""
    mc = configs.get(model_id)
    if mc and mc.api_key:
        return mc.api_key
    # Fallback: DeepSeek models share the default API key
    if model_id.startswith("deepseek/"):
        from config_service import config_service
        default_key = config_service.get_api_key()
        if default_key:
            return default_key
    return ""


def get_default_model(configs: dict[str, ModelConfig]) -> str:
    """Get the default model id, falling back to the first enabled model."""
    from config_service import config_service
    default = config_service.get_default_model()
    if default:
        mc = configs.get(default)
        if mc and mc.enabled and mc.api_key:
            return default
    # Fallback: first enabled model
    enabled = get_enabled_models(configs)
    return enabled[0].model_id if enabled else BUILTIN_MODELS[0].id


def models_for_api(configs: dict[str, ModelConfig]) -> list[dict]:
    """Build the API response for GET /api/models — enrich builtin defs with user config."""
    result = []
    for m in BUILTIN_MODELS:
        mc = configs.get(m.id)
        result.append({
            "id": m.id,
            "name": m.name,
            "provider": m.provider,
            "supports_vision": m.supports_vision,
            "supports_tools": m.supports_tools,
            "api_key": mc.api_key if mc else "",
            "has_key": bool(mc and mc.api_key),
            "enabled": mc.enabled if mc else False,
            "is_default": False,  # filled in below
        })
    # Mark default model
    default_id = get_default_model(configs)
    for r in result:
        if r["id"] == default_id:
            r["is_default"] = True
            break
    return result
