// ParlorNet Play: a PARLORNET/1 client over the web extension's WebSocket (/play/ws; docs/protocol.md, WebSocket).
// One request at a time is in flight per line sent; replies come back in order, each ending with one line
// starting OK, ERR or ASK. request(line) returns a promise of {lines, final}.
"use strict";

var PNNet = (function () {
  function Conn() {
    this.ws = null; this.queue = []; this.waiting = []; this.greeting = null; this.handlers = {};
    this.open = false; this.closedByUs = false;
  }
  Conn.prototype.on = function (name, fn) { (this.handlers[name] = this.handlers[name] || []).push(fn); };
  Conn.prototype.emit = function (name, arg) { (this.handlers[name] || []).forEach(function (f) { f(arg); }); };
  Conn.prototype.connect = function () {
    var self = this;
    return new Promise(function (resolve, reject) {
      var url = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/play/ws";
      var ws;
      try { ws = new WebSocket(url, "parlornet1"); } catch (e) { reject(e); return; }
      self.ws = ws; self.greeting = null; self.waiting = []; self.cur = [];
      var settled = false;
      ws.onmessage = function (ev) {
        var line = String(ev.data);
        if (self.greeting === null) {
          self.greeting = line; self.open = true;
          if (!settled) { settled = true; resolve(line); }
          return;
        }
        var final = /^(OK|ERR|ASK)/.test(line);
        self.cur.push(line);
        if (!final) return;
        var w = self.waiting.shift(), lines = self.cur;
        self.cur = [];
        if (w) w.resolve({lines: lines.slice(0, -1), final: line});
      };
      ws.onclose = function () {
        self.open = false;
        var w = self.waiting; self.waiting = [];
        w.forEach(function (x) { x.resolve({lines: [], final: "ERR CLOSED the connection closed"}); });
        if (!settled) { settled = true; reject(new Error("closed")); }
        if (!self.closedByUs) self.emit("close");
      };
      ws.onerror = function () { /* onclose follows */ };
    });
  };
  // Send one request line (printable ASCII, at most 78 characters, as the protocol allows).
  Conn.prototype.request = function (line) {
    var self = this;
    line = clean(line).slice(0, 78);
    return new Promise(function (resolve) {
      if (!self.open) { resolve({lines: [], final: "ERR CLOSED not connected"}); return; }
      self.waiting.push({resolve: resolve, line: line});
      try { self.ws.send(line); } catch (e) { resolve({lines: [], final: "ERR CLOSED " + e.message}); }
    });
  };
  Conn.prototype.close = function () { this.closedByUs = true; if (this.ws) this.ws.close(); };
  function clean(s) {
    // the protocol is 7-bit ASCII: letters with accents lose them, anything else is dropped
    s = String(s).normalize ? String(s).normalize("NFKD") : String(s);
    return s.replace(/[‘’]/g, "'").replace(/[“”]/g, "\"").replace(/[^\x20-\x7e]/g, "");
  }
  function greetingInfo(g) {
    var out = {name: "", auth: "password", signup: "password", label: "Password"};
    var f = (g || "").split(" ");
    out.name = f[1] || "";
    f.slice(2).forEach(function (kv) {
      var i = kv.indexOf("="), k = kv.slice(0, i), v = kv.slice(i + 1);
      if (k === "AUTH") { out.auth = v; out.label = {none: "", password: "Password"}[v] || v; }
      else if (k === "SIGNUP") out.signup = v;
      else if (k === "LABEL") out.label = v.replace(/_/g, " ");
    });
    return out;
  }
  // "ASK field label [secret] [yn] [cred] [new]" -> {field, label, secret, yn, cred, isNew}; new: a new
  // password is being chosen (a password manager may offer to make one)
  function parseAsk(line) {
    var f = line.split(" ");
    return {field: f[1], label: (f[2] || f[1]).replace(/_/g, " "), secret: f.indexOf("secret") > 2,
            yn: f.indexOf("yn") > 2, cred: f.indexOf("cred") > 2, isNew: f.indexOf("new") > 2};
  }
  function errText(final) {
    var f = final.split(" ");
    var t = f.slice(2).join(" ").replace(/^wait=\d+ /, "");
    return t ? t.charAt(0).toUpperCase() + t.slice(1) : (f[1] || "Error");
  }

  // What Play says it is (CLI; docs/protocol.md, Clients and machines), from the page's own reading of the
  // browser: navigator.userAgentData where the browser has it, else the User-Agent string. -> {prog, mach}:
  // prog "ParlorNet Play in Firefox 132" (just "ParlorNet Play" for a browser not recognized), mach the
  // system ("macOS", "Windows", "iPhone", "iPad", "Android", "Linux", "ChromeOS"), or "" when not known.
  var BRANDS = [["Microsoft Edge", "Edge"], ["Opera", "Opera"], ["Samsung Internet", "Samsung Internet"],
                ["Vivaldi", "Vivaldi"], ["Brave", "Brave"], ["Google Chrome", "Chrome"], ["Chromium", "Chromium"]];
  var UA_BROWSERS = [[/\bEdg(?:e|A|iOS)?\/(\d+)/, "Edge"], [/\b(?:OPR|OPiOS)\/(\d+)/, "Opera"],
                     [/\bSamsungBrowser\/(\d+)/, "Samsung Internet"], [/\b(?:Firefox|FxiOS)\/(\d+)/, "Firefox"],
                     [/\bCriOS\/(\d+)/, "Chrome"], [/\bChrome\/(\d+)/, "Chrome"],
                     [/\bVersion\/(\d+)[\d.]*(?: Mobile\/\S+)? Safari\//, "Safari"]];
  var PLATFORMS = {"macos": "macOS", "windows": "Windows", "android": "Android", "linux": "Linux",
                   "chrome os": "ChromeOS", "chromeos": "ChromeOS", "chromium os": "ChromeOS", "ios": "iPhone"};
  function clientClaim(nav) {
    nav = nav || {};
    var ua = String(nav.userAgent || ""), d = nav.userAgentData, browser = "", ver = "", mach = "", i, m;
    var brands = d && d.brands && d.brands.length ? d.brands : [];
    for (i = 0; i < BRANDS.length && !browser; i++) {
      for (var j = 0; j < brands.length; j++) {
        if (brands[j].brand === BRANDS[i][0]) { browser = BRANDS[i][1]; ver = brands[j].version; break; }
      }
    }
    for (i = 0; i < UA_BROWSERS.length && !browser; i++) {
      m = UA_BROWSERS[i][0].exec(ua);
      if (m) { browser = UA_BROWSERS[i][1]; ver = m[1]; }
    }
    if (d && d.platform) mach = PLATFORMS[String(d.platform).toLowerCase()] || "";
    if (!mach) {
      if (/\biPad\b/.test(ua) || (/\bMacintosh\b/.test(ua) && nav.maxTouchPoints > 1)) mach = "iPad";
      else if (/\biPhone\b/.test(ua)) mach = "iPhone";
      else if (/\biPod\b/.test(ua)) mach = "iPod touch";
      else if (/\bAndroid\b/.test(ua)) mach = "Android";
      else if (/\bCrOS\b/.test(ua)) mach = "ChromeOS";
      else if (/\bWindows\b/.test(ua)) mach = "Windows";
      else if (/\bMacintosh\b|\bMac OS X\b/.test(ua)) mach = "macOS";
      else if (/\bLinux\b|\bX11\b/.test(ua)) mach = "Linux";
    }
    ver = String(ver || "").split(".")[0];
    var prog = "ParlorNet Play" + (browser ? " in " + browser + (/^\d+$/.test(ver) ? " " + ver : "") : "");
    return {prog: prog.slice(0, 40), mach: mach.slice(0, 40)};
  }

  // A turn's program and machine in words, as the classic pages and telnet say them: "with the Outland
  // Gateway on a Macintosh", "with ParlorNet Play in Firefox 132 on macOS"; "" when neither is known. "the" goes
  // before a few products only; a system (macOS, Windows...) takes no article, any other machine "a" or "an".
  var THE_PROGS = /^(Outland Gateway|ParlorNet bot)/;
  var SYSTEMS = /^(macOS|Mac ?OS|OS X|Windows|Linux|Android|ChromeOS|Chrome OS|iOS|iPadOS|Unix|SunOS|OS\/2|BeOS)\b/i;
  function viaText(prog, mach) {
    var out = [];
    if (prog) out.push("with " + (THE_PROGS.test(prog) ? "the " : "") + prog);
    if (mach) out.push("on " + (SYSTEMS.test(mach) ? "" : /^([AEIOU]|i[A-Z])/.test(mach) ? "an " : "a ") + mach);
    return out.join(" ");
  }

  // TRN and FEED replies: "TRN table seq time seat name type/ver", each followed by its TRX lines (MOVE, PROG,
  // MACH; other TRX words are skipped) -> [{tid, seq, at, seat, name, key, move, prog, mach}]
  function parseTurns(lines) {
    var out = [], t = null;
    (lines || []).forEach(function (l) {
      var f = l.split(" ");
      if (f[0] === "TRN" && f.length >= 7) {
        t = {tid: f[1], seq: +f[2], at: f[3], seat: +f[4], name: f[5], key: f[6], move: "", prog: "", mach: ""};
        out.push(t);
      } else if (f[0] === "TRX" && t && f.length >= 3) {
        var v = f.slice(2).join(" ");
        if (f[1] === "MOVE") t.move = v;
        else if (f[1] === "PROG") t.prog = v;
        else if (f[1] === "MACH") t.mach = v;
      }
    });
    return out;
  }

  // "CLIENT seat text" (an event without "EVT table seq", or a line of VUE C or LST clients) -> {seat, text}, text
  // being what the seat's player plays with in words ("with GameMaster 1.0.0 on a Macintosh Plus"); null for any
  // other line and for a CLIENT with no text (a private table's, for a player who may not see it)
  function parseClient(line) {
    var m = /^CLIENT (\d+) (.*\S)/.exec(line || "");
    return m ? {seat: +m[1], text: m[2].trim()} : null;
  }

  // A PFL reply -> {name, pref, games: [{key, n}], progs: [{text, n}], machs: [{text, n}]}
  function parseProfile(lines) {
    var p = {name: "", pref: "", games: [], progs: [], machs: [], score: null};
    (lines || []).forEach(function (l) {
      var f = l.split(" ");
      if (f[0] !== "PFL" || f.length < 3) return;
      if (f[1] === "NAME") p.name = f[2];
      else if (f[1] === "SCORE" && f.length >= 7) p.score = parseScore(f.slice(2));
      else if (f[1] === "PREF") p.pref = f.slice(2).join(" ");
      else if (f[1] === "GAME" && f.length >= 4) p.games.push({key: f[2], n: +f[3] || 0});
      else if ((f[1] === "PROG" || f[1] === "MACH") && f.length >= 4) {
        (f[1] === "PROG" ? p.progs : p.machs).push({n: +f[2] || 0, text: f.slice(3).join(" ")});
      }
    });
    return p;
  }

  // scores (TOP, PFL SCORE): points wins draws losses resigns [rank]; the rank is null when unranked ("-")
  function parseScore(w) {
    var n = w.slice(0, 5).map(Number);
    if (n.length < 5 || n.some(function (x) { return !(x >= 0); })) return null;
    return {points: n[0], wins: n[1], draws: n[2], losses: n[3], resigns: n[4], rank: /^\d+$/.test(w[5] || "") ? +w[5] : null};
  }
  // TOP rank name points wins draws losses resigns -> [{rank, name, points, wins, draws, losses, resigns}]
  function parseTop(lines) {
    var out = [];
    (lines || []).forEach(function (l) {
      var f = l.split(" ");
      if (f[0] !== "TOP" || f.length !== 8 || !/^\d+$/.test(f[1])) return;
      var s = parseScore(f.slice(3));
      if (s) { s.rank = +f[1]; s.name = f[2]; out.push(s); }
    });
    return out;
  }

  return {Conn: Conn, clean: clean, greetingInfo: greetingInfo, parseAsk: parseAsk, errText: errText,
          clientClaim: clientClaim, viaText: viaText, parseTurns: parseTurns, parseProfile: parseProfile,
          parseTop: parseTop, parseClient: parseClient};
})();
