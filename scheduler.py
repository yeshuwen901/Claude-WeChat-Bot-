"""APScheduler-based scheduler for active messages and scheduled restart."""

import json
import logging
import random
import sys
import time
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from config_service import config_service
from database import db

logger = logging.getLogger(__name__)

BEIJING_TZ = timezone(timedelta(hours=8))

scheduler = BackgroundScheduler(timezone=BEIJING_TZ)

# WeChat bot reference, set by main.py after bot starts
_wechat_bot = None


def set_bot(bot):
    global _wechat_bot
    _wechat_bot = bot


def _send_active_message(msg_id: int, content: str):
    """Broadcast an active message to all contacts."""
    if _wechat_bot is None:
        logger.warning("Active message: bot not available")
        return

    contacts = db.get_all_contacts()
    if not contacts:
        logger.info("Active message: no contacts to send to")
        return

    for contact_id in contacts:
        try:
            _wechat_bot.send_reply(contact_id, content)
            time.sleep(1)  # Rate limit between sends
        except Exception as e:
            logger.error(f"Active message send to {contact_id} failed: {e}")

    logger.info(f"Active message {msg_id} broadcast to {len(contacts)} contacts")


def _refresh_active_message_jobs():
    """Remove all active-message jobs and re-add from database."""
    try:
        # Remove existing active-message jobs
        for job in scheduler.get_jobs():
            if job.id.startswith("am_"):
                scheduler.remove_job(job.id)

        messages = db.get_enabled_active_messages()
        for msg in messages:
            cron_expr = msg["cron_expression"].strip()
            if not cron_expr:
                continue
            try:
                parts = cron_expr.split()
                if len(parts) != 5:
                    logger.warning(f"Invalid cron for msg {msg['id']}: {cron_expr}")
                    continue
                trigger = CronTrigger(
                    minute=parts[0], hour=parts[1], day=parts[2],
                    month=parts[3], day_of_week=parts[4],
                )
                job_id = f"am_{msg['id']}"
                scheduler.add_job(
                    _send_active_message,
                    trigger=trigger,
                    args=[msg["id"], msg["content"]],
                    id=job_id,
                    replace_existing=True,
                )
                logger.info(f"Scheduled active msg {msg['id']}: {cron_expr}")
            except Exception as e:
                logger.error(f"Failed to schedule msg {msg['id']}: {e}")
    except Exception as e:
        logger.error(f"_refresh_active_message_jobs error: {e}", exc_info=True)


# Set at scheduler startup; restart is blocked for the first grace period to
# prevent restart loops when a process manager respawns in the same clock minute.
_SCHEDULER_STARTUP_TS = time.time()
_RESTART_GRACE_SECONDS = 90


def _check_restart():
    """Check if current time matches scheduled restart time."""
    try:
        restart_time = config_service.get_scheduled_restart()
        if not restart_time:
            return
        now = datetime.now(BEIJING_TZ)
        now_str = now.strftime("%H:%M")
        if now_str != restart_time:
            return
        # Guard: don't restart within grace period after startup (anti-loop)
        if time.time() - _SCHEDULER_STARTUP_TS < _RESTART_GRACE_SECONDS:
            return
        # Guard: only restart once per minute window
        last_str = getattr(_check_restart, "_last_fired", "")
        if last_str == now_str:
            return
        _check_restart._last_fired = now_str

        logger.info(f"Scheduled restart triggered at {now_str}, shutting down gracefully")
        if _wechat_bot and _wechat_bot._token:
            try:
                from wechat_api import notify_stop
                notify_stop(_wechat_bot._token, _wechat_bot._base_url)
            except Exception:
                pass
        scheduler.shutdown(wait=False)
        import os
        os._exit(0)
    except Exception as e:
        logger.error(f"_check_restart error: {e}", exc_info=True)


def _refresh_all_prompts():
    """Refresh merged prompts for all contacts with user-defined personas (every 2h)."""
    try:
        from ai_client import ai_client
        conv_ids = db.get_users_with_prompts()
        if not conv_ids:
            return
        refreshed = 0
        for conv_id in conv_ids:
            try:
                merged = ai_client.refresh_prompt(conv_id)
                if merged:
                    refreshed += 1
            except Exception as e:
                logger.error(f"Prompt refresh failed for {conv_id}: {e}")
        if refreshed > 0:
            logger.info(f"Refreshed merged prompts for {refreshed}/{len(conv_ids)} contacts")
    except Exception as e:
        logger.error(f"_refresh_all_prompts error: {e}", exc_info=True)


def _run_scheduled_chats():
    """Check scheduled_chats table every minute and send if time matches."""
    try:
        if _wechat_bot is None or not _wechat_bot._token:
            return
        if config_service.is_in_rest_time():
            return

        now = datetime.now(BEIJING_TZ).strftime("%H:%M")
        now_ms = int(time.time() * 1000)
        idle_ms = config_service.get_scheduled_chat_idle_minutes() * 60 * 1000
        chats = db.get_enabled_scheduled_chats()
        for chat in chats:
            if chat["chat_time"] != now:
                continue
            topic = chat["topic"]
            target_type = chat["target_type"]
            target_ids = json.loads(chat["target_ids"]) if chat["target_ids"] else []

            logger.info(f"Scheduled chat triggered: {now} topic='{topic}'")

            # Get target contacts
            if target_type == "all":
                contacts = db.get_all_contacts()
            else:
                contacts = target_ids

            for contact_id in contacts:
                try:
                    # Silence guard: skip if user is actively chatting
                    state = db.get_active_chat_state(contact_id)
                    if state["last_user_reply_at"] > 0:
                        if now_ms - state["last_user_reply_at"] < idle_ms:
                            logger.info(f"Scheduled chat skipped for {contact_id}: user active")
                            continue

                    from ai_client import ai_client
                    recent = db.get_recent_messages(contact_id, count=6)
                    message = ai_client.generate_active_message(
                        contact_id, topic=topic, recent_context=recent
                    )
                    if message:
                        _wechat_bot.send_reply(contact_id, message)
                        logger.info(f"Scheduled chat sent to {contact_id}: {message[:50]}...")
                        time.sleep(1)
                    else:
                        logger.warning(f"Scheduled chat: empty message for {contact_id}")
                except Exception as e:
                    logger.error(f"Scheduled chat to {contact_id} failed: {e}")
    except Exception as e:
        logger.error(f"_run_scheduled_chats error: {e}", exc_info=True)


def _run_active_chat():
    """Check if any contact is due for an active chat message."""
    try:
        if _wechat_bot is None or not _wechat_bot._token:
            return
        if not config_service.is_active_chat_enabled():
            return
        if config_service.is_in_rest_time():
            return

        from ai_client import ai_client

        contacts = db.get_all_contacts()
        cooldown_ms = config_service.get_active_chat_cooldown_minutes() * 60 * 1000
        idle_ms = config_service.get_active_chat_idle_minutes() * 60 * 1000
        max_silent = config_service.get_active_chat_max_silent()
        now_ms = int(time.time() * 1000)

        for contact_id in contacts:
            state = db.get_active_chat_state(contact_id)

            # Check cooldown
            if now_ms - state["last_active_at"] < cooldown_ms:
                continue

            # Silence guard: skip if user is actively chatting
            if state["last_user_reply_at"] > 0:
                if now_ms - state["last_user_reply_at"] < idle_ms:
                    continue

            # Check silent count (user hasn't replied to N previous active messages)
            if state["silent_count"] >= max_silent:
                continue

            # Check allowed time ranges for this contact
            settings = db.get_active_chat_settings(contact_id)
            allowed_ranges = settings.get("allowed_time_ranges", "[]")
            if not config_service.is_in_active_chat_allowed_time(allowed_ranges):
                continue

            # Pick a random trigger text
            trigger_text = ""
            try:
                texts = json.loads(settings.get("trigger_texts", "[]"))
                if texts:
                    trigger_text = random.choice(texts)
            except (json.JSONDecodeError, TypeError):
                pass

            # Also try global settings if contact-specific has no trigger texts
            if not trigger_text:
                global_settings = db.get_active_chat_settings("__global__")
                try:
                    texts = json.loads(global_settings.get("trigger_texts", "[]"))
                    if texts:
                        trigger_text = random.choice(texts)
                except (json.JSONDecodeError, TypeError):
                    pass

            try:
                recent = db.get_recent_messages(contact_id, count=6)
                message = ai_client.generate_active_message(
                    contact_id, trigger_text=trigger_text, recent_context=recent
                )
                if message:
                    _wechat_bot.send_reply(contact_id, message)
                    logger.info(f"Active chat sent to {contact_id}: {message[:50]}...")

                    # Atomically increment silent_count + update last_active_at
                    db.increment_silent_count(contact_id, delta=1, last_active_at=now_ms)
                    time.sleep(1)
                else:
                    logger.warning(f"Active chat: empty message for {contact_id}")
            except Exception as e:
                logger.error(f"Active chat to {contact_id} failed: {e}")
    except Exception as e:
        logger.error(f"_run_active_chat error: {e}", exc_info=True)


def _decay_bot_mood():
    """Gradually decay bot mood by 1 point per hour."""
    try:
        if not config_service.is_bot_mood_enabled():
            return
        if config_service.get_bot_mood() > 0:
            config_service.adjust_bot_mood(-1)
            logger.debug(f"Bot mood decayed to {config_service.get_bot_mood()}")
    except Exception as e:
        logger.error(f"_decay_bot_mood error: {e}", exc_info=True)


_last_sharing_at = 0


def _run_proactive_sharing():
    """Periodically share interesting web content with a random contact."""
    global _last_sharing_at
    try:
        if _wechat_bot is None or not _wechat_bot._token:
            return
        if not config_service.is_proactive_sharing_enabled():
            return
        if config_service.is_in_rest_time():
            return

        topics = config_service.get_proactive_sharing_topics()
        if not topics:
            return

        contacts = db.get_all_contacts()
        if not contacts:
            return

        interval_ms = config_service.get_proactive_sharing_interval_minutes() * 60 * 1000
        now_ms = int(time.time() * 1000)
        if now_ms - _last_sharing_at < interval_ms:
            return

        contact_id = random.choice(contacts)
        topic = random.choice(topics)

        idle_ms = config_service.get_active_chat_idle_minutes() * 60 * 1000
        state = db.get_active_chat_state(contact_id)
        if state["last_user_reply_at"] > 0:
            if now_ms - state["last_user_reply_at"] < idle_ms:
                logger.info(f"Proactive sharing skipped for {contact_id}: user active")
                return

        settings = db.get_active_chat_settings(contact_id)
        allowed_ranges = settings.get("allowed_time_ranges", "[]")
        if not config_service.is_in_active_chat_allowed_time(allowed_ranges):
            return

        from web_search import web_search_formatted
        search_result = web_search_formatted(topic)
        if not search_result:
            logger.info(f"Proactive sharing: no results for '{topic}'")
            return

        from ai_client import ai_client
        message = ai_client.generate_sharing_message(contact_id, topic, search_result)
        if message:
            _wechat_bot.send_reply(contact_id, message)
            logger.info(f"Proactive sharing sent to {contact_id}: {message[:60]}...")
            _last_sharing_at = now_ms
            db.increment_silent_count(contact_id, delta=1, last_active_at=now_ms)
    except Exception as e:
        logger.error(f"_run_proactive_sharing error: {e}", exc_info=True)


def _run_intimacy_update():
    """Periodically recompute intimacy scores for all conversations."""
    try:
        from intimacy_engine import (
            compute_intimacy, classify_tier, DEFAULT_TIER_CONFIG
        )
        conv_ids = db.get_all_conv_ids()
        updated = 0
        for conv_id in conv_ids:
            try:
                record = db.get_intimacy(conv_id)
                old_score = record["intimacy_score"]
                prev_tier = record.get("intimacy_tier", "new_friend")

                stats = db.get_message_stats(conv_id)
                state = db.get_active_chat_state(conv_id)

                # Estimate days since last message
                last_active = state.get("last_user_reply_at", 0)
                days_since = 0
                if last_active:
                    days_since = int((time.time() * 1000 - last_active) / (86400 * 1000))

                # Rough estimates (should be refined with actual data)
                msg_count = stats.get("user_message_count", 0)
                active_days_7 = min(msg_count // 2, 7)
                active_days_30 = min(msg_count // 2, 30)

                new_score = compute_intimacy(
                    active_days_7=active_days_7,
                    active_days_30=active_days_30,
                    avg_message_length=stats.get("avg_message_length", 0),
                    bot_question_count=stats.get("bot_question_count", 0),
                    media_count=stats.get("media_count", 0),
                    days_since_last_msg=days_since,
                    prev_tier=prev_tier,
                )
                # Classify tier with hysteresis to prevent oscillation
                new_tier = classify_tier(int(new_score), prev_tier, DEFAULT_TIER_CONFIG)
                if new_score != old_score or new_tier != prev_tier:
                    db.save_intimacy(conv_id, int(new_score), new_tier)
                    updated += 1
            except Exception as e:
                logger.error(f"Intimacy update failed for {conv_id}: {e}")
        if updated:
            logger.info(f"Intimacy scores updated for {updated} conversations")
    except Exception as e:
        logger.error(f"_run_intimacy_update error: {e}", exc_info=True)


def start_scheduler():
    """Start the background scheduler."""
    # Initial job load
    _refresh_active_message_jobs()

    # Refresh active messages every 60 seconds (picks up new/updated/deleted)
    scheduler.add_job(_refresh_active_message_jobs, "interval", seconds=60, id="am_refresh", max_instances=1, misfire_grace_time=10)

    # Check restart every 30 seconds
    scheduler.add_job(_check_restart, "interval", seconds=30, id="restart_check", max_instances=1, misfire_grace_time=10)

    # Refresh all merged prompts every 2 hours (at an off-minute to avoid fleet stampedes)
    scheduler.add_job(_refresh_all_prompts, "interval", minutes=120, id="prompt_refresh", max_instances=1, misfire_grace_time=300)

    # Scheduled chats: check every minute
    scheduler.add_job(_run_scheduled_chats, "interval", seconds=60, id="scheduled_chats", max_instances=1, misfire_grace_time=10)

    # Bot mood decay: every 60 minutes
    scheduler.add_job(_decay_bot_mood, "interval", minutes=60, id="mood_decay", max_instances=1, misfire_grace_time=120)

    # Proactive sharing: fires every 15 min, actual interval controlled by config
    scheduler.add_job(_run_proactive_sharing, "interval", seconds=900, id="proactive_sharing", max_instances=1, misfire_grace_time=30)

    # Active chat: check every 10 minutes
    scheduler.add_job(_run_active_chat, "interval", seconds=600, id="active_chat", max_instances=1, misfire_grace_time=30)

    # Bot 拟人化：每 10 分钟更新亲密度
    scheduler.add_job(
        _run_intimacy_update, "interval", seconds=600,
        id="intimacy_update", max_instances=1, misfire_grace_time=30
    )

    scheduler.start()
    logger.info("Scheduler started")


def stop_scheduler():
    scheduler.shutdown(wait=False)
