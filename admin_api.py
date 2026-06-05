"""FastAPI admin backend for bot behavior configuration."""

import base64
import json
import logging
import os
import re
import secrets
import sys
import time as time_mod
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config_service import config_service
from database import db
from wechat_api import fetch_qr_code

logger = logging.getLogger(__name__)

# Detect PyInstaller bundle for static (read-only) assets
if getattr(sys, "frozen", False):
    STATIC_DIR = Path(sys._MEIPASS) / "static"
else:
    STATIC_DIR = Path(__file__).parent / "static"

# Data (read-write) paths use config.data_dir — consistent with all other modules
from config import config as _bot_config
DATA_DIR = Path(_bot_config.data_dir)
UPLOAD_DIR = DATA_DIR / "stickers"
TOKEN_FILE = DATA_DIR / ".admin_token"
QR_CACHE_FILE = DATA_DIR / "qrcode.png"
QR_CACHE_META = DATA_DIR / "qrcode_meta.json"


def _init_admin_token() -> str:
    """Generate or read the admin auth token. Returns the token."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_hex(32)
    TOKEN_FILE.write_text(token, encoding="utf-8")
    # Restrict read permissions on non-Windows
    if os.name != "nt":
        try:
            os.chmod(TOKEN_FILE, 0o600)
        except OSError:
            pass
    return token


try:
    ADMIN_TOKEN = _init_admin_token()
except Exception:
    import sys as _sys
    ADMIN_TOKEN = secrets.token_hex(32)
    print(f"WARNING: Could not persist admin token to {TOKEN_FILE}: {_sys.exc_info()[1]}", file=_sys.stderr)
    print(f"Admin token (temporary): {ADMIN_TOKEN}", file=_sys.stderr)

app = FastAPI(title="Claude WeChat Bot Admin")


@app.middleware("http")
async def _auth_middleware(request: Request, call_next):
    """Require admin token for all /api/* routes."""
    if request.url.path.startswith("/api/") and request.method != "OPTIONS":
        token = request.headers.get("X-Admin-Token", "")
        if not token or not secrets.compare_digest(token, ADMIN_TOKEN):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return await call_next(request)


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = STATIC_DIR / "admin.html"
    if not html_path.exists():
        return HTMLResponse("<h1>admin.html not found</h1>", status_code=404)
    html = html_path.read_text(encoding="utf-8")
    # Inject admin token into the page
    html = html.replace(
        "</head>",
        f'<script>window.ADMIN_TOKEN = "{ADMIN_TOKEN}";</script>\n</head>',
        1,
    )
    return HTMLResponse(html)


# ── config ───────────────────────────────────────────────────────────

@app.get("/api/config")
async def get_config():
    cfg = config_service.get_all()
    # Mask API key
    key = cfg.get("api_key", "")
    if key:
        cfg["api_key"] = key[:8] + "****" + key[-4:] if len(key) > 12 else "****"
    # Also mask DashScope key
    dkey = cfg.get("dashscope_api_key", "")
    if dkey:
        cfg["dashscope_api_key"] = dkey[:8] + "****" + dkey[-4:] if len(dkey) > 12 else "****"
    return JSONResponse(cfg)


@app.put("/api/config")
async def update_config(updates: dict[str, str]):
    config_service.update_bulk(updates)
    return JSONResponse({"ok": True})


@app.put("/api/config/apikey")
async def update_api_key(body: dict):
    key = body.get("api_key", "").strip()
    if key:
        config_service.set_api_key(key)
        return JSONResponse({"ok": True, "masked": key[:8] + "****" + key[-4:] if len(key) > 12 else "****"})
    raise HTTPException(400, "api_key is required")


@app.put("/api/config/dashscope-apikey")
async def update_dashscope_api_key(body: dict):
    key = body.get("api_key", "").strip()
    if key:
        config_service.set_dashscope_api_key(key)
        return JSONResponse({"ok": True, "masked": key[:8] + "****" + key[-4:] if len(key) > 12 else "****"})
    raise HTTPException(400, "api_key is required")


# ── stickers ─────────────────────────────────────────────────────────

@app.get("/api/stickers")
async def list_stickers():
    return JSONResponse(db.list_stickers())


@app.post("/api/stickers")
async def add_sticker(body: dict):
    url = body.get("url", "")
    filename = body.get("filename", "")
    if not url:
        raise HTTPException(400, "url is required")
    sid = db.add_sticker(url, filename)
    return JSONResponse({"id": sid, "ok": True})


@app.delete("/api/stickers/{sticker_id}")
async def delete_sticker(sticker_id: int):
    db.delete_sticker(sticker_id)
    return JSONResponse({"ok": True})


ALLOWED_STICKER_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
ALLOWED_STICKER_MIMES = {
    "image/png", "image/jpeg", "image/gif", "image/webp",
}

@app.post("/api/stickers/upload")
async def upload_sticker(file: UploadFile = File(...)):
    ext = Path(file.filename or "sticker").suffix.lower()
    if ext not in ALLOWED_STICKER_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type: {ext}. Allowed: {', '.join(sorted(ALLOWED_STICKER_EXTENSIONS))}")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    save_name = f"{uuid.uuid4().hex}{ext}"
    save_path = UPLOAD_DIR / save_name
    content = await file.read()
    # Validate actual MIME type from file magic bytes
    import mimetypes
    detected = mimetypes.guess_type(save_name)[0]
    if detected not in ALLOWED_STICKER_MIMES:
        # Try using the file header directly
        if len(content) < 4:
            raise HTTPException(400, "File too small")
        header = content[:4]
        if header[:3] == b"\xff\xd8\xff":  # JPEG
            pass
        elif header[:4] == b"\x89PNG":  # PNG
            pass
        elif header[:3] == b"GIF":  # GIF
            pass
        elif header[:4] == b"RIFF":  # WebP
            pass
        else:
            raise HTTPException(400, "File content is not a valid image")
    save_path.write_bytes(content)
    url = f"/static/stickers/{save_name}"
    sid = db.add_sticker(url, save_name)
    return JSONResponse({"id": sid, "url": url, "filename": save_name, "ok": True})


# ── active messages ──────────────────────────────────────────────────

@app.get("/api/active-messages")
async def list_active_messages():
    return JSONResponse(db.list_active_messages())


@app.post("/api/active-messages")
async def create_active_message(body: dict):
    content = body.get("content", "")
    cron_expr = body.get("cron_expression", "")
    enabled = body.get("enabled", True)
    if not content:
        raise HTTPException(400, "content is required")
    mid = db.create_active_message(content, cron_expr, enabled)
    return JSONResponse({"id": mid, "ok": True})


@app.put("/api/active-messages/{msg_id}")
async def update_active_message(msg_id: int, body: dict):
    db.update_active_message(
        msg_id,
        content=body.get("content"),
        cron_expression=body.get("cron_expression"),
        enabled=body.get("enabled"),
    )
    return JSONResponse({"ok": True})


@app.delete("/api/active-messages/{msg_id}")
async def delete_active_message(msg_id: int):
    db.delete_active_message(msg_id)
    return JSONResponse({"ok": True})


# ── QR code ──────────────────────────────────────────────────────────

QR_CACHE_TTL = 300  # 5 minutes


@app.get("/api/qrcode")
async def get_qrcode():
    try:
        # Check cache
        if QR_CACHE_FILE.exists() and QR_CACHE_META.exists():
            meta = json.loads(QR_CACHE_META.read_text())
            if time_mod.time() - meta.get("ts", 0) < QR_CACHE_TTL:
                return JSONResponse({
                    "qrcode": meta.get("qrcode", ""),
                    "image_url": "/static/data/qrcode.png",
                    "cached": True,
                })

        qr = fetch_qr_code()
        qrcode_url = qr.qrcode_url or ""

        # Save QR image to file (handle data URL or regular URL)
        image_url = qrcode_url
        if qrcode_url.startswith("data:image"):
            # Extract base64 from data URL and save as file
            match = re.match(r"data:image/\w+;base64,(.+)", qrcode_url)
            if match:
                img_data = base64.b64decode(match.group(1))
                QR_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
                QR_CACHE_FILE.write_bytes(img_data)
                image_url = "/static/data/qrcode.png"
        elif qrcode_url.startswith("http"):
            # Download the QR image from URL and cache locally
            import urllib.request
            try:
                req = urllib.request.Request(qrcode_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    QR_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
                    QR_CACHE_FILE.write_bytes(resp.read())
                image_url = "/static/data/qrcode.png"
            except Exception:
                pass  # Fall through to use original URL

        QR_CACHE_META.parent.mkdir(parents=True, exist_ok=True)
        QR_CACHE_META.write_text(json.dumps({
            "qrcode": qr.qrcode,
            "ts": time_mod.time(),
        }))

        return JSONResponse({
            "qrcode": qr.qrcode,
            "image_url": image_url,
            "cached": False,
        })
    except Exception as e:
        raise HTTPException(500, f"Failed to fetch QR code: {e}")


@app.get("/static/data/qrcode.png")
async def serve_qrcode_image():
    """Serve cached QR code image (limited scope, not a full static mount)."""
    if not QR_CACHE_FILE.exists():
        raise HTTPException(404, "No QR code cached")
    from fastapi.responses import FileResponse
    return FileResponse(str(QR_CACHE_FILE), media_type="image/png")


# ── user prompts ───────────────────────────────────────────────────

@app.get("/api/user-prompts")
async def list_user_prompts():
    return JSONResponse(db.list_user_prompts())


@app.get("/api/user-prompts/{conv_id}")
async def get_user_prompt(conv_id: str):
    full = db.get_user_prompt_full(conv_id)
    if full:
        return JSONResponse({
            "conv_id": conv_id,
            "prompt": full.get("prompt") or "",
            "language_habits": full.get("language_habits") or "{}",
            "merged_prompt": full.get("merged_prompt") or "",
            "habits_updated_at": full.get("habits_updated_at") or 0,
        })
    return JSONResponse({"conv_id": conv_id, "prompt": ""})


@app.put("/api/user-prompts/{conv_id}")
async def set_user_prompt(conv_id: str, body: dict):
    db.set_user_prompt(conv_id, body.get("prompt", ""))
    return JSONResponse({"ok": True})


@app.delete("/api/user-prompts/{conv_id}")
async def delete_user_prompt(conv_id: str):
    db.delete_user_prompt(conv_id)
    return JSONResponse({"ok": True})


# ── language habit analysis & merged prompt ────────────────────────

@app.post("/api/contacts/{conv_id}/analyze")
async def analyze_language_habits(conv_id: str):
    """Analyze language habits for a contact from recent chat history."""
    from ai_client import ai_client
    habits = ai_client.analyze_language_habits(conv_id)
    if not habits:
        raise HTTPException(400, "No messages found for this contact")
    return JSONResponse({"ok": True, "habits": habits})


@app.post("/api/contacts/{conv_id}/prompt/refresh")
async def refresh_prompt(conv_id: str):
    """Full pipeline: analyze habits → merge with persona → save."""
    from ai_client import ai_client
    merged = ai_client.refresh_prompt(conv_id)
    if merged is None:
        full = db.get_user_prompt_full(conv_id)
        if not full or not (full.get("prompt") or "").strip():
            raise HTTPException(400, "No persona prompt set for this contact")
        raise HTTPException(400, "Failed to analyze or generate merged prompt")
    return JSONResponse({"ok": True, "merged_prompt": merged})


# ── sticker emotions ────────────────────────────────────────────────

@app.get("/api/stickers/with-emotions")
async def list_stickers_with_emotions():
    return JSONResponse(db.get_all_stickers_with_emotions())


@app.put("/api/stickers/{sticker_id}/emotions")
async def set_sticker_emotions(sticker_id: int, body: dict):
    emotions = body.get("emotions", [])
    db.set_sticker_emotions(sticker_id, emotions)
    return JSONResponse({"ok": True})


# ── sticker sync ──────────────────────────────────────────────────

@app.post("/api/stickers/sync")
async def sync_stickers():
    """Scan data/stickers/ directories and register files in DB."""
    import os as _os
    STICKER_ROOT = UPLOAD_DIR
    EMOTIONS = ["happy", "sad", "angry", "surprised", "love", "neutral"]
    added = 0
    for emotion in EMOTIONS:
        folder = STICKER_ROOT / emotion
        if not folder.is_dir():
            continue
        for f in sorted(folder.iterdir()):
            if not f.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                continue
            # Build relative URL path
            url = f"/static/stickers/{emotion}/{f.name}"
            # Check if already exists by URL
            existing = db.list_stickers()
            if any(s["url"] == url for s in existing):
                continue
            sid = db.add_sticker(url, f.name)
            db.set_sticker_emotions(sid, [emotion])
            added += 1
    return JSONResponse({"ok": True, "added": added})


# ── test chat ─────────────────────────────────────────────────────

@app.post("/api/test-chat")
async def test_chat(body: dict):
    """Send a test message to AI and get reply (without WeChat)."""
    message = body.get("message", "").strip()
    if not message:
        raise HTTPException(400, "message is required")

    from ai_client import ai_client
    from config import config as bot_config
    reply = ai_client.chat(
        [{"role": "user", "content": message}],
        contact_name="TestUser",
        is_room=False,
        conv_id="_test_",
    )
    return JSONResponse({"reply": reply, "model": bot_config.anthropic_model})


# ── contacts ────────────────────────────────────────────────────────

@app.get("/api/contacts")
async def list_contacts():
    return JSONResponse(db.get_all_contacts())


# ── bot lifecycle ───────────────────────────────────────────────────

@app.post("/api/bot/start")
async def start_bot():
    """Start the WeChat login flow in background."""
    from wechat_bot import start_async_login, get_login_state
    state = get_login_state()
    if state["status"] in ("fetching_qr", "waiting_scan", "connected"):
        return JSONResponse({"ok": True, "status": state["status"]})
    start_async_login()
    return JSONResponse({"ok": True, "status": "fetching_qr"})


@app.get("/api/bot/status")
async def get_bot_status():
    """Get current bot login state (QR code, scan status, connection)."""
    from wechat_bot import get_login_state
    return JSONResponse(get_login_state())


# ── scheduled chats (F1) ────────────────────────────────────────────

@app.get("/api/scheduled-chats")
async def list_scheduled_chats():
    return JSONResponse(db.list_scheduled_chats())


@app.post("/api/scheduled-chats")
async def create_scheduled_chat(body: dict):
    chat_time = body.get("chat_time", "").strip()
    topic = body.get("topic", "").strip()
    target_type = body.get("target_type", "all")
    target_ids = body.get("target_ids", [])
    if not chat_time:
        raise HTTPException(400, "chat_time is required")
    sid = db.create_scheduled_chat(chat_time, topic, target_type, target_ids)
    return JSONResponse({"id": sid, "ok": True})


@app.put("/api/scheduled-chats/{chat_id}")
async def update_scheduled_chat(chat_id: int, body: dict):
    kwargs = {}
    for k in ("chat_time", "topic", "target_type", "target_ids", "enabled"):
        if k in body:
            kwargs[k] = body[k]
    db.update_scheduled_chat(chat_id, **kwargs)
    return JSONResponse({"ok": True})


@app.delete("/api/scheduled-chats/{chat_id}")
async def delete_scheduled_chat(chat_id: int):
    db.delete_scheduled_chat(chat_id)
    return JSONResponse({"ok": True})


# ── active chat settings (F2) ───────────────────────────────────────

@app.get("/api/active-chat-settings")
async def get_active_chat_settings():
    conv_id = "__global__"
    return JSONResponse(db.get_active_chat_settings(conv_id))


@app.get("/api/active-chat-settings/{conv_id}")
async def get_active_chat_settings_for_contact(conv_id: str):
    return JSONResponse(db.get_active_chat_settings(conv_id))


@app.put("/api/active-chat-settings/{conv_id}")
async def set_active_chat_settings(conv_id: str, body: dict):
    trigger_texts = body.get("trigger_texts", "[]")
    cooldown_minutes = body.get("cooldown_minutes", 60)
    allowed_time_ranges = body.get("allowed_time_ranges", "[]")
    if isinstance(trigger_texts, list):
        trigger_texts = json.dumps(trigger_texts, ensure_ascii=False)
    if isinstance(allowed_time_ranges, list):
        allowed_time_ranges = json.dumps(allowed_time_ranges, ensure_ascii=False)
    db.set_active_chat_settings(conv_id, trigger_texts, cooldown_minutes, allowed_time_ranges)
    return JSONResponse({"ok": True})



# ── intimacy & bot humanization ───────────────────────────────

@app.get("/api/intimacy/{conv_id}")
async def get_intimacy(conv_id: str):
    """Get intimacy details for a contact."""
    try:
        record = db.get_intimacy(conv_id)
        if not record:
            return JSONResponse({"conv_id": conv_id, "intimacy_score": 10, "tier": "新朋友"})
        from intimacy_engine import classify_tier, persona_weight, followup_max_rounds, Tier
        score = record.get("intimacy_score", 10)
        prev_tier = record.get("intimacy_tier", "new_friend")
        tier_str = classify_tier(score, prev_tier)
        return JSONResponse({
            "conv_id": conv_id,
            "intimacy_score": score,
            "intimacy_updated_at": record.get("intimacy_updated_at", 0),
            "tier": Tier.label(tier_str),
            "persona_weight": persona_weight(score),
            "followup_max_rounds": followup_max_rounds(score),
        })
    except Exception as e:
        raise HTTPException(500, f"Failed to get intimacy: {e}")


@app.get("/api/contacts/with-intimacy")
async def list_contacts_with_intimacy():
    """List all contacts with intimacy scores."""
    from intimacy_engine import classify_tier, persona_weight, Tier
    conv_ids = db.get_all_contacts()
    # Batch fetch all intimacy scores in a single query (was N+1)
    intimacy_map = db.get_all_intimacy_scores(conv_ids)
    results = []
    for cid in conv_ids:
        record = intimacy_map.get(cid, {"intimacy_score": 10, "intimacy_tier": "new_friend"})
        score = record.get("intimacy_score", 10)
        prev_tier = record.get("intimacy_tier", "new_friend")
        tier_str = classify_tier(score, prev_tier)
        results.append({
            "conv_id": cid,
            "intimacy_score": score,
            "tier": Tier.label(tier_str),
            "persona_weight": persona_weight(score),
        })
    results.sort(key=lambda x: x["intimacy_score"], reverse=True)
    return JSONResponse(results)


@app.post("/api/intimacy/{conv_id}/reset")
async def reset_intimacy(conv_id: str):
    """Reset intimacy score for a contact back to default (10)."""
    db.save_intimacy(conv_id, 10)
    return JSONResponse({"ok": True, "conv_id": conv_id, "intimacy_score": 10})


# ── status ───────────────────────────────────────────────────────────


@app.get("/api/status")
async def get_status():
    from middleware import auto_reply
    from wechat_bot import get_login_state
    from config import config as bot_config
    login_state = get_login_state()
    return JSONResponse({
        "running": login_state["status"] == "connected",
        "account_id": login_state.get("account_id", ""),
        "model": bot_config.anthropic_model,
        "auto_reply": auto_reply.enabled,
        "login_status": login_state["status"],
        "qrcode_url": login_state.get("qrcode_url", ""),
    })


# Static file mounts (after all routes)
if UPLOAD_DIR.exists():
    app.mount("/static/stickers", StaticFiles(directory=str(UPLOAD_DIR)), name="stickers_static")
else:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static/stickers", StaticFiles(directory=str(UPLOAD_DIR)), name="stickers_static")
