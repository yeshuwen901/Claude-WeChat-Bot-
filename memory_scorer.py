"""AI-based memory importance scoring and trimming."""

import json
import logging
import threading
import time
from datetime import datetime

from ai_client import ai_client
from config import config
from database import db

logger = logging.getLogger(__name__)

SCORE_INTERVAL = 600  # Run every 10 minutes
MAX_MEMORIES = 20     # Keep top N exchanges
SCORE_WEIGHT_IMPORTANCE = 0.6
SCORE_WEIGHT_AGE = 0.4

_running = False
_thread = None


def _score_conversation(conv_id: str):
    """Score message pairs for a conversation and trim low-value ones."""
    try:
        messages = db.load_messages(conv_id)
        if not messages:
            return

        # Build exchange pairs (user + assistant) handling non-text messages.
        # Walk through messages and pair each user msg with the next assistant msg.
        pairs = []
        for i in range(len(messages) - 1):
            user_msg = messages[i]
            assistant_msg = messages[i + 1]
            if user_msg.get("role") == "user" and assistant_msg.get("role") == "assistant":
                pairs.append({
                    "index": i,
                    "user": user_msg.get("content", "")[:200],
                    "assistant": assistant_msg.get("content", "")[:200],
                })

        if len(pairs) <= MAX_MEMORIES:
            return

        # Ask AI to score each exchange
        scoring_prompt = (
            "Rate the importance of each conversation exchange below on a scale of 1-10.\n"
            "Importance criteria: contains personal info (5+), emotional content (4+), "
            "factual information (4+), casual chat (1-3).\n\n"
            "For each exchange, reply with exactly one line in format: INDEX:SCORE\n"
            "Example: 0:7\n\n"
            "Exchanges:\n"
        )
        for p in pairs:
            scoring_prompt += (
                f"Exchange {p['index']}:\n"
                f"  User: {p['user'][:100]}\n"
                f"  Assistant: {p['assistant'][:100]}\n\n"
            )

        try:
            ai_client._ensure_client()
            response = ai_client.client.messages.create(
                model=ai_client._get_effective_model(),
                max_tokens=500,
                temperature=0.0,
                system="You are a memory importance scorer. Rate conversation exchanges by importance (1-10). Reply ONLY with INDEX:SCORE lines, one per exchange.",
                messages=[{"role": "user", "content": scoring_prompt}],
            )
            score_text = ""
            for block in response.content:
                if block.type == "text":
                    score_text = block.text
                    break
        except Exception as e:
            logger.warning(f"Memory scoring AI call failed for {conv_id}: {e}")
            return

        # Parse scores: "INDEX:SCORE"
        scores = {}
        for line in score_text.strip().split("\n"):
            line = line.strip()
            if ":" in line:
                try:
                    idx_str, score_str = line.split(":", 1)
                    scores[int(idx_str.strip())] = int(score_str.strip())
                except (ValueError, TypeError):
                    continue

        if not scores:
            return

        # Apply scoring formula and select top pairs
        scored_pairs = []
        for rank, p in enumerate(pairs):
            importance = scores.get(p["index"], 5)
            # Estimate age based on scoring interval: older pairs = smaller rank = larger penalty
            hours_age = (len(pairs) - rank) * (SCORE_INTERVAL / 3600)
            final_score = SCORE_WEIGHT_IMPORTANCE * importance - SCORE_WEIGHT_AGE * hours_age
            scored_pairs.append((final_score, p))

        scored_pairs.sort(key=lambda x: x[0], reverse=True)

        # Reload messages before saving to avoid overwriting newer data (TOCTOU fix)
        current_messages = db.load_messages(conv_id)
        if len(current_messages) != len(messages):
            logger.info(
                f"Skipping memory trim for {conv_id}: messages changed during scoring "
                f"({len(messages)} -> {len(current_messages)})"
            )
            return

        keep_set = set()
        for _, p in scored_pairs[:MAX_MEMORIES]:
            idx = p["index"]
            keep_set.add(idx)
            if idx + 1 < len(messages):
                keep_set.add(idx + 1)

        trimmed = [msg for i, msg in enumerate(messages) if i in keep_set]
        if len(trimmed) < len(messages):
            db.save_messages(conv_id, trimmed)
            logger.info(
                f"Memory trimmed for {conv_id}: {len(messages)} -> {len(trimmed)} messages "
                f"({len(pairs)} -> {len(keep_set) // 2} exchanges)"
            )

    except Exception as e:
        logger.error(f"Memory scoring error for {conv_id}: {e}")


def _scorer_loop():
    """Background thread: periodically score and trim conversations."""
    global _running
    while _running:
        try:
            # Get all conversation IDs from database
            conv_ids = db.get_all_conversation_ids()
            for conv_id in conv_ids:
                _score_conversation(conv_id)
        except Exception as e:
            logger.error(f"Memory scorer loop error: {e}")
        time.sleep(SCORE_INTERVAL)


def start_memory_scorer():
    global _running, _thread
    if _running:
        return
    _running = True
    _thread = threading.Thread(target=_scorer_loop, daemon=True, name="memory_scorer")
    _thread.start()
    logger.info("Memory scorer started")


def stop_memory_scorer():
    global _running
    _running = False
