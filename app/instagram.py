import logging
import os
import random
import re
import time

from urllib.parse import parse_qsl, urlsplit

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


# never replayed from the captured request: set per call or owned by the HTTP stack
_SKIP_HEADERS = {"cookie", "host", "content-length", "connection", "accept-encoding", "referer"}


def parse_curl(text: str) -> tuple[dict, dict, dict]:
    """(headers, cookies, query) from a DevTools "Copy as cURL" of the followers request."""
    text = text.replace("'\\''", "\x00")
    headers = {k.strip().lower(): v.strip().replace("\x00", "'")
               for k, v in re.findall(r"-H \$?'([^:']+):([^']*)'", text)}
    cookie = headers.get("cookie") or (re.search(r"(?:-b|--cookie) \$?'([^']*)'", text) or [None, ""])[1]
    cookies = dict(p.strip().split("=", 1) for p in cookie.split(";") if "=" in p)
    url = re.search(r"curl\s+(?:-X\s+\w+\s+)?\$?'([^']+)'", text)
    query = dict(parse_qsl(urlsplit(url[1]).query)) if url else {}
    query.pop("max_id", None)
    return {k: v for k, v in headers.items() if k not in _SKIP_HEADERS}, cookies, query


def load_creds() -> dict:
    """{'cookies', 'headers', 'query'}. Sources: .env seed, then data/request.curl and
    data/session.json (rotated cookies, /session) in order of age, newest wins."""
    creds = {"cookies": {k: v for k in COOKIE_KEYS if (v := os.environ.get(f"IG_{k.upper()}", "").strip())},
             "headers": {}, "query": {}}
    files = [p for p in (config.REQUEST_FILE, config.SESSION_FILE) if p.exists()]
    for path in sorted(files, key=lambda p: p.stat().st_mtime):
        if path == config.REQUEST_FILE:
            creds["headers"], cookies, creds["query"] = parse_curl(path.read_text())
            claim = creds["headers"].get("x-ig-www-claim")
        else:
            data = state.read_json(path, {})
            cookies = data.get("cookies") or {k: data[k] for k in COOKIE_KEYS if data.get(k)}  # v0.1 layout
            claim = data.get("www_claim")
        creds["cookies"].update(cookies)
        if claim:
            creds["www_claim"] = claim
    c = creds["cookies"]
    if not c.get("ds_user_id") and (m := re.match(r"\d+", c.get("sessionid", ""))):
        c["ds_user_id"] = m.group()
    return creds


def save_creds(creds: dict) -> None:
    state.write_json(config.SESSION_FILE, {"cookies": creds["cookies"], "www_claim": creds.get("www_claim", "0")},
                     mode=0o600)


def human_pause(lo: float, hi: float) -> None:
    """Scroll-like rhythm: mostly short gaps, now and then a longer reading pause."""
    time.sleep(random.uniform(lo, hi) + (random.uniform(6, 20) if random.random() < 0.12 else 0))


class Client:
    def __init__(self, creds: dict, persist: bool = True):
        self.creds = {**creds, "cookies": dict(creds["cookies"])}
        self.persist = persist
        self.username = config.IG_USERNAME
        self.template = creds.get("headers") or {}
        if not self.template:
            log.warning("no %s: falling back to generic headers and a partial cookie jar, "
                        "which does not look like your browser", config.REQUEST_FILE)
        ua = self.template.get("user-agent", "")
        self.s = requests.Session(impersonate=config.IG_IMPERSONATE or ("firefox" if "Firefox/" in ua else "chrome"))
        for k, v in self.creds["cookies"].items():
            self.s.cookies.set(k, v, domain=".instagram.com")
        # one page load = one web session id
        alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
        self.web_session_id = ":".join("".join(random.choices(alphabet, k=6)) for _ in range(3))

    def _headers(self, referer: str) -> dict:
        h = dict(self.template) or {
            "accept": "*/*",
            "x-ig-app-id": config.IG_APP_ID,
            "x-asbd-id": config.IG_ASBD_ID,
            "x-requested-with": "XMLHttpRequest",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        }
        h.update({"referer": referer,
                  "x-csrftoken": self.creds["cookies"].get("csrftoken", ""),
                  "x-ig-www-claim": self.creds.get("www_claim", "0")})
        if "x-web-session-id" in h:
            h["x-web-session-id"] = self.web_session_id
        return h

    def _rotate(self, resp) -> None:
        """Persist cookies / claim header that Instagram refreshed."""
        changed = False
        for c in self.s.cookies.jar:
            if c.value not in (None, "", '""') and c.value != self.creds["cookies"].get(c.name):
                self.creds["cookies"][c.name] = c.value
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
            self._followers_page(self.creds["cookies"]["ds_user_id"], None)
            return config.IG_USERNAME

    def profile(self) -> tuple[str, int]:
        """Own (user_id, official follower count). Also learns the real username."""
        user_id = self.creds["cookies"].get("ds_user_id")
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
        params = dict(self.creds.get("query") or {"count": config.PAGE_SIZE, "search_surface": "follow_list_page"})
        if max_id:
            params["max_id"] = max_id
        return self._get(config.EP_FOLLOWERS.format(user_id=user_id), params)

    def followers(self, user_id: str, expected: int | None) -> dict[str, dict]:
        """Full follower list keyed by pk (deduplicated)."""
        users: dict[str, dict] = {}
        max_pages = (expected or 10000) // 10 + 20
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
            human_pause(config.PAGE_SLEEP_MIN_S, config.PAGE_SLEEP_MAX_S)
        raise IGError(f"pagination did not terminate after {max_pages} pages")

    def classify_gone(self, pk: str) -> tuple[str, bool | None]:
        """(kind, i_follow_them); kind: 'deactivated' | 'following' (list glitch) | 'unfollow' | 'unverified'."""
        try:
            try:
                self._get(config.EP_USER_INFO.format(user_id=pk))
            except NotFound:
                return "deactivated", None
            human_pause(2, 5)
            show = self._get(config.EP_FRIENDSHIP.format(user_id=pk))
            return ("following" if show.get("followed_by") else "unfollow"), bool(show.get("following"))
        except (SessionInvalid, RateLimited):
            raise
        except IGError as e:
            log.warning("could not classify %s: %s", pk, e)
            return "unverified", None
