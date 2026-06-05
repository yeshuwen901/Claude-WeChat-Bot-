"""Anthropic API client wrapper."""

import json
import logging
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

import random

import anthropic
from anthropic import APIError, APIStatusError, RateLimitError

from config import config, get_base_url_for_model
from config_service import config_service
from database import db

logger = logging.getLogger(__name__)


class AIClient:
    def __init__(self):
        self._last_key: str = ""
        self._last_model: str = ""
        self.client: anthropic.Anthropic | None = None
        self._ensure_client()

    def _get_effective_key(self) -> str:
        return config_service.get_api_key() or config.anthropic_api_key

    def _get_effective_model(self) -> str:
        return config_service.get_anthropic_model() or config.anthropic_model

    def _ensure_client(self):
        key = self._get_effective_key()
        model = self._get_effective_model()
        if self.client is None or key != self._last_key or model != self._last_model:
            kwargs = dict(api_key=key)
            base_url = get_base_url_for_model(model)
            if base_url:
                kwargs["base_url"] = base_url
            self.client = anthropic.Anthropic(**kwargs)
            self._last_key = key
            self._last_model = model

    def _get_stories_block(self, conv_id: str) -> str:
        """Get matched personal stories for the current conversation context."""
        if not conv_id or not config_service.is_personal_stories_enabled():
            return ""
        stories = config_service.get_personal_stories()
        if not stories:
            return ""
        recent = db.get_recent_messages(conv_id, count=6)
        if not recent:
            return ""
        recent_text = " ".join(m.get("content", "") for m in recent).lower()
        matched = []
        for story in stories:
            keywords = story.get("trigger_keywords", [])
            if not keywords:
                continue
            if any(kw.lower() in recent_text for kw in keywords):
                matched.append(story.get("text", ""))
        if not matched:
            return ""
        selected = random.sample(matched, min(2, len(matched)))
        return "\n\n## 你的个人经历（如果话题相关可以自然提及，但不要生硬插入）\n" + \
               "\n".join(f"- {s}" for s in selected)

    def build_system_prompt(
        self, contact_name: str, is_room: bool, room_topic: str = "",
        conv_id: str = "", followup_hint: str = ""
    ) -> str:
        """Build system prompt with intimacy-driven layered architecture.

        Layers (in order):
        1. Core rules - always 100% (from DB bot_config.core_rules)
        2. Persona - dynamic weight based on intimacy score (40%/65%/90%)
        3. Colloquialism - speaking style directive by intimacy tier
        4. Followup hint - injected from caller based on intent + state
        5. Intent classification - AI self-classifies user intent
        6. Extra modifiers - mood, personal stories, length rules
        """
        now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M CST")
        context = ""
        if is_room and room_topic:
            context = f"This is a group chat called \"{room_topic}\"."
        elif is_room:
            context = "This is a group chat."
        else:
            context = f"This is a direct message with {contact_name}."

        # Query intimacy score
        intimacy_score = 10  # default for new users
        if conv_id:
            try:
                record = db.get_intimacy(conv_id)
                intimacy_score = record.get("intimacy_score", 10)
            except Exception:
                pass

        # Read core rules from DB config
        core_rules = config_service._get("core_rules", "")
        if not core_rules:
            core_rules = (
                "保持基本礼貌，不骂人不说脏话。\n"
                "不涉及政治敏感话题。\n"
                "不使用歧视性语言。\n"
                "语气友好温暖。"
            )

        # Get user persona (existing logic preserved)
        persona_text = ""
        if conv_id:
            full = db.get_user_prompt_full(conv_id)
            if full:
                merged = (full.get("merged_prompt") or "").strip()
                if merged:
                    persona_text = merged
                else:
                    persona_text = (full.get("prompt") or "").strip()

        # ---- Assemble layers ----
        from intimacy_engine import (
            persona_weight, persona_weight_directive,
            colloquialism_directive, classify_tier, Tier
        )

        prev_tier = record.get("intimacy_tier", "new_friend") if conv_id else "new_friend"
        tier = classify_tier(intimacy_score, prev_tier)
        weight = persona_weight(intimacy_score)
        weight_directive = persona_weight_directive(weight)
        colloquialism = colloquialism_directive(intimacy_score)
        tier_label = Tier.label(tier)

        parts = []

        # Layer 1: Core rules (always 100%)
        parts.append(f"【核心规则 - 必须始终遵守】\n{core_rules}")

        # Layer 2: Persona with dynamic weight
        if persona_text:
            parts.append(
                f"【角色设定 - 当前互动深度：{tier_label}】\n"
                f"{weight_directive}\n"
                f"{persona_text}\n\n"
                "Guidelines:\n"
                "- Use Chinese if the user writes in Chinese; English if the user writes in English.\n"
                "- Do not mention that you are an AI unless asked directly.\n"
                "- Do not generate harmful, illegal, or unethical content."
            )
        else:
            # Default persona (simplified: no user prompt set)
            parts.append(
                f"You are {config.bot_name}, a helpful, friendly AI assistant "
                f"connected via WeChat.\n"
                f"The current time is {now}.\n"
                f"You are chatting with {contact_name}.\n"
                f"{context}\n\n"
                "Guidelines:\n"
                "- Keep responses concise. WeChat messages are best under 500 characters.\n"
                "- Use Chinese if the user writes in Chinese; English if the user writes in English.\n"
                "- Be warm and conversational.\n"
                "- If you don't know something, say so honestly.\n"
                "- Do not mention that you are an AI unless asked directly.\n"
                "- Do not generate harmful, illegal, or unethical content."
            )

        # Layer 3: Colloquialism
        if colloquialism:
            parts.append(f"【说话风格指引】\n{colloquialism}")

        # Layer 4: Followup hint (from caller)
        if followup_hint:
            parts.append(f"【对话互动指引】\n{followup_hint}")

        # Layer 5: Intent classification (always included)
        parts.append(
            "【对话意图】先判断对方这句话的类型："
            "陈述/分享/吐槽/提问/求助。"
            "如果是分享或吐槽，回复后可自然追问一句。"
        )

        # Build the main prompt
        result = "\n\n".join(parts)

        # Layer 6: Extra modifiers
        mood = self._get_mood_tone()
        stories = self._get_stories_block(conv_id)
        length = self._get_length_rule()
        extra = mood + stories + length
        if extra:
            result += extra

        # Apply variable substitution
        result = result.replace("{bot_name}", config.bot_name) \
                       .replace("{contact_name}", contact_name) \
                       .replace("{time}", now) \
                       .replace("{context}", context)

        return result

    def classify_emotion(self, text: str) -> str:

        """Classify the emotional tone of a text. Returns happy/sad/angry/surprised/love/neutral."""
        self._ensure_client()
        try:
            response = self.client.messages.create(
                model=self._get_effective_model(),
                max_tokens=16,
                temperature=0.0,
                system="Classify the emotional tone of the text. Reply with exactly one word from: happy, sad, angry, surprised, love, neutral. No other output.",
                messages=[{"role": "user", "content": text}],
            )
            for block in response.content:
                if block.type == "text":
                    result = block.text.strip().lower().rstrip(".,!;:，。！；：")
                    return result
            return "neutral"
        except Exception:
            logger.warning("classify_emotion failed", exc_info=True)
            return "neutral"

    def classify_user_sentiment(self, text: str) -> str:
        """Classify the user's attitude toward the bot. Returns praise/insult/neutral."""
        self._ensure_client()
        try:
            response = self.client.messages.create(
                model=self._get_effective_model(),
                max_tokens=16,
                temperature=0.0,
                system="Classify the user's attitude toward the chatbot in this message. Reply with exactly one word: praise (compliment, gratitude, affection), insult (rudeness, anger, mockery), or neutral (ordinary conversation). No other output.",
                messages=[{"role": "user", "content": text}],
            )
            for block in response.content:
                if block.type == "text":
                    result = block.text.strip().lower().rstrip(".,!;:，。！；：")
                    return result
            return "neutral"
        except Exception:
            logger.warning("classify_user_sentiment failed", exc_info=True)
            return "neutral"

    def _get_mood_tone(self) -> str:
        """Get the mood tone instruction based on current bot mood."""
        if not config_service.is_bot_mood_enabled():
            return ""
        mood = config_service.get_bot_mood()
        if mood >= 70:
            return "\n\n【你今天心情很好，回复时语气热情、开朗、主动活泼。】"
        elif mood <= 30:
            return "\n\n【你今天心情不太好，回复时语气可以稍冷淡、简短一些，但不要失礼。】"
        return ""

    @staticmethod
    def _strip_code_block(text: str) -> str:
        """Remove markdown code block fences from AI output.
        Handles: ```text```, ```json\ntext\n```, etc.
        """
        text = text.strip()
        # Remove opening fence with optional language tag
        if text.startswith("```"):
            newline_pos = text.find("\n")
            if newline_pos != -1:
                text = text[newline_pos + 1:]
            else:
                text = text[3:]
        # Remove closing fence
        if text.endswith("```"):
            text = text[:-3]
        return text.strip()

    @staticmethod
    def _extract_json(text: str) -> dict:
        """Parse JSON from AI output, handling markdown code blocks."""
        text = AIClient._strip_code_block(text)
        import json as _json
        return _json.loads(text)

    @staticmethod
    def _get_length_rule() -> str:
        """Get the length constraint rule if configured."""
        limit = config_service.get_reply_max_chars()
        if not limit:
            return ""
        return f"\n\n【重要：你的每条回复必须控制在{limit}字以内。像真人发消息一样简短自然。】"

    def _chat_with_image_qwen(
        self, image_base64: str, image_mime: str, user_prompt: str,
        system_prompt: str, messages: list[dict]
    ) -> str:
        """Vision call via Qwen-VL DashScope API (supports images natively)."""
        import os as _os

        api_key = config_service.get_dashscope_api_key()
        if not api_key:
            raise RuntimeError("DASHSCOPE_API_KEY not configured")

        # Build conversation context (last 3 turns)
        api_messages = []
        for m in messages[-6:]:
            api_messages.append({"role": m["role"], "content": m["content"]})

        # Add image message in OpenAI multimodal format
        api_messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": user_prompt},
                {"type": "image_url", "image_url": {
                    "url": f"data:{image_mime};base64,{image_base64}"
                }},
            ],
        })

        body = json.dumps({
            "model": "qwen3-vl-plus",
            "messages": [
                {"role": "system", "content": system_prompt},
                *api_messages,
            ],
            "max_tokens": 1024,
            "temperature": 0.7,
        }, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            body_text = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Qwen-VL API HTTP {e.code}: {body_text}")

    def chat_with_image(
        self,
        image_base64: str,
        image_mime: str,
        messages: list[dict],
        contact_name: str = "",
        is_room: bool = False,
        room_topic: str = "",
        conv_id: str = "",
    ) -> str:
        """Chat with vision via Qwen-VL DashScope API, using user's persona."""

        # Get the user's persona / merged prompt
        persona_prompt = self.build_system_prompt(
            contact_name, is_room, room_topic, conv_id=conv_id
        )

        # Vision task instructions appended after persona
        vision_task = (
            f"\n\nThe user just sent you an image. Your task:\n"
            f"1. Briefly describe what you see in the image (1-2 sentences).\n"
            f"2. Based on the conversation context, infer WHY the user sent this image — "
            f"what emotion, meaning, or reaction are they expressing?\n"
            f"3. Respond naturally according to your character above. "
            f"If the image is funny, share the humor. If it's touching, acknowledge it. "
            f"If it seems to express frustration, validate their feelings.\n"
            f"4. End your reply with exactly one emotion tag on its own line, "
            f"chosen from: [happy] [sad] [angry] [surprised] [love] [neutral]. "
            f"Pick the emotion that best matches what the USER is expressing through the image.\n"
            f"Keep the response under 500 characters."
        )

        system_prompt = persona_prompt + vision_task

        user_prompt = (
            f"Please analyze the image the user just sent. "
            f"Consider the conversation context above and infer what emotion or meaning "
            f"the user is expressing by sending this image. "
            f"Reply in the language and style defined by your character."
        )

        logger.info("Calling Qwen-VL vision API...")
        return self._chat_with_image_qwen(
            image_base64, image_mime, user_prompt, system_prompt, messages
        )

    def _build_thinking_config(self) -> dict | None:
        """Return thinking config if enabled AND model supports it (Claude only)."""
        if not config_service.is_deep_thinking_enabled():
            return None
        model = self._get_effective_model()
        if not model.startswith("claude"):
            return None
        return {"type": "enabled", "budget_tokens": 2048}

    def _build_tools(self) -> list[dict] | None:
        """Return tool definitions for AI-driven web search."""
        if not config_service.is_web_search_enabled():
            return None
        return [{
            "name": "web_search",
            "description": (
                "Search the web for current information. Use this when the user asks "
                "about recent events, news, facts you are unsure about, or questions "
                "that require up-to-date information (time, date, weather, stock prices, etc.). "
                "Do NOT use for simple conversations, greetings, opinions, or personal matters."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query (keywords or question, in Chinese or English)"
                    }
                },
                "required": ["query"]
            }
        }]

    # Keywords that force a pre-injection web search (program-side trigger)
    _SEARCH_TRIGGER_PATTERNS: list[str] = [
        # Time
        "几点", "什么时间", "现在几点", "今天几号", "今天星期几", "当前时间",
        "日期", "星期几", "今天日期", "几月几号",
        # Weather
        "天气", "气温", "下雨", "下雪", "台风", "雾霾", "空气质量",
        "多少度", "会下雨吗", "带伞", "冷不冷", "热不热",
        # News
        "新闻", "最新", "最近发生", "热点", "热搜", "今天有什么",
        # Real-time
        "股价", "股票", "汇率", "油价", "金价", "比特币",
        "最新消息", "最新数据",
        # Factual lookup (only trigger if message is substantial)
        "什么是", "是谁", "在哪里", "什么时候", "怎么去", "多少钱",
    ]

    _STRONG_TRIGGERS: set[str] = {
        "天气", "新闻", "几点", "几号", "星期几", "热搜",
        "股价", "股票", "汇率", "油价", "金价", "比特币", "台风",
    }

    def _should_trigger_search(self, message: str, is_room: bool) -> bool:
        """Check if the user message should force a web search (path 1)."""
        if is_room:
            return False
        if not message or len(message.strip()) < 2:
            return False

        msg = message.strip()

        # Check for strong trigger words (always trigger)
        for kw in self._STRONG_TRIGGERS:
            if kw in msg:
                return True

        # Check general patterns
        for pat in self._SEARCH_TRIGGER_PATTERNS:
            if pat in msg:
                # Skip false positives for very short casual messages
                if len(msg) <= 4:
                    continue
                return True

        return False

    def _chat_inner(
        self, system_block: list, api_messages: list,
        thinking: dict | None, tools: list | None,
        max_tokens: int | None = None,
    ) -> "anthropic.types.Message":
        """Single API call, returns the response object."""
        kwargs = dict(
            model=self._get_effective_model(),
            max_tokens=max_tokens if max_tokens is not None else config.anthropic_max_tokens,
            temperature=config.anthropic_temperature,
            system=system_block,
            messages=api_messages,
        )
        if thinking:
            kwargs["thinking"] = thinking
        if tools:
            kwargs["tools"] = tools
        return self.client.messages.create(**kwargs)

    def chat(
        self,
        messages: list[dict],
        contact_name: str = "",
        is_room: bool = False,
        room_topic: str = "",
        conv_id: str = "",
        followup_hint: str = "",
    ) -> str:
        self._ensure_client()
        system_prompt = self.build_system_prompt(
            contact_name, is_room, room_topic, conv_id=conv_id,
            followup_hint=followup_hint
        )

        # Dual-trigger web search logic
        keyword_triggered = False
        if config_service.is_web_search_enabled() and messages and not is_room:
            last_user_msg = ""
            for m in reversed(messages):
                if m["role"] == "user":
                    last_user_msg = m["content"]
                    break
            if last_user_msg and self._should_trigger_search(last_user_msg, is_room):
                try:
                    from web_search import web_search_formatted
                    search_result = web_search_formatted(last_user_msg)
                    if search_result:
                        system_prompt = (
                            f"{system_prompt}\n\n"
                            f"## 实时信息参考（联网搜索结果，优先采用其中的事实信息）\n"
                            f"{search_result}"
                        )
                        keyword_triggered = True
                        logger.info(f"Keyword-triggered web search: {last_user_msg[:50]}...")
                except Exception as e:
                    logger.warning(f"Web search failed, continuing without: {e}")

        # Build system with cache_control
        system_block = [
            {"type": "text", "text": system_prompt,
             "cache_control": {"type": "ephemeral"}}
        ]

        # Mark first 2 messages with cache_control
        api_messages = []
        for i, msg in enumerate(messages):
            if i < 2:
                api_messages.append({
                    "role": msg["role"],
                    "content": [{"type": "text", "text": msg["content"],
                                 "cache_control": {"type": "ephemeral"}}],
                })
            else:
                api_messages.append(msg)

        thinking = self._build_thinking_config()
        # Path 1 (keyword) and Path 2 (AI tool) are mutually exclusive
        tools = None if keyword_triggered else self._build_tools()

        # Length is controlled solely by the system prompt (_get_length_rule),
        # not by max_tokens. A tight max_tokens can garble Chinese text when
        # the API hard-cuts at a token boundary mid-character.
        last_exception = None
        for attempt in range(3):
            try:
                response = self._chat_inner(
                    system_block, api_messages, thinking, tools,
                )

                # Path 2: handle AI-initiated tool_use
                tool_use_blocks = [
                    b for b in response.content if b.type == "tool_use"
                ]
                if tool_use_blocks:
                    api_messages.append({
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": b.text}
                            if b.type == "text" else b.model_dump()
                            for b in response.content
                        ],
                    })
                    tool_results = []
                    for block in tool_use_blocks:
                        if block.name == "web_search":
                            from web_search import web_search_formatted
                            query = block.input.get("query", "")
                            logger.info(f"AI-triggered web search: {query}")
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": web_search_formatted(query),
                            })
                    api_messages.append({
                        "role": "user",
                        "content": tool_results,
                    })
                    response = self._chat_inner(
                        system_block, api_messages, None, None,
                    )

                for block in response.content:
                    if block.type == "text":
                        return block.text
                return "(no text in response)"

            except RateLimitError as e:
                last_exception = e
                if attempt < 2:
                    time.sleep(2 ** attempt)
            except APIStatusError as e:
                last_exception = e
                if e.status_code >= 500 and attempt < 2:
                    time.sleep(2 ** attempt)
                else:
                    raise
            except APIError as e:
                last_exception = e
                if attempt < 2:
                    time.sleep(2 ** attempt)

        raise last_exception or RuntimeError("Unknown API error")

    # ── language habit analysis ──────────────────────────────────────

    def analyze_language_habits(self, conv_id: str) -> dict:
        """Analyze a contact's language habits from recent chat history."""
        self._ensure_client()
        raw_msgs = db.load_messages(conv_id)

        # Extract user messages (role=user), last 50
        user_msgs = [m for m in raw_msgs if m.get("role") == "user"]
        if len(user_msgs) > 50:
            user_msgs = user_msgs[-50:]

        if not user_msgs:
            return {}

        sample = "\n".join(
            f"- {m.get('content', '')[:200]}" for m in user_msgs[-30:]
        )

        prompt = (
            "Analyze the following WeChat messages from a single user. "
            "Return ONLY a JSON object (no markdown, no backticks) with these keys:\n"
            "- style: string (口语化/正式/幽默/简洁/热情/冷淡/其他)\n"
            "- top_phrases: array of strings (top 10 frequently used words/phrases)\n"
            "- emoji_usage: string (频繁/偶尔/几乎不用)\n"
            "- msg_length: string (简短/中等/长篇)\n"
            "- reply_speed: string (秒回/较快/较慢/不定)\n"
            "- address_habit: string (常用称呼方式, e.g. 直呼其名/昵称/无特定称呼)\n\n"
            f"Messages:\n{sample}"
        )

        try:
            response = self.client.messages.create(
                model=self._get_effective_model(),
                max_tokens=512,
                temperature=0.3,
                system="You are a linguistic analyst. Output only valid JSON, no explanation.",
                messages=[{"role": "user", "content": prompt}],
            )
            text = ""
            for block in response.content:
                if block.type == "text":
                    text += block.text
            habits = self._extract_json(text)
            db.set_language_habits(conv_id, habits)
            return habits
        except Exception as e:
            logger.error(f"Language habit analysis failed for {conv_id}: {e}")
            return {}

    def generate_merged_prompt(self, conv_id: str, persona: str, habits: dict) -> str:
        """Merge persona and language habits into a single layered prompt."""
        self._ensure_client()

        habits_str = json.dumps(habits, ensure_ascii=False, indent=2)
        prompt = (
            "You are a prompt engineer. Merge the following two pieces into a single "
            "system prompt for an AI chatbot on WeChat.\n\n"
            f"# Persona (set by user):\n{persona}\n\n"
            f"# Language habits of the conversation partner (auto-analyzed):\n{habits_str}\n\n"
            "Write a clean, layered system prompt with these sections:\n"
            "# 角色设定\n{persona content}\n\n"
            "# 对方语言习惯\n{language habit summary}\n\n"
            "# 对话规则\n{rules for matching the partner's style}\n\n"
            "Rules:\n"
            "- Output ONLY the merged prompt text, no markdown fences, no explanations.\n"
            "- Keep it concise, under 800 characters total.\n"
            "- The 角色设定 section preserves the user's persona exactly.\n"
            "- The 对方语言习惯 section summarizes habits in 3-4 bullet points.\n"
            "- The 对话规则 section gives 3-4 practical rules for matching tone/style."
        )

        try:
            response = self.client.messages.create(
                model=self._get_effective_model(),
                max_tokens=1024,
                temperature=0.4,
                system="You are a prompt optimization expert. Output only the merged prompt, no other text.",
                messages=[{"role": "user", "content": prompt}],
            )
            text = ""
            for block in response.content:
                if block.type == "text":
                    text += block.text
            merged = self._strip_code_block(text)
            db.set_merged_prompt(conv_id, merged)
            return merged
        except Exception as e:
            logger.error(f"Merged prompt generation failed for {conv_id}: {e}")
            return ""

    def refresh_prompt(self, conv_id: str) -> str | None:
        """Full pipeline: analyze habits → merge with persona → save → return merged."""
        full = db.get_user_prompt_full(conv_id)
        if not full:
            return None
        persona = (full.get("prompt") or "").strip()
        if not persona:
            return None
        habits = self.analyze_language_habits(conv_id)
        if not habits:
            return None
        merged = self.generate_merged_prompt(conv_id, persona, habits)
        return merged or None


    # ── topic summary extraction ─────────────────────────────────────

    def extract_topic_summary(self, user_msg: str, bot_reply: str) -> str:
        """Extract a 1-2 sentence topic summary from a chat exchange."""
        self._ensure_client()
        try:
            response = self.client.messages.create(
                model=self._get_effective_model(),
                max_tokens=128,
                temperature=0.3,
                system="Summarize the conversation topic in 1-2 brief Chinese sentences. Be specific about what was discussed. Output only the summary, no other text.",
                messages=[{"role": "user", "content": f"User: {user_msg[:300]}\nBot: {bot_reply[:300]}"}],
            )
            for block in response.content:
                if block.type == "text":
                    return block.text.strip()
            return ""
        except Exception as e:
            logger.warning(f"Topic summary extraction failed: {e}")
            return ""

    # ── proactive sharing message generation ───────────────────────────

    def generate_sharing_message(
        self, conv_id: str, topic: str, search_result: str
    ) -> str:
        """Generate a casual 'hey check this out' sharing message."""
        self._ensure_client()

        now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M CST")
        contact_name = conv_id

        full = db.get_user_prompt_full(conv_id)
        merged = (full.get("merged_prompt") or "").strip() if full else ""
        persona = (full.get("prompt") or "").strip() if full else ""

        if merged:
            system_prompt = merged.replace("{bot_name}", config.bot_name) \
                                  .replace("{contact_name}", contact_name) \
                                  .replace("{time}", now)
            mood = self._get_mood_tone()
            stories = self._get_stories_block(conv_id)
            if mood:
                system_prompt += mood
            if stories:
                system_prompt += stories
        elif persona:
            system_prompt = persona.replace("{bot_name}", config.bot_name) \
                                   .replace("{contact_name}", contact_name) \
                                   .replace("{time}", now)
            mood = self._get_mood_tone()
            stories = self._get_stories_block(conv_id)
            if mood:
                system_prompt += mood
            if stories:
                system_prompt += stories
        else:
            system_prompt = self.build_system_prompt(contact_name, False, "", conv_id)

        user_message = (
            f"你刚刚在网上看到了一个关于「{topic}」的有趣内容：\n\n"
            f"{search_result}\n\n"
            f"请用你的人格化语气，自然地分享给 {contact_name}。\n"
            f"要求：\n"
            f"- 用「刚看到」「刷到一条」「分享一个」等口语化表达开头\n"
            f"- 像朋友分享趣事一样自然，不要像新闻播报\n"
            f"- 120 字以内\n"
            f"- 用中文"
        )

        try:
            response = self.client.messages.create(
                model=self._get_effective_model(),
                max_tokens=384,
                temperature=0.85,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            for block in response.content:
                if block.type == "text":
                    return block.text
            return ""
        except Exception as e:
            logger.error(f"Sharing message generation failed: {e}")
            return ""

    # ── active / scheduled chat message generation ─────────────────────

    def generate_active_message(
        self, conv_id: str, topic: str = "", trigger_text: str = "",
        recent_context: list[dict] | None = None,
    ) -> str:
        """Generate a proactive message for active/scheduled chat.

        Args:
            conv_id: The conversation ID (contact or group).
            topic: Topic keyword from scheduled chat (e.g. '早安').
                   Used for scheduled chat only.
            trigger_text: A user-defined trigger text from active chat settings.
                          Used for interval-based active chat only.
            recent_context: Recent messages for context-aware continuation.
                            The AI will decide whether to continue the previous
                            topic or start a new one based on this context.
        """
        self._ensure_client()
        from config import config as bot_config
        from datetime import datetime, timezone, timedelta

        now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M CST")
        contact_name = conv_id

        # Load persona / merged prompt for this contact
        from database import db
        full = db.get_user_prompt_full(conv_id)
        merged = (full.get("merged_prompt") or "").strip() if full else ""
        persona = (full.get("prompt") or "").strip() if full else ""

        if merged:
            system_prompt = merged.replace("{bot_name}", bot_config.bot_name) \
                                  .replace("{contact_name}", contact_name) \
                                  .replace("{time}", now)
            mood = self._get_mood_tone()
            stories = self._get_stories_block(conv_id)
            if mood:
                system_prompt += mood
            if stories:
                system_prompt += stories
        elif persona:
            system_prompt = persona.replace("{bot_name}", bot_config.bot_name) \
                                   .replace("{contact_name}", contact_name) \
                                   .replace("{time}", now)
            mood = self._get_mood_tone()
            stories = self._get_stories_block(conv_id)
            if mood:
                system_prompt += mood
            if stories:
                system_prompt += stories
        else:
            system_prompt = self.build_system_prompt(contact_name, False, "", conv_id)

        # Build the active-chat instruction
        if topic:
            guidance = (
                f"用户预设的话题方向是：「{topic}」。\n"
                f"请围绕这个方向，根据你的人设自然地开启话题。"
            )
        elif trigger_text:
            guidance = (
                f"用户预设了一个话题方向供你参考：\n「{trigger_text}」\n\n"
                f"重要规则：\n"
                f"- 如果这个话题方向符合你的人设和语言风格，请围绕它自然展开\n"
                f"- 如果不符合（例如你是高冷人设但话题是撒娇），请完全忽略它，按照你的人设自由发起一个合适的话题"
            )
        else:
            guidance = "请根据你的人设和当前时间，自然地开启一个新话题。"

        # Build context-aware continuation instruction
        context_block = ""
        if recent_context:
            # Build a readable conversation log
            lines = []
            for m in recent_context:
                role = contact_name if m["role"] == "user" else "你"
                content = m.get("content", "")[:200]
                lines.append(f"- {role}：{content}")
            history = "\n".join(lines)

            context_block = (
                f"\n\n## 最近的对话记录（供你判断话题是否应该延续）\n\n"
                f"{history}\n\n"
                f"## 对话续接判断规则：\n"
                f"- 如果上一个话题有未完成的讨论、用户有未回答的问题、或有值得关心的情绪表达 → "
                f"请自然延续上一个话题\n"
                f"- 如果上一个对话已自然结束（如互道晚安、终结性回复、敷衍性短回复等）→ "
                f"请开启一个新话题\n"
                f"- 如果最后一条消息的时间距现在超过 2 小时 → 优先开启新话题\n"
                f"- 无论续聊还是新话题，都不要生硬地提「上次我们聊到……」这类元描述，直接自然切入\n"
            )

        # Check for stored topic summary (persists even when messages are trimmed)
        topic_block = ""
        state = db.get_active_chat_state(conv_id)
        if state.get("last_topic_summary") and state.get("last_topic_at", 0):
            topic_age_ms = int(time.time() * 1000) - state["last_topic_at"]
            topic_age_hours = topic_age_ms / (1000 * 60 * 60)
            if topic_age_hours < 24:
                topic_block = (
                    f"\n\n## 上次互动的话题摘要（供你判断是否延续）\n"
                    f"上次你和 {contact_name} 聊的是：{state['last_topic_summary']}\n"
                    f"如果这个话题还有延续空间且不算突兀，可以自然地继续聊下去；"
                    f"如果已经过时或不适合再提，就开启新话题。"
                )

        user_message = (
            f"你正在主动向 {contact_name} 发起一段对话。\n"
            f"当前时间：{now}\n\n"
            f"{guidance}"
            f"{context_block}"
            f"{topic_block}\n\n"
            f"要求：\n"
            f"- 必须符合你的人设和语言风格\n"
            f"- 像真人朋友一样自然，不要像机器人定时播报\n"
            f"- 回复简洁，150 字以内\n"
            f"- 不要每次都说同样的内容，保持多样性\n"
            f"- 用中文"
        )

        try:
            response = self.client.messages.create(
                model=self._get_effective_model(),
                max_tokens=512,
                temperature=0.9,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            for block in response.content:
                if block.type == "text":
                    return block.text
            return ""
        except Exception as e:
            logger.error(f"Active message generation failed: {e}")
            return ""


ai_client = AIClient()
