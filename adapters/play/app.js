// ParlorNet Play: the shell. Sign-in, the lobby, a table, chat, settings, sounds, full screen, and the board
// stage every game draws on. Each game is a module (game-*.js) registered with PN.register; a game without one
// is shown with a plain fallback and a link to its classic page. docs/modern-client.md describes the
// interface a game module implements.
"use strict";

var PN = (function () {
  var $ = function (id) { return document.getElementById(id); };
  var games = {};                 // game type ("reversi/1") -> module
  var BOTS = ["bot", "bot1", "bot2", "bot3"];
  var S = {conn: null, info: null, me: null, gam: [], view: null, table: null, lobbyTimer: null,
           palette: null, paletteValue: null, presets: null, sessionTried: false, motd: null, bghome: "right",
           notify: null, rt: null, rtValue: null, rtPresets: null};
  var prefs = loadPrefs();
  // the classic pages' address (http://host:6500/), filled in by the Play extension; "" when they are not offered
  var CLASSIC = (document.querySelector('meta[name="pn-classic"]') || {}).content || "";
  // the Play extension's VAPID public key while it offers push notifications, else ""
  var PUSHKEY = (document.querySelector('meta[name="pn-push"]') || {}).content || "";

  // ---- small helpers ---------------------------------------------------------------------------------
  function el(tag, attrs, kids) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") e.textContent = attrs[k];
      else if (k === "on") Object.keys(attrs.on).forEach(function (ev) { e.addEventListener(ev, attrs.on[ev]); });
      else if (k === "cls") e.className = attrs[k];
      else if (attrs[k] !== null && attrs[k] !== undefined && attrs[k] !== false) e.setAttribute(k, attrs[k] === true ? "" : attrs[k]);
    });
    (kids || []).forEach(function (c) { if (c != null) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return e;
  }
  function clear(e) { while (e.firstChild) e.removeChild(e.firstChild); return e; }
  function announce(text) { var l = $("live"); l.textContent = ""; setTimeout(function () { l.textContent = text; }, 30); }
  function loadPrefs() {
    var d = {sound: true, motion: !!(window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches), render: "auto"};
    try { var s = JSON.parse(localStorage.getItem("pn-play") || "{}"); Object.keys(s).forEach(function (k) { d[k] = s[k]; }); } catch (e) { /* none kept */ }
    return d;
  }
  function savePrefs() { try { localStorage.setItem("pn-play", JSON.stringify(prefs)); } catch (e) { /* private mode */ } }
  function req(line) { return S.conn.request(line); }
  function ok(r) { return r.final.indexOf("OK") === 0; }
  function fmtSecs(s) {
    s = Math.max(0, Math.round(s));
    if (s >= 86400) return Math.floor(s / 86400) + "d " + Math.floor(s % 86400 / 3600) + "h";
    if (s >= 3600) return Math.floor(s / 3600) + "h " + Math.floor(s % 3600 / 60) + "m";
    return Math.floor(s / 60) + ":" + ("0" + s % 60).slice(-2);
  }
  function gameName(key) {
    var g = S.gam.filter(function (x) { return x.key === key; })[0];
    return g ? g.name : (games[key] && games[key].name) || key.split("/")[0];
  }
  // a player's name that opens their profile
  function nameLink(name) {
    return el("button", {type: "button", cls: "plink", text: name, title: name + "'s profile", "aria-haspopup": "dialog",
                         on: {click: function (e) { e.stopPropagation(); openProfile(name); }}});
  }
  // a time from the server (2026-11-23T14:05:09Z) as "just now", "5 min ago", "3 h ago", "2 d ago" or a date
  function ago(iso) {
    var t = Date.parse(iso), s = (Date.now() - t) / 1000;
    if (isNaN(t)) return "";
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + " min ago";
    if (s < 86400) return Math.floor(s / 3600) + " h ago";
    if (s < 7 * 86400) return Math.floor(s / 86400) + " d ago";
    return new Date(t).toLocaleDateString();
  }
  // a turn (TRN, FEED) as a sentence: "eric played FRAULEIN for 65 in Grid Words with BobGrid 2.3 on a
  // Sun SPARCstation", the name opening the profile, with when and where under it
  function turnItem(t) {
    var via = PNNet.viaText(t.prog, t.mach);
    return el("li", {}, [
      el("p", {cls: "what"}, [nameLink(t.name), " " + (t.move || "played a turn") + " in " + gameName(t.key) + (via ? " " + via : "")]),
      el("p", {cls: "when small muted"}, [el("time", {datetime: t.at, title: isNaN(Date.parse(t.at)) ? null : new Date(t.at).toLocaleString(), text: ago(t.at)}),
                                         " at ", el("a", {href: "#/table/" + t.tid, text: "table " + t.tid})])]);
  }

  // ---- sounds, made here with Web Audio --------------------------------------------------------------
  var audio = null;
  function sound(kind) {
    if (!prefs.sound) return;
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      var t = audio.currentTime;
      var knock = function (at, freq, len, vol) {
        var o = audio.createOscillator(), g = audio.createGain();
        o.type = "triangle"; o.frequency.setValueAtTime(freq, at); o.frequency.exponentialRampToValueAtTime(freq * 0.5, at + len);
        g.gain.setValueAtTime(vol, at); g.gain.exponentialRampToValueAtTime(0.001, at + len);
        o.connect(g); g.connect(audio.destination); o.start(at); o.stop(at + len + 0.02);
      };
      var noise = function (at, len, vol, hp) {
        var n = Math.floor(audio.sampleRate * len), b = audio.createBuffer(1, n, audio.sampleRate), d = b.getChannelData(0);
        for (var i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / n, 3);
        var s = audio.createBufferSource(), f = audio.createBiquadFilter(), g = audio.createGain();
        f.type = "bandpass"; f.frequency.value = hp || 2400; f.Q.value = 1.2; g.gain.value = vol;
        s.buffer = b; s.connect(f); f.connect(g); g.connect(audio.destination); s.start(at);
      };
      if (kind === "place") { knock(t, 420, 0.09, 0.25); noise(t, 0.05, 0.25, 3000); }
      else if (kind === "flip") { noise(t, 0.04, 0.12, 4200); }
      else if (kind === "tile") { knock(t, 620, 0.06, 0.18); noise(t, 0.04, 0.2, 2200); }
      else if (kind === "dice") { for (var i = 0; i < 7; i++) noise(t + i * 0.06 + Math.random() * 0.03, 0.035, 0.22, 1800 + Math.random() * 1500); }
      else if (kind === "turn") { knock(t, 880, 0.18, 0.12); knock(t + 0.12, 1320, 0.25, 0.1); }
      else if (kind === "end") { knock(t, 660, 0.3, 0.12); knock(t + 0.15, 990, 0.3, 0.12); knock(t + 0.3, 1320, 0.4, 0.1); }
      else if (kind === "bad") { knock(t, 180, 0.18, 0.2); }
    } catch (e) { /* no sound */ }
  }

  // ---- Grid Words colors (the same arithmetic as adapters/palette.py) -------------------------------------
  function lum(c) {
    var v = PNGfx.hex(c).map(function (x) { return x <= 0.04045 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); });
    return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
  }
  function contrast(a, b) { var x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); }
  function mix(a, b, t) {
    var p = PNGfx.hex(a), q = PNGfx.hex(b);
    return "#" + p.map(function (x, i) { var v = Math.round((x + (q[i] - x) * t) * 255); return ("0" + v.toString(16)).slice(-2); }).join("");
  }
  function bestInk(bg, cands) {
    return (cands || ["#15181c", "#ffffff"]).reduce(function (a, b) { return contrast(bg, b) > contrast(bg, a) ? b : a; });
  }
  var ELEMENTS = ["board", "tw", "dw", "tl", "dl", "tile", "letter"];
  function derived(p) {
    var o = {};
    ELEMENTS.forEach(function (k) { o[k] = p[k]; });
    o.grid = lum(p.board) < 0.18 ? mix(p.board, "#ffffff", 0.16) : mix(p.board, "#000000", 0.32);
    ["tw", "dw", "tl", "dl"].forEach(function (k) { o["label_" + k] = bestInk(p[k]); });
    var acc = ["tl", "tw", "dl", "dw"].map(function (k) { return p[k]; }).filter(function (c) { return contrast(c, p.tile) >= 3; });
    o.blank = acc[0] || p.letter;
    o.arrow = bestInk(p.board, [p.tile, p.dw, p.dl, "#15181c", "#ffffff"]);
    o.points = p.letter;
    return o;
  }
  function decodePalette(value) {
    var v = (value || "").toLowerCase().trim(), presets = (S.presets && S.presets.presets) || [];
    var pre = presets.filter(function (p) { return p.id === v; })[0];
    if (pre) return derived(pre.colours);
    if (v.indexOf("custom:") === 0) {
      var parts = v.slice(7).split(",");
      if (parts.length === 7 && parts.every(function (c) { return /^[0-9a-f]{6}$/.test(c); })) {
        var p = {};
        ELEMENTS.forEach(function (k, i) { p[k] = "#" + parts[i]; });
        return derived(p);
      }
    }
    return null;
  }
  function encodePalette(p) {
    var presets = (S.presets && S.presets.presets) || [];
    for (var i = 0; i < presets.length; i++) {
      if (ELEMENTS.every(function (k) { return presets[i].colours[k].toLowerCase() === p[k].toLowerCase(); })) return presets[i].id;
    }
    return "custom:" + ELEMENTS.map(function (k) { return p[k].slice(1).toLowerCase(); }).join(",");
  }
  function palette() {
    return S.palette || decodePalette((S.presets && S.presets["default"]) || "slate") ||
      derived({board: "#2f343b", tw: "#e07b00", dw: "#f5d22e", tl: "#7b3aa8", dl: "#c9b6f5", tile: "#fafafa", letter: "#15181c"});
  }

  // ---- Rummy Tile colors (adapters/rtpalette.py, docs/design/rummytile-colours.md) ----------------------
  var RT_ELEMENTS = ["table", "tile", "ink", "r", "b", "k", "y"];
  function rtDerived(p) {
    var o = {};
    RT_ELEMENTS.forEach(function (k) { o[k] = p[k]; });
    var dark = contrast(p.table, "#15181c") < contrast(p.table, "#ffffff");
    o.tray = dark ? mix(p.table, "#ffffff", 0.10) : mix(p.table, "#000000", 0.12);
    o.mark = dark ? "#ffffff" : "#15181c";
    return o;
  }
  function rtDecode(value) {
    var v = (value || "").toLowerCase().trim(), presets = (S.rtPresets && S.rtPresets.presets) || [];
    var pre = presets.filter(function (p) { return p.id === v; })[0];
    if (pre) return rtDerived(pre.colours);
    if (v.indexOf("custom:") === 0) {
      var parts = v.slice(7).split(",");
      if (parts.length === 7 && parts.every(function (c) { return /^[0-9a-f]{6}$/.test(c); })) {
        var p = {};
        RT_ELEMENTS.forEach(function (k, i) { p[k] = "#" + parts[i]; });
        return rtDerived(p);
      }
    }
    return null;
  }
  function rtEncode(p) {
    var presets = (S.rtPresets && S.rtPresets.presets) || [];
    for (var i = 0; i < presets.length; i++) {
      if (RT_ELEMENTS.every(function (k) { return presets[i].colours[k].toLowerCase() === p[k].toLowerCase(); })) return presets[i].id;
    }
    return "custom:" + RT_ELEMENTS.map(function (k) { return p[k].slice(1).toLowerCase(); }).join(",");
  }
  function rtPalette() {
    return S.rt || rtDecode((S.rtPresets && S.rtPresets["default"]) || "slate") ||
      rtDerived({table: "#24313c", tile: "#e0e8f5", ink: "#15181c", r: "#a8241c", b: "#2563c9", k: "#1b1e23", y: "#a07c00"});
  }

  // ---- views and the address ---------------------------------------------------------------------------
  function show(name) {
    ["connect", "signin", "lobby", "table", "activity", "scores"].forEach(function (v) { $("v-" + v).hidden = v !== name; });
    S.view = name;
    if (name !== "lobby" && S.lobbyTimer) { clearInterval(S.lobbyTimer); S.lobbyTimer = null; }
    if (name !== "activity" && S.feedTimer) { clearInterval(S.feedTimer); S.feedTimer = null; }
  }
  function crumbs(parts) {
    var c = clear($("crumbs"));
    parts.forEach(function (p, i) {
      if (i) c.appendChild(document.createTextNode(" / "));
      c.appendChild(p[1] ? el("a", {href: p[1], text: p[0]}) : el("span", {text: p[0]}));
    });
  }
  function route() {
    if (!S.me) return;
    var m = /^#\/table\/(\d+)/.exec(location.hash), p = /^#\/player\/([A-Za-z0-9_-]{1,12})$/.exec(location.hash);
    if (!p && $("d-profile").open) $("d-profile").close();     // a link in the profile was followed
    if (m) openTable(m[1]);
    else if (location.hash === "#/activity") { closeTable(); openActivity(); }
    else if (location.hash === "#/scores") { closeTable(); openScores(); }
    else { closeTable(); openLobby(); if (p) openProfile(p[1]); }
  }

  // ---- connecting and signing in -------------------------------------------------------------------------
  function start() {
    $("b-full").addEventListener("click", toggleFull);
    $("b-settings").addEventListener("click", openSettings);
    $("b-motd").addEventListener("click", function () { showMotd(); });
    $("d-motd").addEventListener("close", motdSeen);
    $("s-notify-on").addEventListener("click", notifyOn);
    $("s-notify-off").addEventListener("click", notifyOff);
    $("b-out").addEventListener("click", signOut);
    $("b-retry").addEventListener("click", connect);
    $("b-activity").addEventListener("click", function () { location.hash = "#/activity"; });
    $("b-feed").addEventListener("click", function () { loadFeed(); });
    $("b-scores").addEventListener("click", function () { loadTop(); });
    $("s-scores-game").addEventListener("change", function () { loadTop(); });
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden && S.view === "activity" && Date.now() - (S.feedAt || 0) > FEED_SECS * 1000) loadFeed();
    });
    setupProfile();
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && document.body.classList.contains("is-full") && !fullscreenElement()) { setFull(false); }
      if ((e.key === "f" || e.key === "F") && !e.ctrlKey && !e.metaKey && !e.altKey && S.view === "table" &&
          document.activeElement === $("board")) { toggleFull(); e.preventDefault(); }
    });
    ["fullscreenchange", "webkitfullscreenchange"].forEach(function (ev) {
      document.addEventListener(ev, function () { document.body.classList.toggle("is-full", !!fullscreenElement()); if (S.table) S.table.stage.dirty = true; });
    });
    window.addEventListener("hashchange", route);
    if (!CLASSIC) Array.prototype.forEach.call(document.querySelectorAll(".classic"), function (e) { e.hidden = true; });
    setupSignin();
    setupLobby();
    setupTable();
    fetch("/play/palette.json", {credentials: "same-origin"}).then(function (r) { return r.json(); })
      .then(function (j) { S.presets = j; }).catch(function () { S.presets = null; });
    fetch("/play/rummytile-colours.json", {credentials: "same-origin"}).then(function (r) { return r.json(); })
      .then(function (j) { S.rtPresets = j; if (S.rtValue) S.rt = rtDecode(S.rtValue); }).catch(function () { S.rtPresets = null; });
    connect();
  }
  function connect() {
    show("connect");
    $("connect-msg").textContent = "Connecting to the game server...";
    $("b-retry").hidden = true;
    if (S.conn) S.conn.close();
    S.conn = new PNNet.Conn();
    S.conn.on("close", lost);
    S.conn.connect().then(function (greeting) {
      S.info = PNNet.greetingInfo(greeting);
      $("server-line").textContent = "Server " + S.info.name + (S.info.signup === "email" ? ": new accounts get a password by email." : "");
      sayClient();
      return req("MOTD");
    }).then(function (r) {
      if (ok(r)) {
        S.motd = r.lines.filter(function (l) { return l === "MOTD" || l.indexOf("MOTD ") === 0; }).map(function (l) { return l.slice(5); }).join("\n").replace(/\s+$/, "");
        if (S.motd && cookie("pnmotd") !== hash(S.motd) && !document.querySelector("dialog[open]")) showMotd();
      }
      return req("SES");
    }).then(function (r) {
      // SES alone resumes the cookie's session, or signs in with the Stay signed in key, and then an HND for
      // the new session comes first. A key the server refused is cleared here too.
      var h = r.lines.filter(function (l) { return l.indexOf("HND ") === 0; })[0];
      if (h) keepSession(h.split(" ")[1]);
      if (ok(r) && r.final.split(" ")[1]) return signedIn(r.final.split(" ")[1]);
      if (/^ERR NOKEY/.test(r.final)) {
        forgetCookies();
        return showSignin(PNNet.errText(r.final));
      }
      showSignin();
    }).catch(function () {
      $("connect-msg").textContent = "The game server isn't answering.";
      $("b-retry").hidden = false;
    });
  }
  // On every new connection, before signing in: which browser and system Play runs in (CLI), so the turns
  // played here say so. Only a label for the turns; a core without CLI answers ERR, which changes nothing.
  function sayClient() {
    var c = PNNet.clientClaim(navigator);
    req("CLI PROG " + c.prog);
    if (c.mach) req("CLI MACH " + c.mach);
  }
  function lost() {
    if (S.table) S.table.pause();
    S.me = null;
    show("connect");
    $("connect-msg").textContent = "The connection was lost. Reconnecting...";
    setTimeout(connect, 1500);
  }
  var flow = {kind: "signin", name: ""};
  function setupSignin() {
    function tab(kind) {
      flow.kind = kind;
      $("t-signin").setAttribute("aria-selected", kind === "signin");
      $("t-signup").setAttribute("aria-selected", kind === "signup");
      $("signin-title").textContent = kind === "signin" ? "Sign in" : "New account";
      $("b-name").textContent = kind === "signin" ? "Sign in" : "Make the account";
      $("l-stay").hidden = kind !== "signin" || (S.info && S.info.auth === "none");
      $("signin-msg").textContent = "";
    }
    $("t-signin").addEventListener("click", function () { tab("signin"); });
    $("t-signup").addEventListener("click", function () { tab("signup"); });
    $("f-name").addEventListener("submit", function (e) {
      e.preventDefault();
      var name = $("i-name").value.trim();
      if (!/^[A-Za-z0-9_-]{1,12}$/.test(name)) { $("signin-msg").textContent = "Names are letters, digits, - and _, up to 12."; return; }
      flow.name = name;
      flow.stay = flow.kind === "signin" && $("i-stay").checked && !$("l-stay").hidden;
      $("b-name").disabled = true;
      var verb = flow.kind === "signup" ? "REG" : (S.info.auth === "none" ? "HELO" : "USER");
      req(verb + " " + name).then(step).then(function () { $("b-name").disabled = false; });
    });
    $("f-step").addEventListener("submit", function (e) {
      e.preventDefault();
      answer($("i-step").value.trim());
    });
    $("step-yn").addEventListener("click", function (e) { var a = e.target.getAttribute("data-a"); if (a) answer(a); });
    $("b-cancel").addEventListener("click", function () { connect(); });
  }
  function answer(value) {
    if (!value || !flow.ask) return;
    $("b-step").disabled = true;
    req("AUT " + flow.ask.field + " " + value).then(step).then(function () { $("b-step").disabled = false; });
    $("i-step").value = "";
  }
  function showSignin(msg) {
    show("signin");
    $("f-name").hidden = false; $("f-step").hidden = true;
    $("l-stay").hidden = flow.kind !== "signin" || !S.info || S.info.auth === "none";
    $("signin-msg").textContent = msg || "";
    crumbs([["Sign in"]]);
    $("who").textContent = ""; $("b-out").hidden = true; $("b-activity").hidden = true;
    if ($("d-profile").open) $("d-profile").close();
    setTimeout(function () { $("i-name").focus(); }, 0);
  }
  // A reply in a sign-in flow: INF lines and an ASK (show the step), OK (signed in) or ERR (say why).
  function step(r) {
    var info = r.lines.filter(function (l) { return l === "INF" || l.indexOf("INF ") === 0; }).map(function (l) { return l.slice(4); });
    if (r.final.indexOf("ASK") === 0) {
      var a = flow.ask = PNNet.parseAsk(r.final);
      $("f-name").hidden = true; $("f-step").hidden = false;
      $("step-info").textContent = info.join("\n");
      $("l-step").textContent = a.label;
      var inp = $("i-step");
      inp.type = a.secret ? "password" : "text";
      inp.autocomplete = a.isNew ? "new-password" : (a.cred ? "current-password" : "off");
      inp.hidden = a.yn; $("l-step").hidden = false; $("step-yn").hidden = !a.yn; $("b-step").hidden = a.yn;
      $("signin-msg").textContent = "";
      setTimeout(function () { (a.yn ? $("step-yn").querySelector("button") : inp).focus(); }, 0);
      return;
    }
    if (ok(r)) {
      var h = r.lines.filter(function (l) { return l.indexOf("HND ") === 0; })[0];
      if (h) keepSession(h.split(" ")[1]);
      if (!flow.stay) return signedIn(flow.name);
      // Stay signed in asks for a sign-in key for this browser right after the password. The page never
      // sees the key, only a handle for the key cookie.
      flow.stay = false;
      return req("KEY NEW").then(function (k) {
        var kh = k.lines.filter(function (l) { return l.indexOf("HND ") === 0; })[0];
        if (ok(k) && kh) keepSession(kh.split(" ")[1]);
        return signedIn(flow.name);
      });
    }
    var msg = PNNet.errText(r.final);
    if (/^ERR NOPLAYER/.test(r.final)) msg = "There is no account named " + flow.name + ". Choose New account to make one.";
    if (/^ERR NAMEINUSE/.test(r.final)) msg = "The name " + flow.name + " is taken. Sign in if it is yours.";
    showSignin(msg);
    $("i-name").value = flow.name;
    // a refused step ends the connection's dialog on the server: start again cleanly
  }
  function forgetCookies() {
    // signing out forgets this browser's session and its Stay signed in key
    return fetch("/play/session", {method: "POST", credentials: "same-origin", body: "out=1",
                                   headers: {"Content-Type": "application/x-www-form-urlencoded"}}).catch(function () {});
  }
  function keepSession(handle) {
    // the Play extension's session cookie, so a reload resumes (SES)
    fetch("/play/session", {method: "POST", credentials: "same-origin", body: "h=" + encodeURIComponent(handle),
                            headers: {"Content-Type": "application/x-www-form-urlencoded"}}).catch(function () {});
  }
  function signedIn(name) {
    S.me = name;
    var who = clear($("who"));
    who.appendChild(document.createTextNode("Signed in as ")); who.appendChild(el("b", {}, [nameLink(name)]));
    $("b-out").hidden = false; $("b-activity").hidden = false;
    S.feedAt = 0;
    announce("Signed in as " + name);
    syncPush();
    return Promise.all([req("GAM"), req("PRF GET gwcolors"), req("PRF GET bghome"), req("PRF GET rtcolors")]).then(function (rs) {
      S.gam = rs[0].lines.filter(function (l) { return l.indexOf("GAM ") === 0; }).map(function (l) {
        var f = l.split(" ");
        return {key: f[1], rules: f[2], name: f[3].replace(/_/g, " "), min: +f[4] || 2, max: +f[5] || 2, flags: f.slice(6)};
      });
      var pv = rs[1].lines.filter(function (l) { return l.indexOf("PRF gwcolors ") === 0; })[0];
      S.paletteValue = pv ? pv.slice(13) : null;
      S.palette = decodePalette(S.paletteValue);
      var bh = rs[2].lines.filter(function (l) { return l.indexOf("PRF bghome ") === 0; })[0];
      S.bghome = bh && bh.slice(11).trim() === "left" ? "left" : "right";
      var rv = rs[3].lines.filter(function (l) { return l.indexOf("PRF rtcolors ") === 0; })[0];
      S.rtValue = rv ? rv.slice(13) : null;
      S.rt = rtDecode(S.rtValue);
      route();
    });
  }
  function signOut() {
    // a signed-out browser gets no more notifications for the account: its subscription goes first, while the
    // session that may remove it is still good
    pushSubscription().then(function (sub) { return sub ? pushOff() : null; }).catch(function () {}).then(function () {
      return req("OUT");
    }).then(function () {
      forgetCookies();
      closeTable();
      S.me = null;
      history.replaceState(null, "", "#/");
      showSignin("Signed out.");
      $("signin-msg").className = "msg ok";
    });
  }

  // ---- the lobby ----------------------------------------------------------------------------------------------
  var pick = {key: null};
  function setupLobby() {
    $("f-new").addEventListener("submit", function (e) { e.preventDefault(); newTable(); });
    $("b-leave").addEventListener("click", function () { location.hash = "#/lobby"; });
  }
  function openLobby() {
    show("lobby");
    crumbs([["Lobby"]]);
    document.title = "Lobby - ParlorNet Play";
    renderGames();
    loadLobby();
    if (S.lobbyTimer) clearInterval(S.lobbyTimer);
    S.lobbyTimer = setInterval(function () { if (!document.hidden) loadLobby(); }, 4000);
  }
  function renderGames() {
    var box = clear($("new-games")), seen = {};
    S.gam.forEach(function (g) {
      if (seen[g.key]) return;
      seen[g.key] = true;
      if (!pick.key) pick.key = g.key;
      // a game without a board of its own in Play is unfinished here: it opens with the plain view (Fallback)
      var b = el("button", {type: "button", role: "radio", "aria-checked": String(pick.key === g.key), on: {click: function () { pick.key = g.key; renderGames(); }}},
                 [g.name, el("small", {}, [(g.min === g.max ? g.min : g.min + "-" + g.max) + " players ",
                                           games[g.key] ? null : el("span", {cls: "badge unfinished", text: "unfinished"})])]);
      box.appendChild(b);
    });
    $("new-unfinished").hidden = !pick.key || !!games[pick.key];
    var rules = clear($("new-rules")), pl = clear($("new-players"));
    S.gam.filter(function (g) { return g.key === pick.key; }).forEach(function (g, i) {
      rules.appendChild(el("option", {value: g.rules, text: g.rules.charAt(0).toUpperCase() + g.rules.slice(1), selected: i === 0}));
    });
    var g0 = S.gam.filter(function (g) { return g.key === pick.key; })[0] || {min: 2, max: 2};
    for (var n = g0.min; n <= g0.max; n++) pl.appendChild(el("option", {value: n, text: String(n)}));
    rules.parentNode.hidden = rules.options.length < 2;
    pl.parentNode.hidden = g0.min === g0.max;
  }
  function loadLobby() {
    Promise.all([req("LST clients").then(function (r) { return ok(r) ? r : req("LST"); }), req("IVL"), req("WHO")]).then(function (rs) {
      if (S.view !== "lobby") return;
      renderTables(rs[0].lines);
      renderInvites(rs[1].lines);
      renderOnline(rs[2].lines);
    });
    if (Date.now() - (S.feedAt || 0) > FEED_SECS * 1000) loadFeed();
    if (Date.now() - (S.topAt || 0) > FEED_SECS * 1000) loadTop();
  }

  // ---- the leaderboard (TOP): the first five in the lobby, the whole of it on its page -----------------------------
  // A server with scores.show off answers ERR NOSCORES: then neither shows. Each says that scores are easy to get.
  function scoreWords(s) {
    var n = function (k, one, many) { return s[k] + " " + (s[k] === 1 ? one : many); };
    return n("points", "point", "points") + ": " + n("wins", "win", "wins") + ", " + n("draws", "draw", "draws") + ", " +
           n("losses", "loss", "losses") + ", " + s.resigns + " resigned";
  }
  function openScores() {
    show("scores");
    crumbs([["Lobby", "#/lobby"], ["Leaderboard"]]);
    document.title = "Leaderboard - ParlorNet Play";
    var sel = $("s-scores-game"), keep = sel.value, seen = {};
    while (sel.options.length > 1) sel.remove(1);
    S.gam.forEach(function (g) {
      if (seen[g.key]) return;
      seen[g.key] = true;
      sel.appendChild(el("option", {value: g.key, text: g.name}));
    });
    sel.value = seen[keep] ? keep : "";
    loadTop();
  }
  function loadTop() {
    S.topAt = Date.now();
    var page = S.view === "scores", game = page ? $("s-scores-game").value : "";
    return req("TOP " + (page ? 50 : 5) + (game ? " game=" + game : "")).then(function (r) {
      var rows = ok(r) ? PNNet.parseTop(r.lines) : null;
      if (page) {
        var tb = clear($("scores"));
        $("scores-note").textContent = rows ? (+(r.final.split(" ")[1]) || 0) + " players with a score" : "";
        if (!rows) tb.appendChild(el("tr", {}, [el("td", {cls: "empty", colspan: "7", text: "This server shows no leaderboard."})]));
        else if (!rows.length) tb.appendChild(el("tr", {}, [el("td", {cls: "empty", colspan: "7", text: "No scores yet: finish a game to get one."})]));
        else rows.forEach(function (s) {
          tb.appendChild(el("tr", {}, [el("td", {cls: "n", text: String(s.rank)}), el("td", {}, [nameLink(s.name)]),
            el("td", {cls: "n", text: String(s.points)}), el("td", {cls: "n", text: String(s.wins)}),
            el("td", {cls: "n", text: String(s.draws)}), el("td", {cls: "n", text: String(s.losses)}),
            el("td", {cls: "n", text: String(s.resigns)})]));
        });
      } else if (S.view === "lobby") {
        $("top-card").hidden = !rows;
        var ol = clear($("top-mini"));
        if (rows && !rows.length) ol.appendChild(el("li", {cls: "empty", text: "No scores yet."}));
        (rows || []).forEach(function (s) {
          ol.appendChild(el("li", {}, [el("span", {}, [s.rank + ". ", nameLink(s.name)]),
                                       el("span", {cls: "n", text: s.points + " point" + (s.points === 1 ? "" : "s")})]));
        });
      }
    });
  }

  // ---- activity: the latest turns at public tables (FEED) ------------------------------------------------------
  // The lobby shows the last few, the activity page more; asked when either opens and every FEED_SECS while
  // the page is visible.
  var FEED_SECS = 45;
  function openActivity() {
    show("activity");
    crumbs([["Lobby", "#/lobby"], ["Activity"]]);
    document.title = "Activity - ParlorNet Play";
    loadFeed();
    S.feedTimer = setInterval(function () { if (!document.hidden) loadFeed(); }, FEED_SECS * 1000);
  }
  function loadFeed() {
    S.feedAt = Date.now();
    var page = S.view === "activity";
    return req("FEED " + (page ? 30 : 5)).then(function (r) {
      var turns = ok(r) ? PNNet.parseTurns(r.lines) : null;
      if (page) {
        var ol = clear($("feed"));
        $("activity-note").textContent = turns && turns.length ? "updated " + new Date().toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) : "";
        if (!turns) ol.appendChild(el("li", {cls: "empty", text: "This server does not keep a record of turns."}));
        else if (!turns.length) ol.appendChild(el("li", {cls: "empty", text: "No turns at public tables yet."}));
        else turns.forEach(function (t) { ol.appendChild(turnItem(t)); });
      } else if (S.view === "lobby") {
        $("feed-card").hidden = !turns;
        var ul = clear($("feed-mini"));
        if (turns && !turns.length) ul.appendChild(el("li", {cls: "empty", text: "No turns at public tables yet."}));
        (turns || []).forEach(function (t) { ul.appendChild(turnItem(t)); });
      }
    });
  }

  // ---- a player's profile (PFL), in a dialog over whatever is open -----------------------------------------------
  function setupProfile() {
    $("pf-close").addEventListener("click", function () { $("d-profile").close(); });
    $("d-profile").addEventListener("close", function () {
      if (/^#\/player\//.test(location.hash)) history.replaceState(null, "", "#/lobby");
    });
    $("f-pref").addEventListener("submit", function (e) {
      e.preventDefault();
      var v = PNNet.clean($("i-pref").value).replace(/\s+/g, " ").trim().slice(0, 40);
      $("b-pref").disabled = true;
      req("PFL SET PREF" + (v ? " " + v : "")).then(function (r) {
        $("b-pref").disabled = false;
        if (!ok(r)) { profileMsg(PNNet.errText(r.final)); return; }
        openProfile(S.me, v ? "Saved." : "Cleared.");
      });
    });
  }
  function profileMsg(text, good) { var m = $("pf-msg"); m.textContent = text || ""; m.className = "msg" + (good ? " ok" : ""); }
  function openProfile(name, saved) {
    var d = $("d-profile");
    $("h-profile").textContent = name;
    $("pf-pref").textContent = "Loading...";
    $("pf-body").hidden = true; $("f-pref").hidden = true; $("pf-score").hidden = true;
    profileMsg(saved || "", !!saved);
    if (!d.open) d.showModal();
    req("PFL " + name).then(function (r) {
      if (!ok(r)) {
        $("pf-pref").textContent = /^ERR NOPLAYER/.test(r.final) ? "There is no player named " + name + "." :
          /^ERR (UNKNOWN|SYNTAX)/.test(r.final) ? "This server does not keep profiles." : PNNet.errText(r.final);
        return;
      }
      var p = PNNet.parseProfile(r.lines), mine = !!S.me && p.name.toLowerCase() === S.me.toLowerCase();
      $("h-profile").textContent = p.name || name;
      $("pf-pref").textContent = p.pref ? "" : mine ? "You have not named a preferred machine." : "No preferred machine given.";
      if (p.pref) { $("pf-pref").appendChild(document.createTextNode("Preferred machine: ")); $("pf-pref").appendChild(el("b", {text: p.pref})); }
      $("f-pref").hidden = !mine;
      if (mine) $("i-pref").value = p.pref;
      var sc = clear($("pf-score"));
      sc.hidden = !p.score;
      if (p.score) {
        sc.appendChild(document.createTextNode("Score: "));
        sc.appendChild(el("b", {text: scoreWords(p.score)}));
        if (p.score.rank) sc.appendChild(document.createTextNode(", number " + p.score.rank + " on the "));
        else sc.appendChild(document.createTextNode(". The "));
        sc.appendChild(el("a", {href: "#/scores", text: "leaderboard"}));
        sc.appendChild(document.createTextNode("."));
      }
      var list = function (id, items, label, unit) {
        var ul = clear($(id));
        if (!items.length) ul.appendChild(el("li", {cls: "empty", text: "None yet."}));
        items.forEach(function (x) {
          ul.appendChild(el("li", {}, [el("span", {text: label(x)}), el("span", {cls: "n", text: x.n + " " + unit + (x.n === 1 ? "" : "s")})]));
        });
      };
      list("pf-games", p.games, function (x) { return gameName(x.key); }, "game");
      list("pf-progs", p.progs, function (x) { return x.text; }, "turn");
      list("pf-machs", p.machs, function (x) { return x.text; }, "turn");
      $("pf-body").hidden = false;
      $("pf-recent-box").hidden = true;
      return req("FEED 5 " + (p.name || name)).then(function (f) {
        var turns = ok(f) ? PNNet.parseTurns(f.lines) : [];
        var ol = clear($("pf-recent"));
        turns.forEach(function (t) { ol.appendChild(turnItem(t)); });
        $("pf-recent-box").hidden = !turns.length;
      });
    });
  }
  function parseTab(l) {
    var f = l.split(" "), fs = f[4].split("/"), seated = f[6].split(",");
    var t = {id: f[1], key: f[2], rules: f[3], filled: +fs[0], seats: +fs[1], status: f[5], seated: seated,
             names: seated.filter(function (n) { return n !== "-"; }), vis: "public", bots: []};
    f.slice(7).forEach(function (x) {
      if (x.indexOf("vis=") === 0) t.vis = x.slice(4);
      if (x.indexOf("bot=") === 0) t.bots = x.slice(4).split(",").map(Number);
      if (x.indexOf("turn=") === 0) t.turn = +x.slice(5);
    });
    return t;
  }
  // LST lines -> the tables, each with "clients": {seat: what its player plays with} from the CLIENT lines after
  // its TAB line (LST clients)
  function parseTabs(lines) {
    var tabs = [];
    lines.forEach(function (l) {
      if (l.indexOf("TAB ") === 0) { var t = parseTab(l); t.clients = {}; tabs.push(t); }
      else if (tabs.length) { var c = PNNet.parseClient(l); if (c) tabs[tabs.length - 1].clients[c.seat] = c.text; }
    });
    return tabs;
  }
  // the order of the other tables: those where a person waits, then the bot's open tables (an open public table where
  // only bots sit, docs/bot.md), then the games being played
  function rank(t) {
    if (t.status !== "open") return 2;
    return t.vis === "public" && t.filled > 0 && t.bots.length === t.filled ? 1 : 0;
  }
  function renderTables(lines) {
    var box = clear($("tables"));
    var tabs = parseTabs(lines);
    tabs.sort(function (a, b) { return (b.names.indexOf(S.me) >= 0) - (a.names.indexOf(S.me) >= 0) || rank(a) - rank(b) || a.id - b.id; });
    var more = lines.filter(function (l) { return /^MORE \d+$/.test(l); }).map(function (l) { return +l.slice(5); })[0];
    $("tables-note").textContent = tabs.length ? tabs.length + " open or playing" + (more ? ", " + more + " more not listed" : "")
                                               : "";
    if (!tabs.length) box.appendChild(el("p", {cls: "empty", text: "No tables yet. Start one: the bot will play you."}));
    tabs.forEach(function (t) {
      var mine = t.names.indexOf(S.me) >= 0;
      var who = [];
      t.seated.forEach(function (n, i) {
        if (i) who.push(", ");
        if (n === "-") who.push("(empty)");
        else { who.push(nameLink(n)); if (t.bots.indexOf(i) >= 0) who.push(" (bot)"); }
      });
      var act;
      if (mine) act = el("button", {type: "button", cls: "primary", text: "Go to your game", on: {click: function () { location.hash = "#/table/" + t.id; }}});
      else if (t.status === "open") act = el("button", {type: "button", text: "Join", on: {click: function () { join(t.id); }}});
      else act = el("button", {type: "button", text: "Watch", on: {click: function () { location.hash = "#/table/" + t.id; }}});
      // what each seated player plays with, where the server says it: "ann with GameMaster 1.0.0 on a Macintosh Plus"
      var via = t.seated.map(function (n, i) { return n !== "-" && t.clients[i] ? n + " " + t.clients[i] : ""; })
                        .filter(Boolean).join("; ");
      box.appendChild(el("div", {cls: "trow" + (mine ? " mine" : "")}, [
        el("span", {cls: "num", text: "#" + t.id}),
        el("div", {}, [el("div", {cls: "gname"}, [gameName(t.key), t.vis === "private" ? el("span", {cls: "badge", text: "private"}) : null,
                                                  t.status === "open" ? el("span", {cls: "badge", text: "open"}) : null]),
                       el("div", {cls: "meta"}, who.concat([" - " + t.filled + "/" + t.seats + " seats" + (t.turn ? ", " + fmtSecs(t.turn) + " a turn" : "")])),
                       via ? el("div", {cls: "meta via", text: via}) : null]),
        act]));
    });
  }
  function renderInvites(lines) {
    var inv = lines.filter(function (l) { return l.indexOf("IVT ") === 0; }).map(function (l) {
      var f = l.split(" "); return {id: f[1], key: f[2], from: f[4]};
    });
    $("inv-card").hidden = !inv.length;
    var ul = clear($("invites"));
    inv.forEach(function (i) {
      ul.appendChild(el("li", {}, [el("span", {}, [gameName(i.key) + " #" + i.id + " from ", nameLink(i.from)]),
        el("span", {cls: "row"}, [el("button", {type: "button", cls: "primary", text: "Join", on: {click: function () { join(i.id); }}}),
                                  el("button", {type: "button", text: "Decline", on: {click: function () { req("UNV " + i.id + " " + S.me).then(loadLobby); }}})])]));
    });
  }
  function renderOnline(lines) {
    var ul = clear($("online"));
    var ps = lines.filter(function (l) { return l.indexOf("PLR ") === 0; });
    if (!ps.length) ul.appendChild(el("li", {cls: "empty", text: "Nobody else right now."}));
    ps.forEach(function (l) {
      var f = l.split(" "), idle = +((f[2] || "idle=0").split("=")[1]), bot = f.indexOf("bot") > 1;
      var note = (f[1] === S.me ? " (you)" : "") + (bot ? " (the bot)" : "");
      ul.appendChild(el("li", {cls: idle > 600 ? "idle" : ""}, [el("span", {}, [nameLink(f[1]), note])]));
    });
  }
  function join(tid) {
    req("JON " + tid).then(function (r) {
      if (ok(r)) location.hash = "#/table/" + tid;
      else { $("new-msg").textContent = PNNet.errText(r.final); loadLobby(); }
    });
  }
  function newTable() {
    var g = S.gam.filter(function (x) { return x.key === pick.key; })[0];
    if (!g) return;
    var rules = $("new-rules").value || g.rules, players = +$("new-players").value || g.min;
    var line = "NEW " + g.key + " " + rules + " turn=" + $("new-turn").value + ($("new-private").checked ? " vis=private" : "") +
               (g.max > g.min ? " players=" + players : "");
    $("b-new").disabled = true; $("new-msg").textContent = "";
    req(line).then(function (r) {
      if (!ok(r)) throw new Error(PNNet.errText(r.final));
      var tid = r.final.split(" ")[1];
      return req("JON " + tid).then(function () {
        if (!$("new-bot").checked) return tid;
        var ps = [];
        for (var i = 0; i < players - 1; i++) ps.push(req("INV " + tid + " " + BOTS[i]));
        return Promise.all(ps).then(function () { return tid; });
      });
    }).then(function (tid) { location.hash = "#/table/" + tid; })
      .catch(function (e) { $("new-msg").textContent = e.message; })
      .then(function () { $("b-new").disabled = false; });
  }

  // ---- the board stage: a canvas, a renderer and the game's scene ----------------------------------------------
  function Stage(canvasEl) {
    this.cv = canvasEl; this.r = PNGfx.make(canvasEl, prefs.render); this.game = null; this.dirty = true;
    var self = this;
    this.loop = function (now) {
      self.raf = null;
      if (!self.game) return;
      var anim = self.game.animating && self.game.animating(now);
      if (self.dirty || anim) {
        self.dirty = false;
        var sc = self.game.frame(now);
        if (sc) {
          var asp = sc.view.aspect || (sc.view.w + 2 * (sc.view.pad || 0.4)) / (sc.view.h + 2 * (sc.view.pad || 0.4));
          if (asp !== self.aspect) { self.aspect = asp; self.cv.parentNode.style.setProperty("--board-aspect", asp.toFixed(3)); }
          self.r.render(sc.view, sc.items, sc.bg);
        }
        self.scene = sc;
      }
      if (anim || self.dirty) self.kick();
    };
    if (window.ResizeObserver) new ResizeObserver(function () { self.dirty = true; self.kick(); }).observe(canvasEl);
    window.addEventListener("resize", function () { self.dirty = true; self.kick(); });
    ["pointerdown", "pointermove", "pointerup", "pointerleave", "pointercancel"].forEach(function (k) {
      canvasEl.addEventListener(k, function (e) {
        if (!self.game || !self.game.pointer || !self.scene) return;
        if (k === "pointerdown") { canvasEl.focus({preventScroll: true}); try { canvasEl.setPointerCapture(e.pointerId); } catch (x) { /* */ } }
        var p = self.toBoard(e, 0);
        p.at = function (z) { return self.toBoard(e, z); };
        self.game.pointer(k.slice(7), p, e);
        if (k === "pointerdown") e.preventDefault();
      });
    });
    canvasEl.addEventListener("keydown", function (e) {
      if (self.game && self.game.key && self.game.key(e)) { e.preventDefault(); self.redraw(); }
    });
    // a game that zooms takes the wheel (true: taken, the page does not scroll)
    canvasEl.addEventListener("wheel", function (e) {
      if (!self.game || !self.game.wheel || !self.scene) return;
      if (self.game.wheel(self.toBoard(e, 0), e)) { e.preventDefault(); self.redraw(); }
    }, {passive: false});
  }
  Stage.prototype.toBoard = function (e, z) {
    var rect = this.cv.getBoundingClientRect(), dpr = this.r.dpr || 1;
    return this.r.cam.unproject((e.clientX - rect.left) * dpr, (e.clientY - rect.top) * dpr, this.cv.width, this.cv.height, z);
  };
  Stage.prototype.kick = function () { if (!this.raf) this.raf = requestAnimationFrame(this.loop); };
  Stage.prototype.redraw = function () { this.dirty = true; this.kick(); };
  Stage.prototype.useRenderer = function (kind) {
    // a canvas keeps the context it was first given: a new canvas for another kind of drawing
    var old = this.cv, cv = old.cloneNode(false);
    old.parentNode.replaceChild(cv, old);
    var game = this.game;
    Stage.call(this, cv);
    this.game = game;
    this.redraw();
    void kind;
  };

  // ---- a table ------------------------------------------------------------------------------------------------------
  function setupTable() {
    S.stage = new Stage($("board"));
    $("f-chat").addEventListener("submit", function (e) {
      e.preventDefault();
      var t = PNNet.clean($("i-chat").value).trim();
      if (!t || !S.table) return;
      $("i-chat").value = "";
      req("CHT " + S.table.tid + " " + t.slice(0, 56)).then(function (r) { if (S.table) S.table.poll(); if (!ok(r)) S.table.toast(PNNet.errText(r.final)); });
    });
    $("f-invite").addEventListener("submit", function (e) {
      e.preventDefault();
      var n = $("i-invite").value.trim();
      if (!S.table || !/^[A-Za-z0-9_-]{1,12}$/.test(n)) return;
      req("INV " + S.table.tid + " " + n).then(function (r) {
        S.table.toast(ok(r) ? n + " is invited" + (/offline/.test(r.final) ? " (not online now; the invitation waits)" : ".") : PNNet.errText(r.final), ok(r));
        $("i-invite").value = ""; S.table.poll();
      });
    });
    $("b-bot").addEventListener("click", function () { if (S.table) S.table.inviteBot(); });
    $("f-note").addEventListener("submit", function (e) {
      e.preventDefault();
      var t = PNNet.clean($("i-note").value).trim();
      if (!t || !S.table) return;
      req("NOTE " + S.table.tid + " " + t.slice(0, 48)).then(function (r) {
        if (!S.table) return;
        if (ok(r)) $("i-note").value = ""; else S.table.toast(PNNet.errText(r.final));
        S.table.loadNotes();
      });
    });
    $("b-resign").addEventListener("click", function () {
      if (!S.table) return;
      confirmBox("Resign this game?", "Your opponent wins. Leaving the page does not resign: your seat waits for you.").then(function (yes) {
        if (yes) req("RES " + S.table.tid).then(function () { S.table.poll(); });
      });
    });
    $("b-stand").addEventListener("click", function () {
      if (!S.table) return;
      req("LVE " + S.table.tid).then(function () { location.hash = "#/lobby"; });
    });
  }
  function confirmBox(title, text) {
    return new Promise(function (resolve) {
      var d = $("d-confirm");
      $("h-confirm").textContent = title; $("p-confirm").textContent = text;
      d.returnValue = "";
      d.addEventListener("close", function once() { d.removeEventListener("close", once); resolve(d.returnValue === "yes"); });
      d.showModal();
    });
  }
  function openTable(tid) {
    if (S.table && S.table.tid === tid) { show("table"); return; }
    closeTable();
    show("table");
    S.table = new Table(tid);
    S.table.open();
  }
  function closeTable() {
    if (S.table) { S.table.close(); S.table = null; }
    S.stage.game = null;
  }

  function Table(tid) {
    this.tid = tid; this.seq = 0; this.seat = -1; this.names = []; this.key = null; this.mod = null;
    this.common = {}; this.timer = null; this.closed = false; this.over = false; this.stage = S.stage;
    this.busy = false; this.wasMine = false;
    this.clients = {}; this.lastJoin = null;     // seat -> what its player plays with (CLIENT); the JOIN line just logged
  }
  Table.prototype.open = function () {
    var self = this;
    crumbs([["Lobby", "#/lobby"], ["Table " + this.tid]]);
    clear($("log")); clear($("game-panel")); clear($("players")); clear($("notes"));
    $("f-note").hidden = true;
    $("a-classic").href = CLASSIC + "table.htm?t=" + this.tid;
    return this.loadTab().then(function (tab) {
      if (self.seat < 0) return req("SEE " + self.tid);
    }).then(function () { return req("VUE " + self.tid + " GMTC"); }).then(function (r) {
      if (!ok(r)) { self.toast("That table isn't here any more."); return; }
      var head = (r.lines[0] || "").split(" ");
      self.key = self.key || head[1];
      document.title = gameName(self.key) + " #" + self.tid + " - ParlorNet Play";
      crumbs([["Lobby", "#/lobby"], [gameName(self.key) + ", table " + self.tid]]);
      self.makeModule();
      self.applyView(r.lines);
      return req("SIN " + self.tid + " 0");
    }).then(function (r) {
      if (!r) return;
      self.takeEvents(r.lines, true);
      self.loadNotes();
      self.loadTurns();
      self.timer = setInterval(function () { if (!document.hidden) self.poll(); }, 1000);
      self.clockTimer = setInterval(function () { self.renderPlayers(); }, 1000);
      self.stage.cv.focus({preventScroll: true});
    });
  };
  Table.prototype.loadTab = function () {
    var self = this;
    return req("LST").then(function (r) {
      var tab = r.lines.filter(function (l) { return l.split(" ")[1] === self.tid && l.indexOf("TAB ") === 0; }).map(parseTab)[0];
      if (tab) {
        self.tab = tab; self.key = tab.key; self.names = tab.seated; self.seat = tab.seated.indexOf(S.me);
      } else self.tab = null;
      self.renderInvite();
      return tab;
    });
  };
  Table.prototype.makeModule = function () {
    var self = this, M = games[this.key] || Fallback;
    var api = {
      tid: this.tid, key: this.key,
      seat: function () { return self.seat; }, names: function () { return self.names; },
      name: function (s) { var n = self.names[s]; return n && n !== "-" ? n : "seat " + (s + 1); },
      req: req, mov: function (tokens) { return self.move(tokens); },
      chk: function (tokens) { return req("CHK " + self.tid + " " + tokens); },
      und: function () { return self.cmd("UND"); }, end: function () { return self.cmd("END"); },
      sound: sound, announce: announce, toast: function (t, good) { self.toast(t, good); },
      redraw: function () { self.stage.redraw(); }, palette: palette, rtPalette: rtPalette, motion: function () { return !prefs.motion; },
      bghome: function () { return S.bghome; },
      panel: $("game-panel"), common: function () { return self.common; }, el: el, clear: clear,
      askLetter: askLetter, over: function () { return self.over; }, myTurn: function () { return self.myTurn(); },
      contrast: contrast
    };
    this.mod = M.create(api);
    this.stage.game = this.mod;
    this.stage.redraw();
  };
  Table.prototype.myTurn = function () {
    return this.seat >= 0 && !this.over && this.common.turn === this.seat;
  };
  Table.prototype.applyView = function (lines) {
    var c = {status: "", scores: {}, clock: null, bots: [], turn: null}, self = this, clients = {};
    lines.forEach(function (l) {
      var f = l.split(" "), cl = PNNet.parseClient(l);
      if (cl) clients[cl.seat] = cl.text;
      else if (f[0] === "S") c.status = l.slice(2);
      else if (f[0] === "T" && f.length === 3) c.scores[+f[1]] = +f[2];
      else if (f[0] === "CLK") c.clock = {seat: +f[1], left: +f[2], turn: +f[3], at: Date.now()};
      else if (f[0] === "BOT") c.bots = f.slice(1).map(Number);
      else if (f[0] === "W") c.turn = f[1] === "-" || f[1] === "-1" ? null : +f[1];
      else if (f[0] === "V") c.seq = +f[2];
    });
    if (/Game over/i.test(c.status)) this.over = true;
    this.clients = clients;     // VUE C: what each seated player plays with, as the server lets this player see it
    // the static geometry (G) comes with views until a game is under way: before it starts there is none
    if (lines.length > 3 && !/Waiting for players/i.test(c.status)) this.geo = true;
    this.common = c;
    if (this.mod) this.mod.view(lines, c);
    var mine = this.myTurn();
    if (mine && !this.wasMine) { sound("turn"); announce("Your move. " + (this.mod.status ? this.mod.status() : "")); }
    this.wasMine = mine;
    this.renderPlayers();
    this.stage.redraw();
    var st = $("stage-status");
    st.textContent = c.status || (this.tab && this.tab.status === "open" ? "Waiting for players" : "");
    st.className = "stage-status" + (mine ? " mine" : "");
    $("b-resign").hidden = !(this.seat >= 0 && !this.over && this.tab && this.tab.status === "playing");
    $("b-stand").hidden = !(this.seat < 0 || (this.tab && this.tab.status === "open"));
    $("b-stand").textContent = this.seat < 0 ? "Stop watching" : "Stand up";
    this.stage.cv.setAttribute("aria-label", gameName(this.key) + " board. " + (c.status || "") + " " + (this.mod.status ? this.mod.status() : ""));
  };
  Table.prototype.renderPlayers = function () {
    var box = clear($("players")), c = this.common, self = this, n = Math.max(this.names.length, this.tab ? this.tab.seats : 2);
    for (var i = 0; i < n; i++) {
      var seated = !!this.names[i] && this.names[i] !== "-", name = seated ? this.names[i] : "(empty seat)";
      var left = "";
      if (c.clock && c.clock.seat === i && !this.over) {
        var secs = c.clock.left - (Date.now() - c.clock.at) / 1000;
        left = fmtSecs(secs);
      }
      var chip = this.mod && this.mod.seatColour ? this.mod.seatColour(i) : null;
      box.appendChild(el("div", {cls: "p" + (c.turn === i && !this.over ? " turn" : "")}, [
        chip ? el("span", {cls: "chip", "aria-hidden": "true"}) : el("span"),
        el("span", {}, [el("b", {}, [seated ? nameLink(name) : name]), i === this.seat ? " (you)" : "", (c.bots || []).indexOf(i) >= 0 ? el("span", {cls: "badge bot", text: "bot"}) : null,
                        seated && this.clients[i] ? el("span", {cls: "via", text: this.clients[i]}) : null]),
        el("span", {cls: "pts", text: c.scores[i] != null ? String(c.scores[i]) : ""}),
        el("span", {cls: "clk" + (left && secs < 60 ? " low" : ""), text: left, "aria-label": left ? "time left " + left : null})]));
      if (chip) box.lastChild.firstChild.style.background = chip;
    }
    var tl = $("turnline");
    var mine = this.myTurn();
    tl.textContent = this.over ? (c.status || "Game over") : mine ? "Your move" : c.turn != null ? self.nameOf(c.turn) + " to move" : (c.status || "");
    tl.className = "turnline" + (mine ? " mine" : "");
  };
  Table.prototype.nameOf = function (s) { var n = this.names[s]; return n && n !== "-" ? n : "seat " + (s + 1); };
  Table.prototype.renderInvite = function () {
    var open = this.tab && this.tab.status === "open" && this.seat >= 0;
    $("invite-card").hidden = !open;
    if (open) {
      var pending = this.pendingInvites || [];
      $("invited").textContent = (this.tab.vis === "private" ? "A private table: only invited players can sit down. " : "") +
        (pending.length ? "Invited: " + pending.join(", ") + "." : "Invite a player, or the bot.");
      $("b-bot").disabled = !this.nextBot();
    }
  };
  Table.prototype.nextBot = function () {
    var taken = (this.names || []).concat(this.pendingInvites || []).map(function (n) { return n.toLowerCase(); });
    return BOTS.filter(function (b) { return taken.indexOf(b) < 0; })[0];
  };
  Table.prototype.inviteBot = function () {
    var b = this.nextBot(), self = this;
    if (!b) return;
    req("INV " + this.tid + " " + b).then(function (r) { self.toast(ok(r) ? "The bot is invited: it sits down in a few seconds." : PNNet.errText(r.final), ok(r)); self.poll(); });
  };
  Table.prototype.toast = function (text, good) {
    var t = $("toast");
    t.textContent = text; t.hidden = false; t.className = "toast" + (good ? " ok" : "");
    if (!good) sound("bad");
    announce(text);
    clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(function () { t.hidden = true; }, 4200);
  };
  // events: "EVT table seq kind ..." lines; quiet: the table's history when it opens (no animation)
  Table.prototype.takeEvents = function (lines, quiet) {
    var self = this, any = false;
    lines.forEach(function (l) {
      var f = l.split(" ");
      if (f[0] !== "EVT" || f[1] !== self.tid) return;
      var seq = +f[2];
      if (seq <= self.seq) return;
      self.seq = seq; any = true;
      var text = f.slice(3).join(" ");
      self.event(text, quiet, seq);
    });
    return any;
  };
  // the events the shell itself describes; the rest are the game's (moves), which a turn's line can be about
  var SHELL_EVENTS = ["JOIN", "CLIENT", "LEAVE", "START", "END", "CHT", "INVITE", "UNINVITE", "GONE", "TURN", "INT"];
  Table.prototype.event = function (text, quiet, seq) {
    var f = text.split(" "), kind = f[0], joined = this.lastJoin;
    this.lastJoin = null;
    if (kind === "CLIENT") { this.client(text, joined, quiet); return; }     // the shell's own, never the game's
    if (kind === "JOIN" && f.length >= 3) { this.names[+f[1]] = f[2]; delete this.clients[+f[1]]; this.pendingInvites = (this.pendingInvites || []).filter(function (n) { return n !== f[2]; }); }
    if (kind === "LEAVE" && f.length >= 2) delete this.clients[+f[1]];
    if (kind === "INVITE" && f.length >= 3) { this.pendingInvites = (this.pendingInvites || []).concat([f[2]]); }
    if (kind === "UNINVITE" && f.length >= 3) { this.pendingInvites = (this.pendingInvites || []).filter(function (n) { return n !== f[2]; }); }
    if (kind === "END") { this.over = true; if (!quiet) sound("end"); }
    if (this.mod && this.mod.event) this.mod.event(text, quiet, seq);
    var line = (this.mod && this.mod.describe && this.mod.describe(text)) || this.describe(text);
    if (line) this.log(line, kind === "CHT" ? "say" : "", kind === "CHT" ? f : null, seq, SHELL_EVENTS.indexOf(kind) < 0);
    if (!quiet && kind !== "CHT" && line) announce(line);
    if (kind === "JOIN" && line) this.lastJoin = {seat: +f[1], li: $("log").lastChild, line: line, quiet: quiet};
  };
  // CLIENT seat text: what the seat's player plays with. Right after their JOIN it finishes that line ("ann sits
  // down with GameMaster 1.0.0 on a Macintosh Plus"); later, the player came back with another program ("ann now
  // plays with ..."). A CLIENT with no text (a private table's, which this player may not see) says nothing.
  Table.prototype.client = function (text, joined, quiet) {
    var c = PNNet.parseClient(text);
    if (!c) return;
    this.clients[c.seat] = c.text;
    var line;
    if (joined && joined.seat === c.seat && joined.li) {
      joined.li.appendChild(document.createTextNode(" " + c.text));
      line = joined.line + " " + c.text;
    } else {
      line = this.nameOf(c.seat) + " now plays " + c.text;
      this.log(line, "", null, 0, false);
    }
    if (!quiet) announce(line);
    this.renderPlayers();
  };
  Table.prototype.describe = function (text) {
    var f = text.split(" "), self = this, nm = function (s) { return s === "-" ? "a watcher" : self.nameOf(+s); };
    switch (f[0]) {
      case "JOIN": return f[2] + " sits down";
      case "LEAVE": return nm(f[1]) + " stands up";
      case "START": return "The game starts";
      case "TURN": return null;
      case "END": return "Game over: " + (f[1] === "win" ? nm(f[2]) + " wins" : f[1] === "draw" ? "a draw" : f[1] === "abandoned" ? "the table was closed" : f[1] === "timeout" ? nm(f[2]) + " ran out of time" : f.slice(1).join(" "));
      case "CHT": return null;
      case "INVITE": return f[2] + " is invited";
      case "UNINVITE": return f[2] + "'s invitation ended" + (f[3] ? " (" + f[3] + ")" : "");
      case "GONE": return nm(f[1]) + " is out of the game (" + f[2] + ")";
      case "INT": return f[1] === "false" ? "Integrity: " + f.slice(2).join(" ").replace(/\.$/, "") + "." : null;
    }
    return null;
  };
  // the table's notes (NOTE)
  Table.prototype.loadNotes = function () {
    var self = this;
    return req("NOTE " + this.tid).then(function (r) {
      if (self.closed || !ok(r)) return;
      var ul = clear($("notes"));
      r.lines.forEach(function (l) {
        var f = l.split(" ");
        if (f[0] !== "NOTE" || f.length < 4) return;
        ul.appendChild(el("li", {}, [el("span", {cls: "muted", text: f[1] + " "}), f[2] === "-" ? "" : el("b", {}, [nameLink(f[2]), ": "]), f.slice(3).join(" ")]));
      });
      if (!ul.children.length) ul.appendChild(el("li", {cls: "muted", text: "No notes yet."}));
      $("f-note").hidden = self.seat < 0;
    });
  };
  // a line in the table talk; seq: the event's number, move: a game's event (a turn's "with ... on ..." may
  // be added to it, annotate)
  Table.prototype.log = function (text, cls, chat, seq, move) {
    var ol = $("log"), li;
    if (chat) {
      var seat = chat[1], who = seat === "-" ? "watcher" : this.nameOf(+seat);
      var known = seat !== "-" && this.names[+seat] && this.names[+seat] !== "-";
      li = el("li", {cls: "say"}, [el("b", {}, [known ? nameLink(who) : who, ": "]), chat.slice(2).join(" ")]);
    } else li = el("li", {cls: cls || ""}, this.linked(text));
    if (seq) li.setAttribute("data-seq", seq);
    if (move) li.setAttribute("data-move", "");
    ol.appendChild(li);
    while (ol.children.length > 200) ol.removeChild(ol.firstChild);
    ol.scrollTop = ol.scrollHeight;
  };
  // a line's text with the seated players' names opening their profiles
  Table.prototype.linked = function (text) {
    var names = (this.names || []).filter(function (n) { return n && n !== "-"; });
    if (!names.length) return [text];
    var re = new RegExp("(^|[^A-Za-z0-9_-])(" + names.join("|") + ")(?![A-Za-z0-9_-])", "g"), out = [], last = 0, m;
    while ((m = re.exec(text))) {
      var at = m.index + m[1].length;
      out.push(text.slice(last, at), nameLink(m[2]));
      last = at + m[2].length;
    }
    out.push(text.slice(last));
    return out;
  };
  // The table's turns (TRN): who played each with which program on which machine. Asked when the table opens
  // and after new events, at most every TURNS_SECS; each turn's "with ... on ..." goes on its line in the table
  // talk. A core without TRN (ERR), or a table whose turns are not for this player, is not asked again.
  var TURNS_SECS = 3;
  Table.prototype.loadTurns = function () {
    var self = this, wait = (this.turnsAt || 0) + TURNS_SECS * 1000 - Date.now();
    if (this.noTurns || this.closed) return;
    if (this.turnsBusy || wait > 0) {
      if (!this.turnsTimer) this.turnsTimer = setTimeout(function () { self.turnsTimer = null; self.loadTurns(); }, Math.max(wait, 500));
      return;
    }
    this.turnsAt = Date.now(); this.turnsBusy = true;
    req("TRN " + this.tid + " " + (this.turnSeq || 0)).then(function (r) {
      self.turnsBusy = false;
      if (self.closed) return;
      if (!ok(r)) { self.noTurns = true; return; }
      var turns = PNNet.parseTurns(r.lines), first = !self.turnSeq;
      turns.forEach(function (t, i) {
        if (t.seq > self.seq) return;            // its events are not here yet: next time
        // the first of a cut list (the latest 50) has no known start: it only marks where the next begins
        if (!(first && i === 0 && turns.length >= 50)) self.annotate(t);
        self.turnSeq = t.seq;
      });
    });
  };
  // A turn's line: the first move line after the turn before it (turnSeq) up to the turn's last event, the
  // one naming its player if there is one.
  Table.prototype.annotate = function (t) {
    var via = PNNet.viaText(t.prog, t.mach), lo = this.turnSeq || 0, pick = null;
    if (!via) return;
    Array.prototype.forEach.call($("log").children, function (li) {
      var s = +li.getAttribute("data-seq");
      if (!li.hasAttribute("data-move") || !(s > lo && s <= t.seq) || li.querySelector(".via")) return;
      var named = (" " + li.textContent + " ").indexOf(" " + t.name + " ") >= 0;
      if (!pick || (named && !pick.named)) pick = {li: li, named: named};
    });
    if (pick) pick.li.appendChild(el("span", {cls: "via", text: via}));
  };
  Table.prototype.poll = function () {
    var self = this;
    if (this.busy || this.closed) return Promise.resolve();
    this.busy = true;
    return req("SIN " + this.tid).then(function (r) {
      var any = self.takeEvents(r.lines);
      if (any) self.loadTurns();
      if (any || self.needView) {
        self.needView = false;
        var join = r.lines.some(function (l) { return / (JOIN|LEAVE|START|END|INVITE|UNINVITE) /.test(l); });
        return (join ? self.loadTab() : Promise.resolve()).then(function () { return req("VUE " + self.tid + (self.geo ? " MTC" : " GMTC")); })
          .then(function (v) { if (ok(v)) self.applyView(v.lines); });
      }
    }).then(function () { self.busy = false; }, function () { self.busy = false; });
  };
  Table.prototype.move = function (tokens) {
    var self = this;
    return req("MOV " + this.tid + " " + tokens).then(function (r) {
      if (ok(r)) {
        if (self.takeEvents(r.lines)) self.loadTurns();
        return req("VUE " + self.tid + (self.geo ? " MTC" : " GMTC")).then(function (v) { if (ok(v)) self.applyView(v.lines); return r.final; });
      }
      self.toast(PNNet.errText(r.final));
      return r.final;
    });
  };
  Table.prototype.cmd = function (verb) {
    var self = this;
    return req(verb + " " + this.tid).then(function (r) {
      if (!ok(r)) self.toast(PNNet.errText(r.final));
      if (self.takeEvents(r.lines)) self.loadTurns();
      return req("VUE " + self.tid + (self.geo ? " MTC" : " GMTC")).then(function (v) { if (ok(v)) self.applyView(v.lines); return r.final; });
    });
  };
  Table.prototype.pause = function () { clearInterval(this.timer); clearInterval(this.clockTimer); };
  Table.prototype.close = function () {
    this.closed = true; this.pause(); clearTimeout(this.turnsTimer);
    if (this.mod && this.mod.destroy) this.mod.destroy();
    if (this.seat < 0 && S.conn && S.conn.open) req("LVE " + this.tid);
  };

  // A game without a module of its own: its view as text, the legal moves as buttons, a move box, and its
  // classic page.
  var Fallback = {name: "", create: function (api) {
    var pre = el("pre", {cls: "view-lines"}), moves = el("div", {cls: "moves"}), inp = el("input", {"aria-label": "Move", placeholder: "Move, as the protocol writes it"});
    var p = clear(api.panel);
    p.appendChild(el("h2", {text: "This game"}));
    p.appendChild(el("p", {cls: "small"}, CLASSIC ? ["This game has no board here yet. ", el("a", {href: CLASSIC + "table.htm?t=" + api.tid, text: "Open its classic page"}), " for the full board, or play from the view below."] : ["This game has no board here yet. Play from the view below."]));
    p.appendChild(pre); p.appendChild(moves);
    p.appendChild(el("form", {cls: "row", on: {submit: function (e) { e.preventDefault(); if (inp.value.trim()) api.mov(inp.value.trim()); inp.value = ""; }}}, [inp, el("button", {type: "submit", text: "Move"})]));
    return {
      view: function (lines) {
        pre.textContent = lines.filter(function (l) { return !/^(M|V) /.test(l); }).join("\n");
        clear(moves);
        lines.filter(function (l) { return l.indexOf("M ") === 0; }).slice(0, 60).forEach(function (l) {
          var m = l.slice(2);
          moves.appendChild(el("button", {type: "button", text: m, on: {click: function () { api.mov(m); }}}));
        });
      },
      frame: function () {
        return {view: {w: 8, h: 3, tilt: 0, pad: 0.2}, bg: "#14171b", items: [{t: "text", x: 4, y: 1.5, text: "Board on the classic page", size: 0.5, color: "#a3adb8"}]};
      }
    };
  }};

  // ---- the blank's letter ------------------------------------------------------------------------------------
  function askLetter() {
    return new Promise(function (resolve) {
      var d = $("d-letter"), box = clear($("letters")), chosen = null;
      "ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("").forEach(function (c) {
        box.appendChild(el("button", {type: "button", text: c, on: {click: function () { chosen = c; d.close("ok"); }}}));
      });
      d.addEventListener("close", function once() { d.removeEventListener("close", once); resolve(chosen); });
      d.showModal();
      box.firstChild.focus();
    });
  }

  // ---- full screen ---------------------------------------------------------------------------------------------
  function fullscreenElement() { return document.fullscreenElement || document.webkitFullscreenElement || null; }
  function setFull(on) {
    var root = document.documentElement;
    if (on) {
      var rq = root.requestFullscreen || root.webkitRequestFullscreen;
      if (rq) {
        try { var pr = rq.call(root); if (pr && pr.catch) pr.catch(function () { document.body.classList.add("is-full"); }); }
        catch (e) { document.body.classList.add("is-full"); }
      } else document.body.classList.add("is-full");     // no Fullscreen API: the page fills the window
    } else {
      if (fullscreenElement()) (document.exitFullscreen || document.webkitExitFullscreen).call(document);
      document.body.classList.remove("is-full");
    }
    $("b-full").setAttribute("aria-pressed", String(on));
    if (S.stage) S.stage.redraw();
  }
  function toggleFull() { setFull(!(fullscreenElement() || document.body.classList.contains("is-full"))); }

  // ---- the message of the day ---------------------------------------------------------------------------------
  // Shown when it has changed since this browser last closed it: the cookie pnmotd holds a hash of the text.
  function hash(text) {
    var h = 0x811c9dc5;           // FNV-1a, 32 bits: enough to tell one message from the next
    for (var i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
    return ("0000000" + h.toString(16)).slice(-8);
  }
  function cookie(name) {
    var m = new RegExp("(?:^|; )" + name + "=([^;]*)").exec(document.cookie);
    return m ? m[1] : null;
  }
  function showMotd() {
    var pre = clear($("motd-text"));
    var text = S.motd || "There is no message of the day.";
    // addresses in it become links
    text.split(/(https?:\/\/[^\s<>"]+[^\s<>".,;:!?)])/).forEach(function (part, i) {
      pre.appendChild(i % 2 ? el("a", {href: part, text: part, target: "_blank", rel: "noopener"}) : document.createTextNode(part));
    });
    if (!$("d-motd").open) $("d-motd").showModal();
    $("motd-ok").focus();
  }
  function motdSeen() {
    if (!S.motd) return;
    document.cookie = "pnmotd=" + hash(S.motd) + "; Path=/play/; Max-Age=31536000; SameSite=Strict" +
      (location.protocol === "https:" ? "; Secure" : "");
  }

  // ---- push notifications (docs/play-notifications.md) ---------------------------------------------------------
  var KINDS = ["turn", "end", "join"];
  function isIOS() { return /iPhone|iPad|iPod/.test(navigator.userAgent) || (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1); }
  function pushSupported() { return !!(PUSHKEY && "serviceWorker" in navigator && window.PushManager && window.Notification); }
  function swReady() {
    return navigator.serviceWorker.register("sw.js").then(function () { return navigator.serviceWorker.ready; });
  }
  function pushSubscription() {
    if (!pushSupported()) return Promise.resolve(null);
    return navigator.serviceWorker.getRegistration("./").then(function (reg) { return reg ? reg.pushManager.getSubscription() : null; });
  }
  function keyBytes(b64) {
    var s = atob(b64.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((b64.length + 3) % 4)), out = new Uint8Array(s.length);
    for (var i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
    return out;
  }
  function pushPost(fields) {
    var body = Object.keys(fields).map(function (k) { return k + "=" + encodeURIComponent(fields[k]); }).join("&");
    return fetch("/play/push", {method: "POST", credentials: "same-origin", body: body,
                                headers: {"Content-Type": "application/x-www-form-urlencoded"}}).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) throw new Error(j.error || "The server did not take that.");
        if (j.kinds) S.notify = j.kinds;
        return j;
      });
    });
  }
  function subFields(sub, kinds) {
    var j = sub.toJSON();
    return {op: "on", endpoint: j.endpoint, p256dh: j.keys.p256dh, auth: j.keys.auth, kinds: kinds};
  }
  function pushOn(kinds) {
    // the permission is asked first, straight from the click: Safari asks only in answer to one
    var asked = Notification.permission === "granted" ? Promise.resolve("granted") :
      new Promise(function (resolve) { var r = Notification.requestPermission(resolve); if (r && r.then) r.then(resolve); });
    return asked.then(function (perm) {
      if (perm !== "granted") throw new Error(perm === "denied" ? "Notifications are blocked for this site in the browser's settings." : "Notifications were not allowed.");
      return swReady();
    }).then(function (reg) {
      return reg.pushManager.getSubscription().then(function (sub) {
        return sub || reg.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: keyBytes(PUSHKEY)});
      });
    }).then(function (sub) { return pushPost(subFields(sub, kinds)); });
  }
  function pushOff() {
    return pushSubscription().then(function (sub) {
      if (!sub) return null;
      var endpoint = sub.endpoint;
      return sub.unsubscribe().catch(function () {}).then(function () { return pushPost({op: "off", endpoint: endpoint}); });
    });
  }
  // After signing in: a subscription this browser holds is told to the server again (it may have dropped it).
  function syncPush() {
    if (!pushSupported() || Notification.permission !== "granted") return;
    pushSubscription().then(function (sub) { if (sub) return pushPost(subFields(sub, "")); }).catch(function () {});
  }
  function kindsChecked() { return KINDS.filter(function (k) { return $("s-nk-" + k).checked; }).join(",") || "-"; }
  function renderNotify(msg, good) {
    var box = $("s-notify");
    box.hidden = !PUSHKEY || !S.me;
    if (box.hidden) return Promise.resolve();
    var m = $("s-notify-msg");
    m.textContent = msg || ""; m.className = "msg" + (good ? " ok" : "");
    var kinds = (S.notify && S.notify !== "-" ? S.notify : S.notify === "-" ? "" : "turn,end").split(",");
    KINDS.forEach(function (k) { $("s-nk-" + k).checked = kinds.indexOf(k) >= 0; });
    if (!pushSupported()) {
      $("s-notify-state").textContent = isIOS() && !navigator.standalone ?
        "On iPhone and iPad, notifications come only to Play on the Home Screen (iOS 16.4 or later): tap Share, then Add to Home Screen, and open Play from there." :
        "This browser can't receive notifications from Play.";
      $("s-notify-on").hidden = $("s-notify-off").hidden = true;
      return Promise.resolve();
    }
    return pushSubscription().then(function (sub) {
      var blocked = Notification.permission === "denied";
      $("s-notify-state").textContent = sub ? "Notifications are on for this device." :
        blocked ? "Notifications are blocked for this site in the browser's settings. The page below says how to allow them." :
        "Notifications are off for this device.";
      $("s-notify-on").hidden = !!sub || blocked;
      $("s-notify-off").hidden = !sub;
    });
  }
  function notifyOn() {
    $("s-notify-on").disabled = true;
    pushOn(kindsChecked()).then(function () { return renderNotify("Notifications are on. Your other devices keep their own setting.", true); },
                                function (e) { return renderNotify(e.message); })
      .then(function () { $("s-notify-on").disabled = false; });
  }
  function notifyOff() {
    $("s-notify-off").disabled = true;
    pushOff().then(function () { return renderNotify("Notifications are off for this device.", true); },
                   function (e) { return renderNotify(e.message); })
      .then(function () { $("s-notify-off").disabled = false; });
  }

  // ---- settings ----------------------------------------------------------------------------------------------
  var draft = null;
  function openSettings() {
    $("s-sound").checked = prefs.sound; $("s-motion").checked = prefs.motion; $("s-render").value = prefs.render;
    $("s-colours").hidden = !S.me || !S.presets;
    $("s-msg").textContent = "";
    var cur = palette();
    draft = {};
    ELEMENTS.forEach(function (k) { draft[k] = cur[k]; });
    renderColourForm();
    $("s-rt").hidden = !S.me || !S.rtPresets;
    var rc = rtPalette();
    rtDraft = {};
    RT_ELEMENTS.forEach(function (k) { rtDraft[k] = rc[k]; });
    renderRtForm();
    $("s-bg").hidden = !S.me;
    $("s-bghome-" + (S.bghome === "left" ? "left" : "right")).checked = true;
    $("s-notify").hidden = true;
    if (S.me && PUSHKEY) req("PRF GET notify").then(function (r) {
      var l = r.lines.filter(function (x) { return x.indexOf("PRF notify ") === 0; })[0];
      S.notify = l ? l.split(" ")[2] : null;
      renderNotify();
    });
    $("s-close").onclick = function () { $("d-settings").close(); };
    $("s-save").onclick = function (e) { e.preventDefault(); saveSettings(); };
    $("d-settings").showModal();
  }
  function renderColourForm() {
    if (!S.presets) return;
    var pre = clear($("s-presets")), cust = clear($("s-custom")), code = encodePalette(draft);
    S.presets.presets.forEach(function (p) {
      pre.appendChild(el("button", {type: "button", role: "radio", "aria-checked": String(code === p.id), text: p.name,
        on: {click: function () { ELEMENTS.forEach(function (k) { draft[k] = p.colours[k]; }); renderColourForm(); }}}));
    });
    pre.appendChild(el("button", {type: "button", role: "radio", "aria-checked": String(code.indexOf("custom:") === 0), text: "Your own", tabindex: "-1"}));
    ELEMENTS.forEach(function (k) {
      var inp = el("input", {type: "color", value: draft[k], "aria-label": S.presets.names[k]});
      inp.addEventListener("input", function () { draft[k] = inp.value; renderPreview(); pre.lastChild.setAttribute("aria-checked", "true"); });
      cust.appendChild(el("label", {}, [S.presets.names[k], inp]));
    });
    renderPreview();
  }
  function renderPreview() {
    var c = $("s-preview"), x = c.getContext("2d"), d = derived(draft), s = 64, pad = 8;
    x.fillStyle = d.board; x.fillRect(0, 0, c.width, c.height);
    var kinds = [["", null], ["tw", "3W"], ["dw", "2W"], ["tl", "3L"], ["dl", "2L"]];
    kinds.forEach(function (k, i) {
      var X = pad + i * (s + pad), Y = pad;
      x.fillStyle = k[0] ? d[k[0]] : d.board; x.fillRect(X, Y, s, s);
      x.strokeStyle = d.grid; x.lineWidth = 2; x.strokeRect(X, Y, s, s);
      if (k[0]) {
        x.fillStyle = d["label_" + k[0]]; x.font = "700 20px " + PNGfx.FONT; x.textAlign = "center"; x.textBaseline = "middle"; x.fillText(k[1], X + s / 2, Y + s / 2);
        if (k[0] === "tw" || k[0] === "dw") { x.strokeStyle = d["label_" + k[0]]; x.strokeRect(X + 6, Y + 6, s - 12, s - 12); }
      }
    });
    var X0 = pad + 5 * (s + pad);
    [["A", "1"], ["Q", "10"]].forEach(function (t, i) {
      var X = X0 + i * (s + pad);
      x.fillStyle = d.tile; PNGfx.roundRect(x, X, pad, s, s, 8); x.fill();
      x.fillStyle = d.letter; x.font = "700 34px " + PNGfx.FONT; x.textAlign = "center"; x.textBaseline = "middle"; x.fillText(t[0], X + s * 0.46, pad + s * 0.54);
      x.font = "600 13px " + PNGfx.FONT; x.textAlign = "right"; x.fillText(t[1], X + s - 6, pad + s - 9);
    });
  }
  // the Rummy Tile colors: presets, a color per element and a preview of the table and five tiles
  var rtDraft = null;
  function renderRtForm() {
    if (!S.rtPresets) return;
    var pre = clear($("s-rt-presets")), cust = clear($("s-rt-custom")), code = rtEncode(rtDraft);
    S.rtPresets.presets.forEach(function (p) {
      pre.appendChild(el("button", {type: "button", role: "radio", "aria-checked": String(code === p.id), text: p.name,
        on: {click: function () { RT_ELEMENTS.forEach(function (k) { rtDraft[k] = p.colours[k]; }); renderRtForm(); }}}));
    });
    pre.appendChild(el("button", {type: "button", role: "radio", "aria-checked": String(code.indexOf("custom:") === 0), text: "Your own", tabindex: "-1"}));
    RT_ELEMENTS.forEach(function (k) {
      var inp = el("input", {type: "color", value: rtDraft[k], "aria-label": "Rummy Tile " + S.rtPresets.names[k]});
      inp.addEventListener("input", function () { rtDraft[k] = inp.value; renderRtPreview(); pre.lastChild.setAttribute("aria-checked", "true"); });
      cust.appendChild(el("label", {}, [S.rtPresets.names[k], inp]));
    });
    renderRtPreview();
  }
  function renderRtPreview() {
    var c = $("s-rt-preview"), x = c.getContext("2d"), d = rtDerived(rtDraft), w = 52, h = 70, pad = 12, g = games["rummytile/1"];
    x.fillStyle = d.table; x.fillRect(0, 0, c.width, c.height);
    if (g && g.paintTile) ["R7", "B8", "K9", "Y10", "J"].forEach(function (t, i) {
      var X = pad + i * (w + 10);
      x.save(); x.translate(X, 5); PNGfx.roundRect(x, 0, 0, w, h, 7); x.clip();
      g.paintTile(x, w, h, t, d); x.restore();
    });
  }
  function saveSettings() {
    var rerender = prefs.render !== $("s-render").value;
    prefs.sound = $("s-sound").checked; prefs.motion = $("s-motion").checked; prefs.render = $("s-render").value;
    savePrefs();
    if (rerender && S.stage) S.stage.useRenderer(prefs.render);
    if (S.me) {
      var home = $("s-bghome-left").checked ? "left" : "right";
      if (home !== S.bghome) {
        S.bghome = home;
        req("PRF SET bghome " + home);
        if (S.stage) S.stage.redraw();
        if (S.table && S.table.mod && S.table.mod.paletteChanged) S.table.mod.paletteChanged();
      }
    }
    // the kinds of notification, for the account (the server keeps them with its notify key)
    if (S.me && PUSHKEY && !$("s-notify").hidden && S.notify !== null && kindsChecked() !== S.notify) {
      pushPost({op: "kinds", kinds: kindsChecked()}).catch(function (e) { $("s-msg").textContent = e.message; });
    }
    if (S.me && S.rtPresets && rtDraft) {
      var rtv = rtEncode(rtDraft);
      if (rtv !== S.rtValue && !(!S.rtValue && rtv === S.rtPresets["default"])) {
        req("PRF SET rtcolors " + rtv).then(function (r) {
          if (!ok(r)) { $("s-msg").textContent = PNNet.errText(r.final); return; }
          S.rtValue = rtv; S.rt = rtDecode(rtv);
          if (S.stage) S.stage.redraw();
          if (S.table && S.table.mod && S.table.mod.paletteChanged) S.table.mod.paletteChanged();
        });
      }
    }
    if (!S.me || !S.presets || !draft) { $("d-settings").close(); return; }
    var value = encodePalette(draft);
    if (value === S.paletteValue || (!S.paletteValue && value === S.presets["default"])) { $("d-settings").close(); return; }
    req("PRF SET gwcolors " + value).then(function (r) {
      if (ok(r)) {
        S.paletteValue = value; S.palette = decodePalette(value);
        if (S.stage) S.stage.redraw();
        if (S.table && S.table.mod && S.table.mod.paletteChanged) S.table.mod.paletteChanged();
        $("d-settings").close();
      } else { $("s-msg").textContent = PNNet.errText(r.final); }
    });
  }

  window.addEventListener("load", start);
  return {register: function (key, mod) { games[key] = mod; }, el: el, clear: clear, fmtSecs: fmtSecs, derived: derived,
          contrast: contrast,
          // for checks from outside the page (a headless browser): the board stage and the open table
          inspect: function () { return {stage: S.stage, table: S.table, me: S.me, view: S.view}; }};
})();
