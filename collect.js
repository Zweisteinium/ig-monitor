// Collects your follower list inside your real, logged-in browser tab.
// 1. Open https://www.instagram.com/<you>/ , open DevTools -> Console, paste this file, Enter.
// 2. Click "followers" on your profile. Do not scroll; leave the tab in the foreground.
// 3. When it finishes, followers-YYYY-MM-DD.json is downloaded:
//      uv run --env-file .env python -m app.main --import ~/Downloads/followers-YYYY-MM-DD.json
//
// It waits for the request Instagram itself sends when the dialog opens, then continues
// that pagination with the same URL parameters and headers, at a scrolling pace. Cookies,
// TLS, IP and device are the browser's own; nothing is sent anywhere else.
(() => {
  const RE = /\/api\/v1\/friendships\/\d+\/followers\/\?/;
  const users = new Map();
  const nativeFetch = window.fetch;
  const X = XMLHttpRequest.prototype;
  const nativeXhr = { open: X.open, setRequestHeader: X.setRequestHeader, send: X.send };
  let template = null, cursor = null, timer = null, running = false;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  // mostly short gaps, now and then a longer reading pause
  const pause = () => sleep(1800 + Math.random() * 3700 + (Math.random() < 0.12 ? 6000 + Math.random() * 14000 : 0));
  const add = (body) => (body.users || []).forEach((u) =>
    users.set(String(u.pk), { pk: String(u.pk), username: u.username, full_name: u.full_name }));

  function restore() {
    window.fetch = nativeFetch;
    Object.assign(X, nativeXhr);
  }

  function finish(complete, reason) {
    restore();
    const day = new Date().toISOString().slice(0, 10);
    const blob = new Blob([JSON.stringify({ version: 1, collected_at: new Date().toISOString(), complete,
                                            users: [...users.values()] })], { type: "application/json" });
    const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob),
                                                           download: `followers-${day}.json` });
    a.click();
    console.log(`[collect] ${complete ? "done" : "STOPPED (" + reason + ")"}: ${users.size} followers`);
  }

  async function loop() {
    running = true;
    while (cursor) {
      await pause();
      const url = new URL(template.url, location.origin);
      url.searchParams.set("max_id", cursor);
      let r, body;
      try {
        r = await nativeFetch(url, { headers: template.headers, credentials: "include" });
        if (!r.ok) return finish(false, `HTTP ${r.status}`); // never retry: back off and try another day
        body = await r.json();
      } catch (e) {
        return finish(false, String(e));
      }
      add(body);
      console.log(`[collect] ${users.size} followers`);
      cursor = body.next_max_id && String(body.next_max_id) !== cursor ? String(body.next_max_id) : null;
    }
    finish(true);
  }

  // called for every followers request the page itself makes
  function seen(url, headers, body) {
    add(body);
    if (running) return;
    template = { url, headers };
    cursor = body.next_max_id ? String(body.next_max_id) : null;
    clearTimeout(timer);
    timer = setTimeout(() => (cursor ? loop() : finish(true)), 3000 + Math.random() * 2000);
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
      const url = typeof input === "string" ? input : input.url;
      if (RE.test(url) && res.ok) {
        const raw = (init && init.headers) || (input && input.headers) || {};
        const headers = raw instanceof Headers ? Object.fromEntries(raw.entries()) : { ...raw };
        res.clone().json().then((body) => seen(url, headers, body));
      }
    } catch (e) { console.warn("[collect] could not read response", e); }
    return res;
  };
  console.log("[collect] ready: now open your followers list and do not scroll");
})();
