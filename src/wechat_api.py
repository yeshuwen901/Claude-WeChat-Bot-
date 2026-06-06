"""Tencent WeChat ilink API client.

Official API at https://ilinkai.weixin.qq.com.
Based on @tencent-weixin/openclaw-weixin protocol analysis.
"""

import base64
import json
import logging
import os
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

import urllib.request
import urllib.error


API_BASE_URL = "https://ilinkai.weixin.qq.com"
ILINK_APP_ID = "bot"
CHANNEL_VERSION = "2.4.4"
DEFAULT_BOT_TYPE = "3"


def _build_client_version(version: str) -> int:
    parts = [int(p) for p in version.split(".")]
    major = parts[0] if len(parts) > 0 else 0
    minor = parts[1] if len(parts) > 1 else 0
    patch = parts[2] if len(parts) > 2 else 0
    return ((major & 0xFF) << 16) | ((minor & 0xFF) << 8) | (patch & 0xFF)


ILINK_APP_CLIENT_VERSION = _build_client_version(CHANNEL_VERSION)


def _random_wechat_uin() -> str:
    n = random.getrandbits(32)
    return base64.b64encode(str(n).encode()).decode()


def _build_base_info() -> dict:
    return {
        "channel_version": CHANNEL_VERSION,
        "bot_agent": "ClaudeWeChatBot",
    }


def _build_headers(token: Optional[str] = None) -> dict:
    h = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
        "X-WECHAT-UIN": _random_wechat_uin(),
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _api_get(endpoint: str, timeout: float = 15) -> str:
    url = f"{API_BASE_URL}/{endpoint}"
    headers = {
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GET {endpoint} HTTP {e.code}: {body}")


def _api_post(endpoint: str, body: dict, token: Optional[str] = None, timeout: float = 35) -> str:
    url = f"{API_BASE_URL}/{endpoint}"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = _build_headers(token)
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"POST {endpoint} HTTP {e.code}: {body_text}")


# ── QR Login ────────────────────────────────────────────────────────────

@dataclass
class QRCodeResult:
    qrcode: str
    qrcode_url: str
    session_key: str


def fetch_qr_code(bot_type: str = DEFAULT_BOT_TYPE) -> QRCodeResult:
    """Get QR code for WeChat login."""
    body = {"local_token_list": []}
    raw = _api_post(
        f"ilink/bot/get_bot_qrcode?bot_type={bot_type}",
        body=body,
    )
    data = json.loads(raw)
    return QRCodeResult(
        qrcode=data["qrcode"],
        qrcode_url=data.get("qrcode_img_content", ""),
        session_key=str(uuid.uuid4()),
    )


@dataclass
class LoginResult:
    connected: bool
    already_connected: bool = False
    bot_token: Optional[str] = None
    account_id: Optional[str] = None
    base_url: Optional[str] = None
    user_id: Optional[str] = None
    message: str = ""


def poll_qr_status(qrcode: str, verify_code: Optional[str] = None, timeout: float = 35) -> dict:
    """Poll QR code scan status (long-poll)."""
    ep = f"ilink/bot/get_qrcode_status?qrcode={qrcode}"
    if verify_code:
        ep += f"&verify_code={verify_code}"
    try:
        raw = _api_get(ep, timeout=timeout)
        return json.loads(raw)
    except Exception:
        logger.debug("poll_qr_status network error, treating as wait", exc_info=True)
        return {"status": "wait"}


def wait_for_login(
    qr: QRCodeResult,
    timeout_minutes: float = 8,
    bot_type: str = DEFAULT_BOT_TYPE,
) -> LoginResult:
    """Poll until user scans QR code and confirms login."""
    deadline = time.time() + timeout_minutes * 60
    scanned_printed = False
    refresh_count = 0
    pending_verify_code: Optional[str] = None

    while time.time() < deadline:
        status_resp = poll_qr_status(qr.qrcode, verify_code=pending_verify_code)
        status = status_resp.get("status", "wait")

        if status == "wait":
            print(".", end="", flush=True)
            pending_verify_code = None

        elif status == "scaned":
            if pending_verify_code:
                pending_verify_code = None
            if not scanned_printed:
                print("\nScanned! Waiting for confirmation...")
                scanned_printed = True

        elif status == "need_verifycode":
            pending_verify_code = input(
                "\nEnter the 6-digit code shown on your phone: "
            ).strip()

        elif status == "confirmed":
            print("\nLogin confirmed!")
            return LoginResult(
                connected=True,
                bot_token=status_resp.get("bot_token"),
                account_id=status_resp.get("ilink_bot_id"),
                base_url=status_resp.get("baseurl"),
                user_id=status_resp.get("ilink_user_id"),
                message="Connected to WeChat!",
            )

        elif status == "expired":
            refresh_count += 1
            if refresh_count > 3:
                return LoginResult(connected=False, message="QR code expired too many times.")
            print(f"\nQR expired, refreshing ({refresh_count}/3)...")
            new_qr = fetch_qr_code(bot_type)
            qr.qrcode = new_qr.qrcode
            qr.qrcode_url = new_qr.qrcode_url
            scanned_printed = False
            pending_verify_code = None
            print(f"\nNew QR URL: {new_qr.qrcode_url}")
            print("Scan with WeChat:")

        elif status == "binded_redirect":
            print("\nAlready connected to this OpenClaw instance.")
            return LoginResult(
                connected=False,
                already_connected=True,
                message="Already connected.",
            )

        elif status == "scaned_but_redirect":
            pass  # Will use new host from redirect_host

        elif status == "verify_code_blocked":
            print("\nToo many wrong attempts. Refreshing QR...")
            pending_verify_code = None
            refresh_count += 1
            if refresh_count > 3:
                return LoginResult(connected=False, message="Too many wrong attempts.")
            new_qr = fetch_qr_code(bot_type)
            qr.qrcode = new_qr.qrcode
            qr.qrcode_url = new_qr.qrcode_url
            scanned_printed = False

        else:
            print(f"\nUnknown status: {status}")

        time.sleep(1)

    return LoginResult(connected=False, message="Login timed out.")


# ── Message Operations ──────────────────────────────────────────────────

def notify_start(token: str, base_url: str | None = None) -> dict:
    """Notify server that bot is starting."""
    body = {"base_info": _build_base_info()}
    raw = _api_post(
        "ilink/bot/msg/notifystart",
        body,
        token=token,
        timeout=10,
    )
    return json.loads(raw)


def notify_stop(token: str, base_url: str | None = None) -> dict:
    """Notify server that bot is stopping."""
    body = {"base_info": _build_base_info()}
    raw = _api_post(
        "ilink/bot/msg/notifystop",
        body,
        token=token,
        timeout=10,
    )
    return json.loads(raw)


def get_updates(token: str, get_updates_buf: str = "", timeout: float = 35) -> dict:
    """Long-poll for new messages."""
    body = {
        "get_updates_buf": get_updates_buf,
        "base_info": _build_base_info(),
    }
    try:
        raw = _api_post("ilink/bot/getupdates", body, token=token, timeout=timeout)
        return json.loads(raw)
    except Exception as e:
        err_str = str(e)
        if "timed out" in err_str.lower() or "timeout" in err_str.lower():
            return {"ret": 0, "msgs": [], "get_updates_buf": get_updates_buf}
        raise


def send_message(
    token: str,
    to_user_id: str,
    text: str,
    context_token: Optional[str] = None,
    client_id: Optional[str] = None,
) -> str:
    """Send a text message to a user."""
    if client_id is None:
        client_id = str(uuid.uuid4())
    body: dict = {
        "msg": {
            "from_user_id": "",
            "to_user_id": to_user_id,
            "client_id": client_id,
            "message_type": 2,  # BOT
            "message_state": 2,  # FINISH
            "item_list": [
                {"type": 1, "text_item": {"text": text}}  # TEXT
            ],
        },
        "base_info": _build_base_info(),
    }
    if context_token:
        body["msg"]["context_token"] = context_token

    _api_post("ilink/bot/sendmessage", body, token=token, timeout=15)
    return client_id
