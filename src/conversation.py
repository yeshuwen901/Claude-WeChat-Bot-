"""Conversation context management."""

import time

from config import config
from database import db


class ConversationManager:
    def __init__(self):
        self._pending: dict[str, bool] = {}

    def get_messages(self, conv_id: str) -> list[dict]:
        messages = db.load_messages(conv_id)
        trimmed = self._trim(messages)
        if len(trimmed) != len(messages):
            db.save_messages(conv_id, trimmed)
        return trimmed

    def add_user_message(
        self, conv_id: str, text: str,
        contact_name: str = "", is_room: bool = False
    ):
        messages = self.get_messages(conv_id)
        messages.append({"role": "user", "content": text})
        db.save_messages(conv_id, messages, contact_name=contact_name, is_room=is_room)

    def add_assistant_message(self, conv_id: str, text: str):
        messages = self.get_messages(conv_id)
        messages.append({"role": "assistant", "content": text})
        db.save_messages(conv_id, messages)

    def clear(self, conv_id: str):
        db.clear_conversation(conv_id)

    def is_pending(self, conv_id: str) -> bool:
        return self._pending.get(conv_id, False)

    def set_pending(self, conv_id: str, pending: bool):
        self._pending[conv_id] = pending

    def _trim(self, messages: list[dict]) -> list[dict]:
        if len(messages) <= config.conversation_max_turns * 2:
            return messages

        # Keep system-like first messages and trim from the middle
        # Always keep pairs complete: user + assistant
        max_messages = config.conversation_max_turns * 2
        return messages[-max_messages:]


conversations = ConversationManager()
