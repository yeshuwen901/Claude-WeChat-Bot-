"""WeChat bot using Tencent ilink official API."""

import json
import logging
import os
import random
import sys
import threading
import time

from ai_client import ai_client
from config import config
from config_service import config_service
from conversation import conversations
from database import db
from middleware import allowlist, auto_reply, rate_limiter
from intimacy_engine import (
    followup_max_rounds, followup_directive,
    detect_followup
)
from url_fetcher import extract_urls, fetch_and_extract
from voice import download_image, download_voice, send_sticker, transcribe
from wechat_api import (
    fetch_qr_code,
    wait_for_login,
    notify_start,
    notify_stop,
    get_updates,
    send_message,
)

logger = logging.getLogger(__name__)

ACCOUNT_FILE = os.path.join(config.data_dir, "wechat_account.json")
STICKER_DIR = os.path.join(config.data_dir, "stickers")

# Emotion keyword matching (order matters: stronger emotions first)
EMOTION_KEYWORDS: dict[str, list[str]] = {
    "happy":   ["哈哈", "开心", "高兴", "太好了", "太棒了", "真棒", "nice", "great", "😊", "😂", "😄",
                "嘿嘿", "不错哦", "真厉害", "好厉害", "厉害啊", "妙啊", "好玩", "有趣", "有意思", "笑死",
                "绝了", "恭喜", "祝贺", "太牛了", "优秀", "愉快", "快乐", "爽"],
    "love":    ["爱你", "喜欢你", "想你", "我爱你", "❤", "love you", "亲爱的", "抱抱你", "么么哒", "比心",
                "好喜欢你", "真喜欢你", "最喜欢你"],
    "surprised": ["哇塞", "天哪", "不会吧", "真的假的", "震惊", "wow", "😮", "😲", "竟然",
                  "不可思议", "难以置信", "我的天", "我天", "什么情况", "怎么做到的"],
    "sad":     ["难过", "伤心", "遗憾", "可惜了", "sorry", "😢", "😭", "对不起", "很抱歉",
                "唉", "好难过", "心疼", "难受", "挺遗憾"],
    "angry":   ["生气", "太过分了", "可恶", "讨厌", "烦死了", "😠", "😡",
                "气死", "怒了", "恶心", "无耻", "不能忍", "忍不了"],
}


def _detect_emotion(text: str) -> str:
    """Detect emotional tone from reply text using keyword matching."""
    text_lower = text.lower()
    for emotion, keywords in EMOTION_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text_lower:
                return emotion
    return "neutral"


def _list_image_files(root: str) -> list[str]:
    """List image files in a directory."""
    if not os.path.isdir(root):
        return []
    return [
        os.path.join(root, f) for f in os.listdir(root)
        if f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp'))
    ]


def _select_sticker(emotion: str, reply_text: str = "") -> str | None:
    """Pick a sticker from the emotion folder.

    Tries to match image filenames (without extension) against the reply text.
    Longest match wins. Falls back to random if no match.
    If the emotion folder is empty, scans all emotion dirs.
    """
    files = _list_image_files(os.path.join(STICKER_DIR, emotion))

    if not files:
        logger.info(f"No stickers in {emotion}/, scanning all dirs")
        if os.path.isdir(STICKER_DIR):
            for d in sorted(os.listdir(STICKER_DIR)):
                files.extend(_list_image_files(os.path.join(STICKER_DIR, d)))

    if not files:
        return None

    if reply_text:
        matches: list[tuple[str, int]] = []
        for f in files:
            name = os.path.splitext(os.path.basename(f))[0]
            if name and name in reply_text:
                matches.append((f, len(name)))
        if matches:
            matches.sort(key=lambda x: x[1], reverse=True)
            best = matches[0][0]
            logger.info(f"Keyword-matched sticker: {os.path.basename(best)} (emotion={emotion})")
            return best

    return random.choice(files)


def _detect_emotion_with_ai(text: str) -> str:
    """Detect emotion from bot reply: local keywords first, then DeepSeek AI.

    1. Fast path: local keyword matching
    2. If neutral, ask DeepSeek to classify the emotional tone
    """
    # Fast path: local keyword match
    emotion = _detect_emotion(text)
    if emotion != "neutral":
        return emotion

    # Use DeepSeek AI to understand context, sarcasm, nuance
    try:
        ai_emotion = ai_client.classify_emotion(text)
        if ai_emotion in EMOTION_KEYWORDS:
            logger.info(f"Emotion from AI: {ai_emotion} (text={text[:50]})")
            return ai_emotion
    except Exception as e:
        logger.warning(f"AI emotion classification failed: {e}")

    return "neutral"


def save_account(token: str, account_id: str, base_url: str | None, user_id: str | None):
    """Save WeChat credentials with encrypted token."""
    from crypto_utils import encrypt
    data = {
        "token": encrypt(token),
        "account_id": account_id,
        "base_url": base_url or "",
        "user_id": user_id or "",
        "v": 2,  # format version: 1=plaintext, 2=encrypted
    }
    with open(ACCOUNT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_account() -> dict | None:
    """Load WeChat credentials, transparently upgrading v1 (plaintext) to v2 (encrypted)."""
    try:
        with open(ACCOUNT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if data.get("v", 1) >= 2 and data.get("token"):
        from crypto_utils import decrypt
        try:
            data["token"] = decrypt(data["token"])
        except Exception:
            logger.warning("Failed to decrypt account token — key file may have been rotated")
            return None
    else:
        # v1 plaintext — auto-upgrade to encrypted
        logger.info("Upgrading wechat_account.json to encrypted storage")
        save_account(
            data.get("token", ""),
            data.get("account_id", ""),
            data.get("base_url", ""),
            data.get("user_id", ""),
        )
    return data


class WeChatBot:
    def __init__(self):
        self.bot = None
        self._running = False
        self._token: str = ""
        self._account_id: str = ""
        self._base_url: str = ""
        self._get_updates_buf: str = ""
        self._recent_msgs: dict[str, float] = {}  # msg_hash -> timestamp for dedup
        self._sticker_probability = config_service.get_sticker_probability()

    def start(self):
        """Compatibility wrapper — use start_async_login() for new flow."""
        start_async_login()
        # Keep the calling thread alive while login/polling runs
        while True:
            state = get_login_state()
            if state["status"] == "failed":
                print(f"\nLogin failed: {state['error']}")
                sys.exit(1)
            if state["status"] == "idle":
                break
            time.sleep(1)

    def _poll_loop(self):
        """Message polling loop (after login is complete)."""
        self._running = True
        self._get_updates_buf = ""
        consecutive_failures = 0
        max_failures = 3

        print(">>> Bot is running. Waiting for messages...\n")

        while self._running:
            try:
                resp = get_updates(
                    self._token, self._get_updates_buf, timeout=35
                )

                ret = resp.get("ret", 0)
                errcode = resp.get("errcode", 0)

                if (ret != 0 and ret is not None) or (errcode != 0 and errcode is not None):
                    # Session expired
                    if errcode == -14 or ret == -14:
                        logger.error(f"Session expired (errcode={errcode}), re-login required")
                        print("\n>>> Session expired. Please re-login.")
                        os.remove(ACCOUNT_FILE) if os.path.exists(ACCOUNT_FILE) else None
                        self._restart_login()
                        continue

                    consecutive_failures += 1
                    logger.error(
                        f"getUpdates error: ret={ret} errcode={errcode} "
                        f"({consecutive_failures}/{max_failures})"
                    )
                    if consecutive_failures >= max_failures:
                        logger.error("Too many failures, backing off 30s")
                        time.sleep(30)
                        consecutive_failures = 0
                    else:
                        time.sleep(2)
                    continue

                consecutive_failures = 0

                # Save sync buffer
                buf = resp.get("get_updates_buf", "")
                if buf:
                    self._get_updates_buf = buf

                # Process messages
                msgs = resp.get("msgs", [])
                for msg in msgs:
                    self._process_message(msg)

            except KeyboardInterrupt:
                break
            except Exception as e:
                consecutive_failures += 1
                logger.error(f"Poll error ({consecutive_failures}/{max_failures}): {e}")
                if consecutive_failures >= max_failures:
                    time.sleep(30)
                    consecutive_failures = 0
                else:
                    time.sleep(2)

        # Step 5: Cleanup
        try:
            notify_stop(self._token, self._base_url)
        except Exception as e:
            logger.debug(f"notify_stop failed during cleanup: {e}")

    def _restart_login(self):
        """Re-login after session expiry."""
        _set_login_state(status="fetching_qr", qrcode_url="", qrcode="", error="")
        try:
            notify_stop(self._token, self._base_url)
        except Exception as e:
            logger.debug(f"notify_stop failed during restart: {e}")
        if os.path.exists(ACCOUNT_FILE):
            os.remove(ACCOUNT_FILE)
        self._token = ""
        self._get_updates_buf = ""
        print("\n>>> Re-login required. Fetching new QR code...")
        qr = fetch_qr_code()
        print(f"\n{'='*60}")
        print(f"  SCAN WITH WECHAT:")
        print(f"  {qr.qrcode_url}")
        print(f"{'='*60}\n")

        _set_login_state(status="waiting_scan", qrcode_url=qr.qrcode_url or "", qrcode=qr.qrcode or "")

        import webbrowser
        webbrowser.open(qr.qrcode_url)
        result = wait_for_login(qr)
        if result.connected:
            self._token = result.bot_token or ""
            self._account_id = result.account_id or ""
            self._base_url = result.base_url or ""
            save_account(self._token, self._account_id, self._base_url, result.user_id)
            notify_start(self._token, self._base_url)
            _set_login_state(status="connected", account_id=self._account_id)
            print(">>> Re-login successful!")
        else:
            _set_login_state(status="failed", error=result.message or "Re-login failed")
            logger.error(f"Re-login failed: {result.message}, shutting down")
            try:
                notify_stop(self._token, self._base_url)
            except Exception:
                pass
            import os as _os
            _os._exit(1)

    def _process_message(self, msg: dict):
        try:
            from_user_id = msg.get("from_user_id", "")
            to_user_id = msg.get("to_user_id", "")
            context_token = msg.get("context_token", "")
            group_id = msg.get("group_id", "")

            # Extract text and image from item_list
            items = msg.get("item_list", [])
            text = ""
            image_item = None
            for item in items:
                item_type = item.get("type", 0)
                if item_type == 1:  # TEXT
                    text = item.get("text_item", {}).get("text", "")
                    break
                elif item_type == 2:  # IMAGE
                    image_item = item.get("image_item", {})
                    if image_item:
                        break
                elif item_type == 3:  # VOICE
                    voice_item = item.get("voice_item", {})
                    text = voice_item.get("text", "")
                    # STT fallback: if server didn't transcribe, do it locally
                    if not text and voice_item:
                        logger.info("Voice message without server text, running local STT...")
                        try:
                            audio_data = download_voice(voice_item)
                            if audio_data:
                                text = transcribe(audio_data)
                                if text:
                                    logger.info(f"Local STT result: {text[:80]}")
                        except Exception as e:
                            logger.warning(f"Local STT failed: {e}")
                    if text:
                        break

            if not text and not image_item:
                return

            # Route to image handler
            if image_item:
                # Dedup images same as text: skip duplicates within 8-second window
                filekey = image_item.get("filekey", "") or image_item.get("file_id", "") or ""
                if filekey:
                    img_hash = f"{from_user_id}:img:{filekey}"
                    now = time.time()
                    if now - self._recent_msgs.get(img_hash, 0) < 8:
                        logger.debug(f"Image dedup skipped")
                        return
                    self._recent_msgs[img_hash] = now
                self._handle_image_message(
                    msg, image_item, from_user_id, group_id, context_token
                )
                return

            # Dedup: skip duplicate messages within 8-second window
            msg_hash = f"{from_user_id}:{text}"
            now = time.time()
            last_seen = self._recent_msgs.get(msg_hash, 0)
            if now - last_seen < 8:
                logger.debug(f"Dedup skipped: {text[:30]}")
                return
            self._recent_msgs[msg_hash] = now
            # Cleanup old entries periodically
            if len(self._recent_msgs) > 500:
                cutoff = now - 30
                self._recent_msgs = {
                    k: v for k, v in self._recent_msgs.items()
                    if v > cutoff
                }

            is_room = bool(group_id)
            conv_id = group_id if is_room else from_user_id
            sender_name = from_user_id

            logger.info(
                f"Message: from={from_user_id} room={is_room} "
                f"text={text[:50]}"
            )

            # Record user reply for active chat tracking
            if not is_room:
                db.record_user_reply(conv_id)

            # Bot commands (direct messages only)
            if not is_room:
                if self._handle_command(text, conv_id, context_token):
                    return

            # Auto-reply check
            if not auto_reply.enabled:
                return

            # Allowlist
            if not is_room:
                if not allowlist.is_user_allowed(from_user_id, from_user_id):
                    return
            else:
                if not allowlist.is_room_allowed(group_id, group_id):
                    return

            # Rate limiting
            allowed, wait = rate_limiter.check(conv_id)
            if not allowed:
                logger.debug(f"Rate limited: {conv_id}")
                return

            # Pending check
            if conversations.is_pending(conv_id):
                return

            # Rest time check
            if config_service.is_in_rest_time():
                reply_to = group_id if is_room else from_user_id
                self._send_reply(
                    reply_to,
                    "我正在休息中，请稍后再试。",
                    context_token,
                )
                return

            # URL content fetching: extract URLs and prepend content
            urls = extract_urls(text)
            for url in urls:
                try:
                    content = fetch_and_extract(url)
                    if content:
                        text = f"[URL content from {url}]:\n{content}\n\n[User message]:\n{text}"
                        logger.info(f"Fetched URL content: {url} ({len(content)} chars)")
                except Exception as e:
                    logger.warning(f"URL fetch skipped for {url}: {e}")

            rate_limiter.record(conv_id)

            reply_to = group_id if is_room else from_user_id

            conversations.set_pending(conv_id, True)
            try:
                # Memory mode: if disabled, keep only current
                if not config_service.is_memory_mode_enabled():
                    conversations.clear(conv_id)
                    conversations.add_user_message(
                        conv_id, text,
                        contact_name=sender_name, is_room=is_room
                    )
                else:
                    conversations.add_user_message(
                        conv_id, text,
                        contact_name=sender_name, is_room=is_room
                    )

                messages = conversations.get_messages(conv_id)

                # === 拟人化：构建追问指引 ===
                intimacy_score = 10
                try:
                    record = db.get_intimacy(conv_id)
                    intimacy_score = record.get("intimacy_score", 10)
                except Exception:
                    pass
                max_rounds = followup_max_rounds(intimacy_score)
                followup_state = db.get_followup_state(conv_id)
                consecutive = followup_state.get("consecutive_followups", 0)
                followup_hint = followup_directive("auto", consecutive, max_rounds)

                reply = ai_client.chat(
                    messages, contact_name=sender_name,
                    is_room=is_room, room_topic=group_id,
                    conv_id=conv_id, followup_hint=followup_hint
                )

                if reply:
                    # Detect emotion from bot's reply (keywords + DeepSeek AI)
                    _emotion = _detect_emotion_with_ai(reply)
                    logger.info(f"Detected emotion: {_emotion}")

                    conversations.add_assistant_message(conv_id, reply)

                    # === 拟人化：追问状态追踪 ===
                    if not is_room:
                        try:
                            state = db.get_followup_state(conv_id)
                            consecutive = state.get("consecutive_followups", 0)
                            if detect_followup(reply):
                                db.upsert_followup_state({
                                    "conv_id": conv_id,
                                    "consecutive_followups": consecutive + 1,
                                    "last_intent": "auto",
                                })
                            else:
                                # Natural close - reset counter
                                db.upsert_followup_state({
                                    "conv_id": conv_id,
                                    "consecutive_followups": 0,
                                    "last_intent": "auto",
                                })
                        except Exception as e:
                            logger.warning(f"Followup state update failed: {e}")

                    # Reply interval delay
                    interval = config_service.get_reply_interval()
                    if interval > 0:
                        time.sleep(interval)

                    self._send_reply(reply_to, reply, context_token)

                    # Bot mood adjustment based on user sentiment
                    if not is_room and config_service.is_bot_mood_enabled():
                        try:
                            sentiment = ai_client.classify_user_sentiment(text)
                            if "praise" in sentiment:
                                delta = random.randint(5, 10)
                                config_service.adjust_bot_mood(delta)
                                logger.info(f"Bot mood +{delta} to {config_service.get_bot_mood()} (praise)")
                            elif "insult" in sentiment:
                                delta = -random.randint(5, 10)
                                config_service.adjust_bot_mood(delta)
                                logger.info(f"Bot mood {delta} to {config_service.get_bot_mood()} (insult)")
                        except Exception as e:
                            logger.warning(f"Mood adjustment failed: {e}")

                    # Auto-sticker: match filename against bot reply, fallback to random
                    if random.random() < self._sticker_probability:
                        try:
                            sticker_path = _select_sticker(_emotion, reply)
                            if sticker_path:
                                logger.info(f"Sending sticker: {sticker_path}")
                                send_sticker(self._token, reply_to, sticker_path, context_token=context_token or None)
                            else:
                                logger.warning(f"No sticker files found in {STICKER_DIR}")
                        except Exception as e:
                            logger.error(f"Auto sticker error: {e}")

                    # Topic summary extraction (throttled: every ~5 exchanges)
                    if not is_room:
                        try:
                            msg_count = len(conversations.get_messages(conv_id))
                            if msg_count % 10 == 0:
                                topic = ai_client.extract_topic_summary(text, reply)
                                if topic:
                                    db.update_topic_summary(conv_id, topic)
                                    logger.info(f"Topic summary updated for {conv_id}: {topic[:60]}")
                        except Exception as e:
                            logger.warning(f"Topic extraction failed: {e}")

            except Exception as e:
                logger.error(f"AI reply failed: {e}")
                try:
                    self._send_reply(
                        reply_to,
                        "(Sorry, I'm having trouble. Please try again later.)",
                        context_token,
                    )
                except Exception as e:
                    logger.error(f"Failed to send error reply: {e}")
            finally:
                conversations.set_pending(conv_id, False)

        except Exception as e:
            logger.error(f"Message processing error: {e}", exc_info=True)

    def _handle_command(self, text: str, conv_id: str, context_token: str) -> bool:
        cmd = text.strip()

        # Chinese + English commands
        if cmd in ("自动回复", "/bot on"):
            auto_reply.set(True)
            self._send_reply(conv_id, "自动回复已开启", context_token)
            return True

        if cmd in ("关闭自动回复", "/bot off"):
            auto_reply.set(False)
            self._send_reply(conv_id, "自动回复已关闭", context_token)
            return True

        if cmd in ("清空对话", "/bot reset"):
            conversations.clear(conv_id)
            self._send_reply(conv_id, "对话记录已清空", context_token)
            return True

        if cmd in ("状态", "/bot status"):
            status = (
                f"状态: {'开启' if auto_reply.enabled else '关闭'}\n"
                f"模型: {config.anthropic_model}\n"
                f"最大对话轮数: {config.conversation_max_turns}"
            )
            self._send_reply(conv_id, status, context_token)
            return True

        return False

    def _handle_image_message(
        self, msg: dict, image_item: dict,
        from_user_id: str, group_id: str, context_token: str
    ):
        """Process an incoming image message with vision AI."""
        import base64
        is_room = bool(group_id)
        conv_id = group_id if is_room else from_user_id

        logger.info(
            f"Image received: from={from_user_id} room={is_room} "
            f"image_item_keys={list(image_item.keys())}"
        )

        # Auto-reply check
        if not auto_reply.enabled:
            return

        # Allowlist
        if not is_room:
            if not allowlist.is_user_allowed(from_user_id, from_user_id):
                return
        else:
            if not allowlist.is_room_allowed(group_id, group_id):
                return

        # Rate limiting
        allowed, wait = rate_limiter.check(conv_id)
        if not allowed:
            return

        # Pending check
        if conversations.is_pending(conv_id):
            return

        # Rest time check
        if config_service.is_in_rest_time():
            reply_to = group_id if is_room else from_user_id
            self._send_reply(
                reply_to,
                "我正在休息中，请稍后再试。",
                context_token,
            )
            return

        rate_limiter.record(conv_id)

        # Record user reply for active chat tracking
        if not is_room:
            db.record_user_reply(conv_id)

        # Download image
        result = download_image(image_item, token=self._token)
        if not result:
            logger.warning(f"Failed to download image from {from_user_id}")
            reply_to = group_id if is_room else from_user_id
            self._send_reply(reply_to, "收到你的图片了，但我暂时看不到它，能描述一下吗？", context_token)
            return

        image_data, image_mime = result
        image_b64 = base64.b64encode(image_data).decode("ascii")
        logger.info(f"Image downloaded: {len(image_data)} bytes, {image_mime}")

        reply_to = group_id if is_room else from_user_id

        conversations.set_pending(conv_id, True)
        try:
            # Get conversation context
            conv_messages = conversations.get_messages(conv_id)

            # Call vision AI
            reply = ai_client.chat_with_image(
                image_b64, image_mime,
                messages=conv_messages,
                contact_name=from_user_id,
                is_room=is_room,
                room_topic=group_id,
                conv_id=conv_id,
            )

            if reply:
                # Extract emotion tag from AI response: [happy], [sad], etc.
                import re
                emotion = "neutral"
                tag_match = re.search(r'\[(happy|sad|angry|surprised|love|neutral)\]', reply)
                if tag_match:
                    emotion = tag_match.group(1)
                    reply = reply[:tag_match.start()].strip()

                logger.info(f"Image vision reply emotion: {emotion}, text: {reply[:100]}")

                conversations.add_user_message(
                    conv_id, "[User sent an image]",
                    contact_name=from_user_id, is_room=is_room
                )
                conversations.add_assistant_message(conv_id, reply)

                # Reply interval
                interval = config_service.get_reply_interval()
                if interval > 0:
                    time.sleep(interval)

                self._send_reply(reply_to, reply, context_token)

                # Auto-sticker: match filename against bot reply, fallback to random
                if random.random() < self._sticker_probability:
                    try:
                        sticker_path = _select_sticker(emotion, reply)
                        if sticker_path:
                            logger.info(f"Sending sticker for image emotion {emotion}: {sticker_path}")
                            send_sticker(self._token, reply_to, sticker_path, context_token=context_token or None)
                        else:
                            logger.warning(f"No sticker files found in {STICKER_DIR}")
                    except Exception as e:
                        logger.error(f"Image sticker error: {e}")

                # Bot mood adjustment based on detected user emotion from image
                if not is_room and config_service.is_bot_mood_enabled():
                    try:
                        if emotion in ("happy", "love", "surprised"):
                            delta = random.randint(3, 8)
                            config_service.adjust_bot_mood(delta)
                            logger.info(f"Bot mood +{delta} to {config_service.get_bot_mood()} (image emotion: {emotion})")
                        elif emotion in ("sad", "angry"):
                            delta = -random.randint(3, 8)
                            config_service.adjust_bot_mood(delta)
                            logger.info(f"Bot mood {delta} to {config_service.get_bot_mood()} (image emotion: {emotion})")
                    except Exception as e:
                        logger.warning(f"Image mood adjustment failed: {e}")

                # Topic summary extraction
                if not is_room:
                    try:
                        msg_count = len(conversations.get_messages(conv_id))
                        if msg_count % 10 == 0:
                            topic = ai_client.extract_topic_summary("[User sent an image]", reply)
                            if topic:
                                db.update_topic_summary(conv_id, topic)
                                logger.info(f"Topic summary updated for {conv_id} (image): {topic[:60]}")
                    except Exception as e:
                        logger.warning(f"Image topic extraction failed: {e}")

        except Exception as e:
            logger.error(f"Vision AI failed: {e}")
            try:
                self._send_reply(
                    reply_to,
                    "收到你的图片啦！不过我现在有点看不清，等我眼睛好了再看看~",
                    context_token,
                )
            except Exception as e:
                logger.error(f"Failed to send image error reply: {e}")
        finally:
            conversations.set_pending(conv_id, False)

    def send_reply(self, to_user_id: str, text: str, context_token: str = ""):
        """Public method to send a text reply (used by scheduler for broadcast)."""
        self._send_reply(to_user_id, text, context_token)

    def _send_reply(self, to_user_id: str, text: str, context_token: str = ""):
        if not self._token:
            logger.error("Cannot send: no token")
            return

        chunks = self._split_text(text, 2000)
        for i, chunk in enumerate(chunks):
            if len(chunks) > 1:
                chunk = f"({i + 1}/{len(chunks)})\n{chunk}"
            try:
                send_message(
                    self._token, to_user_id, chunk,
                    context_token=context_token or None,
                )
                logger.debug(f"Sent to {to_user_id}: {chunk[:50]}...")
            except Exception as e:
                logger.error(f"Send failed: {e}")

            if i < len(chunks) - 1:
                time.sleep(0.5)

    @staticmethod
    def _split_text(text: str, max_len: int) -> list[str]:
        if len(text) <= max_len:
            return [text]
        chunks = []
        paragraphs = text.split("\n\n")
        current = ""
        for para in paragraphs:
            if len(current) + len(para) + 2 <= max_len:
                current = f"{current}\n\n{para}" if current else para
            else:
                if current:
                    chunks.append(current.strip())
                if len(para) > max_len:
                    for i in range(0, len(para), max_len - 100):
                        chunks.append(para[i:i + max_len - 100])
                    current = ""
                else:
                    current = para
        if current:
            chunks.append(current.strip())
        return chunks or [text]


wechat_bot = WeChatBot()

# ── Async login state (shared between bot thread and admin API) ──────
_login_state: dict = {
    "status": "idle",       # idle | fetching_qr | waiting_scan | connected | failed
    "qrcode_url": "",
    "qrcode": "",
    "account_id": "",
    "error": "",
}
_login_lock = threading.Lock()


def get_login_state() -> dict:
    """Thread-safe read of login state."""
    with _login_lock:
        return dict(_login_state)


def _set_login_state(**kwargs):
    with _login_lock:
        _login_state.update(kwargs)


def start_async_login():
    """Start the WeChat login flow in a background daemon thread."""
    if get_login_state()["status"] in ("fetching_qr", "waiting_scan", "connected"):
        return  # Already in progress
    _set_login_state(status="fetching_qr", qrcode_url="", qrcode="", account_id="", error="")
    t = threading.Thread(target=_login_thread, daemon=True, name="bot_login")
    t.start()


def _login_thread():
    """Background: fetch QR → wait for scan → start polling."""
    try:
        qr = fetch_qr_code()
        _set_login_state(
            status="waiting_scan",
            qrcode_url=qr.qrcode_url or "",
            qrcode=qr.qrcode or "",
        )

        # Open browser with QR scan link
        import webbrowser
        webbrowser.open(qr.qrcode_url)

        result = wait_for_login(qr)
        if not result.connected:
            if result.already_connected:
                # Bot is already linked — restore from saved credentials
                saved = load_account()
                if saved:
                    token = saved.get("token", "")
                    base_url = saved.get("base_url", "")
                    account_id = saved.get("account_id", "")
                    if token and base_url:
                        wechat_bot._token = token
                        wechat_bot._account_id = account_id
                        wechat_bot._base_url = base_url
                        _set_login_state(status="connected", account_id=account_id)
                        logger.info("Restored connection from saved credentials")
                        wechat_bot._poll_loop()
                        return
                logger.warning("already_connected but no saved credentials found")
            _set_login_state(status="failed", error=result.message or "Login failed")
            return

        token = result.bot_token or ""
        account_id = result.account_id or ""
        base_url = result.base_url or ""
        save_account(token, account_id, base_url, result.user_id)

        wechat_bot._token = token
        wechat_bot._account_id = account_id
        wechat_bot._base_url = base_url

        _set_login_state(status="connected", account_id=account_id)

        # Notify Tencent server that bot is online
        try:
            resp = notify_start(token, base_url)
            if resp.get("ret", 0) != 0:
                logger.warning(f"notifyStart returned ret={resp.get('ret')}")
        except Exception as e:
            logger.warning(f"notifyStart failed (non-fatal): {e}")

        # Start message polling (this blocks until bot stops)
        wechat_bot._poll_loop()

    except Exception as e:
        logger.error(f"Login thread error: {e}", exc_info=True)
        _set_login_state(status="failed", error=str(e))