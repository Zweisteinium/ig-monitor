# ig-monitor

Checks my own Instagram follower list twice a day, diffs it by numeric user id and
reports unfollows / deactivations / new followers via Telegram. One container, state in `./data`.

## Setup

1. Telegram: create a bot with @BotFather (`/newbot`, `/setjoingroups` -> Disable), send it `/start`,
   get the chat id from `https://api.telegram.org/bot<TOKEN>/getUpdates`.
2. Instagram: log in at instagram.com in a dedicated browser profile, copy the cookies
   `sessionid`, `csrftoken`, `ds_user_id` (DevTools -> Storage -> Cookies). Never log out of that profile.
3. `cp .env.example .env` and fill it in. Set `IG_IMPERSONATE` to the browser family of step 2.
4. Dry run, then start. First run replies "Baseline saved: N followers".

```sh
uv run --env-file .env python -m app.main --dry-run   # one check, prints only, writes nothing
docker compose up -d --build                          # start / update
docker compose logs -f --tail 100                     # logs
docker compose down                                   # stop
```

## Telegram commands

`/status`, `/check` (`/check force` within 1 h of the last run), `/history`,
`/session sessionid=...; csrftoken=...` (validated, stored in `data/session.json`, message deleted), `/help`.
Messages from any other chat id are ignored.

## How it works

- Requests go through `curl_cffi` with browser impersonation (TLS/JA4, HTTP/2 and header order of real
  Chrome), plus the web app's `X-IG-App-ID`, `X-CSRFToken`, `X-IG-WWW-Claim`, `Sec-Fetch-*` and Referer.
  Plain `requests`/`httpx` are fingerprinted by Instagram at the TLS layer.
- Per run: `users/<my_id>/info/` (official count; `web_profile_info` only as fallback, it 429s easily) -> paginated `friendships/<id>/followers/` -> sanity gate
  (`COUNT_TOLERANCE_PCT`) -> diff. State is committed only after a fully validated run.
- Every vanished user is verified: `users/<pk>/info/` 404 -> deactivated; otherwise
  `friendships/show/<pk>/` `followed_by=true` -> list glitch, kept silently; `false` -> unfollowed. The same
  response tells whether I (still) follow them, which is added to the message at no extra request.
- Dead session (login redirect, 401/403, `require_login`, `login_required`, checkpoint/challenge, HTML):
  one Telegram alert per 24 h, renew with `/session`. Rotated cookies are persisted automatically.
- 429 / "wait a few minutes" without `require_login`: run aborted, next run delayed by +2 h.
  Three failed runs in a row trigger a warning.
- All endpoints and tunables live in `app/config.py`.

Automated access violates Instagram's ToS; keep the interval at 12 h or more and the account read-only.
