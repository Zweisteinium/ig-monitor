# ig-monitor

Diffs my own Instagram follower list by numeric user id and reports unfollows, deactivations and new
followers (terminal or Telegram). In browser mode it also snapshots and diffs the followers or
following list of any account I can see, and tells who does not follow me back. State lives in `./data`.

Unofficial and against Instagram's ToS: read-only, use at your own risk. Instagram does flag accounts
for automated behaviour. Use mode A on an account you care about, run it rarely (monthly, or when the
follower count drops), collect only a few lists per session, and stop for two weeks after any warning.

## A. Collect in the browser (recommended, nothing to fingerprint)

The list is fetched by your real, logged-in browser tab, so cookies, TLS, IP, device and headers are
genuine. The tool only diffs the downloaded file and never contacts Instagram.

1. Open instagram.com, DevTools -> Console, paste `collect.js`, Enter.
2. Open a profile, click "followers" or "following" (then "Show all" if offered), do not scroll.
   The script recognises the list from the request the dialog just sent and continues it with the
   same parameters and headers at a fast scrolling pace. Other people's lists are re-ranked on every
   page, so even Instagram's own dialog repeats and skips accounts; the script walks the list several
   times until the profile's count is reached or passes stop finding anyone new (typically 4-5
   passes; my own list is chronological and needs one). It saves
   `<username>-<followers|following>-YYYY-MM-DD.json` and waits for the next list.
   `collectStop()` saves and unhooks.
3. Import each download:

```sh
uv run --env-file .env python -m app.main --import ~/Downloads/<username>-followers-YYYY-MM-DD.json
```

- My own followers: unfollow check against `data/state.json`; the first import saves the baseline.
  Vanished users are listed with a profile link; open it to see whether they unfollowed or deactivated.
- Any other list: snapshot in `data/lists/<user id>/<kind>/`, diffed against the previous snapshot
  of the same list (added, removed, renamed). My own following list also reports who does not follow
  back and whom I do not follow back, so import my followers first.
- Several runs of the same list (same account and kind, within 24 h) can be imported together and are
  merged: `--import run1.json run2.json`. If a run stays short of the count, removals are labelled as
  possibly just not listed this time.
- Single accounts can be checked against the last collected list in the same tab:
  `collectVerify(["name.one", "name_two"])`. It uses the dialog's search, which matches name parts
  (it stops at `.` and `_`, at most 12 hits), adds confirmed accounts and downloads the list again.
  When a run is short, the import report prints this call with the uncertain names.
- Lists Instagram marks as limited (`should_limit_list_of_followers`) are saved with a warning and
  never reported as removals; a limited own follower list is refused.
- Own lists are recognised by `my_user_id` in the state, else by the browser's `ds_user_id` cookie.

`.env` is optional here: without Telegram settings the result is printed. `--dry-run` writes nothing.

`debug.js` is for when Instagram changes something: it records what the page requests while you
scroll by hand and can probe pagination and search (see its header).

To browse the history, open `viewer.html` in the browser (`xdg-open viewer.html`) and choose the
`data` folder. Pick any two snapshots: the same list at two dates shows added, removed and renamed
accounts; two different lists (e.g. my following vs my followers) show what is only in either one.
It runs offline and reads only the files you choose.

## B. Fetch from the server (convenient, residual risk)

A replayed session can match the browser's headers, cookies and TLS, but never its JavaScript
telemetry and page loads. Prefer one-shot runs over the daemon.

1. In the browser open your followers dialog, DevTools -> Network, filter `followers`, right click the
   `friendships/<id>/followers/` request -> Copy -> Copy as cURL (bash). Save it as `data/request.curl`
   (git-ignored, contains your session). Close the tab without logging out.
   This gives the tool the full cookie jar (`mid`, `ig_did`, `datr`, ...), the exact User-Agent and
   client hints, `x-asbd-id`, `x-ig-www-claim` and the dialog's own query parameters. The TLS
   fingerprint follows the captured User-Agent. Re-capture after browser updates or a new login.
2. `cp .env.example .env`, fill in Telegram (bot from @BotFather, chat id from
   `https://api.telegram.org/bot<TOKEN>/getUpdates` after sending `/start`).

```sh
uv run --env-file .env python -m app.main --dry-run            # one check, prints only, writes nothing
docker compose run --rm ig-monitor python -m app.main --once   # one check, then exit
docker compose up -d --build                                   # daemon: every CHECK_INTERVAL_HOURS (>= 12, default 24), never at night
docker compose logs -f --tail 100
docker compose down
```

Daemon Telegram commands: `/status`, `/check` (`/check force` within 1 h of the last run), `/history`,
`/session <cookie string>` (validated, stored in `data/session.json`, message deleted), `/help`.
Other chat ids are ignored.

## How it works

- `curl_cffi` impersonates the browser's TLS/JA4 and HTTP/2 fingerprint; headers and cookies are
  replayed from `data/request.curl`, with a fresh `x-web-session-id` per run and the current
  `x-csrftoken` / `x-ig-www-claim`. Rotated cookies are persisted to `data/session.json`;
  whichever of the two files is newer wins.
- Per run: `users/<my_id>/info/` (official count) -> paginated `friendships/<id>/followers/` with
  human-like pauses -> sanity gate (`COUNT_TOLERANCE_PCT`) -> diff. State is committed only after a
  fully validated run; partial imports are refused.
- Mode B verifies every vanished user: `users/<pk>/info/` 404 -> deactivated; otherwise
  `friendships/show/<pk>/` `followed_by=true` -> list glitch, kept silently; `false` -> unfollowed,
  including whether I still follow them.
- Dead session (login redirect, 401/403, `require_login`, checkpoint/challenge, HTML): one alert per
  24 h. 429: run aborted without retry, state untouched, next run delayed.
- Endpoints and tunables: `app/config.py`.
