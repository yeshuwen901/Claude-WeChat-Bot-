"""Voice: STT (speech-to-text), CDN upload, image/sticker messaging."""

import base64
import hashlib
import json
import logging
import os
import struct
import tempfile
import time
import uuid
from pathlib import Path

import urllib.request
import urllib.error

from wechat_api import (
    API_BASE_URL,
    _build_headers,
    _build_base_info,
    ILINK_APP_CLIENT_VERSION,
)

logger = logging.getLogger(__name__)

DATA_DIR = Path("./data/voice")
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── AES-128-ECB ────────────────────────────────────────────────────────

def aes_ecb_padded_size(plaintext_size: int) -> int:
    return ((plaintext_size + 1 + 15) // 16) * 16


def aes_ecb_encrypt(data: bytes, key: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    encryptor = cipher.encryptor()
    pad_len = 16 - (len(data) % 16)
    padded = data + bytes([pad_len] * pad_len)
    return encryptor.update(padded) + encryptor.finalize()


def aes_ecb_decrypt(data: bytes, key: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    decryptor = cipher.decryptor()
    padded = decryptor.update(data) + decryptor.finalize()
    pad_len = padded[-1]
    if pad_len > 16:
        return padded  # PKCS7 pad byte is invalid, return raw
    return padded[:-pad_len]


def aes_cbc_decrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(data) + decryptor.finalize()
    pad_len = padded[-1]
    if pad_len > 16:
        return padded
    return padded[:-pad_len]


def _parse_aes_key(raw: str) -> bytes | None:
    """Parse AES key from hex, base64-of-hex, or base64-of-bytes format."""
    if not raw:
        return None
    # Hex string: 32 chars = AES-128
    if len(raw) == 32:
        try:
            return bytes.fromhex(raw)
        except ValueError:
            pass
    # Base64-encoded hex string (44 chars, ends with =)
    if len(raw) == 44 and raw.endswith("="):
        try:
            decoded = base64.b64decode(raw)
            decoded_str = decoded.decode("ascii")
            if len(decoded_str) == 32:
                return bytes.fromhex(decoded_str)
        except Exception:
            pass
    # Raw base64 key
    try:
        decoded = base64.b64decode(raw)
        if len(decoded) in (16, 24, 32):
            return decoded
    except Exception:
        pass
    return None


_VALID_IMAGE_HEADERS = (b"\x89PNG", b"\xff\xd8", b"GIF8", b"RIFF", b"BM")


def _is_valid_image(data: bytes) -> bool:
    return data.startswith(b"\x89PNG") or \
           data.startswith(b"\xff\xd8") or \
           data.startswith(b"GIF8") or \
           data.startswith(b"RIFF") or \
           data.startswith(b"BM")


# ── CDN Upload ─────────────────────────────────────────────────────────

def get_upload_url(
    token: str,
    to_user_id: str,
    filekey: str,
    media_type: int,
    rawsize: int,
    rawfilemd5: str,
    filesize: int,
    aeskey_hex: str,
    base_url: str = API_BASE_URL,
) -> dict:
    """Get pre-signed CDN upload URL for a file."""
    body = {
        "filekey": filekey,
        "media_type": media_type,
        "to_user_id": to_user_id,
        "rawsize": rawsize,
        "rawfilemd5": rawfilemd5,
        "filesize": filesize,
        "no_need_thumb": True,
        "aeskey": aeskey_hex,
        "base_info": _build_base_info(),
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = _build_headers(token)
    url = f"{base_url}/ilink/bot/getuploadurl"

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"getUploadUrl HTTP {e.code}: {body_text}")


def upload_to_cdn(
    plaintext: bytes,
    upload_full_url: str,
    aeskey: bytes,
) -> str:
    """Upload encrypted file to CDN, return download encrypted query param."""
    ciphertext = aes_ecb_encrypt(plaintext, aeskey)

    req = urllib.request.Request(
        upload_full_url,
        data=ciphertext,
        headers={"Content-Type": "application/octet-stream"},
        method="POST",
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                download_param = resp.headers.get("x-encrypted-param", "")
                if not download_param:
                    raise RuntimeError("CDN response missing x-encrypted-param")
                return download_param
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                raise RuntimeError(f"CDN client error {e.code}")
            if attempt >= 2:
                raise
            time.sleep(2 ** attempt)


def upload_audio_file(
    token: str,
    to_user_id: str,
    file_path: str,
    media_type: int = 4,
    base_url: str = API_BASE_URL,
) -> dict:
    """Upload a file to WeChat CDN. Returns upload info dict."""
    import os as _os

    with open(file_path, "rb") as f:
        plaintext = f.read()

    rawsize = len(plaintext)
    rawfilemd5 = hashlib.md5(plaintext).hexdigest()
    filesize = aes_ecb_padded_size(rawsize)
    filekey = os.urandom(16).hex()
    aeskey = os.urandom(16)
    aeskey_hex = aeskey.hex()
    filename = _os.path.basename(file_path)

    logger.info(
        f"Uploading file: rawsize={rawsize} ciphertext_size={filesize} "
        f"md5={rawfilemd5} media_type={media_type}"
    )

    url_resp = get_upload_url(
        token, to_user_id, filekey,
        media_type=media_type,
        rawsize=rawsize,
        rawfilemd5=rawfilemd5,
        filesize=filesize,
        aeskey_hex=aeskey_hex,
        base_url=base_url,
    )

    CDN_BASE = "https://novac2c.cdn.weixin.qq.com/c2c"

    upload_full_url = (url_resp.get("upload_full_url", "") or "").strip()
    upload_param = (url_resp.get("upload_param", "") or "").strip()

    if not upload_full_url and not upload_param:
        raise RuntimeError(f"getUploadUrl returned no upload URL: {url_resp}")

    if upload_full_url:
        cdn_url = upload_full_url
    else:
        from urllib.parse import quote
        cdn_url = (
            f"{CDN_BASE}/upload?"
            f"encrypted_query_param={quote(upload_param)}"
            f"&filekey={quote(filekey)}"
        )

    download_param = upload_to_cdn(plaintext, cdn_url, aeskey)

    return {
        "filekey": filekey,
        "download_encrypted_query_param": download_param,
        "aeskey_hex": aeskey_hex,
        "file_size": rawsize,
        "file_size_ciphertext": filesize,
        "filename": filename,
    }


# ── Media helpers ──────────────────────────────────────────────────────

def _build_media(uploaded: dict) -> dict:
    """Build CDNMedia dict matching reference implementation."""
    return {
        "encrypt_query_param": uploaded["download_encrypted_query_param"],
        "aes_key": base64.b64encode(
            uploaded["aeskey_hex"].encode("utf-8")
        ).decode(),
        "encrypt_type": 1,
    }


# ── Send Image Message ──────────────────────────────────────────────────

def send_image_message(
    token: str,
    to_user_id: str,
    uploaded: dict,
    context_token: str | None = None,
    base_url: str = API_BASE_URL,
) -> str:
    """Send an IMAGE type message via ilink API."""
    client_id = str(uuid.uuid4())
    body = {
        "msg": {
            "from_user_id": "",
            "to_user_id": to_user_id,
            "client_id": client_id,
            "message_type": 2,
            "message_state": 2,
            "item_list": [{
                "type": 2,
                "image_item": {
                    "media": _build_media(uploaded),
                },
            }],
        },
        "base_info": _build_base_info(),
    }
    if context_token:
        body["msg"]["context_token"] = context_token

    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = _build_headers(token)
    url = f"{base_url}/ilink/bot/sendmessage"

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_data = resp.read().decode("utf-8", errors="replace")
            logger.info(f"sendImageMessage response: {resp_data[:300]}")
        return client_id
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"sendImageMessage HTTP {e.code}: {body_text}")


def send_sticker(
    token: str,
    to_user_id: str,
    image_path: str,
    context_token: str | None = None,
    base_url: str = API_BASE_URL,
) -> bool:
    """Full pipeline: upload local image to CDN → send as IMAGE message."""
    try:
        uploaded = upload_audio_file(
            token, to_user_id, image_path,
            media_type=1, base_url=base_url,
        )
        logger.info(f"Sticker uploaded: filekey={uploaded['filekey']}")

        send_image_message(
            token, to_user_id, uploaded,
            context_token=context_token, base_url=base_url,
        )
        logger.info(f"Sticker sent: {image_path}")
        return True
    except Exception as e:
        logger.error(f"send_sticker failed: {e}")
        return False


# ── Image Download ─────────────────────────────────────────────────────

def _download_thumbnail(image_item: dict) -> tuple[bytes, str] | None:
    """Download thumbnail version of an image (often unencrypted)."""
    thumb_url = image_item.get("thumb_url", "") or image_item.get("cdn_thumb_url", "")
    if not thumb_url and isinstance(image_item.get("media"), dict):
        thumb_url = image_item["media"].get("thumb_url", "")
    if not thumb_url:
        return None

    try:
        logger.info(f"download_image: trying thumbnail: {thumb_url[:120]}")
        req = urllib.request.Request(thumb_url, headers={"User-Agent": "Mozilla/5.0"}, method="GET")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
        if _is_valid_image(data):
            mime = _guess_image_mime(data)
            logger.info(f"download_image: thumbnail success, {len(data)} bytes, {mime}")
            return data, mime
    except Exception as e:
        logger.warning(f"download_image: thumbnail failed: {e}")
    return None


def _try_decrypt_image(ciphertext: bytes, aes_key: bytes, label: str) -> tuple[bytes, str] | None:
    """Try multiple AES modes to decrypt ciphertext. Returns (data, mime) or None."""
    for mode_label, decrypt_fn in [
        ("ECB", aes_ecb_decrypt),
    ]:
        try:
            plaintext = decrypt_fn(ciphertext, aes_key)
            if _is_valid_image(plaintext):
                mime = _guess_image_mime(plaintext)
                logger.info(f"download_image: AES-{mode_label} success ({label}), {len(plaintext)} bytes, {mime}")
                return plaintext, mime
        except Exception as e:
            logger.warning(f"download_image: AES-{mode_label} error ({label}): {e}")

    # CBC with zero IV
    try:
        plaintext = aes_cbc_decrypt(ciphertext, aes_key, bytes(16))
        if _is_valid_image(plaintext):
            mime = _guess_image_mime(plaintext)
            logger.info(f"download_image: AES-CBC-zeroIV success ({label}), {len(plaintext)} bytes, {mime}")
            return plaintext, mime
    except Exception:
        pass

    # CBC with IV = first 16 bytes of ciphertext (skip them as IV)
    if len(ciphertext) > 16:
        try:
            iv = ciphertext[:16]
            ct = ciphertext[16:]
            plaintext = aes_cbc_decrypt(ct, aes_key, iv)
            if _is_valid_image(plaintext):
                mime = _guess_image_mime(plaintext)
                logger.info(f"download_image: AES-CBC-skip16 success ({label}), {len(plaintext)} bytes, {mime}")
                return plaintext, mime
        except Exception:
            pass

    return None


def download_image(image_item: dict, token: str = "") -> tuple[bytes, str] | None:
    """Download an image from WeChat CDN. Returns (data, mime_type) or None."""
    import base64 as _b64

    image_item_keys = list(image_item.keys())
    logger.info(f"download_image: image_item keys={image_item_keys}")
    media = image_item.get("media", {})
    if isinstance(media, dict):
        media_keys = list(media.keys())
        logger.info(f"download_image: media keys={media_keys}")

    # Case 1: Use full_url from media (direct download, no decryption needed)
    full_url = ""
    if isinstance(media, dict):
        full_url = media.get("full_url", "") or media.get("url", "")
    if not full_url:
        full_url = image_item.get("full_url", "") or image_item.get("url", "") or image_item.get("image_url", "")

    if full_url:
        logger.info(f"download_image: trying full_url: {full_url[:120]}...")
        try:
            req = urllib.request.Request(full_url, headers={"User-Agent": "Mozilla/5.0"}, method="GET")
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            if _is_valid_image(data):
                mime = _guess_image_mime(data)
                logger.info(f"download_image: full_url success, {len(data)} bytes, {mime}")
                return data, mime
            logger.warning(f"download_image: full_url returned non-image: {data[:30].hex()}")
        except Exception as e:
            logger.error(f"download_image: full_url failed: {e}")

    # Case 2: Download via ilink API (authenticated, for CDN images)
    filekey = image_item.get("filekey", "") or (media.get("filekey", "") if isinstance(media, dict) else "")
    if filekey:
        import json as _json
        try:
            if token:
                body = _json.dumps({
                    "filekey": filekey,
                    "base_info": _build_base_info(),
                }, ensure_ascii=False).encode("utf-8")
                headers = _build_headers(token)
                for endpoint in [
                    f"{API_BASE_URL}/ilink/bot/getmedia",
                    f"{API_BASE_URL}/ilink/bot/download",
                ]:
                    try:
                        logger.info(f"download_image: trying ilink API: {endpoint}")
                        req = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
                        with urllib.request.urlopen(req, timeout=30) as resp:
                            raw = resp.read()
                        data = _json.loads(raw)
                        b64 = data.get("data", "") or data.get("content", "") or data.get("base64", "")
                        if b64:
                            img_data = _b64.b64decode(b64)
                            if _is_valid_image(img_data):
                                mime = _guess_image_mime(img_data)
                                logger.info(f"download_image: ilink API success, {len(img_data)} bytes, {mime}")
                                return img_data, mime
                        new_url = data.get("url", "") or data.get("download_url", "")
                        if new_url:
                            logger.info(f"download_image: ilink returned URL: {new_url[:100]}")
                            req2 = urllib.request.Request(new_url, headers={"User-Agent": "Mozilla/5.0"}, method="GET")
                            with urllib.request.urlopen(req2, timeout=30) as resp2:
                                img_data = resp2.read()
                            if _is_valid_image(img_data):
                                mime = _guess_image_mime(img_data)
                                logger.info(f"download_image: ilink URL success, {len(img_data)} bytes, {mime}")
                                return img_data, mime
                    except Exception as e:
                        logger.warning(f"download_image: ilink {endpoint} failed: {e}")
        except Exception as e:
            logger.warning(f"download_image: ilink approach failed: {e}")

    # Case 3: CDN encrypted download via encrypt_query_param
    if isinstance(media, dict):
        encrypt_param = media.get("encrypt_query_param", "") or media.get("encrypt_param", "")
        raw_key = image_item.get("aeskey", "") or media.get("aes_key", "") or media.get("aeskey", "")
        aes_key = _parse_aes_key(raw_key)
        filekey = image_item.get("filekey", "") or media.get("filekey", "") or media.get("file_id", "")

        if encrypt_param:
            from urllib.parse import quote
            patterns = []
            if filekey:
                patterns.append((
                    f"https://novac2c.cdn.weixin.qq.com/c2c/download?"
                    f"encrypted_query_param={quote(encrypt_param)}&filekey={quote(filekey)}",
                    "c2c+filekey"
                ))
            patterns.append((
                f"https://novac2c.cdn.weixin.qq.com/c2c/download?"
                f"encrypted_query_param={quote(encrypt_param)}",
                "c2c"
            ))

            for download_url, label in patterns:
                logger.info(f"download_image: trying CDN [{label}]: {download_url[:120]}")
                try:
                    req = urllib.request.Request(download_url, headers={"User-Agent": "Mozilla/5.0"}, method="GET")
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        ciphertext = resp.read()
                    logger.info(f"download_image: CDN [{label}] got {len(ciphertext)} bytes")

                    # Raw image (unencrypted)?
                    if _is_valid_image(ciphertext):
                        mime = _guess_image_mime(ciphertext)
                        logger.info(f"download_image: CDN raw is valid image, {mime}")
                        return ciphertext, mime

                    # Try AES decryption with parsed key
                    if aes_key:
                        result = _try_decrypt_image(ciphertext, aes_key, label)
                        if result:
                            return result

                    logger.warning(f"download_image: CDN [{label}] decrypt failed, header: {ciphertext[:30].hex()}")
                except Exception as e:
                    logger.warning(f"download_image: CDN [{label}] error: {e}")

    # Case 4: Thumbnail fallback
    result = _download_thumbnail(image_item)
    if result:
        return result

    logger.warning("download_image: all methods failed")
    return None


def _guess_image_mime(data: bytes) -> str:
    """Guess MIME type from image header bytes."""
    if data[:4] == b"\x89PNG":
        return "image/png"
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data[:6] == b"GIF89a" or data[:6] == b"GIF87a":
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


# ── Voice Download ─────────────────────────────────────────────────────

def download_voice(voice_item: dict) -> bytes | None:
    """Download a voice message audio file from WeChat CDN.

    Returns raw audio bytes (SILK/AMR/MP3 depending on WeChat format),
    or None if download fails.
    """
    media = voice_item.get("media", {})
    if not isinstance(media, dict):
        logger.warning("download_voice: no media dict in voice_item")
        return None

    encrypt_param = media.get("encrypt_query_param", "") or media.get("encrypt_param", "")
    aes_key_b64 = voice_item.get("aeskey", "") or media.get("aes_key", "") or media.get("aeskey", "")
    filekey = voice_item.get("filekey", "") or media.get("filekey", "")

    if not encrypt_param:
        logger.warning("download_voice: no encrypt_param")
        return None

    from urllib.parse import quote

    if filekey:
        download_url = (
            f"https://novac2c.cdn.weixin.qq.com/c2c/download?"
            f"encrypted_query_param={quote(encrypt_param)}&filekey={quote(filekey)}"
        )
    else:
        download_url = (
            f"https://novac2c.cdn.weixin.qq.com/c2c/download?"
            f"encrypted_query_param={quote(encrypt_param)}"
        )

    logger.info(f"download_voice: {download_url[:120]}")
    try:
        req = urllib.request.Request(download_url, headers={"User-Agent": "Mozilla/5.0"}, method="GET")
        with urllib.request.urlopen(req, timeout=30) as resp:
            ciphertext = resp.read()
        logger.info(f"download_voice: got {len(ciphertext)} bytes")
    except Exception as e:
        logger.error(f"download_voice: download failed: {e}")
        return None

    if not ciphertext:
        return None

    # Try AES decrypt if we have the key
    if aes_key_b64:
        try:
            aes_key = base64.b64decode(aes_key_b64)
            plaintext = aes_ecb_decrypt(ciphertext, aes_key)
            logger.info(f"download_voice: AES decrypted, {len(plaintext)} bytes")
            return plaintext
        except Exception as e:
            logger.warning(f"download_voice: AES decrypt failed: {e}")

        # Try hex key
        try:
            if len(aes_key_b64) == 32:
                aes_key_raw = bytes.fromhex(aes_key_b64)
                plaintext = aes_ecb_decrypt(ciphertext, aes_key_raw)
                logger.info(f"download_voice: AES hex-key decrypted, {len(plaintext)} bytes")
                return plaintext
        except Exception:
            pass

    # Return raw ciphertext as fallback (may be unencrypted SILK/AMR)
    logger.info("download_voice: returning raw data (possibly unencrypted)")
    return ciphertext


# ── STT (Speech-to-Text) ───────────────────────────────────────────────

_stt_model = None
_stt_lock = None


def _get_stt_lock():
    import threading
    global _stt_lock
    if _stt_lock is None:
        _stt_lock = threading.Lock()
    return _stt_lock


def _load_stt_model():
    """Lazy-load the faster-whisper model (singleton)."""
    global _stt_model
    if _stt_model is not None:
        return _stt_model
    with _get_stt_lock():
        if _stt_model is not None:
            return _stt_model
        try:
            from faster_whisper import WhisperModel
            _stt_model = WhisperModel("small", device="cpu", compute_type="int8")
            logger.info("STT model loaded (faster-whisper small)")
        except Exception as e:
            logger.error(f"STT model load failed: {e}")
            _stt_model = False
    return _stt_model


def transcribe(audio_data: bytes, audio_format: str = "silk") -> str:
    """Transcribe audio data to text using faster-whisper.

    Args:
        audio_data: Raw audio bytes (SILK, AMR, or MP3 format).
        audio_format: Format hint ('silk', 'amr', 'mp3', or 'wav').

    Returns transcribed text, or empty string on failure.
    """
    model = _load_stt_model()
    if not model:
        return ""

    tmp_path = None
    try:
        # Write raw audio to temp file
        suffix_map = {"silk": ".silk", "amr": ".amr", "mp3": ".mp3", "wav": ".wav"}
        suffix = suffix_map.get(audio_format, ".raw")

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        os.close(tmp_fd)
        with open(tmp_path, "wb") as f:
            f.write(audio_data)

        segments, info = model.transcribe(tmp_path, language="zh", beam_size=5)

        text_parts = []
        for segment in segments:
            text_parts.append(segment.text)
        result = " ".join(text_parts).strip()
        logger.info(f"STT result ({info.language} p={info.language_probability:.2f}): {result[:80]}")
        return result

    except Exception as e:
        logger.error(f"STT transcription failed: {e}")
        return ""
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
