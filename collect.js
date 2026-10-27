// Collects a follower or following list inside your real, logged-in browser tab.
// 1. Open any instagram.com page, DevTools -> Console, paste this file, Enter.
// 2. Open a profile (yours or someone else's) and click "followers" or "following" (and "Show all"
//    if Instagram first offers only mutual followers). Do not scroll; keep the tab in the foreground.
// 3. When it finishes, <username>-<followers|following>-YYYY-MM-DD.json is downloaded:
//      uv run --env-file .env python -m app.main --import ~/Downloads/<file>.json
//    Then open the next list (no need to paste again). collectStop() saves and unhooks.
//
// The list is identified from the request the dialog itself sends and continued with the same
// parameters and headers. Other people's lists are re-ranked on every page (Instagram's own dialog
// repeats and skips accounts), so the list is walked in several passes until the profile's count
// is reached or passes stop finding anyone new. Nothing is sent anywhere else.
(() => {
  const RE = /\/api\/v1\/friendships\/(\d+)\/(followers|following)\/(?:\?|$)/;
  const nativeFetch = window.fetch;
  const X = XMLHttpRequest.prototype;
  const nativeXhr = { open: X.open, setRequestHeader: X.setRequestHeader, send: X.send };
  const MAX_PAGES = 1000; // per pass
  const MAX_PASSES = 8;
  const RESERVED = /^(explore|reels?|direct|accounts|p|stories|tv|about|legal|developer|web)$/;
  let list = null; // being collected
  let last = null; // last finished, for collectVerify()

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  // fast scrolling: next page right after the previous one rendered, now and then a short stop
  const pause = () => sleep(600 + Math.random() * 900 + (Math.random() < 0.08 ? 2000 + Math.random() * 3000 : 0));
  // between passes, like reopening the dialog
  const reopen = () => sleep(3000 + Math.random() * 4000);
  const add = (l, body) => (body.users || []).forEach((u) =>
    l.users.set(String(u.pk), { pk: String(u.pk), username: u.username, full_name: u.full_name }));
  const cookie = (name) => (document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`)) || [])[1] || null;
  const label = () => `${list.username || list.id} ${list.kind}`;

  function restore() {
    window.fetch = nativeFetch;
    Object.assign(X, nativeXhr);
  }

  // username from the URL, exact count from the header text "123 followers" (title holds it when abbreviated)
  function profileInfo(kind) {
    const seg = location.pathname.split("/")[1];
    const username = seg && !RESERVED.test(seg) ? seg : null;
    const word = kind === "followers" ? "followers?" : "following";
    const re = new RegExp(`^\\s*(\\d[\\d.,\\s\\u00a0\\u202f]*)\\s*([kmb]|mio\\.?|tsd\\.?)?\\s*${word}\\s*$`, "i");
    let expected = null;
    for (const el of document.querySelectorAll("main a, main button, main li, main div[role=button], main span")) {
      if (el.closest('div[role="dialog"]') || el.textContent.length > 40) continue;
      const m = el.textContent.match(re);
      if (!m) continue;
      const title = el.querySelector("[title]")?.getAttribute("title");
      if (title && /^\s*\d[\d.,\s]*$/.test(title)) { expected = parseInt(title.replace(/\D/g, ""), 10); break; }
      if (!m[2]) { expected = parseInt(m[1].replace(/\D/g, ""), 10); break; }
    }
    return { username, expected };
  }

  function save(l) {
    const day = new Date().toLocaleDateString("sv-SE"); // local YYYY-MM-DD
    const name = `${l.username || l.id}-${l.kind}-${day}.json`;
    const json = JSON.stringify({
      version: 2, collected_at: l.collectedAt, complete: l.complete,
      kind: l.kind, target: { id: l.id, username: l.username }, expected: l.expected, passes: l.passes,
      viewer_id: cookie("ds_user_id"), // recognises own lists
      limited: l.limited, verified: l.verified, users: [...l.users.values()] });
    window.collectResult = json; // fallback if the download is blocked: copy(collectResult)
    const a = Object.assign(document.createElement("a"), {
      href: URL.createObjectURL(new Blob([json], { type: "application/json" })), download: name });
    document.body.appendChild(a);
    a.click();
    a.remove();
    return name;
  }

  function finish(complete, reason) {
    if (!list || list.done) return;
    const l = list;
    l.done = true;
    clearTimeout(l.timer);
    Object.assign(l, { complete, collectedAt: new Date().toISOString() });
    const name = save(l);
    const of = l.expected ? ` of ${l.expected}` : "";
    console.log(`[collect] ${complete ? "done" : "STOPPED (" + reason + ")"}: ${label()}, ${l.users.size}${of} accounts ` +
                `in ${l.passes} pass(es), saved ${name} (no download? run copy(collectResult) and paste into that file)`);
    if (l.limited) console.warn("[collect] Instagram limited this list: it is not the full list");
    if (l.expected && l.users.size < l.expected - Math.max(2, l.expected * 0.02))
      console.warn(`[collect] ${l.expected - l.users.size} fewer than the profile shows (Instagram never lists some, ` +
                   "e.g. deactivated or restricted accounts; a big gap means incomplete)");
    list = null;
    last = l;
    console.log("[collect] ready for the next list (collectStop() to unhook)");
  }

  // collectVerify(["name.one", "name_two"]): checks accounts against the last finished list via the
  // dialog's search. It matches name parts ("." and "_" end a part) and returns at most 12 hits, so each
  // part is tried until the account turns up or an uncapped answer lacks it.
  window.collectVerify = async (names) => {
    const l = last;
    if (!l || !l.complete) return console.warn("[collect] collect a list completely first, then verify names against it");
    if (list?.running) return console.warn(`[collect] busy with ${label()}`);
    const v = (l.verified ||= { found: [], absent: [], unsure: [] });
    for (const name of names) {
      const parts = name.toLowerCase().split(/[._]+/).filter((p) => p.length >= 2).slice(0, 3);
      let verdict = "unsure";
      for (const q of parts.length ? parts : [name]) {
        await pause();
        const url = new URL(l.url, location.origin);
        url.searchParams.delete("max_id");
        url.searchParams.set("query", q);
        let body;
        try {
          const r = await nativeFetch(url, { headers: l.headers, credentials: "include" });
          if (!r.ok) { console.warn(`[collect] HTTP ${r.status}: verification stopped`); verdict = null; break; }
          body = await r.json();
        } catch (e) {
          console.warn("[collect] verification stopped:", e); verdict = null; break;
        }
        const hits = body.users || [];
        const hit = hits.find((u) => (u.username || "").toLowerCase() === name.toLowerCase());
        if (hit) { add(l, { users: [hit] }); verdict = "found"; break; }
        if (hits.length < 12 && !body.next_max_id) { verdict = "absent"; break; } // uncapped answer without it
      }
      if (verdict === null) break; // never retry after an error
      v[verdict].push(name);
      console.log(`[collect] ${name}: ${{ found: "still in the list", absent: "not in the list", unsure: "unsure (search capped)" }[verdict]}`);
    }
    const file = save(l);
    console.log(`[collect] verified: ${v.found.length} found, ${v.absent.length} not in the list, ${v.unsure.length} unsure; ` +
                `${l.users.size}${l.expected ? " of " + l.expected : ""} accounts, saved ${file} again (import the newest one)`);
  };

  // saves the current list (if any) and removes the hooks
  window.collectStop = () => {
    finish(true, "stopped manually");
    restore();
    console.log("[collect] unhooked");
  };

  // one walk; pass 1 continues after the page the dialog loaded, later passes start at the top
  async function walk(l, first) {
    const cursors = new Set();
    let cursor = first ? l.cursor : null, idle = 0;
    for (let page = 0; !l.done; page++) {
      if (page >= MAX_PAGES) return `no end after ${MAX_PAGES} pages`;
      if (first && !cursor) return null;
      if (cursor) cursors.add(cursor);
      await pause();
      if (l.done) return null;
      const url = new URL(l.url, location.origin);
      if (cursor) url.searchParams.set("max_id", cursor);
      else url.searchParams.delete("max_id");
      let body;
      try {
        const r = await nativeFetch(url, { headers: l.headers, credentials: "include" });
        if (!r.ok) return `HTTP ${r.status}`; // never retry: back off and try another day
        body = await r.json();
      } catch (e) {
        return String(e);
      }
      if (l.done) return null;
      const before = l.users.size;
      add(l, body);
      l.limited ||= Boolean(body.should_limit_list_of_followers || body.should_limit_list_of_followings);
      // own lists can keep issuing cursors that return nothing new at the end
      idle = l.users.size > before ? 0 : idle + 1;
      console.log(`[collect] ${label()}: ${l.users.size}${l.expected ? "/" + l.expected : ""} (pass ${l.passes})`);
      const next = body.next_max_id ? String(body.next_max_id) : null;
      if (!next || body.has_more === false) {
        // ranked lists page by offset and end after exactly as many entries as the profile count
        if (cursor === null || /^\d+$/.test(cursor)) l.served = Number(cursor || 0) + (body.users || []).length;
        return null;
      }
      if (cursors.has(next) || (first && idle >= 2)) return null;
      cursor = next;
    }
    return null;
  }

  async function collect(l) {
    l.running = true;
    let barren = 0; // passes in a row without anyone new
    for (l.passes = 1; l.passes <= MAX_PASSES && !l.done; l.passes++) {
      const before = l.users.size;
      if (l.passes > 1) await reopen();
      const error = await walk(l, l.passes === 1);
      if (l.done) return;
      if (error) return finish(false, error);
      if (!l.expected && l.served) {
        l.expected = l.served;
        console.log(`[collect] the list served ${l.served} entries: using that as the count`);
      }
      if (l.expected && l.users.size >= l.expected) return finish(true);
      // some accounts are served only every few passes: with a known count, require two barren passes
      barren = l.passes > 1 && l.users.size === before ? barren + 1 : 0;
      if (barren >= (l.expected ? 2 : 1)) return finish(true);
      console.log(`[collect] pass ${l.passes}: ${l.users.size}${l.expected ? " of " + l.expected : ""}` +
                  (l.passes > 1 ? ` (+${l.users.size - before})` : "") + ", walking the list again");
    }
    l.passes = Math.min(l.passes, MAX_PASSES);
    finish(true);
  }

  // every followers/following response the page itself receives
  function seen(url, headers, body) {
    const [, id, kind] = url.match(RE);
    const params = new URL(url, location.origin).searchParams;
    if (params.get("query")) return; // the dialog's search box, not the list
    if (list && (list.id !== id || list.kind !== kind)) {
      if (list.running) return console.warn(`[collect] busy with ${label()}: ignoring ${kind} of ${id}`);
      list = null; // switched dialogs before collecting started
    }
    const firstPage = !params.has("max_id");
    if (list && firstPage && !list.running) list.users = new Map(); // first page reloaded, e.g. "Show all"
    if (!list) {
      if (!firstPage) return; // scrolling a finished dialog must not start a partial list
      list = { id, kind, ...profileInfo(kind), users: new Map(), limited: false, passes: 0,
               running: false, done: false, timer: null };
      console.log(`[collect] detected ${label()} (${list.expected ? list.expected + " on the profile" : "count not found on the page"})`);
    }
    add(list, body);
    list.limited ||= Boolean(body.should_limit_list_of_followers || body.should_limit_list_of_followings);
    if (list.running) return; // the page's own requests (scrolling) only add users
    Object.assign(list, { url, headers, cursor: body.next_max_id && body.has_more !== false ? String(body.next_max_id) : null });
    clearTimeout(list.timer);
    const l = list;
    l.timer = setTimeout(() => collect(l), 1500 + Math.random() * 1500);
  }

  X.open = function (method, url) {
    this.__url = String(url);
    this.__headers = {};
    return nativeXhr.open.apply(this, arguments);
  };
  X.setRequestHeader = function (k, v) {
    if (this.__headers) this.__headers[k] = v;
    return nativeXhr.setRequestHeader.apply(this, arguments);
  };
  X.send = function () {
    if (RE.test(this.__url)) {
      this.addEventListener("load", () => {
        if (this.status !== 200) return;
        try {
          seen(this.__url, this.__headers, this.responseType === "json" ? this.response : JSON.parse(this.responseText));
        } catch (e) { console.warn("[collect] could not read response", e); }
      });
    }
    return nativeXhr.send.apply(this, arguments);
  };
  window.fetch = async function (input, init) {
    const res = await nativeFetch.apply(this, arguments);
    try {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (RE.test(url) && res.ok) {
        const raw = (init && init.headers) || (input && input.headers) || {};
        const headers = raw instanceof Headers ? Object.fromEntries(raw.entries()) : { ...raw };
        res.clone().json().then((body) => seen(url, headers, body))
          .catch((e) => console.warn("[collect] could not read response", e));
      }
    } catch (e) { console.warn("[collect] could not read response", e); }
    return res;
  };
  console.log("[collect] v5 (multi-pass) ready: open a followers or following list (\"Show all\" if offered), do not scroll");
})();
