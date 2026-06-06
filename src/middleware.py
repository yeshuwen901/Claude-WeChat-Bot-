"""Rate limiter, allowlist, and auto-reply toggle."""

import time

from config import config


class RateLimiter:
    def __init__(self):
        self._last_reply: dict[str, float] = {}

    def check(self, conv_id: str) -> tuple[bool, float]:
        """Returns (allowed, seconds_until_next)."""
        now = time.time()
        if conv_id in self._last_reply:
            elapsed = now - self._last_reply[conv_id]
            if elapsed < config.auto_reply_cooldown:
                return False, config.auto_reply_cooldown - elapsed
        return True, 0

    def record(self, conv_id: str):
        self._last_reply[conv_id] = time.time()


class AllowList:
    def is_user_allowed(self, remark_name: str, wxid: str) -> bool:
        if config.allowed_users.strip() == "*":
            return True
        allowed = [u.strip() for u in config.allowed_users.split(",")]
        return remark_name in allowed or wxid in allowed

    def is_room_allowed(self, room_name: str, room_id: str) -> bool:
        if config.allowed_rooms.strip() == "*":
            return True
        allowed = [r.strip() for r in config.allowed_rooms.split(",")]
        return room_name in allowed or room_id in allowed


class AutoReply:
    def __init__(self):
        self._enabled = config.auto_reply_enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def toggle(self) -> bool:
        self._enabled = not self._enabled
        return self._enabled

    def set(self, state: bool):
        self._enabled = state


rate_limiter = RateLimiter()
allowlist = AllowList()
auto_reply = AutoReply()
