// Diagnostics for collect.js. Paste into the console on a profile page, then:
// - igDebug.report(): after scrolling a list dialog by hand to its end. Records only what the page
//   itself requested (endpoints, parameters, paging fields, account ids) and what the dialog shows.
// - igDebug.probe(): with a list dialog open. Sends its own requests (~28 plus one full pass) to
//   compare pagination strategies (stable order, overlapping pages, page size).
// - igDebug.searchProbe({ names: [...], prefixes: [...] }): after typing into the dialog's search box.
//   Replays that search for the given names and prefixes (found? capped? paged?).
// Each downloads a JSON report. Probes stop on the first error. Tokens and cookies are never recorded.
(() => {
  const SECRET = /csrf|dtsg|lsd|jazoest|claim|cookie|auth|token|^__(?!a$)/i;
  const NOISE = /\/ajax\/bz|\/logging|falco|\/static\/|\.(js|css|png|jpg|webp|woff2?)(\?|$)/;
  const log = [];
  const nativeFetch = window.fetch;
  const X = XMLHttpRequest.prototype;
  const native = { open: X.open, setRequestHeader: X.setRequestHeader, send: X.send };
  const t0 = performance.now();
  let noise = 0;

  const short = (v) => (v == null ? v : String(v).length > 60 ? String(v).slice(0, 57) + "..." : String(v));
  const params = (sp) => Object.fromEntries([...sp].map(([k, v]) => [k, SECRET.test(k) ? "<redacted>" : short(v)]));

  // every object that looks like an account: {username, pk|id}
  function accounts(node, out = [], depth = 0) {
    if (!node || typeof node !== "object" || depth > 12) return out;
    if (Array.isArray(node)) { node.forEach((x) => accounts(x, out, depth + 1)); return out; }
    if (typeof node.username === "string" && (node.pk || node.id || node.pk_id)) out.push(String(node.pk || node.pk_id || node.id));
    for (const [k, v] of Object.entries(node)) if (v && typeof v === "object") accounts(v, out, depth + 1);
    return out;
  }

  // top-level keys plus every scalar that smells like pagination, wherever it sits
  function summary(body) {
    const paging = {};
    (function walk(n, path, depth) {
      if (!n || typeof n !== "object" || depth > 6) return;
      for (const [k, v] of Object.entries(n)) {
        if (Array.isArray(v)) continue;
        if (v && typeof v === "object") walk(v, `${path}${k}.`, depth + 1);
        else if (/max_id|cursor|has_more|has_next|end_cursor|rank|page|big_list|limit|count|next/i.test(k))
          paging[path + k] = SECRET.test(k) && !/rank/i.test(k) ? "<redacted>" : short(v);
      }
    })(body, "", 0);
    const ids = accounts(body);
    return { keys: Object.keys(body || {}), paging, accounts: ids.length, ids };
  }

  function bodySummary(b) {
    if (!b) return null;
    if (typeof b === "string") {
      try { return { form: params(new URLSearchParams(b)), variables: JSON.parse(new URLSearchParams(b).get("variables") || "null") }; }
      catch { return { text: short(b) }; }
    }
    if (b instanceof URLSearchParams) return { form: params(b) };
    if (b instanceof FormData) return { form: params(new URLSearchParams([...b].filter(([, v]) => typeof v === "string"))) };
    return { type: b.constructor?.name };
  }

  // raw requests (headers included) for the probes; never reported
  let lastList = null;
  let lastSearch = null;

  function record(method, url, reqHeaders, reqBody, status, json) {
    const u = new URL(url, location.origin);
    if (/\/api\/v1\/friendships\/\d+\/(followers|following)\/$/.test(u.pathname) && status === 200) {
      const req = { url: u.href, headers: { ...(reqHeaders || {}) }, kind: u.pathname.match(/(followers|following)/)[1] };
      // a parameter the plain list never sends: the search box
      const key = [...u.searchParams.keys()].find((k) => !["count", "max_id", "search_surface", "rank_token"].includes(k) && u.searchParams.get(k));
      if (key) lastSearch = { ...req, key };
      else if (!u.searchParams.has("max_id")) lastList = req;
    }
    if (u.origin !== location.origin || NOISE.test(u.pathname + u.search)) { noise++; return; }
    const entry = {
      t: Math.round(performance.now() - t0), method, path: u.pathname, query: params(u.searchParams), status,
      headers: Object.fromEntries(Object.entries(reqHeaders || {}).map(([k, v]) => [k.toLowerCase(), SECRET.test(k) ? "<redacted>" : short(v)])),
      body: bodySummary(reqBody), response: json ? summary(json) : null,
    };
    if (entry.body?.form?.fb_api_req_friendly_name) entry.name = entry.body.form.fb_api_req_friendly_name;
    log.push(entry);
    if (entry.response?.accounts) {
      console.log(`[debug] ${method} ${u.pathname}${entry.name ? " " + entry.name : ""}: ${entry.response.accounts} accounts`, entry.response.paging);
    }
  }

  X.open = function (method, url) {
    Object.assign(this, { __m: method, __u: String(url), __h: {} });
    return native.open.apply(this, arguments);
  };
  X.setRequestHeader = function (k, v) {
    if (this.__h) this.__h[k] = v;
    return native.setRequestHeader.apply(this, arguments);
  };
  X.send = function (body) {
    this.addEventListener("loadend", () => {
      let json = null;
      try { json = this.responseType === "json" ? this.response : JSON.parse(String(this.responseText).replace(/^for \(;;\);/, "")); } catch {}
      try { record(this.__m, this.__u, this.__h, body, this.status, json); } catch (e) { console.warn("[debug]", e); }
    });
    return native.send.apply(this, arguments);
  };
  window.fetch = async function (input, init) {
    const res = await nativeFetch.apply(this, arguments);
    try {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const raw = init?.headers || input?.headers || {};
      const headers = raw instanceof Headers ? Object.fromEntries(raw.entries()) : { ...raw };
      const method = init?.method || input?.method || "GET";
      res.clone().text().then((t) => {
        let json = null;
        try { json = JSON.parse(t.replace(/^for \(;;\);/, "")); } catch {}
        record(method, url, headers, init?.body, res.status, json);
      }).catch(() => {});
    } catch (e) { console.warn("[debug]", e); }
    return res;
  };

  function dom() {
    const dialogs = [...document.querySelectorAll('div[role="dialog"]')];
    const scrollers = [];
    dialogs.forEach((d, di) => d.querySelectorAll("*").forEach((el) => {
      const cs = getComputedStyle(el);
      if (el.scrollHeight > el.clientHeight + 20 && /(auto|scroll)/.test(cs.overflowY))
        scrollers.push({ dialog: di, tag: el.tagName, cls: short(el.className), scrollHeight: el.scrollHeight,
                         clientHeight: el.clientHeight, scrollTop: Math.round(el.scrollTop), overflowY: cs.overflowY });
    }));
    const rows = new Set();
    dialogs.forEach((d) => d.querySelectorAll("a[href]").forEach((a) => {
      const m = a.getAttribute("href").match(/^\/([A-Za-z0-9._]+)\/?$/);
      if (m) rows.add(m[1]);
    }));
    const profileLinks = [...document.querySelectorAll("a[href]")]
      .filter((a) => /^\/[^/?#]+\/(followers|following)/.test(a.getAttribute("href")))
      .map((a) => ({ href: a.getAttribute("href"), text: short(a.textContent),
                     title: a.querySelector("[title]")?.getAttribute("title") || null, inDialog: dialogs.some((d) => d.contains(a)) }));
    const headerCounts = [...document.querySelectorAll("header li, header ul *")].map((e) => short(e.textContent)).filter((s) => /\d/.test(s));
    return { location: location.pathname + location.search, dialogs: dialogs.length, scrollers,
             renderedRows: rows.size, profileLinks, headerCounts: [...new Set(headerCounts)].slice(0, 20),
             ua: navigator.userAgent, viewport: [innerWidth, innerHeight] };
  }

  // per endpoint: pages, duplicates, unique accounts
  function analyse() {
    const lists = {};
    for (const e of log) {
      if (!e.response?.accounts) continue;
      const key = e.name || e.path.replace(/\d{5,}/, "<id>");
      const l = (lists[key] ||= { pages: 0, accounts: 0, union: new Set(), dupesWithinRun: 0, pageSizes: [], querySets: new Set() });
      l.pages++;
      l.pageSizes.push(e.response.accounts);
      l.querySets.add(Object.keys(e.query).sort().join(","));
      for (const id of e.response.ids) { if (l.union.has(id)) l.dupesWithinRun++; l.union.add(id); }
    }
    return Object.fromEntries(Object.entries(lists).map(([k, l]) =>
      [k, { pages: l.pages, unique: l.union.size, dupesWithinRun: l.dupesWithinRun, pageSizes: l.pageSizes.join(" "),
            queryKeySets: [...l.querySets] }]));
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const jaccard = (a, b) => { const A = new Set(a), B = new Set(b); const i = [...A].filter((x) => B.has(x)).length;
                               return A.size + B.size ? +(i / (A.size + B.size - i)).toFixed(3) : 1; };

  // how the profile renders its counts
  function countCandidates() {
    const out = [];
    for (const el of document.querySelectorAll("main *")) {
      if (el.closest('div[role="dialog"]') || el.children.length > 4) continue;
      const t = el.textContent.trim();
      if (t.length <= 40 && /\d/.test(t) && /follow/i.test(t))
        out.push({ tag: el.tagName, role: el.getAttribute("role"), text: t, title: el.querySelector("[title]")?.getAttribute("title") || el.getAttribute("title") });
    }
    return out.slice(0, 15);
  }

  function expectedCount(kind) {
    const word = kind === "followers" ? "followers?" : "following";
    const re = new RegExp(`^\\s*(\\d[\\d.,\\s\\u00a0\\u202f]*)\\s*([kmb]|mio\\.?|tsd\\.?)?\\s*${word}\\s*$`, "i");
    for (const c of countCandidates()) {
      const m = c.text.match(re);
      if (!m) continue;
      if (c.title && /^\s*\d[\d.,\s]*$/.test(c.title)) return parseInt(c.title.replace(/\D/g, ""), 10);
      if (!m[2]) return parseInt(m[1].replace(/\D/g, ""), 10);
    }
    return null;
  }

  let probeRequests = 0;
  async function getPage(base, { count, maxId, rank }) {
    const u = new URL(base.url);
    ["max_id", "rank_token"].forEach((k) => u.searchParams.delete(k));
    if (count) u.searchParams.set("count", count);
    if (maxId) u.searchParams.set("max_id", maxId);
    if (rank) u.searchParams.set("rank_token", rank);
    await sleep(800 + Math.random() * 900);
    probeRequests++;
    const r = await nativeFetch(u, { headers: base.headers, credentials: "include" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`); // never retry
    const b = await r.json();
    return { ids: (b.users || []).map((x) => String(x.pk)), next: b.next_max_id ? String(b.next_max_id) : null,
             more: b.has_more, token: b.follow_ranking_token || null, pageSize: b.page_size ?? null };
  }

  const STRATEGIES = [
    { name: "web", count: 12 },                          // what the dialog sends
    { name: "echo-token", count: 12, rank: "echo" },     // page-1 follow_ranking_token sent back
    { name: "uuid-token", count: 12, rank: "uuid" },     // client-made token, app style
    { name: "count50", count: 50 },
    { name: "count50+uuid", count: 50, rank: "uuid" },
    { name: "count50+echo", count: 50, rank: "echo" },
    { name: "count200+echo", count: 200, rank: "echo" },
  ];

  async function tryStrategy(base, s) {
    const uuid = crypto.randomUUID();
    const p0 = await getPage(base, { count: s.count, rank: s.rank === "uuid" ? uuid : null });
    const rank = s.rank === "echo" ? p0.token : s.rank === "uuid" ? uuid : null;
    const res = { strategy: s.name, page0: p0.ids.length, pageSize: p0.pageSize, firstNext: p0.next, tokenGiven: Boolean(p0.token) };
    if (!p0.next || p0.more === false) return { ...res, singlePage: true, stable: 1, overlap: 0 };
    const a = await getPage(base, { count: s.count, maxId: p0.next, rank });
    const b = await getPage(base, { count: s.count, maxId: p0.next, rank });
    // overlap is measured on pages 2 -> 3: in echo mode page 1 is fetched without a token
    const c = a.next && a.more !== false ? await getPage(base, { count: s.count, maxId: a.next, rank }) : { ids: [] };
    return { ...res, page1: a.ids.length, stable: jaccard(a.ids, b.ids), sameOrder: a.ids.join() === b.ids.join(),
             overlap: c.ids.filter((x) => a.ids.includes(x)).length, overlapFromPage0: a.ids.filter((x) => p0.ids.includes(x)).length,
             nextAfter: a.next, tokenStableAcrossPages: a.token && p0.token ? a.token === p0.token : null };
  }

  async function fullPass(base, s, expected) {
    const uuid = crypto.randomUUID(), seen = new Set(), cursors = new Set();
    let entries = 0, pages = 0, rank = s.rank === "uuid" ? uuid : null, cursor = null;
    const cap = Math.ceil((expected || 5000) / Math.min(s.count, 12)) + 10;
    while (pages < cap) {
      const p = await getPage(base, { count: s.count, maxId: cursor, rank });
      if (pages === 0 && s.rank === "echo") rank = p.token;
      pages++; entries += p.ids.length; p.ids.forEach((x) => seen.add(x));
      console.log(`[probe] full pass ${s.name}: ${seen.size}${expected ? "/" + expected : ""} after ${pages} pages`);
      if (!p.next || p.more === false || cursors.has(p.next)) break;
      cursors.add(p.next); cursor = p.next;
    }
    return { strategy: s.name, pages, entries, unique: seen.size, duplicates: entries - seen.size, expected,
             coverage: expected ? +(seen.size / expected).toFixed(3) : null };
  }

  function download(name, kind, report) {
    const json = JSON.stringify(report, null, 1);
    window.igDebugResult = json; // fallback if the download is blocked: copy(igDebugResult)
    const a = Object.assign(document.createElement("a"), {
      href: URL.createObjectURL(new Blob([json], { type: "application/json" })),
      download: `ig-${name}-${kind}-${new Date().toLocaleString("sv-SE").replace(/[: ]/g, "-")}.json` });
    document.body.appendChild(a); a.click(); a.remove();
    return "downloaded (no download? copy(igDebugResult))";
  }

  window.igDebug = {
    log,
    async searchProbe({ names = [], prefixes = ["a"], maxPages = 30 } = {}) {
      if (!lastSearch) return console.warn("[search] type something into the list dialog's search box first (with this script pasted)");
      const base = lastSearch, report = { created: new Date().toString(), kind: base.kind, key: base.key,
        otherParams: [...new URL(base.url).searchParams.keys()], names: [], prefixes: [], error: null };
      const get = async (q, maxId) => {
        const u = new URL(base.url);
        u.searchParams.set(base.key, q);
        if (maxId) u.searchParams.set("max_id", maxId); else u.searchParams.delete("max_id");
        await sleep(900 + Math.random() * 900);
        probeRequests++;
        const r = await nativeFetch(u, { headers: base.headers, credentials: "include" });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const b = await r.json();
        return { users: (b.users || []).map((x) => ({ pk: String(x.pk), username: x.username || "", full_name: x.full_name || "" })),
                 next: b.next_max_id ? String(b.next_max_id) : null, more: b.has_more ?? null, pageSize: b.page_size ?? null,
                 keys: Object.keys(b) };
      };
      const start = probeRequests;
      try {
        for (const name of names) {
          const p = await get(name);
          const hit = p.users.find((x) => x.username.toLowerCase() === name.toLowerCase());
          report.names.push({ name, found: Boolean(hit), pk: hit?.pk || null, results: p.users.length, more: p.more, next: Boolean(p.next) });
          console.log(`[search] ${name}: ${hit ? "FOUND" : "not found"} (${p.users.length} results)`);
        }
        for (const prefix of prefixes) {
          const pages = [], seen = new Map(), cursors = new Set();
          let p = await get(prefix), repeat = null;
          for (let i = 0; i < maxPages; i++) {
            pages.push({ size: p.users.length, next: p.next, more: p.more, pageSize: p.pageSize });
            p.users.forEach((x) => seen.set(x.pk, x));
            if (i === 0 && p.next) { // first page twice: stable order?
              const again = await get(prefix);
              repeat = { sameOrder: again.users.map((x) => x.pk).join() === p.users.map((x) => x.pk).join(),
                         overlap: again.users.filter((x) => p.users.some((y) => y.pk === x.pk)).length, size: again.users.length };
            }
            if (!p.next || p.more === false || cursors.has(p.next)) break;
            cursors.add(p.next);
            p = await get(prefix, p.next);
          }
          const all = [...seen.values()], pre = prefix.toLowerCase();
          const res = { prefix, pages: pages.length, entries: pages.reduce((a, x) => a + x.size, 0), unique: all.length,
                        pageSizes: pages.map((x) => x.size).join(" "), lastPage: pages[pages.length - 1], repeat,
                        responseKeys: p.keys,
                        usernameStarts: all.filter((x) => x.username.toLowerCase().startsWith(pre)).length,
                        usernameContains: all.filter((x) => x.username.toLowerCase().includes(pre)).length,
                        fullNameOnly: all.filter((x) => !x.username.toLowerCase().includes(pre) && x.full_name.toLowerCase().includes(pre)).length,
                        ids: all.map((x) => x.pk) };
          report.prefixes.push(res);
          console.log(`[search] prefix "${prefix}":`, { ...res, ids: undefined });
        }
      } catch (e) {
        report.error = String(e);
        console.warn("[search] stopped:", report.error);
      }
      report.requests = probeRequests - start;
      return download("search", base.kind, report);
    },
    async probe({ full = true } = {}) {
      if (!lastList) return console.warn("[probe] open a followers/following list first (with this script already pasted)");
      const base = lastList, expected = expectedCount(base.kind), results = [], start = probeRequests;
      const report = { created: new Date().toString(), kind: base.kind, path: new URL(base.url).pathname.replace(/\d{5,}/, "<id>"),
                       expected, countCandidates: countCandidates(), strategies: results, fullPass: null, error: null };
      console.log(`[probe] ${base.kind}, profile count ${expected ?? "not found"}; testing ${STRATEGIES.length} strategies`);
      try {
        for (const s of STRATEGIES) {
          const r = await tryStrategy(base, s);
          results.push(r);
          console.log(`[probe] ${s.name}:`, r);
        }
        // best: stable and no overlap, then the biggest page the server honours
        const ok = results.filter((r) => r.stable === 1 && !r.overlap);
        const best = (ok.length ? ok : results).slice().sort((x, y) => (y.page0 - x.page0) || (y.stable - x.stable))[0];
        report.best = best.strategy;
        if (full) report.fullPass = await fullPass(base, STRATEGIES.find((s) => s.name === best.strategy), expected);
      } catch (e) {
        report.error = String(e);
        console.warn("[probe] stopped:", report.error);
      }
      report.requests = probeRequests - start;
      console.log("[probe] result", { best: report.best, fullPass: report.fullPass, requests: report.requests });
      return download("probe", base.kind, report);
    },
    report() {
      const kind = (location.pathname.match(/(followers|following)/) || [])[1] ||
                   (log.filter((e) => /friendships\/\d+\/(followers|following)/.test(e.path)).pop()?.path.match(/(followers|following)/) || [])[1] || "list";
      const report = { created: new Date().toString(), dom: dom(), lists: analyse(), noiseRequests: noise,
                       requests: log.map((e) => ({ ...e, response: e.response && { ...e.response, ids: e.response.ids.length > 0 ? e.response.ids : undefined } })) };
      console.log("[debug] summary", { dom: { dialogs: report.dom.dialogs, scrollers: report.dom.scrollers.length,
                   renderedRows: report.dom.renderedRows, profileLinks: report.dom.profileLinks }, lists: report.lists });
      return download("debug", kind, report);
    },
  };
  console.log("[debug] ready: igDebug.report(), igDebug.probe(), igDebug.searchProbe({ names, prefixes })");
})();
