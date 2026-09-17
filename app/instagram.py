import logging
import os
import random
import re
import time

from curl_cffi import requests
from curl_cffi.requests.exceptions import RequestException

from . import config, state

log = logging.getLogger("ig-monitor.instagram")

COOKIE_KEYS = ("sessionid", "csrftoken", "ds_user_id")


class IGError(Exception):
    pass


class SessionInvalid(IGError):
    pass


class RateLimited(IGError):
    pass


class NotFound(IGError):
    pass


def load_creds() -> dict:
    """session.json (written by /session or cookie rotation) overrides .env."""
    creds = {k: os.environ.get(f"IG_{k.upper()}", "").strip() for k in COOKIE_KEYS}
    creds.update({k: v for k, v in state.read_json(config.SESSION_FILE, {}).items() if v})
    if not creds.get("ds_user_id") and (m := re.match(r"\d+", creds.get("sessionid", ""))):
        creds["ds_user_id"] = m.group()
    return creds


def save_creds(creds: dict) -> None:
    state.write_json(config.SESSION_FILE, creds, mode=0o600)


def _platform(ua: str) -> str:
    for needle, name in (("Windows", "Windows"), ("Macintosh", "macOS"), ("Linux", "Linux")):
        if needle in ua:
            return name
    return "Windows"


class Client:
    def __init__(self, creds: dict, persist: bool = True):
        self.creds = dict(creds)
        self.persist = persist
        self.username = config.IG_USERNAME
        self.s = requests.Session(impersonate=config.IG_IMPERSONATE)
        for k in COOKIE_KEYS:
            if self.creds.get(k):
                self.s.cookies.set(k, self.creds[k], domain=".instagram.com")

    def _headers(self, referer: str) -> dict:
        h = {
            "Accept": "*/*",
            "Referer": referer,
            "X-IG-App-ID": config.IG_APP_ID,
            "X-IG-WWW-Claim": self.creds.get("www_claim", "0"),
            "X-CSRFToken": self.creds.get("csrftoken", ""),
            "X-Requested-With": "XMLHttpRequest",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
        if config.IG_USER_AGENT:
            h["User-Agent"] = config.IG_USER_AGENT
            if config.IG_IMPERSONATE.startswith("chrome"):
                h["sec-ch-ua-platform"] = f'"{_platform(config.IG_USER_AGENT)}"'
        return h

    def _rotate(self, resp) -> None:
        """Persist cookies / claim header that Instagram refreshed."""
        changed = False
        for c in self.s.cookies.jar:
            if c.name in ("sessionid", "csrftoken") and c.value not in (None, "", '""'):
                if c.value != self.creds.get(c.name):
                    self.creds[c.name] = c.value
                    changed = True
        claim = resp.headers.get("x-ig-set-www-claim")
        if claim and claim != self.creds.get("www_claim"):
            self.creds["www_claim"] = claim
            changed = True
        if changed and self.persist:
            save_creds(self.creds)
            log.info("persisted rotated session values")

    def _get(self, path: str, params: dict | None = None, referer: str | None = None) -> dict:
        referer = referer or f"{config.IG_BASE}/{self.username}/followers/"
        last: Exception | None = None
        for attempt in range(3):
            if attempt:
                time.sleep(5 * 3**attempt + random.uniform(0, 3))
            try:
                r = self.s.get(config.IG_BASE + path, params=params, headers=self._headers(referer),
                               allow_redirects=False, timeout=30)
            except RequestException as e:
                last = IGError(f"network error: {type(e).__name__}")
                log.warning("%s on %s (attempt %d)", last, path, attempt + 1)
                continue
            if r.status_code >= 500:
                last = IGError(f"HTTP {r.status_code}")
                log.warning("%s on %s (attempt %d)", last, path, attempt + 1)
                continue
            return self._parse(r, path)
        raise last

    def _parse(self, r, path: str) -> dict:
        try:
            body = r.json()
        except ValueError:
            body = None
        text = (str(body.get("message", "")) if isinstance(body, dict) else "").lower()

        # logged-out requests get 401 "Please wait a few minutes" + require_login
        # (verified live), so require_login must win over the rate-limit text
        if any(m in text for m in ("login_required", "checkpoint_required", "challenge_required")) \
                or (isinstance(body, dict) and body.get("require_login")):
            raise SessionInvalid(f"HTTP {r.status_code} {text or 'require_login'}")
        if r.status_code == 429 or "wait a few minutes" in text:
            raise RateLimited(f"HTTP {r.status_code} {text}".strip())
        if r.status_code in (301, 302):
            raise SessionInvalid(f"redirect to {r.headers.get('location', '?')}")
        if r.status_code in (401, 403):
            raise SessionInvalid(f"HTTP {r.status_code} {text}".strip())
        if r.status_code == 404 or "not found" in text:
            raise NotFound(path)
        if body is None:
            if r.status_code == 200:
                raise SessionInvalid("HTML instead of JSON")
            raise IGError(f"HTTP {r.status_code}, non-JSON body")
        if r.status_code != 200 or body.get("status") == "fail":
            raise IGError(f"HTTP {r.status_code} {text}".strip())
        self._rotate(r)
        return body

    def validate(self) -> str:
        """Authenticated call; returns own username. Raises SessionInvalid."""
        try:
            body = self._get(config.EP_VALIDATE, referer=f"{config.IG_BASE}/accounts/edit/")
            return body.get("form_data", {}).get("username") or config.IG_USERNAME
        except (SessionInvalid, RateLimited):
            raise
        except IGError as e:
            # endpoint gone/changed: the followers endpoint also requires a login
            log.warning("validate endpoint failed (%s), falling back to followers page", e)
            self._followers_page(self.creds["ds_user_id"], None)
            return config.IG_USERNAME

    def profile(self) -> tuple[str, int]:
        """Own (user_id, official follower count). Also learns the real username."""
        user_id = self.creds.get("ds_user_id")
        if user_id:  # authenticated and far less throttled than web_profile_info
            user = self._get(config.EP_USER_INFO.format(user_id=user_id), referer=f"{config.IG_BASE}/").get("user") or {}
            if user.get("username"):
                self.username = user["username"]
            return str(user_id), int(user["follower_count"])
        body = self._get(config.EP_PROFILE, {"username": self.username},
                         referer=f"{config.IG_BASE}/{self.username}/")
        user = (body.get("data") or {}).get("user")
        if not user:
            raise IGError("web_profile_info: no user in response")
        return str(user["id"]), int(user["edge_followed_by"]["count"])

    def _followers_page(self, user_id: str, max_id: str | None) -> dict:
        params = {"count": config.PAGE_SIZE, "search_surface": "follow_list_page"}
        if max_id:
            params["max_id"] = max_id
        return self._get(config.EP_FOLLOWERS.format(user_id=user_id), params)

    def followers(self, user_id: str, expected: int | None) -> dict[str, dict]:
        """Full follower list keyed by pk (deduplicated)."""
        users: dict[str, dict] = {}
        max_pages = (expected or 10000) // max(1, min(config.PAGE_SIZE, 12)) + 20
        max_id, seen_cursors = None, set()
        for page in range(max_pages):
            body = self._followers_page(user_id, max_id)
            for u in body.get("users", []):
                users[str(u["pk"])] = {"username": u.get("username", ""), "full_name": u.get("full_name", "")}
            log.info("followers page %d: %d total", page + 1, len(users))
            max_id = body.get("next_max_id")
            if not max_id or max_id in seen_cursors:
                return users
            seen_cursors.add(max_id)
            time.sleep(random.uniform(config.PAGE_SLEEP_MIN_S, config.PAGE_SLEEP_MAX_S))
        raise IGError(f"pagination did not terminate after {max_pages} pages")

    def classify_gone(self, pk: str) -> tuple[str, bool | None]:
        """(kind, i_follow_them); kind: 'deactivated' | 'following' (list glitch) | 'unfollow' | 'unverified'."""
        try:
            try:
                self._get(config.EP_USER_INFO.format(user_id=pk))
            except NotFound:
                return "deactivated", None
            time.sleep(random.uniform(1, 2))
            show = self._get(config.EP_FRIENDSHIP.format(user_id=pk))
            return ("following" if show.get("followed_by") else "unfollow"), bool(show.get("following"))
        except (SessionInvalid, RateLimited):
            raise
        except IGError as e:
            log.warning("could not classify %s: %s", pk, e)
            return "unverified", None
