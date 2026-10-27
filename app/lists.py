"""Snapshots of follower/following lists collected by collect.js (any account), diffed by user id."""
import html
import json
import logging
from datetime import datetime

from . import config, state
from .telegram import user_line

log = logging.getLogger("ig-monitor.lists")

KIND_TITLES = {"followers": ("New followers", "No longer following"),
               "following": ("Started following", "No longer follows")}


def _users_section(title: str, users: list[dict]) -> str:
    return f"<b>{title} ({len(users)})</b>\n" + "\n".join(user_line(u) for u in users)


def snapshot_path(data: dict, target: dict):
    stamp = datetime.fromisoformat(data["collected_at"].replace("Z", "+00:00")).strftime("%Y-%m-%dT%H-%M-%S")
    return config.LISTS_DIR / str(target["id"]) / data["kind"] / f"{stamp}.json"


def save_snapshot(data: dict, users: dict[str, dict], target: dict) -> None:
    state.write_json(snapshot_path(data, target), {"collected_at": data["collected_at"], "kind": data["kind"],
                                                   "target": target, "limited": bool(data.get("limited")),
                                                   "users": users})


def import_list(data: dict, users: dict[str, dict], own_id: str | None, own_followers: dict,
                dry_run: bool) -> tuple[str, str]:
    """Store the snapshot, diff it against the previous one of the same account and kind.
    Returns (report, one-line summary)."""
    kind, target = data["kind"], data["target"]
    who = f"@{target.get('username') or target['id']}"
    current = snapshot_path(data, target)
    folder = current.parent
    previous = max((p for p in folder.glob("*.json") if p.name < current.name), default=None)
    old = state.read_json(previous, {}).get("users", {}) if previous else {}

    parts = []
    expected = data.get("expected")
    if expected and len(users) < expected - max(2, expected * 0.02):
        parts.append(f"Warning: {len(users)} collected, the profile shows {expected}: probably incomplete.")
    if data.get("limited"):
        parts.append("Warning: Instagram limited this list, it is not complete. Removals are not reported.")
    if previous:
        added_t, removed_t = KIND_TITLES[kind]
        added = [users[pk] for pk in users if pk not in old]
        removed = [] if data.get("limited") else [old[pk] for pk in old if pk not in users]
        renamed = [(old[pk]["username"], u["username"]) for pk, u in users.items()
                   if pk in old and old[pk]["username"] != u["username"]]
        if old and len(removed) > max(2, len(old) * 0.1):
            parts.append(f"Warning: {len(removed)} of {len(old)} gone, more than 10%: "
                         "check that the collection was complete.")
        if added:
            parts.append(_users_section(added_t, added))
        if removed:
            short = expected - len(users) if expected and len(users) < expected else 0
            # a ranked list run can miss accounts: then "removed" may include some that still follow
            parts.append(_users_section(removed_t + (f", or not listed this time (run was {short} short of "
                                                     f"the count)" if short else ""), removed))
            if short:
                names = json.dumps([u["username"] for u in removed[:30]])
                parts.append("Check them in the same browser tab (collect.js still pasted), then import the "
                             f"newest download:\n<code>{html.escape(f'collectVerify({names})')}</code>")
        if renamed:
            parts.append("<b>Renamed</b>\n" + "\n".join(
                f"• @{html.escape(a)} → @{html.escape(b)}" for a, b in renamed))
        since = previous.stem[:10]
        summary = f"{len(added)} added, {len(removed)} removed since {since}"
    else:
        summary = "first snapshot saved"

    # own following vs own followers (state.json): who does not follow back
    if kind == "following" and own_id and str(target["id"]) == own_id and own_followers:
        not_back = [u for pk, u in users.items() if pk not in own_followers]
        fans = [u for pk, u in own_followers.items() if pk not in users]
        if not_back:
            parts.append(_users_section("You follow, they don't follow back", not_back))
        if fans:
            parts.append(_users_section("Follow you, you don't follow back", fans))
        summary += f", {len(not_back)} don't follow back"

    if not dry_run:
        save_snapshot(data, users, target)
    header = f"<b>{html.escape(who)} {kind}</b>: {len(users)} · {summary}"
    log.info("%s %s: %d, %s", who, kind, len(users), summary)
    return "\n\n".join([header, *parts]), f"{who} {kind}: {summary}"
