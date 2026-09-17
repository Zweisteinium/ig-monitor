import argparse
import html
import logging
import random
import sys
import threading
import time
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler

from . import config, state, telegram
from .instagram import Client, IGError, RateLimited, SessionInvalid, load_creds, save_creds

log = logging.getLogger("ig-monitor")
run_lock = threading.Lock()

HELP = (
    "/status - last check, follower count, next run\n"
    "/check - run a check now\n"
    "/history - last 20 events\n"
    "/session sessionid=...; csrftoken=... - renew Instagram cookies\n"
    "/help - this list"
)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def fmt_ts(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%d.%m.%Y %H:%M")


def schedule_next(st: dict, extra: float = 0) -> None:
    jitter = random.uniform(-config.JITTER_MIN, config.JITTER_MIN) * 60
    st["next_run"] = time.time() + config.CHECK_INTERVAL_HOURS * 3600 + jitter + extra


def user_line(u: dict, link: bool = True) -> str:
    name = html.escape(u.get("username", "?"))
    handle = f'<a href="https://instagram.com/{name}">@{name}</a>' if link else f"@{name}"
    full = html.escape(u.get("full_name") or "")
    return f"• {handle}" + (f" — {full}" if full else "")


def run_check(scheduled: bool = True, dry_run: bool = False) -> str:
    """One full check. Returns a one-line result (reply for /check). Caller holds run_lock."""
    st = state.load()
    notify = print if dry_run else telegram.send

    def commit() -> None:
        if not dry_run:
            state.save(st)

    def fail(msg: str, alert: bool = False) -> str:
        log.warning(msg)
        st["failures"] = st.get("failures", 0) + 1
        if scheduled and (alert or st["failures"] == 3):
            notify(f"Warning: {html.escape(msg)}")
        commit()
        return msg

    def session_alert(reason: str) -> str:
        log.error("session invalid: %s", reason)
        if scheduled and time.time() - st.get("last_session_alert", 0) > config.SESSION_ALERT_INTERVAL_S:
            st["last_session_alert"] = time.time()
            notify("Instagram session expired or missing "
                   f"({html.escape(reason)}).\nReply with /session sessionid=…; csrftoken=…")
        commit()
        return f"session invalid: {reason}"

    st["last_attempt"] = time.time()
    if scheduled:
        schedule_next(st)
    commit()

    creds = load_creds()
    if not creds.get("sessionid") or not creds.get("csrftoken"):
        return session_alert("no cookies configured")

    try:
        ig = Client(creds, persist=not dry_run)
        my_id, official = creds.get("ds_user_id") or st.get("my_user_id"), None
        try:
            my_id, official = ig.profile()
        except (SessionInvalid, RateLimited):
            raise
        except IGError as e:
            log.warning("official count unavailable: %s", e)
        if not my_id:
            return fail("own user id unknown (set IG_DS_USER_ID)", alert=True)
        time.sleep(random.uniform(config.PAGE_SLEEP_MIN_S, config.PAGE_SLEEP_MAX_S))
        new = ig.followers(my_id, official)

        old = st["followers"]
        reference = official if official is not None else len(old)
        tolerance = config.COUNT_TOLERANCE_PCT if official is not None else 10.0
        if reference and abs(len(new) - reference) > max(2, reference * tolerance / 100):
            return fail(f"fetched {len(new)} vs {'official' if official is not None else 'previous'} "
                        f"{reference} — skipping diff", alert=True)

        ts = now_iso()
        st.update(my_user_id=my_id, official_count=official)
        if not old:
            st["followers"] = {pk: {**u, "first_seen": ts, "last_seen": ts} for pk, u in new.items()}
            st.update(last_success=ts, failures=0, last_session_alert=0)
            commit()
            notify(f"Baseline saved: {len(new)} followers")
            return f"baseline saved: {len(new)} followers"

        sections = {"unfollow": [], "deactivated": [], "follow": [], "renamed": []}
        followers = {}
        for pk, u in new.items():
            prev = old.get(pk)
            if prev is None:
                sections["follow"].append((pk, u))
            elif prev["username"] != u["username"]:
                log.info("rename %s: %s -> %s", pk, prev["username"], u["username"])
                sections["renamed"].append((pk, {**u, "old": prev["username"]}))
            followers[pk] = {**u, "first_seen": (prev or {}).get("first_seen", ts), "last_seen": ts}

        for i, pk in enumerate(pk for pk in old if pk not in new):
            kind, i_follow = ig.classify_gone(pk) if i < config.MAX_VERIFY_GONE else ("unverified", None)
            if kind == "following":  # list endpoint dropped them, still a follower
                log.info("%s missing from list but still following", old[pk]["username"])
                followers[pk] = old[pk]
                continue
            note = {True: "you still follow them", False: "you don't follow them (anymore) either"}.get(i_follow)
            if kind == "unverified":
                note = "unverified"
            u = dict(old[pk], note=note if kind != "deactivated" else None)
            sections["deactivated" if kind == "deactivated" else "unfollow"].append((pk, u))
            time.sleep(random.uniform(1, 2))
    except SessionInvalid as e:
        return session_alert(str(e))
    except RateLimited as e:
        if scheduled:
            st["next_run"] += config.RATE_LIMIT_EXTRA_DELAY_S
        return fail(f"rate limited by Instagram ({e}), state untouched")
    except IGError as e:
        return fail(f"check failed: {e}")

    for event in ("unfollow", "deactivated", "follow"):
        st["history"] += [{"ts": ts, "event": event, "pk": pk, "username": u["username"]}
                          for pk, u in sections[event]]
    st["followers"] = followers
    st.update(last_success=ts, failures=0, last_session_alert=0)

    parts = []
    for key, title, link in (("unfollow", "Unfollowed you", True),
                             ("deactivated", "Deactivated / deleted", False),
                             ("follow", "New followers", True)):
        if sections[key]:
            lines = [user_line(u, link) + (f" ({u['note']})" if u.get("note") else "")
                     for _, u in sections[key]]
            parts.append(f"<b>{title} ({len(lines)})</b>\n" + "\n".join(lines))
    if sections["renamed"]:
        parts.append("<b>Renamed</b>\n" + "\n".join(
            f"• @{html.escape(u['old'])} → @{html.escape(u['username'])}" for _, u in sections["renamed"]))

    total = f"Total: {len(followers)} (official {official if official is not None else '?'}) · {fmt_ts(time.time())}"
    summary = (f"{len(sections['unfollow'])} unfollowed, {len(sections['deactivated'])} deactivated, "
               f"{len(sections['follow'])} new")
    if parts:
        notify("\n\n".join(parts) + f"\n\n{total}")
    elif scheduled and config.DAILY_SUMMARY and st.get("last_summary") != ts[:10]:
        st["last_summary"] = ts[:10]
        notify(f"No changes. {total}")
    commit()
    log.info("check done: %s, total %d", summary, len(followers))
    return f"no changes. {total}" if not parts else summary


# --- telegram commands ---

def cmd_status() -> str:
    st = state.load()
    failures = st.get("failures", 0)
    return (f"Last success: {st.get('last_success', 'never')}\n"
            f"Followers: {len(st['followers'])} (official {st.get('official_count', '?')})\n"
            f"Session: {'INVALID' if st.get('last_session_alert') else 'ok'}"
            + (f"\nFailed runs in a row: {failures}" if failures else "")
            + (f"\nNext run: {fmt_ts(st['next_run'])}" if st.get("next_run") else ""))


def cmd_check(arg: str) -> None:
    if not run_lock.acquire(blocking=False):
        telegram.send("A check is already running.")
        return
    try:
        gap = time.time() - state.load().get("last_attempt", 0)
        if gap < config.MANUAL_CHECK_MIN_GAP_S and arg != "force":
            telegram.send(f"Last check was {int(gap // 60)} min ago. Use /check force to run anyway.")
            return
        telegram.send("Checking…")
        telegram.send(html.escape(run_check(scheduled=False)))
    finally:
        run_lock.release()


def cmd_history() -> str:
    events = state.load()["history"][-20:]
    return "\n".join(f"{e['ts'][:16].replace('T', ' ')} {e['event']} @{html.escape(e['username'])}"
                     for e in reversed(events)) or "No events yet."


def cmd_session(arg: str, message_id: int) -> str:
    telegram.delete(message_id)  # keep the cookie out of the chat history
    pairs = dict(p.strip().split("=", 1) for p in arg.replace("\n", ";").split(";") if "=" in p)
    new = {k: v.strip().strip('"') for k, v in pairs.items() if k in ("sessionid", "csrftoken", "ds_user_id")}
    if not new.get("sessionid"):
        return "Usage: /session sessionid=…; csrftoken=…"
    creds = {**load_creds(), **new, "www_claim": "0"}
    if "ds_user_id" not in new:
        creds["ds_user_id"] = new["sessionid"].split("%3A")[0].split(":")[0]
    try:
        username = Client(creds, persist=False).validate()
    except SessionInvalid as e:
        return f"Still invalid: {html.escape(str(e))}"
    except IGError as e:
        return f"Could not validate: {html.escape(str(e))}"
    save_creds(creds)
    return f"Session valid, user @{html.escape(username)}. Send /check force to run now."


def handle(text: str, message_id: int) -> None:
    cmd, _, arg = text.strip().partition(" ")
    cmd, arg = cmd.split("@")[0].lower(), arg.strip()
    if cmd == "/check":
        threading.Thread(target=cmd_check, args=(arg,), daemon=True).start()
    elif cmd == "/status":
        telegram.send(html.escape(cmd_status()))
    elif cmd == "/history":
        telegram.send(cmd_history())
    elif cmd == "/session":
        telegram.send(cmd_session(arg, message_id))
    elif cmd in ("/help", "/start"):
        telegram.send(html.escape(HELP))


def setup_logging() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [logging.StreamHandler(sys.stdout),
                TimedRotatingFileHandler(config.LOG_FILE, when="midnight", backupCount=7)]
    logging.basicConfig(level=config.LOG_LEVEL, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run one check and exit")
    ap.add_argument("--dry-run", action="store_true", help="print instead of Telegram, never write state")
    args = ap.parse_args()
    setup_logging()

    if not args.dry_run and not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        sys.exit("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")

    if args.once or args.dry_run:
        print(run_check(scheduled=not args.dry_run, dry_run=args.dry_run))
        return

    threading.Thread(target=telegram.poll, args=(handle,), daemon=True).start()
    log.info("started, interval %.1fh", config.CHECK_INTERVAL_HOURS)
    while True:
        config.HEARTBEAT_FILE.touch()
        if time.time() >= state.load().get("next_run", 0):
            with run_lock:
                try:
                    run_check()
                except Exception:
                    log.exception("unexpected error in run_check")
        time.sleep(30)


if __name__ == "__main__":
    main()
