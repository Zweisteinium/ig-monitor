# ig-monitor

Diffs my own Instagram follower list by numeric user id and reports unfollows, deactivations and new
followers (terminal or Telegram). State lives in `./data`.

Unofficial and against Instagram's ToS: own account only, read-only, use at your own risk. Instagram
does flag accounts for automated behaviour. Use mode A on an account you care about, run it rarely
(monthly, or when the follower count drops), and stop for two weeks after any warning.

## A. Collect in the browser (recommended, nothing to fingerprint)

The list is fetched by your real, logged-in browser tab, so cookies, TLS, IP, device and headers are
genuine. The tool only diffs the downloaded file and never contacts Instagram.

1. Open `https://www.instagram.com/<you>/`, DevTools -> Console, paste `collect.js`, Enter.
2. Click "followers" and do not scroll. The script takes the request Instagram itself just sent and
   continues that pagination with identical parameters and headers at a scrolling pace
   (about 5 min per 500 followers). On any HTTP error it stops without retrying.
3. Import the download. The first import saves the baseline.

```sh
uv run --env-file .env python -m app.main --import ~/Downloads/followers-YYYY-MM-DD.json
```

Vanished users are listed with a profile link; open it to see whether they unfollowed or deactivated.
`.env` is optional here: without Telegram settings the result is printed.

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
