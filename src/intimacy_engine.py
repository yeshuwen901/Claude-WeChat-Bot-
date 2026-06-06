"""Intimacy scoring engine: frequency + depth + decay -> three tiers.

Drives persona weight, colloquialism, and follow-up behavior based on intimacy score.
"""

import math


# =============================================================================
# Tier configuration
# =============================================================================

class Tier:
    NEW_FRIEND = "new_friend"       # 0-30
    ACQUAINTANCE = "acquaintance"   # 30-60
    CLOSE_FRIEND = "close_friend"   # 60-100

    LABELS = {
        NEW_FRIEND: "新朋友",
        ACQUAINTANCE: "熟人",
        CLOSE_FRIEND: "老友",
    }

    @classmethod
    def label(cls, tier: str) -> str:
        return cls.LABELS.get(tier, "未知")


class TierConfig:
    def __init__(self, new_friend_max=30, acquaintance_max=60, buffer=5):
        self.new_friend_max = new_friend_max
        self.acquaintance_max = acquaintance_max
        self.buffer = buffer


DEFAULT_TIER_CONFIG = TierConfig()


def classify_tier(score: int, prev_tier: str, cfg: TierConfig | None = None) -> str:
    """Classify intimacy score into tier with hysteresis buffer.

    Once a user enters a higher tier, they only drop back after
    falling below the threshold MINUS the buffer.
    """
    if cfg is None:
        cfg = DEFAULT_TIER_CONFIG

    if prev_tier == Tier.NEW_FRIEND:
        if score > cfg.new_friend_max:
            return Tier.ACQUAINTANCE
        return Tier.NEW_FRIEND
    elif prev_tier == Tier.ACQUAINTANCE:
        if score > cfg.acquaintance_max:
            return Tier.CLOSE_FRIEND
        if score < cfg.new_friend_max - cfg.buffer:
            return Tier.NEW_FRIEND
        return Tier.ACQUAINTANCE
    elif prev_tier == Tier.CLOSE_FRIEND:
        if score < cfg.acquaintance_max - cfg.buffer:
            return Tier.ACQUAINTANCE
        return Tier.CLOSE_FRIEND
    return Tier.NEW_FRIEND


def tier_from_score(score: int, cfg: TierConfig | None = None) -> str:
    """Simple tier classification WITHOUT hysteresis (one-shot lookup)."""
    if cfg is None:
        cfg = DEFAULT_TIER_CONFIG
    if score <= cfg.new_friend_max:
        return Tier.NEW_FRIEND
    elif score <= cfg.acquaintance_max:
        return Tier.ACQUAINTANCE
    return Tier.CLOSE_FRIEND


# =============================================================================
# Scoring formula
# =============================================================================

def compute_intimacy(
    active_days_7: int,
    active_days_30: int,
    avg_message_length: float,
    bot_question_count: int,
    media_count: int,
    days_since_last_msg: int,
    prev_tier: str,
    cfg: TierConfig | None = None,
) -> int:
    """Compute intimacy score (0-100) from engagement metrics.

    Formula:
        frequency(0-40) + depth(0-40) + decay(-N) -> capped 0-100
    """
    if cfg is None:
        cfg = DEFAULT_TIER_CONFIG

    # 1. Frequency score (0-40)
    #    7-day active days * 5 (max 35)
    #    OR 30-day ratio * 40, whichever is higher
    freq: float = min(active_days_7 * 5.0, 35.0)
    ratio: float = active_days_30 / 30.0 * 40.0
    if ratio > freq:
        freq = ratio
    freq = min(freq, 40.0)

    # 2. Depth score (0-40)
    depth: float = 0.0
    # Average message length: ~200 chars = full 15 points
    depth += min(avg_message_length / 200.0 * 15.0, 15.0)
    # Bot question count: 10 questions = full 10 points
    depth += min(bot_question_count / 10.0 * 10.0, 10.0)
    # Media count: 5 = full 5 points
    depth += min(media_count / 5.0 * 5.0, 5.0)
    # Baseline topic initiation (hard to detect accurately, give a baseline)
    depth += 5.0
    depth = min(depth, 40.0)

    # 3. Time decay
    decay: int = 0
    if days_since_last_msg > 3:
        decay = -(days_since_last_msg - 3) * 2

    raw: int = int(round(freq + depth)) + decay
    raw = max(0, min(raw, 100))

    # Floor: don't drop below the minimum of the current tier
    floor = _tier_floor(prev_tier, cfg)
    raw = max(raw, floor)

    return raw


def _tier_floor(tier: str, cfg: TierConfig) -> int:
    """Minimum score for a given tier (bottom of buffer zone)."""
    if tier == Tier.ACQUAINTANCE:
        return cfg.new_friend_max - cfg.buffer
    elif tier == Tier.CLOSE_FRIEND:
        return cfg.acquaintance_max - cfg.buffer
    return 0


# =============================================================================
# Prompt weight mappings
# =============================================================================

def persona_weight(score: int, cfg: TierConfig | None = None) -> int:
    """Map intimacy score to persona adherence percentage (40/65/90)."""
    if cfg is None:
        cfg = DEFAULT_TIER_CONFIG
    if score <= cfg.new_friend_max:
        return 40
    elif score <= cfg.acquaintance_max:
        return 65
    return 90


def persona_weight_directive(weight: int) -> str:
    """Instruction telling the AI how tightly to follow the persona."""
    if weight <= 40:
        return (
            "保持友好礼貌即可，"
            "不用刻意扮演某个角色。"
            "做你自己，自然回应。"
        )
    elif weight <= 65:
        return (
            "按以下人设说话，"
            "但保持自然不做作。"
            "可以有自己的即兴发挥。"
        )
    return (
        "完全进入以下角色，"
        "像老朋友一样自然互动。"
        "你的言行都应符合这个角色。"
    )


def colloquialism_directive(score: int, cfg: TierConfig | None = None) -> str:
    """Speaking style instruction based on intimacy tier."""
    if cfg is None:
        cfg = DEFAULT_TIER_CONFIG
    if score <= cfg.new_friend_max:
        return (
            "语法完整，句式清晰。"
            "不用语气词、网络用语或梗。"
            "保持基本礼貌。"
        )
    elif score <= cfg.acquaintance_max:
        return (
            "可以自然使用语气词"
            "（嘻、嘞、咯、哈哈），"
            "偶尔用流行语和梗。"
            "句式有长短变化。"
        )
    return (
        "随意说话——用梗、吐槽、"
        "简短回复、纯表情包都可以。"
        "语气词和网络用语自由使用。"
        "不用每条都完整句式，"
        "像跟死党聊天一样。"
    )


# =============================================================================
# Follow-up question logic
# =============================================================================

def followup_max_rounds(score: int, cfg: TierConfig | None = None) -> int:
    """Max consecutive follow-up rounds based on intimacy tier."""
    if cfg is None:
        cfg = DEFAULT_TIER_CONFIG
    if score <= cfg.new_friend_max:
        return 1
    elif score <= cfg.acquaintance_max:
        return 2
    return 3


def followup_directive(intent: str, consecutive_count: int, max_rounds: int) -> str:
    """Return follow-up instruction for a given intent.

    Returns empty string if follow-up should NOT happen for this intent.
    """
    if consecutive_count >= max_rounds:
        return "这次对话请自然收尾，不要再追问。"

    directives: dict[str, str] = {
        "陈述": (
            "对方在陈述一件事——"
            "表达你的理解，"
            "然后可以自然反问一句延展话题。"
        ),
        "分享": (
            "对方在分享——"
            "先共情回应，"
            "然后追问细节让他继续说下去。"
        ),
        "吐槽": (
            "对方在吐槽——"
            "先共鸣，再反问一句。"
        ),
        "提问": "",  # Just answer, don't follow up
        "求助": "",  # Just solve, don't follow up
        "auto": (     # Default when intent is not classified
            "自然地回应对方，适当追问让对话延续。"
        ),
    }
    return directives.get(intent, directives["auto"])


def detect_followup(reply_text: str) -> bool:
    """Heuristic: does the reply end with a follow-up indicator?

    Returns True if the last char is a question mark (？ or ?),
    suggesting the bot is asking a follow-up question.
    Exclamation marks are excluded — they signal excitement/sentiment, not a question.
    """
    text = reply_text.strip()
    if not text:
        return False
    last_char = text[-1]
    return last_char in ("？", "?")
