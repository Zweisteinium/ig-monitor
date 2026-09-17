import logging
import time

from curl_cffi import requests

from . import config

log = logging.getLogger("ig-monitor.telegram")

MAX_LEN = 4000


def call(method: str, http_timeout: int = 20, **params) -> dict:
    # never log the URL or exception text: both contain the bot token
    try:
        r = requests.post(f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/{method}",
                          data=params, timeout=http_timeout)
        body = r.json()
    except Exception as e:
        raise RuntimeError(f"telegram {method}: {type(e).__name__}") from None
    if not body.get("ok"):
        raise RuntimeError(f"telegram {method}: {body.get('description')}")
    return body


def send(text: str) -> None:
    chunk = ""
    for line in text.split("\n"):
        if chunk and len(chunk) + len(line) + 1 > MAX_LEN:
            _send_chunk(chunk)
            chunk = ""
        chunk += line + "\n"
    if chunk.strip():
        _send_chunk(chunk)


def _send_chunk(text: str) -> None:
    for attempt in range(3):
        try:
            call("sendMessage", chat_id=config.TELEGRAM_CHAT_ID, text=text,
                 parse_mode="HTML", disable_web_page_preview="true")
            return
        except RuntimeError as e:
            log.warning("%s (attempt %d)", e, attempt + 1)
            time.sleep(5 * (attempt + 1))
    log.error("telegram message dropped")


def delete(message_id: int) -> None:
    try:
        call("deleteMessage", chat_id=config.TELEGRAM_CHAT_ID, message_id=message_id)
    except RuntimeError as e:
        log.warning("%s", e)


def poll(handler) -> None:
    """Long-poll forever; handler(text, message_id) only for the owner's chat."""
    offset = None
    while offset is None:  # drain updates that arrived while we were down
        try:
            pending = call("getUpdates", offset=-1, timeout=0)["result"]
            offset = pending[-1]["update_id"] + 1 if pending else 0
        except RuntimeError as e:
            log.warning("%s", e)
            time.sleep(30)
    while True:
        try:
            updates = call("getUpdates", http_timeout=45, offset=offset, timeout=30,
                           allowed_updates='["message"]')["result"]
        except RuntimeError as e:
            log.warning("%s", e)
            time.sleep(15)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            msg = u.get("message") or {}
            if str(msg.get("chat", {}).get("id")) != config.TELEGRAM_CHAT_ID or not msg.get("text"):
                continue
            try:
                handler(msg["text"], msg["message_id"])
            except Exception:
                log.exception("command handler failed")
