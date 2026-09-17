import os
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


DATA_DIR = Path(_env("DATA_DIR", "data"))
STATE_FILE = DATA_DIR / "state.json"
SESSION_FILE = DATA_DIR / "session.json"
HEARTBEAT_FILE = DATA_DIR / "heartbeat"
LOG_FILE = DATA_DIR / "monitor.log"
LOG_LEVEL = _env("LOG_LEVEL", "INFO").upper()

# only a fallback: the real handle is read from the session
IG_USERNAME = _env("IG_USERNAME").lstrip("@").lower()
# curl_cffi target: spoofs TLS (JA3/JA4), HTTP/2 settings and the matching
# default headers (User-Agent, sec-ch-ua, ...). "chrome" = newest bundled.
IG_IMPERSONATE = _env("IG_IMPERSONATE", "chrome")
# Optional: exact UA of the browser the session was created in. Leave empty
# to use the UA that belongs to the impersonated fingerprint.
IG_USER_AGENT = _env("IG_USER_AGENT")

TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID")

CHECK_INTERVAL_HOURS = max(6.0, float(_env("CHECK_INTERVAL_HOURS", "12")))
JITTER_MIN = 20
PAGE_SIZE = int(_env("PAGE_SIZE", "50"))
PAGE_SLEEP_MIN_S = float(_env("PAGE_SLEEP_MIN_S", "1.5"))
PAGE_SLEEP_MAX_S = float(_env("PAGE_SLEEP_MAX_S", "4.0"))
COUNT_TOLERANCE_PCT = float(_env("COUNT_TOLERANCE_PCT", "3"))
DAILY_SUMMARY = _env("DAILY_SUMMARY", "false").lower() in ("1", "true", "yes")

RATE_LIMIT_EXTRA_DELAY_S = 2 * 3600
SESSION_ALERT_INTERVAL_S = 24 * 3600
MANUAL_CHECK_MIN_GAP_S = 3600
MAX_VERIFY_GONE = 40
HISTORY_CAP = 5000

# --- Instagram web endpoints (same ones the logged-in web app calls) ---
IG_BASE = "https://www.instagram.com"
IG_APP_ID = "936619743392459"
EP_PROFILE = "/api/v1/users/web_profile_info/"  # ?username=
EP_FOLLOWERS = "/api/v1/friendships/{user_id}/followers/"  # ?count=&max_id=
EP_FRIENDSHIP = "/api/v1/friendships/show/{user_id}/"
EP_USER_INFO = "/api/v1/users/{user_id}/info/"
EP_VALIDATE = "/api/v1/accounts/edit/web_form_data/"
