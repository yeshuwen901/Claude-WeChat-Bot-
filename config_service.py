"""Dynamic config service backed by SQLite with 1-second cache."""

import json
import time
from datetime import datetime, timezone, timedelta

BEIJING_TZ = timezone(timedelta(hours=8))

from crypto_utils import decrypt, encrypt
from database import db


class ConfigService:
    def __init__(self):
        self._cache: dict[str, tuple[float, str]] = {}
        self._ttl = 1.0

    def _get(self, key: str, default: str = "") -> str:
        """Get config value, using 1-second cache to reduce DB reads."""
        now = time.time()
        entry = self._cache.get(key)
        if entry and now - entry[0] < self._ttl:
            return entry[1]
        val = db.get_config(key)
        if val is None:
            val = default
        self._cache[key] = (now, val)
        return val

    def _get_direct(self, key: str, default: str = "") -> str:
        """Get config value directly from DB, bypassing cache.
        Use for atomic read-modify-write operations.
        """
        val = db.get_config(key)
        return val if val is not None else default

    def _set(self, key: str, value: str):
        db.set_config(key, value)
        self._cache[key] = (time.time(), value)

    def invalidate(self):
        self._cache.clear()

    # ── typed accessors ──────────────────────────────────────────────

    def get_api_key(self) -> str:
        """Get API key from DB (decrypted), fall back to env var."""
        val = self._get("api_key", "")
        if val:
            return decrypt(val)
        import os
        return os.getenv("ANTHROPIC_API_KEY", "")

    def set_api_key(self, value: str):
        self._set("api_key", encrypt(value))

    def get_dashscope_api_key(self) -> str:
        """Get DashScope API key from DB (decrypted), fall back to env var."""
        val = self._get("dashscope_api_key", "")
        if val:
            return decrypt(val)
        import os
        return os.getenv("DASHSCOPE_API_KEY", "")

    def set_dashscope_api_key(self, value: str):
        self._set("dashscope_api_key", encrypt(value))

    def get_anthropic_model(self) -> str:
        """Get AI model from DB, fall back to config (env)."""
        from config import config
        val = self._get("anthropic_model", "")
        return val if val else config.anthropic_model

    def is_deep_thinking_enabled(self) -> bool:
        return self._get("deep_thinking", "0") == "1"

    def set_deep_thinking(self, enabled: bool):
        self._set("deep_thinking", "1" if enabled else "0")

    def is_web_search_enabled(self) -> bool:
        return self._get("web_search", "0") == "1"

    def set_web_search(self, enabled: bool):
        self._set("web_search", "1" if enabled else "0")

    def get_sticker_probability(self) -> float:
        try:
            return float(self._get("sticker_probability", "0.3"))
        except ValueError:
            return 0.3

    def set_sticker_probability(self, value: float):
        self._set("sticker_probability", str(value))

    def get_reply_interval(self) -> float:
        try:
            return float(self._get("reply_interval", "0"))
        except ValueError:
            return 0.0

    def set_reply_interval(self, seconds: float):
        self._set("reply_interval", str(seconds))

    def is_memory_mode_enabled(self) -> bool:
        return self._get("memory_mode", "1") == "1"

    def set_memory_mode(self, enabled: bool):
        self._set("memory_mode", "1" if enabled else "0")

    def get_sticker_list(self) -> list[str]:
        raw = self._get("sticker_list", "[]")
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_sticker_list(self, urls: list[str]):
        self._set("sticker_list", json.dumps(urls, ensure_ascii=False))

    def get_rest_time_ranges(self) -> list[dict]:
        raw = self._get("rest_time_ranges", "[]")
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_rest_time_ranges(self, ranges: list[dict]):
        self._set("rest_time_ranges", json.dumps(ranges, ensure_ascii=False))

    def get_scheduled_restart(self) -> str:
        """Returns 'HH:MM' or empty string."""
        return self._get("scheduled_restart", "")

    def set_scheduled_restart(self, time_str: str):
        self._set("scheduled_restart", time_str)

    def is_in_rest_time(self) -> bool:
        """Check if current time falls within any rest time range."""
        ranges = self.get_rest_time_ranges()
        if not ranges:
            return False
        now = datetime.now(BEIJING_TZ)
        current_minutes = now.hour * 60 + now.minute
        current_weekday = now.weekday()  # 0=Mon, 6=Sun
        for r in ranges:
            start = r.get("start", "")  # "HH:MM"
            end = r.get("end", "")      # "HH:MM"
            days = r.get("days", [])    # list of 0-6, empty = every day
            if not start or not end:
                continue
            if days and current_weekday not in days:
                continue
            start_mins = self._hm_to_minutes(start)
            end_mins = self._hm_to_minutes(end)
            if start_mins <= end_mins:
                # Normal range: e.g. 22:00 - 07:00
                if start_mins <= current_minutes < end_mins:
                    return True
            else:
                # Overnight: e.g. 22:00 - 07:00
                if current_minutes >= start_mins or current_minutes < end_mins:
                    return True
        return False

    @staticmethod
    def _hm_to_minutes(hm: str) -> int:
        try:
            h, m = hm.strip().split(":")
            return int(h) * 60 + int(m)
        except (ValueError, AttributeError):
            return 0

    # ── active chat ──────────────────────────────────────────────────

    def is_active_chat_enabled(self) -> bool:
        return self._get("active_chat_enabled", "1") == "1"

    def set_active_chat_enabled(self, enabled: bool):
        self._set("active_chat_enabled", "1" if enabled else "0")

    def get_active_chat_cooldown_minutes(self) -> int:
        try:
            return int(self._get("active_chat_cooldown_minutes", "60"))
        except ValueError:
            return 60

    def set_active_chat_cooldown_minutes(self, minutes: int):
        self._set("active_chat_cooldown_minutes", str(minutes))

    def get_active_chat_max_silent(self) -> int:
        try:
            return int(self._get("active_chat_max_silent", "3"))
        except ValueError:
            return 3

    def set_active_chat_max_silent(self, count: int):
        self._set("active_chat_max_silent", str(count))

    def get_active_chat_idle_minutes(self) -> int:
        try:
            return int(self._get("active_chat_idle_minutes", "15"))
        except ValueError:
            return 15

    def get_scheduled_chat_idle_minutes(self) -> int:
        try:
            return int(self._get("scheduled_chat_idle_minutes", "5"))
        except ValueError:
            return 5

    def is_in_active_chat_allowed_time(self, allowed_ranges_json: str) -> bool:
        """Check if current time is within allowed time ranges for active chat."""
        if not allowed_ranges_json or allowed_ranges_json == "[]":
            return True
        try:
            ranges = json.loads(allowed_ranges_json)
        except (json.JSONDecodeError, TypeError):
            return True
        if not ranges:
            return True
        now = datetime.now(BEIJING_TZ)
        current_minutes = now.hour * 60 + now.minute
        for r in ranges:
            start = r.get("start", "")
            end = r.get("end", "")
            if not start or not end:
                continue
            start_mins = self._hm_to_minutes(start)
            end_mins = self._hm_to_minutes(end)
            if start_mins <= end_mins:
                if start_mins <= current_minutes < end_mins:
                    return True
            else:
                if current_minutes >= start_mins or current_minutes < end_mins:
                    return True
        return False

    # ── bot mood ─────────────────────────────────────────────────────

    def is_bot_mood_enabled(self) -> bool:
        return self._get("bot_mood_enabled", "1") == "1"

    def set_bot_mood_enabled(self, enabled: bool):
        self._set("bot_mood_enabled", "1" if enabled else "0")

    def get_bot_mood(self) -> int:
        try:
            return max(0, min(100, int(self._get("bot_mood_value", "50"))))
        except ValueError:
            return 50

    def set_bot_mood(self, value: int):
        value = max(0, min(100, value))
        self._set("bot_mood_value", str(value))

    def adjust_bot_mood(self, delta: int):
        """Atomically adjust bot mood by delta (clamped 0-100).
        Reads directly from DB to avoid read-modify-write races with cache."""
        current = int(self._get_direct("bot_mood_value", "50"))
        new_value = max(0, min(100, current + delta))
        self._set("bot_mood_value", str(new_value))

    # ── personal stories ─────────────────────────────────────────────

    def is_personal_stories_enabled(self) -> bool:
        return self._get("personal_stories_enabled", "0") == "1"

    def set_personal_stories_enabled(self, enabled: bool):
        self._set("personal_stories_enabled", "1" if enabled else "0")

    def get_personal_stories(self) -> list[dict]:
        raw = self._get("personal_stories", "[]")
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_personal_stories(self, stories: list[dict]):
        self._set("personal_stories", json.dumps(stories, ensure_ascii=False))

    # ── reply length ──────────────────────────────────────────────────

    def get_reply_max_chars(self) -> int | None:
        """Get max reply characters, or None if not configured."""
        val = self._get("reply_max_chars", "")
        if not val or not val.strip():
            return None
        try:
            return int(val)
        except ValueError:
            return None

    # ── proactive sharing ────────────────────────────────────────────

    def is_proactive_sharing_enabled(self) -> bool:
        return self._get("proactive_sharing_enabled", "0") == "1"

    def set_proactive_sharing_enabled(self, enabled: bool):
        self._set("proactive_sharing_enabled", "1" if enabled else "0")

    def get_proactive_sharing_interval_minutes(self) -> int:
        try:
            return int(self._get("proactive_sharing_interval_minutes", "180"))
        except ValueError:
            return 180

    def set_proactive_sharing_interval_minutes(self, minutes: int):
        self._set("proactive_sharing_interval_minutes", str(minutes))

    def get_proactive_sharing_topics(self) -> list[str]:
        raw = self._get("proactive_sharing_topics", "[]")
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_proactive_sharing_topics(self, topics: list[str]):
        self._set("proactive_sharing_topics", json.dumps(topics, ensure_ascii=False))

    # ── bulk ─────────────────────────────────────────────────────────

    def get_all(self) -> dict[str, str]:
        cfg = db.get_all_config()
        for key in ("api_key", "dashscope_api_key"):
            if cfg.get(key):
                cfg[key] = decrypt(cfg[key])
        return cfg

    def update_bulk(self, updates: dict[str, str]):
        for key, value in updates.items():
            if key in ("api_key", "dashscope_api_key"):
                value = encrypt(value)
            self._set(key, value)


config_service = ConfigService()
