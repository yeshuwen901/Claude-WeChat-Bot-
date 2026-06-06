"""Claude WeChat Bot - Entry point."""

import logging
import os
import sys
from pathlib import Path

# Add src/ to path so flat imports work from src directory.
# When frozen (PyInstaller), modules are bundled in the archive — no path needed.
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import uvicorn

from admin_api import app
from config import APP_DIR, config
from memory_scorer import start_memory_scorer, stop_memory_scorer
from scheduler import start_scheduler, stop_scheduler, set_bot
from wechat_bot import wechat_bot

_ENV_TEMPLATE = """\
# ============================================================
# WeChat Bot 配置文件
# 修改后重启 bot 生效
# ============================================================

# 必填：AI API Key（Anthropic 或 DeepSeek）
ANTHROPIC_API_KEY=your_api_key_here

# 模型选择（默认用 Claude Sonnet，可用 deepseek-chat 等）
# ANTHROPIC_MODEL=claude-sonnet-4-20250514

# 如使用 DeepSeek，保留上面 API Key 为你的 DeepSeek Key，并改为：
# ANTHROPIC_MODEL=deepseek-chat
# ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic

# Bot 名称（微信中显示的名字）
# BOT_NAME=小助手

# 对话保留轮数（默认 20）
# CONVERSATION_MAX_TURNS=20

# 日志级别: DEBUG / INFO / WARNING / ERROR
# LOG_LEVEL=INFO

# 管理面板端口
# ADMIN_PORT=8080
"""


def _init_first_start():
    """Create data directory and .env template on first run."""
    data_dir = Path(config.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    env_path = Path(APP_DIR) / ".env"
    if not env_path.exists():
        env_path.write_text(_ENV_TEMPLATE, encoding="utf-8")
        logging.getLogger(__name__).info(f"Created .env template at {env_path}")


def setup_logging():
    log_dir = Path(config.data_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, config.log_level.upper(), logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(str(log_dir / "bot.log"), encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )


def main():
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]

    setup_logging()
    _init_first_start()
    logger = logging.getLogger(__name__)

    host = os.getenv("ADMIN_HOST", "0.0.0.0")
    port = int(os.getenv("ADMIN_PORT", "8080"))

    logger.info("=" * 50)
    logger.info("Claude WeChat Bot starting...")
    logger.info(f"Admin panel: http://localhost:{port}")
    logger.info(f"Model: {config.anthropic_model}")
    logger.info(f"Data dir: {config.data_dir}")
    logger.info("=" * 50)

    config.validate()  # Warns if API key not set, but doesn't block

    # Start background services (scheduler, memory scorer)
    set_bot(wechat_bot)
    start_scheduler()
    start_memory_scorer()

    # Run admin server in main thread (blocking).
    # Bot login is triggered from the admin panel via POST /api/bot/start
    import threading
    import webbrowser

    def _open_browser():
        import time
        time.sleep(1.5)
        webbrowser.open(f"http://localhost:{port}")

    threading.Thread(target=_open_browser, daemon=True).start()

    from admin_api import ADMIN_TOKEN
    logger.info(f"Admin token: {ADMIN_TOKEN}")
    logger.info(f"Admin server running at http://localhost:{port}")
    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    except KeyboardInterrupt:
        logger.info("Server stopped by user.")
    finally:
        stop_scheduler()
        stop_memory_scorer()


if __name__ == "__main__":
    main()
