"""SQLite database for conversation persistence and bot behavior config."""

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from config import config


class Database:
    def __init__(self):
        db_path = Path(config.data_dir) / "conversations.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._migrate()

    def _migrate(self):
        with self._lock:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    contact_name TEXT DEFAULT '',
                    is_room INTEGER DEFAULT 0,
                    messages_json TEXT DEFAULT '[]',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
            """)
            self.conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_conversations_updated
                ON conversations(updated_at)
            """)

            # Behavior config (key-value)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS bot_config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                )
            """)

            # Active scheduled messages
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS active_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    cron_expression TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
            """)

            # Stickers
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS stickers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL,
                    filename TEXT DEFAULT '',
                    created_at INTEGER NOT NULL
                )
            """)

            # Per-user custom prompts
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS user_prompts (
                    conv_id TEXT PRIMARY KEY,
                    prompt TEXT NOT NULL DEFAULT '',
                    updated_at INTEGER NOT NULL,
                    language_habits TEXT NOT NULL DEFAULT '{}',
                    merged_prompt TEXT NOT NULL DEFAULT '',
                    habits_updated_at INTEGER NOT NULL DEFAULT 0
                )
            """)
            # Add new columns for existing databases (ignore if already present)
            for col, col_def in [
                ("language_habits", "TEXT NOT NULL DEFAULT '{}'"),
                ("merged_prompt", "TEXT NOT NULL DEFAULT ''"),
                ("habits_updated_at", "INTEGER NOT NULL DEFAULT 0"),
            ]:
                try:
                    self.conn.execute(f"ALTER TABLE user_prompts ADD COLUMN {col} {col_def}")
                except sqlite3.OperationalError:
                    pass

# Insert 1 content: intimacy columns + followup_state table
            # Bot 拟人化：user_prompts 新增亲密度字段
            for col, col_def in [
                ("intimacy_score", "INTEGER DEFAULT 10"),
                ("intimacy_updated_at", "INTEGER DEFAULT 0"),
                ("intimacy_tier", "TEXT DEFAULT 'new_friend'"),
            ]:
                try:
                    self.conn.execute(f"ALTER TABLE user_prompts ADD COLUMN {col} {col_def}")
                except sqlite3.OperationalError:
                    pass

            # Bot 拟人化：追问状态表
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS followup_state (
                    conv_id TEXT PRIMARY KEY,
                    consecutive_followups INTEGER DEFAULT 0,
                    last_intent TEXT DEFAULT '',
                    updated_at INTEGER NOT NULL,
                    FOREIGN KEY (conv_id) REFERENCES conversations(id)
                )
            """)

            # Sticker emotion tags
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS sticker_emotions (
                    sticker_id INTEGER NOT NULL,
                    emotion TEXT NOT NULL,
                    PRIMARY KEY (sticker_id, emotion),
                    FOREIGN KEY (sticker_id) REFERENCES stickers(id) ON DELETE CASCADE
                )
            """)

            # AI scheduled chats (F1)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS scheduled_chats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_time TEXT NOT NULL,
                    topic TEXT NOT NULL DEFAULT '',
                    target_type TEXT NOT NULL DEFAULT 'all',
                    target_ids TEXT NOT NULL DEFAULT '[]',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
            """)

            # Active chat state tracking (F2)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS active_chat_state (
                    conv_id TEXT PRIMARY KEY,
                    last_active_at INTEGER NOT NULL DEFAULT 0,
                    silent_count INTEGER NOT NULL DEFAULT 0,
                    last_user_reply_at INTEGER NOT NULL DEFAULT 0
                )
            """)

            # Migrate active_chat_state: add topic continuation columns
            for col, col_def in [
                ("last_topic_summary", "TEXT NOT NULL DEFAULT ''"),
                ("last_topic_at", "INTEGER NOT NULL DEFAULT 0"),
            ]:
                try:
                    self.conn.execute(f"ALTER TABLE active_chat_state ADD COLUMN {col} {col_def}")
                except sqlite3.OperationalError:
                    pass

            # Active chat settings (F2)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS active_chat_settings (
                    conv_id TEXT PRIMARY KEY,
                    trigger_texts TEXT NOT NULL DEFAULT '[]',
                    cooldown_minutes INTEGER NOT NULL DEFAULT 60,
                    allowed_time_ranges TEXT NOT NULL DEFAULT '[]',
                    updated_at INTEGER NOT NULL
                )
            """)
            self.conn.commit()

        # Seed default config values.
        # api_key is NOT seeded from env — config_service
        # falls back to env at read time, and values written via the admin
        # panel are encrypted before storage.
        defaults = {
            "api_key": "",
            "model_configs": "",
            "default_model": "",
            "vision_enabled": "1",
            "reply_interval": "0",
            "sticker_probability": "0.3",
            "memory_mode": "1",
            "sticker_list": "[]",
            "rest_time_ranges": "[]",
            "scheduled_restart": "",
            "deep_thinking": "0",
            "web_search": "0",
            "active_chat_enabled": "1",
            "active_chat_cooldown_minutes": "60",
            "active_chat_max_silent": "3",
            "active_chat_idle_minutes": "15",
            "scheduled_chat_idle_minutes": "5",
            "bot_mood_enabled": "1",
            "bot_mood_value": "50",
            "personal_stories_enabled": "0",
            "personal_stories": "[]",
            "proactive_sharing_enabled": "0",
            "proactive_sharing_interval_minutes": "180",
            "proactive_sharing_topics": "[]",
            "reply_max_chars": "",
            "core_rules": "保持基本礼貌，不骂人不说脏话。\n不涉及政治敏感话题。\n不使用歧视性语言。\n语气友好温暖。",
        }
        with self._lock:
            for key, val in defaults.items():
                self.conn.execute(
                    "INSERT OR IGNORE INTO bot_config (key, value, updated_at) VALUES (?, ?, ?)",
                    (key, val, int(time.time() * 1000)),
                )
            self.conn.commit()

    # ── conversation methods ──────────────────────────────────────────

    def load_messages(self, conv_id: str) -> list[dict]:
        with self._lock:
            row = self.conn.execute(
                "SELECT messages_json FROM conversations WHERE id = ?", (conv_id,)
            ).fetchone()
        if row:
            return json.loads(row["messages_json"])
        return []

    def save_messages(
        self, conv_id: str, messages: list[dict],
        contact_name: str = "", is_room: bool = False
    ):
        now = int(time.time() * 1000)
        with self._lock:
            self.conn.execute(
                """INSERT INTO conversations (id, contact_name, is_room, messages_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                   messages_json = excluded.messages_json,
                   contact_name = excluded.contact_name,
                   updated_at = excluded.updated_at""",
                (conv_id, contact_name, 1 if is_room else 0,
                 json.dumps(messages, ensure_ascii=False), now, now),
            )
            self.conn.commit()

    def clear_conversation(self, conv_id: str):
        with self._lock:
            self.conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
            self.conn.commit()

    def get_recent_messages(self, conv_id: str, count: int = 6) -> list[dict]:
        """Get the last N messages for a contact, for context-aware active chat."""
        msgs = self.load_messages(conv_id)
        return msgs[-count:] if len(msgs) > count else msgs

    # ── bot config methods ────────────────────────────────────────────

    def _set_config_if_missing(self, key: str, default_value: str):
        with self._lock:
            exists = self.conn.execute(
                "SELECT 1 FROM bot_config WHERE key = ?", (key,)
            ).fetchone()
            if not exists:
                now = int(time.time() * 1000)
                self.conn.execute(
                    "INSERT INTO bot_config (key, value, updated_at) VALUES (?, ?, ?)",
                    (key, default_value, now),
                )
                self.conn.commit()

    def get_config(self, key: str) -> str | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT value FROM bot_config WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else None

    def set_config(self, key: str, value: str):
        now = int(time.time() * 1000)
        with self._lock:
            self.conn.execute(
                """INSERT INTO bot_config (key, value, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value, updated_at = excluded.updated_at""",
                (key, value, now),
            )
            self.conn.commit()

    def get_all_config(self) -> dict:
        with self._lock:
            rows = self.conn.execute(
                "SELECT key, value FROM bot_config"
            ).fetchall()
        return {r["key"]: r["value"] for r in rows}

    # ── active messages CRUD ──────────────────────────────────────────

    def list_active_messages(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM active_messages ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_active_message(self, msg_id: int) -> dict | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM active_messages WHERE id = ?", (msg_id,)
            ).fetchone()
        return dict(row) if row else None

    def create_active_message(self, content: str, cron_expression: str,
                              enabled: bool = True) -> int:
        now = int(time.time() * 1000)
        with self._lock:
            cur = self.conn.execute(
                """INSERT INTO active_messages (content, cron_expression, enabled, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (content, cron_expression, 1 if enabled else 0, now, now),
            )
            self.conn.commit()
        return cur.lastrowid

    def update_active_message(self, msg_id: int, content: str | None = None,
                              cron_expression: str | None = None,
                              enabled: bool | None = None):
        now = int(time.time() * 1000)
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM active_messages WHERE id = ?", (msg_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"Active message {msg_id} not found")
            new_content = content if content is not None else row["content"]
            new_cron = cron_expression if cron_expression is not None else row["cron_expression"]
            new_enabled = (1 if enabled else 0) if enabled is not None else row["enabled"]
            self.conn.execute(
                """UPDATE active_messages SET content=?, cron_expression=?, enabled=?, updated_at=?
                   WHERE id=?""",
                (new_content, new_cron, new_enabled, now, msg_id),
            )
            self.conn.commit()

    def delete_active_message(self, msg_id: int):
        with self._lock:
            self.conn.execute("DELETE FROM active_messages WHERE id = ?", (msg_id,))
            self.conn.commit()

    def get_enabled_active_messages(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM active_messages WHERE enabled = 1 ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    # ── stickers CRUD ─────────────────────────────────────────────────

    def add_sticker(self, url: str, filename: str = "") -> int:
        now = int(time.time() * 1000)
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO stickers (url, filename, created_at) VALUES (?, ?, ?)",
                (url, filename, now),
            )
            self.conn.commit()
        return cur.lastrowid

    def delete_sticker(self, sticker_id: int):
        with self._lock:
            self.conn.execute("DELETE FROM stickers WHERE id = ?", (sticker_id,))
            self.conn.commit()

    def list_stickers(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM stickers ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    # ── user prompts ──────────────────────────────────────────────────

    def get_user_prompt(self, conv_id: str) -> str | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT prompt FROM user_prompts WHERE conv_id = ?", (conv_id,)
            ).fetchone()
        return row["prompt"] if row else None

    def set_user_prompt(self, conv_id: str, prompt: str):
        now = int(time.time() * 1000)
        with self._lock:
            self.conn.execute(
                """INSERT INTO user_prompts (conv_id, prompt, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(conv_id) DO UPDATE SET
                   prompt = excluded.prompt, updated_at = excluded.updated_at""",
                (conv_id, prompt, now),
            )
            self.conn.commit()

    def delete_user_prompt(self, conv_id: str):
        with self._lock:
            self.conn.execute("DELETE FROM user_prompts WHERE conv_id = ?", (conv_id,))
            self.conn.commit()

    def list_user_prompts(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM user_prompts ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    # ── language habits & merged prompts ──────────────────────────────

    def get_user_prompt_full(self, conv_id: str) -> dict | None:
        """Get full prompt record including habits and merged prompt."""
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM user_prompts WHERE conv_id = ?", (conv_id,)
            ).fetchone()
        return dict(row) if row else None

    def set_language_habits(self, conv_id: str, habits: dict):
        now = int(time.time() * 1000)
        with self._lock:
            self.conn.execute(
                """INSERT INTO user_prompts (conv_id, prompt, updated_at, language_habits, habits_updated_at)
                   VALUES (?, '', ?, ?, ?)
                   ON CONFLICT(conv_id) DO UPDATE SET
                   language_habits = excluded.language_habits,
                   habits_updated_at = excluded.habits_updated_at""",
                (conv_id, now, json.dumps(habits, ensure_ascii=False), now),
            )
            self.conn.commit()

    def set_merged_prompt(self, conv_id: str, merged_prompt: str):
        now = int(time.time() * 1000)
        with self._lock:
            self.conn.execute(
                """INSERT INTO user_prompts (conv_id, prompt, updated_at, merged_prompt)
                   VALUES (?, '', ?, ?)
                   ON CONFLICT(conv_id) DO UPDATE SET
                   merged_prompt = excluded.merged_prompt,
                   updated_at = excluded.updated_at""",
                (conv_id, now, merged_prompt),
            )
            self.conn.commit()

    def get_users_with_prompts(self) -> list[str]:
        """Return conv_ids that have a user-defined prompt."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT conv_id FROM user_prompts WHERE prompt != ''"
            ).fetchall()
        return [r["conv_id"] for r in rows]

    # ── sticker emotions ──────────────────────────────────────────────

    def set_sticker_emotions(self, sticker_id: int, emotions: list[str]):
        with self._lock:
            self.conn.execute(
                "DELETE FROM sticker_emotions WHERE sticker_id = ?", (sticker_id,)
            )
            now = int(time.time() * 1000)
            for em in emotions:
                self.conn.execute(
                    "INSERT OR IGNORE INTO sticker_emotions (sticker_id, emotion) VALUES (?, ?)",
                    (sticker_id, em.strip().lower()),
                )
            self.conn.commit()

    def get_sticker_emotions(self, sticker_id: int) -> list[str]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT emotion FROM sticker_emotions WHERE sticker_id = ?", (sticker_id,)
            ).fetchall()
        return [r["emotion"] for r in rows]

    def get_stickers_by_emotion(self, emotion: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT s.* FROM stickers s
                   INNER JOIN sticker_emotions e ON s.id = e.sticker_id
                   WHERE e.emotion = ? ORDER BY s.id""",
                (emotion.strip().lower(),),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_all_stickers_with_emotions(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute("SELECT * FROM stickers ORDER BY id").fetchall()
            sticker_ids = [r["id"] for r in rows]
            # Load all emotions in one query to avoid reentrant lock
            all_emotions = {}
            if sticker_ids:
                placeholders = ",".join("?" * len(sticker_ids))
                emotion_rows = self.conn.execute(
                    f"SELECT sticker_id, emotion FROM sticker_emotions WHERE sticker_id IN ({placeholders})",
                    sticker_ids,
                ).fetchall()
                for er in emotion_rows:
                    all_emotions.setdefault(er["sticker_id"], []).append(er["emotion"])
        result = []
        for r in rows:
            d = dict(r)
            d["emotions"] = all_emotions.get(r["id"], [])
            result.append(d)
        return result

    # ── utility ───────────────────────────────────────────────────────

    def get_all_contacts(self) -> list[str]:
        """Return distinct contact IDs for broadcast."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT DISTINCT id FROM conversations WHERE is_room = 0"
            ).fetchall()
        return [r["id"] for r in rows]

    def get_all_conversation_ids(self) -> list[str]:
        """Return all conversation IDs (users + groups) for memory scoring."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT DISTINCT id FROM conversations"
            ).fetchall()
        return [r["id"] for r in rows]

    # ── scheduled chats (F1) ─────────────────────────────────────────

    def list_scheduled_chats(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM scheduled_chats ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_enabled_scheduled_chats(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM scheduled_chats WHERE enabled = 1 ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    def create_scheduled_chat(self, chat_time: str, topic: str = "",
                               target_type: str = "all",
                               target_ids: list[str] | None = None) -> int:
        now = int(time.time() * 1000)
        ids_json = json.dumps(target_ids or [], ensure_ascii=False)
        with self._lock:
            cur = self.conn.execute(
                """INSERT INTO scheduled_chats
                   (chat_time, topic, target_type, target_ids, enabled, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 1, ?, ?)""",
                (chat_time, topic, target_type, ids_json, now, now),
            )
            self.conn.commit()
        return cur.lastrowid

    def update_scheduled_chat(self, chat_id: int, **kwargs):
        now = int(time.time() * 1000)
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM scheduled_chats WHERE id = ?", (chat_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"Scheduled chat {chat_id} not found")
            d = dict(row)
            for k in ("chat_time", "topic", "target_type", "target_ids", "enabled"):
                if k in kwargs:
                    d[k] = kwargs[k]
            self.conn.execute(
                """UPDATE scheduled_chats SET
                   chat_time=?, topic=?, target_type=?, target_ids=?, enabled=?, updated_at=?
                   WHERE id=?""",
                (d["chat_time"], d["topic"], d["target_type"],
                 d["target_ids"] if isinstance(d["target_ids"], str) else json.dumps(d["target_ids"] or [], ensure_ascii=False),
                 d["enabled"], now, chat_id),
            )
            self.conn.commit()

    def delete_scheduled_chat(self, chat_id: int):
        with self._lock:
            self.conn.execute("DELETE FROM scheduled_chats WHERE id = ?", (chat_id,))
            self.conn.commit()

    # ── active chat state (F2) ────────────────────────────────────────

    def get_active_chat_state(self, conv_id: str) -> dict:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM active_chat_state WHERE conv_id = ?", (conv_id,)
            ).fetchone()
        return dict(row) if row else {
            "conv_id": conv_id, "last_active_at": 0,
            "silent_count": 0, "last_user_reply_at": 0,
            "last_topic_summary": "", "last_topic_at": 0,
        }

    def update_active_chat_state(self, conv_id: str, **kwargs):
        """Atomically update specified fields in active_chat_state.
        Only the fields named in kwargs are modified; others keep their current value.
        """
        now = int(time.time() * 1000)
        # Build defaults for INSERT — all fields must be present
        defaults = {
            "last_active_at": now,
            "silent_count": 0,
            "last_user_reply_at": 0,
            "last_topic_summary": "",
            "last_topic_at": 0,
        }
        with self._lock:
            self.conn.execute(
                """INSERT INTO active_chat_state
                   (conv_id, last_active_at, silent_count, last_user_reply_at,
                    last_topic_summary, last_topic_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(conv_id) DO NOTHING""",
                (conv_id,
                 kwargs.get("last_active_at", defaults["last_active_at"]),
                 kwargs.get("silent_count", defaults["silent_count"]),
                 kwargs.get("last_user_reply_at", defaults["last_user_reply_at"]),
                 kwargs.get("last_topic_summary", defaults["last_topic_summary"]),
                 kwargs.get("last_topic_at", defaults["last_topic_at"])),
            )
            if kwargs:
                set_clauses = []
                values = []
                for k in kwargs:
                    set_clauses.append(f"{k}=?")
                    values.append(kwargs[k])
                values.append(conv_id)
                self.conn.execute(
                    f"UPDATE active_chat_state SET {', '.join(set_clauses)} WHERE conv_id=?",
                    values,
                )
            self.conn.commit()

    def record_user_reply(self, conv_id: str):
        now = int(time.time() * 1000)
        with self._lock:
            self.conn.execute(
                """INSERT INTO active_chat_state
                   (conv_id, last_active_at, silent_count, last_user_reply_at,
                    last_topic_summary, last_topic_at)
                   VALUES (?, 0, 0, ?, '', 0)
                   ON CONFLICT(conv_id) DO UPDATE SET
                   silent_count=0, last_user_reply_at=excluded.last_user_reply_at""",
                (conv_id, now),
            )
            self.conn.commit()

    def update_topic_summary(self, conv_id: str, summary: str):
        now = int(time.time() * 1000)
        with self._lock:
            current = self.get_active_chat_state(conv_id)
            self.conn.execute(
                """INSERT INTO active_chat_state
                   (conv_id, last_active_at, silent_count, last_user_reply_at,
                    last_topic_summary, last_topic_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(conv_id) DO UPDATE SET
                   last_topic_summary=excluded.last_topic_summary,
                   last_topic_at=excluded.last_topic_at""",
                (conv_id, current["last_active_at"], current["silent_count"],
                 current["last_user_reply_at"], summary, now),
            )
            self.conn.commit()

    def increment_silent_count(self, conv_id: str, delta: int = 1, last_active_at: int = 0):
        """Atomically increment silent_count to avoid lost updates across threads."""
        with self._lock:
            self.conn.execute(
                """INSERT INTO active_chat_state
                   (conv_id, last_active_at, silent_count, last_user_reply_at,
                    last_topic_summary, last_topic_at)
                   VALUES (?, ?, ?, 0, '', 0)
                   ON CONFLICT(conv_id) DO UPDATE SET
                   silent_count=silent_count + excluded.silent_count,
                   last_active_at=MAX(last_active_at, excluded.last_active_at)""",
                (conv_id, last_active_at, delta),
            )
            self.conn.commit()

    # ── active chat settings (F2) ─────────────────────────────────────

    def get_active_chat_settings(self, conv_id: str) -> dict:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM active_chat_settings WHERE conv_id = ?", (conv_id,)
            ).fetchone()
        return dict(row) if row else {
            "conv_id": conv_id, "trigger_texts": "[]",
            "cooldown_minutes": 60, "allowed_time_ranges": "[]", "updated_at": 0,
        }

    def set_active_chat_settings(self, conv_id: str, trigger_texts: str = "[]",
                                  cooldown_minutes: int = 60,
                                  allowed_time_ranges: str = "[]"):
        now = int(time.time() * 1000)
        with self._lock:
            self.conn.execute(
                """INSERT INTO active_chat_settings
                   (conv_id, trigger_texts, cooldown_minutes, allowed_time_ranges, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(conv_id) DO UPDATE SET
                   trigger_texts=excluded.trigger_texts,
                   cooldown_minutes=excluded.cooldown_minutes,
                   allowed_time_ranges=excluded.allowed_time_ranges,
                   updated_at=excluded.updated_at""",
                (conv_id, trigger_texts, cooldown_minutes, allowed_time_ranges, now),
            )
            self.conn.commit()

    def get_all_active_chat_settings(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM active_chat_settings ORDER BY conv_id"
            ).fetchall()
        return [dict(r) for r in rows]

    # ======== intimacy methods ========

    def get_intimacy(self, conv_id: str) -> dict:
        """Get intimacy score for a conversation. Returns default for new users."""
        with self._lock:
            row = self.conn.execute(
                "SELECT intimacy_score, intimacy_updated_at, intimacy_tier FROM user_prompts WHERE conv_id = ?",
                (conv_id,)
            ).fetchone()
        if row:
            return {"conv_id": conv_id, "intimacy_score": row["intimacy_score"],
                    "intimacy_updated_at": row["intimacy_updated_at"],
                    "intimacy_tier": row["intimacy_tier"] or "new_friend"}
        return {"conv_id": conv_id, "intimacy_score": 10, "intimacy_updated_at": 0,
                "intimacy_tier": "new_friend"}

    def save_intimacy(self, conv_id: str, score: int, tier: str = "new_friend"):
        """Upsert intimacy score and tier for a conversation."""
        now = int(time.time() * 1000)
        with self._lock:
            self.conn.execute(
                """INSERT INTO user_prompts (conv_id, prompt, updated_at, intimacy_score, intimacy_updated_at, intimacy_tier)
                   VALUES (?, '', ?, ?, ?, ?)
                   ON CONFLICT(conv_id) DO UPDATE SET
                   intimacy_score = excluded.intimacy_score,
                   intimacy_updated_at = excluded.intimacy_updated_at,
                   intimacy_tier = excluded.intimacy_tier""",
                (conv_id, now, score, now, tier)
            )
            self.conn.commit()

    def get_all_conv_ids(self) -> list[str]:
        """Get all conversation IDs for batch intimacy scoring.

        Delegates to get_all_conversation_ids() to avoid duplication.
        """
        return self.get_all_conversation_ids()

    def get_all_intimacy_scores(self, conv_ids: list[str]) -> dict[str, dict]:
        """Batch-get intimacy scores for a list of conv_ids. Returns dict keyed by conv_id."""
        result = {}
        if not conv_ids:
            return result
        with self._lock:
            placeholders = ",".join("?" for _ in conv_ids)
            rows = self.conn.execute(
                f"SELECT conv_id, intimacy_score, intimacy_updated_at, intimacy_tier "
                f"FROM user_prompts WHERE conv_id IN ({placeholders})",
                conv_ids
            ).fetchall()
        for row in rows:
            result[row["conv_id"]] = {
                "intimacy_score": row["intimacy_score"],
                "intimacy_updated_at": row["intimacy_updated_at"],
                "intimacy_tier": row["intimacy_tier"] or "new_friend",
            }
        # Fill defaults for conv_ids without a user_prompts row
        for cid in conv_ids:
            if cid not in result:
                result[cid] = {"intimacy_score": 10, "intimacy_updated_at": 0, "intimacy_tier": "new_friend"}
        return result

    def get_message_stats(self, conv_id: str) -> dict:
        """Get message statistics for intimacy calculation (last 30 days).

        Returns stats filtered to messages within the last 30 days, plus
        bot_question_count and media_count for depth scoring.
        """
        thirty_days_ago = int((time.time() - 30 * 86400) * 1000)
        with self._lock:
            rows = self.conn.execute(
                """SELECT messages_json FROM conversations
                   WHERE id = ? AND updated_at >= ?
                   ORDER BY updated_at DESC""",
                (conv_id, thirty_days_ago)
            ).fetchall()
        user_msgs = []
        bot_question_count = 0
        media_count = 0
        for r in rows:
            msgs = json.loads(r["messages_json"])
            for m in msgs:
                # P2-1: filter individual messages by 30-day window
                msg_ts = m.get("timestamp", 0)
                if isinstance(msg_ts, (int, float)) and msg_ts > 0 and msg_ts < thirty_days_ago:
                    continue
                role = m.get("role", "")
                content = str(m.get("content", ""))
                if role == "user":
                    user_msgs.append(content)
                    # Count media messages (images, voice, etc.)
                    if "[User sent an image]" in content or "[image]" in content:
                        media_count += 1
                elif role == "assistant":
                    # Count bot replies ending with a question mark
                    stripped = content.strip()
                    if stripped and stripped[-1] in ("？", "?"):
                        bot_question_count += 1
        total_len = sum(len(msg) for msg in user_msgs)
        return {
            "user_message_count": len(user_msgs),
            "total_user_length": total_len,
            "avg_message_length": total_len / len(user_msgs) if user_msgs else 0,
            "bot_question_count": bot_question_count,
            "media_count": media_count,
        }

    # ======== followup state methods ========

    def get_followup_state(self, conv_id: str) -> dict:
        """Get followup state for a conversation."""
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM followup_state WHERE conv_id = ?", (conv_id,)
            ).fetchone()
        if row:
            return dict(row)
        return {"conv_id": conv_id, "consecutive_followups": 0, "last_intent": "", "updated_at": 0}

    def upsert_followup_state(self, state: dict):
        """Insert or update followup state."""
        now = int(time.time() * 1000)
        with self._lock:
            self.conn.execute(
                """INSERT INTO followup_state (conv_id, consecutive_followups, last_intent, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(conv_id) DO UPDATE SET
                   consecutive_followups = excluded.consecutive_followups,
                   last_intent = excluded.last_intent,
                   updated_at = excluded.updated_at""",
                (state["conv_id"], state.get("consecutive_followups", 0),
                 state.get("last_intent", ""), now)
            )
            self.conn.commit()

    def close(self):
        self.conn.close()


db = Database()
