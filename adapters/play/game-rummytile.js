// ParlorNet Play: Rummy Tile Classic (rummytile/1, docs/types/rummytile-1.md). Each set on the table lies on a mat,
// and the player places the mats where they like: the layout is this browser's own, kept for each table (the
// server keeps only the sets' order). Quiet cues say how a set stands, with no words on the table: a red mat for
// a set that is not valid yet, a light edge for one made or changed this turn, dim tiles for the sets locked before
// your first meld. Your rack lies in a flat tray under them, sorted by color or by number. Pick tiles on the rack
// and put them into a set or a new one; pick a tile on the table and move it to another set, a new set or back to
// your rack; pick a set by its mat and join it onto another; split a set before a picked tile. Each step is a MOV;
// Undo, Reset and Done are the protocol's UND, reset and END. The board zooms (never below the size that fits the
// table in rows in table order) and pans. Tiles are ParlorNet's own: flat, the number drawn in its family's color;
// the joker a star (docs/design/rummytile-colors.md).
"use strict";

(function () {
  var TW = 0.74, TH = 1.0, GX = 0.05, STEP = TW + GX, SETGAP = 0.5, LABEL = 0.4, ROWGAP = 0.2, PADX = 0.3;
  var CHIP = 0.56, CHIPTEXT = 0.32, BAR = 0.78, RLABEL = 0.5, TRAYPAD = 0.14;
  // the table's grid: half a tile across, a row down; a set of n tiles takes 2n columns and one more for the gap
  var HS = STEP / 2, RH = LABEL + TH + ROWGAP, GRIP = 0.34, MATPAD = 0.12, MATBOT = 0.1, MAXC = 160, MAXR = 40;
  var ZOOMMAX = 3.5;
  var ACCENT = "#ffd27a", BAD = "#ea6d62", GOOD = "#6cc497", BTN = "#3a434d", PRIMARY = "#e3a83c", PRIMARY_INK = "#1d1505";
  var FAM = "RBKY", FNAME = {R: "red", B: "blue", K: "black", Y: "yellow"};
  var KIND = {r: "run", g: "group", x: "not a set"};
  var REASON = {none: "nothing came from the rack", sets: "a set was not valid", meld: "the first meld was under 30 points",
                joker: "a joker was taken without its tile from the rack"};

  function ease(t) { return t < 0 ? 0 : t > 1 ? 1 : 1 - Math.pow(1 - t, 3); }
  function num(t) { return t === "J" ? 99 : +t.slice(1); }
  function fam(t) { return t === "J" ? 4 : FAM.indexOf(t.charAt(0)); }
  function byColour(a, b) { return fam(a) - fam(b) || num(a) - num(b); }
  function byNumber(a, b) { return num(a) - num(b) || fam(a) - fam(b); }
  function tileName(t) { return t === "J" ? "joker" : FNAME[t.charAt(0)] + " " + t.slice(1); }
  function plural(n, one) { return n + " " + one + (n === 1 ? "" : "s"); }
  function loadSort() { try { return localStorage.getItem("pn-rt-sort") === "number" ? "number" : "colour"; } catch (e) { return "colour"; } }
  function saveSort(v) { try { localStorage.setItem("pn-rt-sort", v); } catch (e) { /* private mode */ } }
  function clamp(x, lo, hi) { return Math.max(lo, Math.min(hi, x)); }

  // ---- the layout kept in this browser: for each table (by the server's address and the table's number), each
  // set's place, with its tiles, so the sets can be found again in the view after a reload ------------------------
  var INDEX = "pn-rt-layouts", KEEP = 40;
  function storeKey(tid) { var host = ""; try { host = location.host; } catch (e) { /* no page */ } return "pn-rt-layout:" + host + ":" + tid; }
  function loadLayout(tid) {
    try { var o = JSON.parse(localStorage.getItem(storeKey(tid)) || "null"); return o && o.v === 1 && Array.isArray(o.sets) ? o : null; } catch (e) { return null; }
  }
  function saveLayout(tid, o) {
    try {
      var k = storeKey(tid), idx = JSON.parse(localStorage.getItem(INDEX) || "[]").filter(function (x) { return x !== k; });
      idx.unshift(k);
      idx.slice(KEEP).forEach(function (x) { localStorage.removeItem(x); });       // the oldest tables' layouts go
      localStorage.setItem(INDEX, JSON.stringify(idx.slice(0, KEEP)));
      localStorage.setItem(k, JSON.stringify(o));
    } catch (e) { /* private mode */ }
  }

  // tiles two sets have in common
  function common(a, b) {
    var m = {}, n = 0;
    a.forEach(function (t) { m[t] = (m[t] || 0) + 1; });
    b.forEach(function (t) { if (m[t] > 0) { m[t]--; n++; } });
    return n;
  }
  // pairs (i, j), rising in both, with the largest total of w(i, j) (each above 0): the old sets and the new kept in order
  function align(n1, n2, w) {
    var D = [], W = [], i, j;
    for (i = 0; i <= n1; i++) { D.push(new Array(n2 + 1).fill(0)); W.push([]); }
    for (i = n1 - 1; i >= 0; i--) for (j = n2 - 1; j >= 0; j--) {
      var x = W[i][j] = w(i, j);
      D[i][j] = Math.max(D[i + 1][j], D[i][j + 1], x > 0 ? x + D[i + 1][j + 1] : 0);
    }
    var out = [];
    i = 0; j = 0;
    while (i < n1 && j < n2) {
      if (W[i][j] > 0 && D[i][j] === W[i][j] + D[i + 1][j + 1]) { out.push([i, j]); i++; j++; }
      else if (D[i][j] === D[i + 1][j]) i++; else j++;
    }
    return out;
  }
  // places on the grid: a list of {id, c, r, n}
  function fits(list, c, r, n, except) {
    if (c < 0 || r < 0) return false;
    for (var i = 0; i < list.length; i++) {
      var q = list[i];
      if (q.id !== except && q.r === r && c < q.c + 2 * q.n + 1 && q.c < c + 2 * n + 1) return false;
    }
    return true;
  }
  function rowsOf(list, except) { var m = 0; list.forEach(function (q) { if (q.id !== except) m = Math.max(m, q.r + 1); }); return m; }
  // the free place nearest (c0, r0); keep: stay inside the board's columns where it can
  function nearest(list, n, c0, r0, NC, keep, except) {
    var best = null, bc = 1e9, rows = Math.max(rowsOf(list, except), r0) + 1, cmax = Math.min(MAXC, Math.max(NC, c0 + 2 * n) + 2 * n);
    for (var r = 0; r <= rows && r <= MAXR; r++) for (var c = 0; c <= cmax; c++) {
      var cost = Math.abs(c - c0) + 4 * Math.abs(r - r0) + (keep && c + 2 * n > NC ? 12 : 0);
      if (cost < bc && fits(list, c, r, n, except)) { bc = cost; best = {c: c, r: r}; }
    }
    return best || {c: 0, r: rowsOf(list, except)};
  }
  // the first free place, row by row, inside the board's columns (a set wider than them: the first free row)
  function reading(list, n, NC) {
    var rows = rowsOf(list), r, c;
    for (r = 0; r < rows; r++) for (c = 0; c + 2 * n <= NC; c++) if (fits(list, c, r, n)) return {c: c, r: r};
    if (2 * n > NC) for (r = 0; r < rows; r++) if (fits(list, 0, r, n)) return {c: 0, r: r};
    return {c: 0, r: rows};
  }
  function cols(W) { return Math.floor((W - 2 * PADX + GX) / HS + 1e-6); }

  // A tile's face, w by h pixels: the tile color and the number in its family's color, large and bold, so the
  // color of the number is the tile's color. A joker: a star in the ink, with no number.
  function paintTile(ctx, w, h, t, pal, dim) {
    var font = PNGfx.FONT;
    ctx.fillStyle = pal.tile; ctx.fillRect(0, 0, w, h);
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    if (t === "J") {
      var cx = w / 2, cy = h * 0.5, R = Math.min(w, h) * 0.4;
      ctx.beginPath();
      for (var i = 0; i < 10; i++) { var a = -Math.PI / 2 + i * Math.PI / 5, r = i % 2 ? R * 0.42 : R; ctx.lineTo(cx + r * Math.cos(a), cy + r * Math.sin(a)); }
      ctx.closePath(); ctx.fillStyle = pal.ink; ctx.fill();
    } else {
      var n = t.slice(1);
      ctx.fillStyle = pal[t.charAt(0).toLowerCase()];
      // One size for every number: the size that keeps the widest, two-digit numbers clear of the edges.
      var px = Math.min(h * 0.56, w * 0.62);
      ctx.font = "800 " + Math.round(px) + "px " + font;
      var tw = 0;
      if (ctx.measureText) ["10", "11", "12", "13"].forEach(function (s) { tw = Math.max(tw, ctx.measureText(s).width); });
      if (tw > w * 0.8) { px *= w * 0.8 / tw; ctx.font = "800 " + Math.round(px) + "px " + font; }
      ctx.fillText(n, w / 2, h * 0.53);
    }
    if (dim) { ctx.globalAlpha = 0.42; ctx.fillStyle = pal.table; ctx.fillRect(0, 0, w, h); ctx.globalAlpha = 1; }
  }

  function create(api) {
    var el = api.el, clear = api.clear;
    var v = {started: false, pool: 0, counts: [], melded: [], gone: [], turn: -1, sets: [], rack: [], tp: null, scores: {}, seq: 0};
    var version = 0, sortMode = loadSort();
    var sel = {rack: [], tile: null, set: 0};     // rack: indices into the shown rack; tile: {k, p}; set: k
    var L = null, layoutKey = "", curW = 0;      // the layout: where everything is, in board units
    var drag = null, down = null, cursor = -1, kbd = false, armed = 0, busy = false, steps = 0, lastTurn = -2;
    var prev = [], anim = {}, animVersion = 0, animating = false;
    // the sets' places: ids[i] names v.sets[i] while it stays on the table; pos[id] its place {c, r} on the grid
    var ids = [], pos = {}, sizes = {}, hints = {}, nid = 1, gone = [], known = {keys: [], ids: []}, tr = null;
    var placeDirty = true, layoutRev = 0, pendingNew = null, hl = 0;
    var zoom = null, ptrs = {}, pinch = null, pan = null, ctl = null;    // zoom: {s, cx, cy} once the player zooms or pans
    void anim;

    (function restore() {
      var o = loadLayout(api.tid);
      if (!o) return;
      o.sets.forEach(function (q) {
        var id = +q[0], tiles = String(q[3] || "").split(" ").filter(Boolean);
        if (!(id > 0) || !tiles.length) return;
        pos[id] = {c: Math.max(0, +q[1] || 0), r: Math.max(0, +q[2] || 0)}; sizes[id] = tiles.length;
        known.keys.push(tiles); known.ids.push(id);
      });
      nid = Math.max(+o.next || 1, Math.max.apply(null, known.ids.concat([0])) + 1);
    })();
    function save() {
      saveLayout(api.tid, {v: 1, next: nid, sets: v.sets.map(function (s, i) {
        var p = pos[ids[i]] || {c: 0, r: 0};
        return [ids[i], p.c, p.r, s.tiles.join(" ")];
      })});
    }

    function me() { return api.seat(); }
    function mine() { return api.myTurn(); }
    function pal() { return api.rtPalette(); }
    function board() { return document.getElementById("board"); }
    function shown() { return v.rack.slice().sort(sortMode === "number" ? byNumber : byColour); }
    function setOf(k) { return v.sets[k - 1]; }
    function locked(s) { return me() >= 0 && !v.melded[me()] && !s.changed; }
    function staged() { return (v.tp && v.tp.n > 0) || v.sets.some(function (s) { return s.changed; }); }

    // ---- which set is which: from the events of each step when they agree with the view, else from the tiles -----
    // The new view's sets against the last: the same tiles in order, then sets put back as they were (an undo, a
    // reset), then the most tiles in common in order; the rest are new, placed beside the set most of their tiles
    // came from (a split, a tile moved to a new set), or where that set was, or where the player put them.
    function match(oldT, oldIds, newT) {
      var out = new Array(newT.length), used = {}, keyOf = function (t) { return t.join(" "); };
      align(oldT.length, newT.length, function (a, b) { return keyOf(oldT[a]) === keyOf(newT[b]) ? 1000 + oldT[a].length : 0; })
        .forEach(function (p) { out[p[1]] = oldIds[p[0]]; used[p[0]] = 1; });
      newT.forEach(function (t, j) {
        if (out[j] != null) return;
        for (var g = gone.length - 1; g >= 0; g--) {
          if (gone[g].key === keyOf(t) && out.indexOf(gone[g].id) < 0 && oldIds.indexOf(gone[g].id) < 0) { out[j] = gone[g].id; gone.splice(g, 1); return; }
        }
      });
      var ro = [], rn = [];
      oldT.forEach(function (t, a) { if (!used[a]) ro.push(a); });
      newT.forEach(function (t, b) { if (out[b] == null) rn.push(b); });
      align(ro.length, rn.length, function (a, b) { return common(oldT[ro[a]], newT[rn[b]]); }).forEach(function (p) {
        var id = oldIds[ro[p[0]]];
        if (out.indexOf(id) < 0) { out[rn[p[1]]] = id; used[ro[p[0]]] = 1; }
      });
      newT.forEach(function (t, j) {
        if (out[j] != null) return;
        var id = nid++, best = -1, bn = 0;
        oldT.forEach(function (o, a) { var x = common(o, t); if (x > bn) { bn = x; best = a; } });
        if (takePending()) hints[id] = {at: takePending(true)};
        else if (best >= 0) hints[id] = out.indexOf(oldIds[best]) >= 0 ? {beside: oldIds[best]} : pos[oldIds[best]] ? {at: pos[oldIds[best]]} : null;
        out[j] = id;
      });
      oldT.forEach(function (t, a) { if (out.indexOf(oldIds[a]) < 0) gone.push({id: oldIds[a], key: keyOf(t)}); });
      if (gone.length > 80) gone.splice(0, gone.length - 80);
      return out;
    }
    // the place the player chose for a set they are making (the new set's place, or where tiles were dropped)
    function takePending(take) {
      if (!pendingNew || Date.now() - pendingNew.t > 20000) { pendingNew = null; return null; }
      var p = {c: pendingNew.c, r: pendingNew.r};
      if (take) pendingNew = null;
      return p;
    }
    // the events of each step change the table exactly: DEL k, SET k (one past the last: a new set)
    function track(text, seq) {
      if (!tr || seq == null || seq <= tr.seq) return;
      tr.seq = seq;
      if (tr.bad) return;
      var f = text.split(" "), k, id;
      switch (f[0]) {
        case "PUT": case "MOVE": case "BACK": case "MERGE": case "SPLIT":
          tr.step = {seat: +f[1], src: f[0] === "PUT" ? null : tr.ids[+f[2] - 1]};
          return;
        case "DEL":
          k = +f[1];
          if (!(k >= 1 && k <= tr.ids.length)) { tr.bad = true; return; }
          gone.push({id: tr.ids[k - 1], key: tr.sets[k - 1].join(" ")});
          tr.ids.splice(k - 1, 1); tr.sets.splice(k - 1, 1);
          return;
        case "SET":
          k = +f[1];
          var tiles = f.slice(3).filter(Boolean), st = tr.step || {};
          if (k >= 1 && k <= tr.ids.length) { tr.sets[k - 1] = tiles; return; }
          if (k !== tr.ids.length + 1) { tr.bad = true; return; }
          id = nid++;
          if (st.seat === me() && takePending()) hints[id] = {at: takePending(true)};
          else if (st.src != null) hints[id] = tr.ids.indexOf(st.src) >= 0 ? {beside: st.src} : pos[st.src] ? {at: pos[st.src]} : null;
          tr.ids.push(id); tr.sets.push(tiles);
          return;
        case "RESET": case "UNDO": case "UNDONE": tr.bad = true; return;
      }
    }
    // every set gets a place: those that have one keep it (a set that grew first, so a neighbour moves aside for it),
    // new ones go where their hint says or in the first free place; nothing overlaps
    function ensurePlaced(NC) {
      if (!placeDirty) return;
      placeDirty = false;
      var list = [], grown = [], rest = [], fresh = [];
      v.sets.forEach(function (s, i) {
        var q = {id: ids[i], n: s.tiles.length};
        if (!pos[q.id]) fresh.push(q); else if ((sizes[q.id] || 0) < q.n) grown.push(q); else rest.push(q);
      });
      function add(q, p) { pos[q.id] = {c: p.c, r: p.r}; sizes[q.id] = q.n; list.push({id: q.id, c: p.c, r: p.r, n: q.n}); delete hints[q.id]; }
      grown.concat(rest).forEach(function (q) {
        var p = pos[q.id];
        add(q, fits(list, p.c, p.r, q.n) ? p : nearest(list, q.n, p.c, p.r, NC, false));
      });
      fresh.forEach(function (q) {
        var h = hints[q.id], src = null;
        if (h && h.beside) src = list.filter(function (x) { return x.id === h.beside; })[0];
        if (h && h.at) add(q, fits(list, h.at.c, h.at.r, q.n) ? h.at : nearest(list, q.n, h.at.c, h.at.r, NC, true));
        else if (src) add(q, nearest(list, q.n, src.c + 2 * src.n + 1, src.r, NC, true));
        else add(q, reading(list, q.n, NC));
      });
      save();
    }

    // ---- the view and the events -----------------------------------------------------------------------------
    function view(lines) {
      var n = {started: false, pool: 0, counts: [], melded: [], gone: [], turn: -1, sets: [], rack: [], tp: null, scores: {}, seq: 0};
      lines.forEach(function (line) {
        var f = line.split(" ");
        switch (f[0]) {
          case "V": n.seq = +f[2] || 0; break;
          case "P": n.pool = +f[1]; n.started = true; break;
          case "C": n.counts = f.slice(1).map(Number); break;
          case "I": n.melded = f.slice(1).map(function (x) { return x === "1"; }); break;
          case "G": n.gone = f.slice(1).map(function (x) { return x === "1"; }); break;
          case "W": n.turn = f[1] === "-" ? -1 : +f[1]; break;
          case "ST": n.sets.push({k: +f[1], kind: f[2].charAt(0), changed: f[2].indexOf("*") >= 0, tiles: f.slice(3)}); break;
          case "H": n.rack = n.rack.concat(f.slice(1).filter(Boolean)); break;
          case "TP": n.tp = {n: +f[1], pts: f[2] === "-" ? null : +f[2], code: f[3]}; break;
          case "T": if (f.length === 3) n.scores[+f[1]] = +f[2]; break;
        }
      });
      var rackSame = n.rack.join(" ") === v.rack.join(" "), tableSame = JSON.stringify(n.sets) === JSON.stringify(v.sets);
      if (!rackSame) sel.rack = [];
      if (!tableSame) { sel.tile = null; sel.set = 0; }
      if (n.turn !== lastTurn) { steps = 0; armed = 0; lastTurn = n.turn; }
      if (!rackSame || !tableSame) version++;
      // which set is which
      var keys = n.sets.map(function (s) { return s.tiles; }), keyStr = function (t) { return t.join(" "); };
      var same = tr && !tr.bad && tr.sets.length === keys.length && tr.sets.every(function (t, i) { return keyStr(t) === keyStr(keys[i]); });
      var nids = same ? tr.ids.slice() : match(known.keys, known.ids, keys);
      var changedIds = nids.length !== ids.length || nids.some(function (id, i) { return id !== ids[i] || keyStr(keys[i]) !== keyStr((v.sets[i] || {tiles: []}).tiles); });
      gone = gone.filter(function (g) { return nids.indexOf(g.id) < 0; });
      ids = nids;
      known = {keys: keys.map(function (t) { return t.slice(); }), ids: nids.slice()};
      tr = {sets: keys.map(function (t) { return t.slice(); }), ids: nids.slice(), seq: n.seq, bad: false, step: null};
      v = n;
      if (changedIds || !tableSame) { placeDirty = true; layoutRev++; }
      panel();
      api.redraw();
    }
    function event(text, quiet, seq) {
      var f = text.split(" ");
      if (!quiet) track(text, seq);
      if (quiet) return;
      if (/^(PUT|MOVE|MERGE|SPLIT)$/.test(f[0]) && +f[1] !== me()) api.sound("tile");
      if (f[0] === "TURN") { steps = 0; armed = 0; }
    }
    function describe(text) {
      var f = text.split(" "), nm = function (s) { return api.name(+s); };
      var into = function (t) { return t === "n" ? "a new set" : "set " + t; };
      var tiles = function (s) { return (s.match(/[RBKY](?:1[0-3]|[1-9])|J/g) || []).join(" "); };
      switch (f[0]) {
        case "DEAL": return "The deal: " + f[1] + " tiles each, " + f[2] + " in the pool";
        case "PUT": return nm(f[1]) + " puts " + tiles(f[2]) + " into " + into(f[3]);
        case "MOVE": return nm(f[1]) + " moves " + f[3] + " from set " + f[2] + " to " + into(f[4]);
        case "BACK": return nm(f[1]) + " takes " + f[3] + " back from set " + f[2];
        case "MERGE": return nm(f[1]) + " joins set " + f[2] + " onto set " + f[3];
        case "SPLIT": return nm(f[1]) + " splits set " + f[2] + " before its tile " + f[3];
        case "RESET": return nm(f[1]) + " starts the turn over";
        case "UNDO": return nm(f[1]) + " takes a step back";
        case "MELD": return nm(f[1]) + " makes the first meld: " + f[2] + " points";
        case "DONE": return nm(f[1]) + " ends the turn: " + plural(+f[2], "tile") + " played";
        case "UNDONE": return nm(f[1]) + "'s turn does not count (" + (REASON[f[2]] || f[2]) + ") and is taken back";
        case "DRAW": return nm(f[1]) + " draws a tile (" + f[2] + " left in the pool)";
        case "PASS": return nm(f[1]) + " passes: the pool is empty";
        case "OUT": return nm(f[1]) + " has played every tile";
        case "END":
          var sc = f[1] === "win" ? f.slice(3) : f.slice(2);
          var list = sc.map(function (x, i) { return nm(i) + " " + x; }).join(", ");
          if (f[1] === "win") return "Game over: " + nm(f[2]) + " wins (" + list + ")";
          if (f[1] === "draw") return "Game over: a draw (" + list + ")";
          return null;
        case "SET": case "DEL": case "TURN": return null;
      }
      return null;
    }

    function chipTexts() {
      var out = [{text: "Pool " + v.pool, seat: -1}], over = api.over();
      v.counts.forEach(function (cnt, i) {
        out.push({seat: i, text: (v.turn === i && !over ? "▶ " : "") + api.name(i) + " " + cnt + (v.melded[i] ? " ✓" : "") + (v.gone[i] ? " (left)" : "")});
      });
      return out;
    }
    // ---- the layout, in board units ----------------------------------------------------------------------------------
    // flow: the sets in rows in table order, wrapping (only measured: the board's size at the smallest zoom);
    // otherwise each set at its own place on the grid. The head, the bar and the rack are W wide either way.
    function build(W, flow) {
      var o = {W: W, BW: W, sets: [], tiles: [], rack: [], btns: [], newZone: null, chips: []};
      var maxX = W - PADX, x = PADX, y = 0.12;
      // the head: the pool and each player's rack, as chips that wrap
      chipTexts().forEach(function (c) {
        var w = c.text.length * CHIPTEXT * 0.6 + 0.5;
        if (x > PADX && x + w > maxX) { x = PADX; y += CHIP + 0.1; }
        o.chips.push({text: c.text, seat: c.seat, x: x + w / 2, y: y + CHIP / 2, w: w});
        x += w + 0.14;
      });
      o.head = y + CHIP + 0.14;
      // at the end: the result, in a band of its own under the head
      if (api.over()) {
        o.result = {y: o.head + 0.05, h: 0.95 + 0.5 * v.counts.length};
        o.head += o.result.h + 0.2;
      }
      var top = o.head + LABEL, nw = TW * 2 + GX;
      o.top = top;
      if (flow) {
        x = PADX; y = top;
        v.sets.forEach(function (s) {
          var n = s.tiles.length, sw = n * STEP - GX, sx = x;
          if (x > PADX && x + Math.min(sw, maxX - PADX) > maxX) { x = PADX; y += RH; sx = x; }
          s.tiles.forEach(function () { if (sx + TW > maxX + 0.01 && sx > PADX) { y += RH; sx = PADX; } sx += STEP; });
          x = sx - GX + SETGAP;
        });
        if (x > PADX && x + nw > maxX) { x = PADX; y += RH; }
      } else {
        var NC = cols(W), list = [], maxR = 0;
        v.sets.forEach(function (s, i) {
          var id = ids[i], p = pos[id] || {c: 0, r: 0}, n = s.tiles.length, x0 = PADX + p.c * HS, ty = top + p.r * RH;
          var set = {k: s.k, id: id, s: s, c: p.c, r: p.r, n: n, x0: x0, x1: x0 + n * STEP - GX, y: ty, tiles: []};
          s.tiles.forEach(function (t, j) {
            var tile = {face: t, k: s.k, p: j + 1, x: x0 + j * STEP + TW / 2, y: ty + TH / 2, where: "t"};
            set.tiles.push(tile); o.tiles.push(tile);
          });
          o.sets.push(set);
          list.push({id: id, c: p.c, r: p.r, n: n});
          maxR = Math.max(maxR, p.r);
          o.BW = Math.max(o.BW, set.x1 + MATPAD + PADX);
        });
        // the place for a new set: the first free place for two tiles
        var z = reading(list, 2, NC);
        o.zoneCell = z;
        x = PADX + z.c * HS; maxR = Math.max(maxR, z.r);
        o.newZone = {x: x + nw / 2, y: top + z.r * RH + TH / 2, w: nw, h: TH};
        y = top + maxR * RH;
        o.tableEnd = y + TH + MATBOT;
      }
      y += TH + ROWGAP + 0.1;
      // the bar: the turn's state, Undo, Reset, Done (on a narrow board the words get a line of their own)
      o.twoLine = W < 13;
      o.wordsY = y + 0.3;
      o.barY = o.twoLine ? y + 0.6 + BAR / 2 : y + BAR / 2;
      y += o.twoLine ? BAR + 0.6 : BAR;
      // the rack
      if (me() >= 0) {
        var r = shown(), per = Math.max(1, Math.floor((W - 2 * PADX - 2 * TRAYPAD + GX) / STEP)), rows = Math.max(1, Math.ceil(r.length / per));
        o.rlabelY = y + RLABEL / 2; y += RLABEL;
        var trayH = rows * (TH + 0.12) - 0.12 + 2 * TRAYPAD;
        o.tray = {x: W / 2, y: y + trayH / 2, w: W - 2 * PADX + 0.1, h: trayH};
        var cl = Math.min(per, r.length || 1), left = W / 2 - (cl * STEP - GX) / 2;
        if (!flow) r.forEach(function (t, i) {
          var row = Math.floor(i / per), col = i % per;
          var tile = {face: t, i: i, x: left + col * STEP + TW / 2, y: y + TRAYPAD + row * (TH + 0.12) + TH / 2, where: "r"};
          o.rack.push(tile); o.tiles.push(tile);
        });
        y += trayH + 0.2;
      }
      o.H = y + 0.05;
      return o;
    }
    function layout() {
      var cv = board(), narrow = window.matchMedia && matchMedia("(max-width: 900px), (max-aspect-ratio: 4/5)").matches;
      var cw = narrow ? window.innerWidth : (cv && cv.clientWidth) || 900, ch = narrow ? window.innerHeight * 0.72 : (cv && cv.clientHeight) || 700;
      var key = [version, sortMode, cw, ch, me(), v.sets.length, v.rack.length, api.over(), layoutRev].join(":") + chipTexts().map(function (c) { return c.text; }).join("|");
      if (L && key === layoutKey) return L;
      layoutKey = key;
      // the width that shows the tiles largest with the sets in rows: today's size, the smallest zoom
      var best = null, cur = null, minW = narrow ? 8.6 : 10;
      for (var W = minW; W <= 34; W += 0.5) {
        var f = build(W, true), sc = Math.min(cw / (W + 0.3), ch / (f.H + 0.3));
        if (!best || sc > best.sc) best = {o: f, sc: sc};
        if (Math.abs(W - curW) < 0.01) cur = {o: f, sc: sc};
      }
      var pick = cur && cur.sc > best.sc * 0.94 ? cur : best;      // keep the width unless another shows much more
      curW = pick.o.W;
      ensurePlaced(cols(curW));
      var o = build(curW, false);
      o.Hf = pick.o.H;
      flip(o);
      L = o;
      return L;
    }
    function buttons(o) {
      var W = o.W, y = o.barY, h = 0.56, x = W - PADX, my = me() >= 0 && !api.over() && v.started;
      o.btns = [];
      function add(id, label, w, kind, on) { x -= w; o.btns.push({id: id, label: label, x: x + w / 2, y: y, w: w, h: h, kind: kind, on: on}); x -= 0.12; }
      if (my) {
        var t = mine(), ready = v.tp && v.tp.code === "ok";
        add("done", ready ? "Done" : staged() ? "Done…" : v.pool ? "Draw" : "Pass", 1.5, ready ? "primary" : staged() ? "warn" : "plain", t);
        add("reset", "Reset", 1.3, "plain", t && staged());
        add("undo", "Undo", 1.25, "plain", t && (steps > 0 || staged()));
      }
      o.barText = o.twoLine ? {x: PADX, y: o.wordsY, maxW: W - 2 * PADX} : {x: PADX, y: y, maxW: x - PADX};
      if (my && o.rlabelY != null) {
        x = W - PADX; y = o.rlabelY; h = 0.42;
        add("sort-number", "by number", 1.55, sortMode === "number" ? "on" : "plain", true);
        add("sort-colour", "by color", 1.5, sortMode === "colour" ? "on" : "plain", true);
      }
      // with a tile picked on the table, after the first of its set: a split mark before it
      var ps = sel.tile && o.sets.filter(function (q) { return q.k === sel.tile.k; })[0];
      if (ps && sel.tile.p > 1 && mine() && ps.tiles[sel.tile.p - 1]) {
        var at = ps.tiles[sel.tile.p - 1];
        o.btns.unshift({id: "split", label: "split", x: at.x - STEP / 2, y: at.y + TH / 2 + 0.2, w: 0.9, h: 0.34, kind: "plain", on: true});
      }
    }
    // Tiles that moved since the last layout glide from where they were (the same face, the nearest place);
    // a tile that was nowhere drops in.
    function flip(o) {
      var now = performance.now(), changed = animVersion !== version;
      animVersion = version;
      if (changed && api.motion() && prev.length) {
        var pool = prev.slice();
        o.tiles.forEach(function (t) {
          var bi = -1, bd = 1e9;
          pool.forEach(function (q, i) {
            if (!q || q.face !== t.face) return;
            var d = Math.hypot(q.x - t.x, q.y - t.y) + (q.where === t.where && q.k === t.k ? 0 : 0.01);
            if (d < bd) { bd = d; bi = i; }
          });
          if (bi >= 0) {
            var q = pool[bi]; pool[bi] = null;
            if (bd > 0.05) t.anim = {fx: q.x, fy: q.y, fz: 0.25, t0: now, dur: 300};
          } else t.anim = {fx: t.x, fy: t.y - 0.6, fz: 1.6, t0: now, dur: 380};
        });
        if (o.tiles.some(function (t) { return t.anim; })) api.redraw();     // the stage then keeps drawing (animating)
      }
      prev = o.tiles.map(function (t) { return {face: t.face, x: t.x, y: t.y, where: t.where, k: t.k}; });
    }

    // ---- the zoom: s pixels a board unit, the window's middle (cx, cy) -------------------------------------------------
    // The smallest zoom is the size the board had with its sets in rows (minS); by default the zoom shows the whole
    // board when it fits at that size or larger (fitS), and otherwise stays at minS from the board's left and bottom
    // edges (your rack and the buttons), the rest a pan away.
    function cam(o) {
      o = o || layout();
      var cv = board(), cw = (cv && cv.clientWidth) || 900, ch = (cv && cv.clientHeight) || 700;
      var minS = Math.min(cw / (o.W + 0.3), ch / (o.Hf + 0.3)), fitS = Math.min(cw / (o.BW + 0.3), ch / (o.H + 0.3));
      var home = Math.max(minS, fitS), max = home * ZOOMMAX, s, cx, cy;
      if (zoom) { s = clamp(zoom.s, home, max); cx = zoom.cx; cy = zoom.cy; }
      else { s = home; cx = -0.15 + cw / s / 2; cy = o.H + 0.15 - ch / s / 2; }
      var w = cw / s, h = ch / s, c = clampC(o, w, h, cx, cy);
      return {s: s, cx: c.x, cy: c.y, cw: cw, ch: ch, minS: minS, fitS: fitS, home: home, max: max,
              win: {x: c.x - w / 2, y: c.y - h / 2, w: w, h: h}, all: w >= o.BW + 0.3 - 1e-6 && h >= o.H + 0.3 - 1e-6};
    }
    function clampC(o, w, h, cx, cy) {
      var P = 0.15;
      return {x: w >= o.BW + 2 * P ? o.BW / 2 : clamp(cx, w / 2 - P, o.BW + P - w / 2),
              y: h >= o.H + 2 * P ? o.H / 2 : clamp(cy, h / 2 - P, o.H + P - h / 2)};
    }
    function setZoom(z, s, cx, cy) {
      s = clamp(s, z.home, z.max);
      if (s <= z.home * 1.001 && z.fitS >= z.minS) zoom = null;       // back at the default: it follows the board again
      else { var c = clampC(L, z.cw / s, z.ch / s, cx, cy); zoom = {s: s, cx: c.x, cy: c.y}; }
      api.redraw();
    }
    // zoom by f about a point of the canvas (CSS pixels; the middle when none)
    function zoomBy(f, fx, fy) {
      var z = cam();
      if (fx == null) { fx = z.cw / 2; fy = z.ch / 2; }
      var s = clamp(z.s * f, z.home, z.max), bx = z.cx + (fx - z.cw / 2) / z.s, by = z.cy + (fy - z.ch / 2) / z.s;
      setZoom(z, s, bx - (fx - z.cw / 2) / s, by - (fy - z.ch / 2) / s);
      if (kbd || fx === z.cw / 2) api.announce("Zoom " + Math.round(cam().s / z.minS * 100) + "%.");
    }
    function fit() { zoom = null; api.redraw(); api.announce("The whole table."); }
    function panBy(dx, dy) {
      var z = cam();
      if (z.all) return false;
      setZoom(z, z.s, z.cx + dx / z.s, z.cy + dy / z.s);
      return true;
    }
    // keeps a place (board units) in the window, panning as little as it takes
    function reveal(x, y, mx, my) {
      var z = cam(), w = z.win;
      if (z.all) return;
      mx = mx == null ? 0.6 : mx; my = my == null ? 0.7 : my;
      var cx = z.cx, cy = z.cy;
      if (x < w.x + mx) cx -= w.x + mx - x; else if (x > w.x + w.w - mx) cx += x - (w.x + w.w - mx);
      if (y < w.y + my) cy -= w.y + my - y; else if (y > w.y + w.h - my) cy += y - (w.y + w.h - my);
      if (cx !== z.cx || cy !== z.cy) { var c = clampC(L, w.w, w.h, cx, cy); zoom = {s: z.s, cx: c.x, cy: c.y}; api.redraw(); }
    }
    function wheel(p, e) {
      var cv = board(), rect = cv.getBoundingClientRect(), k = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1;
      var dx = (e.deltaX || 0) * k, dy = (e.deltaY || 0) * k;
      if (e.ctrlKey || e.metaKey) { zoomBy(Math.exp(-clamp(dy, -60, 60) * 0.006), e.clientX - rect.left, e.clientY - rect.top); return true; }
      if (e.shiftKey && !dx) { dx = dy; dy = 0; }
      return panBy(dx, dy);
    }
    function canvasXY(pt) { var r = board().getBoundingClientRect(); return {x: pt.x - r.left, y: pt.y - r.top}; }

    // ---- what is under the pointer ------------------------------------------------------------------------------
    function inBox(p, cx, cy, w, h) { return Math.abs(p.x - cx) <= w / 2 && Math.abs(p.y - cy) <= h / 2; }
    function onMat(p, s) { return p.x >= s.x0 - MATPAD && p.x <= s.x1 + MATPAD && p.y >= s.y - GRIP && p.y <= s.y + TH + MATBOT; }
    function hit(p) {
      var o = layout(), i;
      buttons(o);
      for (i = 0; i < o.btns.length; i++) if (inBox(p, o.btns[i].x, o.btns[i].y, o.btns[i].w, o.btns[i].h + 0.1)) return {type: "btn", id: o.btns[i].id};
      for (i = 0; i < o.rack.length; i++) if (inBox(p, o.rack[i].x, o.rack[i].y, STEP, TH + 0.12)) return {type: "rack", i: i};
      if (o.tray && inBox(p, o.tray.x, o.tray.y, o.tray.w, o.tray.h + RLABEL)) return {type: "tray"};
      for (i = 0; i < o.tiles.length; i++) {
        var t = o.tiles[i];
        if (t.where === "t" && inBox(p, t.x, t.y, STEP, TH + 0.1)) return {type: "tile", k: t.k, p: t.p};
      }
      for (i = 0; i < o.sets.length; i++) if (onMat(p, o.sets[i])) return {type: "set", k: o.sets[i].k};
      if (o.newZone && inBox(p, o.newZone.x, o.newZone.y, o.newZone.w + 0.1, o.newZone.h + 0.1)) return {type: "new"};
      if (p.y >= o.top - GRIP && p.y <= o.tableEnd && p.x >= 0 && p.x <= o.BW + 1) return {type: "table", x: p.x, y: p.y};
      return null;
    }
    function setAt(o, k) { return o.sets.filter(function (q) { return q.k === k; })[0]; }
    // the grid place for tiles dropped at p: a new set's first tile there
    function cellAt(o, p) { return {c: Math.max(0, Math.round((p.x - PADX - TW / 2) / HS)), r: clamp(Math.floor((p.y - o.top + GRIP + ROWGAP / 2) / RH), 0, MAXR)}; }

    // ---- steps -----------------------------------------------------------------------------------------------------
    function clearSel() { sel.rack = []; sel.tile = null; sel.set = 0; }
    function picked() { var r = shown(); return sel.rack.map(function (i) { return r[i]; }).filter(Boolean); }
    function send(tok, what, at) {
      if (!mine()) { api.toast(api.over() ? "The game is over." : "Wait for your turn."); return Promise.resolve(); }
      if (busy) return Promise.resolve();
      busy = true;
      if (at) pendingNew = {c: at.c, r: at.r, t: Date.now()};
      return api.mov(tok).then(function (final) {
        busy = false;
        if (final.indexOf("OK") === 0) {
          steps++; armed = 0;
          clearSel();
          api.sound("tile");
          if (what) api.announce(what + (final === "OK done" ? ". Ready: Done plays your turn." : "."));
        } else pendingNew = null;
        panel(); api.redraw();
      }, function () { busy = false; pendingNew = null; });
    }
    // at: where a new set goes (the place for a new set, unless the tiles were dropped elsewhere on the table)
    function newAt(at) { return at || (layout().zoneCell); }
    function put(target, at) {
      var t = picked();
      if (!t.length) { api.toast("Pick tiles on your rack first."); return; }
      if (t.length > 13) { api.toast("A set holds at most 13 tiles."); return; }
      send(t.join("") + "-" + target, "Put " + t.join(" ") + " into " + (target === "n" ? "a new set" : "set " + target), target === "n" ? newAt(at) : null);
    }
    function moveTile(target, at) {
      var s = sel.tile;
      if (!s) return;
      send(s.k + "." + s.p + "-" + target, "Moved " + setOf(s.k).tiles[s.p - 1] + (target === "r" ? " back to your rack" : " to " + (target === "n" ? "a new set" : "set " + target)),
           target === "n" ? newAt(at) : null);
    }
    function split() {
      var s = sel.tile;
      if (!s) { api.toast("Pick the tile to split before."); return; }
      if (s.p < 2) { api.toast("Pick a tile after the first: the set is split before it."); return; }
      send(s.k + "/" + s.p, "Split set " + s.k + " before " + setOf(s.k).tiles[s.p - 1]);
    }
    function merge(a, b) { send(a + "-" + b, "Joined set " + a + " onto set " + b); }
    function undo() {
      if (!mine()) { api.toast("Wait for your turn."); return; }
      api.und().then(function (final) { if (final.indexOf("OK") === 0) { steps = Math.max(0, steps - 1); clearSel(); api.announce("Took a step back."); } panel(); });
    }
    function reset() { send("reset", "Started the turn over"); }
    function done() {
      if (!mine()) { api.toast("Wait for your turn."); return; }
      var ready = v.tp && v.tp.code === "ok";
      if (!ready && staged() && Date.now() - armed > 5000) {
        armed = Date.now();
        api.toast("This turn does not count yet: " + turnText() + " Press Done again to take it all back and " + (v.pool ? "draw a tile." : "pass."));
        return;
      }
      armed = 0; busy = true;
      api.end().then(function () { busy = false; clearSel(); panel(); }, function () { busy = false; });
    }
    function setSort(m) { sortMode = m; saveSort(m); sel.rack = []; version++; panel(); api.redraw(); api.announce("Rack sorted by " + m + "."); }
    function toggleRack(i) {
      var at = sel.rack.indexOf(i), face = shown()[i];
      sel.tile = null; sel.set = 0;
      if (at >= 0) sel.rack.splice(at, 1); else sel.rack.push(i);
      api.announce((at >= 0 ? "Put down " : "Picked ") + tileName(face) + ". " + plural(sel.rack.length, "tile") + " picked.");
    }
    function button(id) {
      if (id === "done") done(); else if (id === "undo") undo(); else if (id === "reset") reset(); else if (id === "split") split();
      else if (id === "sort-colour") setSort("colour"); else if (id === "sort-number") setSort("number");
    }
    // a click (or Enter on the keyboard cursor) on what h names
    function click(h) {
      if (!h || h.type === "table") { clearSel(); return; }
      switch (h.type) {
        case "btn": button(h.id); return;
        case "rack": if (sel.tile) moveTile("r"); else toggleRack(h.i); return;
        case "tray": if (sel.tile) moveTile("r"); else clearSel(); return;
        case "new":
          if (sel.rack.length) put("n"); else if (sel.tile) moveTile("n");
          else api.toast("Pick tiles on your rack, or a tile on the table, then the new set.");
          return;
        case "tile":
          if (sel.rack.length) { put(h.k); return; }
          if (sel.tile) {
            if (sel.tile.k === h.k && sel.tile.p === h.p) { sel.tile = null; api.announce("Put down."); }
            else if (sel.tile.k !== h.k) moveTile(h.k);
            else { sel.tile = {k: h.k, p: h.p}; api.announce("Picked " + tileName(setOf(h.k).tiles[h.p - 1]) + " in set " + h.k + "."); }
            return;
          }
          if (sel.set) { if (sel.set === h.k) sel.set = 0; else merge(sel.set, h.k); return; }
          sel.tile = {k: h.k, p: h.p};
          api.announce("Picked " + tileName(setOf(h.k).tiles[h.p - 1]) + " in set " + h.k + ": choose a set, a new set or your rack" + (h.p > 1 ? ", or split before it" : "") + ".");
          return;
        case "set":
          if (sel.rack.length) { put(h.k); return; }
          if (sel.tile) { if (sel.tile.k !== h.k) moveTile(h.k); else sel.tile = null; return; }
          if (sel.set) { if (sel.set === h.k) sel.set = 0; else merge(sel.set, h.k); return; }
          sel.set = h.k; api.announce("Picked set " + h.k + ": choose the set to join it onto, or move it with Shift and the arrow keys.");
          return;
      }
    }
    // a drag of tiles from src let go over h: into a set, a new set (the place for one, or anywhere free on the
    // table: the new set goes there), or back to the rack
    function drop(src, h) {
      if (!h) return;
      var target = h.type === "tile" || h.type === "set" ? h.k : h.type === "new" || h.type === "table" ? "n" : h.type === "rack" || h.type === "tray" ? "r" : null;
      var at = h.type === "table" ? cellAt(layout(), h) : null;
      if (target == null) return;
      if (src.type === "rack") {
        if (sel.rack.indexOf(src.i) < 0) { sel.rack = [src.i]; sel.tile = null; sel.set = 0; }
        if (target !== "r") put(target, at);
      } else if (src.type === "tile") {
        if (target === src.k) return;
        sel.tile = {k: src.k, p: src.p}; sel.rack = []; sel.set = 0;
        moveTile(target, at);
      }
    }
    // ---- moving a set on the table: the player's own layout; no step is sent ---------------------------------------------
    // the place a dragged set would land: snapped to the grid, at most a row below the others and a set past them
    function dragCell(d) {
      var o = layout(), q = setAt(o, d.src.k);
      if (!q) return null;
      var list = o.sets.filter(function (x) { return x !== q; }).map(function (x) { return {id: x.id, c: x.c, r: x.r, n: x.n}; });
      var right = list.reduce(function (m, x) { return Math.max(m, x.c + 2 * x.n + 1); }, 0);
      var c = Math.round((d.x - d.ox - PADX) / HS), r = Math.round((d.y - d.oy - o.top) / RH);
      c = clamp(c, 0, Math.min(MAXC, Math.max(cols(o.W) - 2 * q.n, right, q.c)));
      r = clamp(r, 0, Math.min(MAXR, Math.max(rowsOf(list), q.r)));
      var p = fits(list, c, r, q.n) ? {c: c, r: r} : nearest(list, q.n, c, r, cols(o.W), false);
      return {q: q, c: p.c, r: p.r};
    }
    function moveSet(q, p, say) {
      if (!q || (p.c === q.c && p.r === q.r)) return false;
      pos[q.id] = {c: p.c, r: p.r};
      layoutRev++; save(); api.redraw();
      if (say) api.announce("Set " + q.k + " moved: row " + (p.r + 1) + ", " + (p.c / 2 + 1) + " tiles from the left.");
      return true;
    }
    // Shift and an arrow key: the set a tile or a step further that way, past any set in the way
    function nudgeSet(k, dc, dr) {
      var o = layout(), q = setAt(o, k);
      if (!q) return;
      var list = o.sets.filter(function (x) { return x !== q; }).map(function (x) { return {id: x.id, c: x.c, r: x.r, n: x.n}; });
      var lastR = Math.max(rowsOf(list), q.r);
      for (var i = 1; i <= MAXC; i++) {
        var c = q.c + dc * i, r = q.r + dr * i;
        if (c < 0 || r < 0 || c > MAXC || r > lastR) { api.announce("Set " + k + " cannot go further that way."); return; }
        if (fits(list, c, r, q.n)) { moveSet(q, {c: c, r: r}, true); var o2 = layout(), q2 = setAt(o2, k); if (q2) reveal((q2.x0 + q2.x1) / 2, q2.y + TH / 2, Math.min(2, (q2.x1 - q2.x0) / 2 + 0.4), 1); return; }
      }
    }

    function pointer(kind, p, e) {
      var cv = board(), pid = e && e.pointerId;
      // two fingers: pinch to zoom, about the point between them
      if (pid != null && e.clientX != null) {
        if (kind === "down") ptrs[pid] = {x: e.clientX, y: e.clientY};
        else if (kind === "move" && ptrs[pid]) ptrs[pid] = {x: e.clientX, y: e.clientY};
        var two = Object.keys(ptrs);
        if (kind === "down" && two.length === 2) {
          var a = canvasXY(ptrs[two[0]]), b = canvasXY(ptrs[two[1]]);
          drag = null; down = null; pan = null;
          pinch = {d: Math.max(10, Math.hypot(a.x - b.x, a.y - b.y)), m: {x: (a.x + b.x) / 2, y: (a.y + b.y) / 2}, z: cam()};
          api.redraw();
          return;
        }
        if (kind === "up" || kind === "cancel") delete ptrs[pid];
        if (pinch) {
          if (kind === "move" && two.length >= 2) {
            var a2 = canvasXY(ptrs[two[0]]), b2 = canvasXY(ptrs[two[1]]), z = pinch.z;
            var m = {x: (a2.x + b2.x) / 2, y: (a2.y + b2.y) / 2}, s = clamp(z.s * Math.hypot(a2.x - b2.x, a2.y - b2.y) / pinch.d, z.home, z.max);
            var bx = z.cx + (pinch.m.x - z.cw / 2) / z.s, by = z.cy + (pinch.m.y - z.ch / 2) / z.s;
            setZoom(z, s, bx - (m.x - z.cw / 2) / s, by - (m.y - z.ch / 2) / s);
          }
          if (Object.keys(ptrs).length < 2 && (kind === "up" || kind === "cancel")) pinch = null;
          return;
        }
      }
      if (kind === "down") {
        var h = hit(p);
        kbd = false;
        down = {h: h, x: p.x, y: p.y};
        if (h && (h.type === "rack" || h.type === "tile" || h.type === "set")) {
          drag = {src: h, x: p.x, y: p.y, sx: p.x, sy: p.y, moved: false};
          if (h.type === "set") { var q = setAt(layout(), h.k); drag.ox = p.x - q.x0; drag.oy = p.y - q.y; }
        } else if (e && e.clientX != null) pan = {x: e.clientX, y: e.clientY, z: null};
        return;
      }
      if (kind === "move") {
        if (pan && e) {
          var dx = e.clientX - pan.x, dy = e.clientY - pan.y;
          if (!pan.z && Math.hypot(dx, dy) > 5) { pan.z = cam(); down = null; }
          if (pan.z) { if (!pan.z.all) setZoom(pan.z, pan.z.s, pan.z.cx - dx / pan.z.s, pan.z.cy - dy / pan.z.s); cv.style.cursor = pan.z.all ? "default" : "grabbing"; }
          return;
        }
        if (drag) {
          drag.x = p.x; drag.y = p.y;
          if (!drag.moved && Math.hypot(p.x - drag.sx, p.y - drag.sy) > 0.25) drag.moved = true;
          if (drag.moved) {
            // near the edge of a zoomed board, the board follows
            if (e && e.clientX != null) {
              var cp = canvasXY({x: e.clientX, y: e.clientY}), zz = cam(), E = 36;
              var ex = cp.x < E ? cp.x - E : cp.x > zz.cw - E ? cp.x - zz.cw + E : 0, ey = cp.y < E ? cp.y - E : cp.y > zz.ch - E ? cp.y - zz.ch + E : 0;
              if ((ex || ey) && !zz.all) panBy(ex * 0.4, ey * 0.4);
            }
            api.redraw();
          }
        }
        var over = hit(p);
        cv.style.cursor = drag && drag.moved ? "grabbing" : over && over.type === "set" ? "grab" : over && over.type !== "tray" && over.type !== "table" ? "pointer" : "default";
        return;
      }
      if (kind === "up") {
        var d = drag, dn = down, pn = pan;
        drag = null; down = null; pan = null;
        if (pn && pn.z) { cv.style.cursor = "default"; return; }
        if (d && d.moved) {
          if (d.src.type === "set") { var t = dragCell(d); if (t) moveSet(t.q, t, false); }
          else drop(d.src, hit(p));
        } else if (dn) click(dn.h);
        panel(); api.redraw();
        return;
      }
      if (kind === "leave" || kind === "cancel") {
        if (kind === "cancel") { pan = null; pinch = null; }
        if (drag && drag.moved) { drag = null; down = null; api.redraw(); }
        cv.style.cursor = "default";
      }
    }

    // ---- the keyboard: a cursor over everything that can be picked ---------------------------------------------------------
    function spots() {
      var o = layout(), out = [];
      buttons(o);
      o.sets.slice().sort(function (a, b) { return a.r - b.r || a.c - b.c; }).forEach(function (s) {
        out.push({h: {type: "set", k: s.k}, x: s.x0 + 0.3, y: s.y - GRIP / 2, w: 0.8, hh: GRIP,
                  box: {x: (s.x0 + s.x1) / 2, y: s.y - GRIP + (GRIP + TH + MATBOT) / 2, w: s.x1 - s.x0 + 2 * MATPAD, h: GRIP + TH + MATBOT}});
        s.tiles.forEach(function (t) { out.push({h: {type: "tile", k: t.k, p: t.p}, x: t.x, y: t.y, w: TW, hh: TH}); });
      });
      out.push({h: {type: "new"}, x: o.newZone.x, y: o.newZone.y, w: o.newZone.w, hh: TH});
      o.btns.forEach(function (b) { if (b.id.indexOf("sort") < 0) out.push({h: {type: "btn", id: b.id}, x: b.x, y: b.y, w: b.w, hh: b.h}); });
      o.rack.forEach(function (t) { out.push({h: {type: "rack", i: t.i}, x: t.x, y: t.y, w: TW, hh: TH}); });
      return out;
    }
    function spotName(s) {
      var h = s.h;
      if (h.type === "set") { var st = setOf(h.k); return "Set " + h.k + ": " + setWords(st); }
      if (h.type === "tile") return tileName(setOf(h.k).tiles[h.p - 1]) + ", tile " + h.p + " of set " + h.k + (sel.tile && sel.tile.k === h.k && sel.tile.p === h.p ? ", picked" : "");
      if (h.type === "new") return "A new set";
      if (h.type === "btn") return {done: "Done", undo: "Undo", reset: "Reset the turn"}[h.id] || h.id;
      if (h.type === "rack") return tileName(shown()[h.i]) + " on your rack" + (sel.rack.indexOf(h.i) >= 0 ? ", picked" : "");
      return "";
    }
    function key(e) {
      if (e.ctrlKey || e.metaKey || e.altKey) return false;
      kbd = true;
      // the zoom: + and - about the middle, 0 the whole table
      if (e.key === "+" || e.key === "=") { zoomBy(1.25); return true; }
      if (e.key === "-" || e.key === "_") { zoomBy(0.8); return true; }
      if (e.key === "0") { fit(); return true; }
      var ss = spots();
      if (!ss.length) return false;
      if (cursor < 0 || cursor >= ss.length) cursor = Math.max(0, ss.length - 1 - (L ? L.rack.length - 1 : 0));
      var c = ss[cursor];
      var moveTo = function (i) { cursor = clamp(i, 0, ss.length - 1); api.announce(spotName(ss[cursor])); reveal(ss[cursor].x, ss[cursor].y); };
      if (e.shiftKey && /^Arrow/.test(e.key)) {
        // Shift and an arrow: the picked set, or the set at the cursor, moves on the table
        var k = sel.set || (c.h.type === "set" || c.h.type === "tile" ? c.h.k : 0);
        if (!k) { api.toast("Shift and the arrow keys move a set: put the frame on one, or pick it."); return true; }
        nudgeSet(k, e.key === "ArrowLeft" ? -2 : e.key === "ArrowRight" ? 2 : 0, e.key === "ArrowUp" ? -1 : e.key === "ArrowDown" ? 1 : 0);
        var ns = spots();
        for (var i = 0; i < ns.length; i++) if (ns[i].h.type === c.h.type && ns[i].h.k === c.h.k && ns[i].h.p === c.h.p) { cursor = i; break; }
        return true;
      }
      switch (e.key) {
        case "ArrowLeft": moveTo(cursor - 1); return true;
        case "ArrowRight": moveTo(cursor + 1); return true;
        case "ArrowUp": case "ArrowDown":
          var dir = e.key === "ArrowUp" ? -1 : 1, best = -1, bd = 1e9;
          ss.forEach(function (s, j) {
            var dy = (s.y - c.y) * dir;
            if (dy < 0.4) return;
            var d = dy * 3 + Math.abs(s.x - c.x);
            if (d < bd) { bd = d; best = j; }
          });
          if (best >= 0) moveTo(best);
          return true;
        case "Enter": case " ": click(c.h); panel(); return true;
        case "Escape": clearSel(); panel(); api.announce("Nothing picked."); return true;
        case "n": case "N": if (sel.rack.length) put("n"); else if (sel.tile) moveTile("n"); else api.toast("Pick tiles first."); return true;
        case "s": case "S": split(); return true;
        case "r": case "R": if (sel.tile) moveTile("r"); else api.toast("Pick a tile on the table that you played this turn."); return true;
        case "u": case "U": undo(); return true;
        case "d": case "D": done(); return true;
        case "o": case "O": setSort(sortMode === "colour" ? "number" : "colour"); return true;
      }
      return false;
    }

    // ---- words -------------------------------------------------------------------------------------------------------
    function setWords(s) {
      if (!s) return "";
      return KIND[s.kind] + (s.kind === "x" ? "" : ", valid") + (s.changed ? ", made or changed this turn" : "") +
        (locked(s) && !api.over() ? ", locked until your first meld" : "") + ": " + s.tiles.join(" ");
    }
    function turnText() {
      var tp = v.tp;
      if (!v.started) return "Waiting for players.";
      if (api.over()) return (api.common().status || "Game over") + ".";
      if (!mine()) {
        var who = v.turn >= 0 ? api.name(v.turn) : "";
        return who ? who + " is playing" + (tp && tp.n ? ": " + plural(tp.n, "tile") + " from the rack so far." : ".") : "";
      }
      if (!tp) return "Your turn.";
      switch (tp.code) {
        case "ok": return "Ready: Done plays your " + plural(tp.n, "tile") + (tp.pts != null ? " and makes your first meld of " + tp.pts + " points" : "") + ".";
        case "sets":
          var bad = v.sets.filter(function (s) { return s.kind === "x"; })[0];
          return (bad ? "Set " + bad.k + " (" + bad.tiles.join(" ") + ", on a red mat) is not a valid set yet." : "A set is not valid yet.") + (tp.pts != null ? " First meld " + tp.pts + " of 30 points." : "");
        case "meld": return "First meld: " + tp.pts + " of 30 points.";
        case "joker": return "A joker taken from the table needs its tile from your rack.";
        default:
          return staged() ? "No tile from your rack yet: put at least one on the table." :
            "Your turn: put tiles on the table, or press " + (v.pool ? "Draw to draw a tile." : "Pass.");
      }
    }
    function barWords() {
      var tp = v.tp;
      if (!v.started || api.over()) return "";
      if (!mine()) return v.turn >= 0 ? api.name(v.turn) + " is playing" : "";
      if (!tp || tp.code === "none") return staged() ? "No tile from your rack yet" : "Your turn";
      var meld = tp.pts != null ? "first meld " + tp.pts + " / 30" : "";
      if (tp.code === "ok") return "✓ Ready: " + plural(tp.n, "tile") + (meld ? ", " + meld : "");
      if (tp.code === "sets") return "✗ a set is not valid yet" + (meld ? ", " + meld : "");
      if (tp.code === "meld") return "✗ " + meld;
      return "✗ joker needs its tile";
    }
    function status() {
      var parts = [];
      if (!v.started) return "Waiting for players.";
      parts.push("Pool " + v.pool + ".");
      v.counts.forEach(function (c, i) { parts.push(api.name(i) + (i === me() ? " (you)" : "") + " " + plural(c, "tile") + (v.melded[i] ? ", first meld made" : "") + (v.gone[i] ? ", left the game" : "") + "."); });
      if (v.sets.length) parts.push("Table: " + v.sets.map(function (s) { return "set " + s.k + " " + setWords(s); }).join("; ") + ".");
      else parts.push("The table is empty.");
      if (me() >= 0) parts.push("Your rack: " + shown().join(" ") + ".");
      parts.push(turnText());
      return parts.join(" ");
    }

    // ---- the panel -----------------------------------------------------------------------------------------------------
    function panel() {
      var p = api.panel, focusId = document.activeElement && p.contains(document.activeElement) ? document.activeElement.getAttribute("data-id") : null;
      clear(p);
      var my = me() >= 0, t = mine(), over = api.over();
      p.appendChild(el("h2", {text: "Rummy Tile Classic"}));
      if (!v.started) { p.appendChild(el("p", {cls: "small", text: "Waiting for players: two to four sit down, then 14 tiles are dealt to each."})); help(p); return; }
      p.appendChild(el("p", {cls: "small", text: "Pool: " + plural(v.pool, "tile") + "."}));
      var ul = el("ul", {cls: "list small", "aria-label": "Racks"});
      v.counts.forEach(function (c, i) {
        var li = el("li", {}, [el("span", {}, [(v.turn === i && !over ? "▶ " : "") , el("b", {text: api.name(i)}), i === me() ? " (you)" : "",
          ": " + plural(c, "tile") + (v.melded[i] ? ", first meld made" : ", no first meld yet") + (v.gone[i] ? ", left the game" : "")])]);
        li.style.justifyContent = "flex-start";
        ul.appendChild(li);
      });
      p.appendChild(ul);
      if (over) { result(p); help(p); return; }
      p.appendChild(el("p", {cls: "score-preview" + (t && v.tp ? (v.tp.code === "ok" ? " good" : v.tp.code === "none" ? "" : " bad") : ""), "aria-live": "polite", text: turnText()}));
      if (my) {
        var pk = picked(), what = pk.length ? "Picked on your rack: " + pk.join(" ") + "." :
          sel.tile ? "Picked " + setOf(sel.tile.k).tiles[sel.tile.p - 1] + ", tile " + sel.tile.p + " of set " + sel.tile.k + "." :
          sel.set ? "Picked set " + sel.set + " (" + setOf(sel.set).tiles.join(" ") + ")." : "Nothing picked. Click tiles on your rack, a tile on the table, or a set by the space around its tiles.";
        p.appendChild(el("p", {cls: "small", text: what}));
        var row = el("div", {cls: "panel-buttons"});
        var b = function (id, label, on, fn, cls, aria) { row.appendChild(el("button", {type: "button", "data-id": id, cls: cls || null, text: label, "aria-label": aria || null, disabled: !on, on: {click: fn}})); };
        var nums = v.sets.map(function (s) { return s.k; });
        if (pk.length) {
          b("new", "New set (N)", t, function () { put("n"); });
          if (nums.length) row.appendChild(target("put", "Into set", nums, t, function (k) { put(k); }));
        } else if (sel.tile) {
          b("new", "To a new set (N)", t, function () { moveTile("n"); });
          b("split", "Split before it (S)", t && sel.tile.p > 1, split);
          b("back", "Back to rack (R)", t, function () { moveTile("r"); });
          var others = nums.filter(function (k) { return k !== sel.tile.k; });
          if (others.length) row.appendChild(target("move", "Move to set", others, t, function (k) { moveTile(k); }));
        } else if (sel.set) {
          var rest = nums.filter(function (k) { return k !== sel.set; });
          if (rest.length) row.appendChild(target("join", "Join onto set", rest, t, function (k) { merge(sel.set, k); }));
          // moving the set on the table: anyone, at any time (it is this browser's own layout)
          [["left", "←", -2, 0], ["right", "→", 2, 0], ["up", "↑", 0, -1], ["down", "↓", 0, 1]].forEach(function (m) {
            b("set-" + m[0], m[1], true, function () { nudgeSet(sel.set, m[2], m[3]); }, null, "Move set " + sel.set + " " + m[0]);
          });
        }
        if (pk.length || sel.tile || sel.set) b("clear", "Clear (Esc)", true, function () { clearSel(); panel(); api.redraw(); });
        if (row.children.length) p.appendChild(row);
        var row2 = el("div", {cls: "panel-buttons"}), ready = v.tp && v.tp.code === "ok";
        row2.style.marginTop = "8px";
        row2.appendChild(el("button", {type: "button", "data-id": "done", cls: ready ? "primary" : null, disabled: !t || busy,
          text: ready ? "Done (D)" : staged() ? "Done: take back and " + (v.pool ? "draw" : "pass") : v.pool ? "Draw a tile (D)" : "Pass (D)", on: {click: done}}));
        row2.appendChild(el("button", {type: "button", "data-id": "undo", text: "Undo step (U)", disabled: !t || !(steps > 0 || staged()), on: {click: undo}}));
        row2.appendChild(el("button", {type: "button", "data-id": "reset", text: "Reset turn", disabled: !t || !staged(), on: {click: reset}}));
        p.appendChild(row2);
        var row3 = el("div", {cls: "panel-buttons", role: "group", "aria-label": "Sort your rack"});
        row3.style.marginTop = "8px";
        ["colour", "number"].forEach(function (m) {
          row3.appendChild(el("button", {type: "button", "data-id": "sort-" + m, "aria-pressed": String(sortMode === m), text: "Sort by " + (m === "colour" ? "color" : m),
            on: {click: function () { setSort(m); }}}));
        });
        p.appendChild(row3);
        tilesAsButtons(p);
        typed(p);
      }
      help(p);
      if (focusId) { var f = p.querySelector('[data-id="' + focusId + '"]'); if (f && !f.disabled) f.focus(); }
    }
    // a button and a list of sets; the set chosen in the list is lit on the board, since the table shows no numbers
    function target(id, label, nums, on, fn) {
      var light = function () { hl = +s.value; api.redraw(); };
      var s = el("select", {"aria-label": label, "data-id": id + "-sel", on: {change: light, focus: light, blur: function () { hl = 0; api.redraw(); }}});
      nums.forEach(function (k) { s.appendChild(el("option", {value: k, text: "set " + k + " (" + setOf(k).tiles.join(" ") + ")"})); });
      var bt = el("button", {type: "button", "data-id": id, text: label, disabled: !on, on: {click: function () { hl = 0; fn(+s.value); }}});
      var w = el("span", {cls: "row"}, [bt, s]);
      w.style.flex = "1 1 100%";
      return w;
    }
    // every tile as a button, for screen readers and switch access: the same as clicking it on the board
    function tilesAsButtons(p) {
      var d = el("details", {}, [el("summary", {text: "Tiles as buttons"})]);
      d.style.marginTop = "8px";
      var rack = el("div", {cls: "panel-buttons", role: "group", "aria-label": "Your rack"});
      shown().forEach(function (t, i) {
        rack.appendChild(el("button", {type: "button", "data-id": "rk" + i, "aria-pressed": String(sel.rack.indexOf(i) >= 0), "aria-label": tileName(t),
          text: t, on: {click: function () { click({type: "rack", i: i}); panel(); api.redraw(); }}}));
      });
      d.appendChild(el("p", {cls: "small muted", text: "Your rack"})); d.appendChild(rack);
      v.sets.forEach(function (s) {
        var row = el("div", {cls: "panel-buttons", role: "group", "aria-label": "Set " + s.k});
        row.appendChild(el("button", {type: "button", "data-id": "st" + s.k, "aria-pressed": String(sel.set === s.k), text: "Set " + s.k,
          "aria-label": "Set " + s.k + ", " + setWords(s), on: {click: function () { click({type: "set", k: s.k}); panel(); api.redraw(); }}}));
        s.tiles.forEach(function (t, i) {
          var on = sel.tile && sel.tile.k === s.k && sel.tile.p === i + 1;
          row.appendChild(el("button", {type: "button", "data-id": "tl" + s.k + "." + (i + 1), "aria-pressed": String(!!on), "aria-label": tileName(t) + " in set " + s.k,
            text: t, on: {click: function () { click({type: "tile", k: s.k, p: i + 1}); panel(); api.redraw(); }}}));
        });
        d.appendChild(row);
      });
      d.appendChild(el("p", {}, [el("button", {type: "button", "data-id": "newset", text: "A new set", on: {click: function () { click({type: "new"}); panel(); api.redraw(); }}})]));
      d.open = !!tilesOpen;
      d.addEventListener("toggle", function () { tilesOpen = d.open; });
      p.appendChild(d);
    }
    var tilesOpen = false, typedOpen = false;
    function typed(p) {
      var inp = el("input", {"aria-label": "A step as the protocol writes it", placeholder: "R7B7K7-n, 3.2-5, 3/4, 3-5", "data-id": "typed"});
      var f = el("form", {cls: "row", on: {submit: function (e) {
        e.preventDefault();
        var tok = inp.value.trim();
        if (!/^(reset|[RBKYJ0-9.\-\/nNrR]+)$/i.test(tok)) { api.toast("Write a step like R7-3, R7B7K7-n, 3.2-5, 3.2-r, 3-5, 3/4 or reset."); return; }
        send(tok, "Played " + tok);
      }}}, [inp, el("button", {type: "submit", text: "Send", disabled: !mine()})]);
      var d = el("details", {}, [el("summary", {text: "Type a step"}), el("p", {cls: "small muted", text: "R7-3 puts R7 into set 3; R7B7K7-n makes a new set; 3.2-5 moves tile 2 of set 3 to set 5 (n a new set, r back to your rack); 3-5 joins set 3 onto set 5; 3/4 splits set 3 before its tile 4; reset starts the turn over."}), f]);
      d.style.marginTop = "8px";
      d.open = typedOpen;
      d.addEventListener("toggle", function () { typedOpen = d.open; });
      p.appendChild(d);
    }
    function result(p) {
      var sc = api.common().scores || {}, win = null, best = -1e9;
      Object.keys(sc).forEach(function (s) { if (sc[s] > best) { best = sc[s]; win = +s; } });
      p.appendChild(el("p", {cls: "score-preview good", "aria-live": "polite", text: "Game over: " + winnerLine(api.common().status || "") + "."}));
      var tb = el("table", {cls: "small"});
      tb.appendChild(el("tr", {}, [el("th", {text: "Player"}), el("th", {text: "Tiles left"}), el("th", {text: "Score"})]));
      v.counts.forEach(function (c, i) {
        tb.appendChild(el("tr", {}, [el("td", {text: api.name(i) + (i === me() ? " (you)" : "") + (i === win && best > 0 ? " - winner" : "")}),
                                     el("td", {text: String(c)}), el("td", {text: sc[i] != null ? String(sc[i]) : ""})]));
      });
      tb.style.width = "100%";
      p.appendChild(tb);
      p.appendChild(el("p", {cls: "small muted", text: "Each rack counts its numbers, a joker 30. The lowest rack wins the others' differences."}));
    }
    function help(p) {
      var d = el("details", {}, [el("summary", {text: "How to play"}),
        el("p", {cls: "small", text: "Lay sets on the table: a run is three or more tiles of one color in a row (1 to 13, no wrapping), a group three or four tiles of one number in different colors. A joker, the star, stands for any tile."}),
        el("p", {cls: "small", text: "Your first meld must come from your own rack and be worth 30 points or more; until then the sets already on the table are locked. After it, you may take the table apart and build it again however you like, as long as every set is valid when you press Done and at least one tile came from your rack. Otherwise Done takes the turn back and you draw a tile."}),
        el("p", {cls: "small", text: "On the table, a set that is not valid yet lies on a red mat with a red edge, a set made or changed this turn has a thin light edge, and the sets you may not touch before your first meld are dimmed. The line over the buttons says how the turn stands, and the first press of Done says why a turn would not count. On the racks line, ✓ after a name: that player has made the first meld."}),
        el("p", {cls: "small muted", text: "On the board: click tiles on your rack to pick them, then a set or the new set (or drag them there, or onto a free part of the table to start a new set there). Click a tile on the table, then another set, the new set or your rack. Click a set by the space around its tiles, then another set, to join them."}),
        el("p", {cls: "small muted", text: "Drag a set by the space around its tiles to put it anywhere on the table; the layout is yours, kept in this browser for each table. Zoom with the + and − buttons, Ctrl or ⌘ with the scroll wheel, or two fingers; drag the free table, or scroll, to look around; Fit shows the whole table again."}),
        el("p", {cls: "small muted", text: "Keys: arrows move, Enter or Space picks or places, Shift and the arrows move the set at the frame, + and − zoom, 0 fits, N new set, S split, R back to the rack, U undo, D done, O sort, Escape puts down."})]);
      d.style.marginTop = "8px";
      p.appendChild(d);
      p.appendChild(el("p", {cls: "small muted"}, ["Colors: ", el("a", {href: "#", text: "Settings", on: {click: function (e) { e.preventDefault(); document.getElementById("b-settings").click(); }}}), "."]));
    }

    // ---- the zoom buttons, over the board's corner ---------------------------------------------------------------------
    (function controls() {
      var stage = document.getElementById("stage");
      if (!stage || !stage.appendChild) return;
      var b = function (id, text, label, fn) {
        return el("button", {type: "button", "data-id": id, text: text, "aria-label": label, title: label, on: {click: function () { fn(); }}});
      };
      ctl = el("div", {cls: "zoom-ctl", role: "group", "aria-label": "Zoom"}, [
        b("zoom-out", "−", "Zoom out (−)", function () { zoomBy(0.8); }),
        b("zoom-in", "+", "Zoom in (+)", function () { zoomBy(1.25); }),
        b("zoom-fit", "Fit", "Show the whole table (0)", fit)]);
      stage.appendChild(ctl);
      if (stage.classList) stage.classList.add("has-zoom");
    })();
    function destroy() {
      if (ctl && ctl.parentNode) ctl.parentNode.removeChild(ctl);
      var stage = document.getElementById("stage");
      if (stage && stage.classList) stage.classList.remove("has-zoom");
    }

    // ---- drawing ---------------------------------------------------------------------------------------------------------
    function palKey(c) { return [c.table, c.tile, c.ink, c.r, c.b, c.k, c.y].join(""); }
    function tileItem(c, face, x, y, z, extra) {
      var pk = palKey(c), dim = extra && extra.dim;
      var it = {t: "tile", x: x, y: y, z: z || 0, w: TW, h: TH, d: 0.09, color: c.tile, key: face + (dim ? "d" : "") + pk,
                paint: function (ctx, w, h) { paintTile(ctx, w, h, face, c, dim); }};
      if (extra && extra.lift) it.lift = extra.lift;
      return it;
    }
    function pill(items, b, c) {
      var col = b.kind === "primary" ? PRIMARY : b.kind === "warn" ? "#7a3b33" : b.kind === "on" ? "#56616d" : BTN;
      var ink = b.kind === "primary" ? PRIMARY_INK : "#ffffff";
      items.push({t: "quad", x: b.x, y: b.y, z: 0.02, w: b.w, h: b.h, shape: "round", color: col, alpha: b.on ? 0.95 : 0.4});
      if (b.kind === "on") items.push({t: "quad", x: b.x, y: b.y, z: 0.021, w: b.w + 0.06, h: b.h + 0.06, shape: "frame", color: ACCENT, alpha: 0.7});
      items.push({t: "text", x: b.x, y: b.y, z: 0.03, text: b.label, size: b.h * 0.56, color: ink, alpha: b.on ? 1 : 0.6});
      void c;
    }
    // a thin edge round a box, of four strips (a "frame" quad would stretch its lines on a long mat)
    function edge(items, x, y, w, h, color, alpha, lw, z) {
      items.push({t: "quad", x: x, y: y - h / 2 + lw / 2, z: z, w: w, h: lw, color: color, alpha: alpha});
      items.push({t: "quad", x: x, y: y + h / 2 - lw / 2, z: z, w: w, h: lw, color: color, alpha: alpha});
      items.push({t: "quad", x: x - w / 2 + lw / 2, y: y, z: z, w: lw, h: h - 2 * lw, color: color, alpha: alpha});
      items.push({t: "quad", x: x + w / 2 - lw / 2, y: y, z: z, w: lw, h: h - 2 * lw, color: color, alpha: alpha});
    }
    function frame(now) {
      var c = pal(), o = layout(), W = o.W, items = [], over = api.over(), my = me() >= 0;
      animating = false;
      var z = cam(o), view = {w: o.BW, h: o.H, tilt: 3, pad: 0.15, z: 0.3, win: z.win, aspect: (W + 0.3) / (Math.max(o.H, o.Hf) + 0.3)};
      items.push({t: "board", w: o.BW, h: o.H, d: 0.3, frame: PNGfx.shade(c.table, -0.35), key: "rt" + c.table + o.BW + "x" + o.H.toFixed(2), spec: 0.03,
                  paint: function (ctx, w, h) {
                    var g = ctx.createRadialGradient(w * 0.5, h * 0.35, 0, w * 0.5, h * 0.45, Math.max(w, h) * 0.75);
                    g.addColorStop(0, PNGfx.shade(c.table, 0.08)); g.addColorStop(1, PNGfx.shade(c.table, -0.12));
                    ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);
                  }});
      if (!v.started) {
        items.push({t: "text", x: W / 2, y: o.H / 2, text: "Waiting for players", size: 0.7, color: c.mark});
        return {view: view, items: items, bg: "#121519"};
      }
      buttons(o);
      // the head: the pool and each player's rack
      o.chips.forEach(function (ch) {
        var turn = ch.seat === v.turn && !over;
        items.push({t: "quad", x: ch.x, y: ch.y, w: ch.w, h: CHIP, shape: "round", color: turn ? ACCENT : c.mark, alpha: turn ? 0.3 : 0.08});
        items.push({t: "text", x: ch.x, y: ch.y, text: ch.text, size: CHIPTEXT, color: ch.seat === me() ? ACCENT : c.mark});
      });
      var drawTile = function (t, sx, sy, z, extra) {
        var x = sx, y = sy, zz = z || 0;
        if (t.anim) {
          var k = (now - t.anim.t0) / t.anim.dur;
          if (k < 1) { animating = true; var e = ease(k); x = t.anim.fx + (sx - t.anim.fx) * e; y = t.anim.fy + (sy - t.anim.fy) * e; zz += t.anim.fz * (1 - e); }
          else delete t.anim;
        }
        items.push({t: "shadow", x: x, y: y, z: zz, r: 0.42, alpha: 0.35});
        items.push(tileItem(c, t.face, x, y, zz, extra));
      };
      var dragging = drag && drag.moved ? drag : null, overH = dragging && dragging.src.type !== "set" ? hit(dragging) : null;
      var draggedRack = dragging && dragging.src.type === "rack" ? (sel.rack.indexOf(dragging.src.i) >= 0 ? sel.rack : [dragging.src.i]) : [];
      // the sets: each on its mat, with no words; a set that is not valid on a red mat with a red edge, one made or
      // changed this turn with a thin light edge, a locked one's tiles dimmed; a short bar at the top where it is taken
      var landing = dragging && dragging.src.type === "set" ? dragCell(dragging) : null;
      if (landing) {
        var lq = landing.q, lx0 = PADX + landing.c * HS, ly = o.top + landing.r * RH, lw = lq.x1 - lq.x0 + 2 * MATPAD;
        items.push({t: "quad", x: lx0 - MATPAD + lw / 2, y: ly - GRIP + (GRIP + TH + MATBOT) / 2, w: lw, h: GRIP + TH + MATBOT, shape: "round", color: ACCENT, alpha: 0.16});
        edge(items, lx0 - MATPAD + lw / 2, ly - GRIP + (GRIP + TH + MATBOT) / 2, lw, GRIP + TH + MATBOT, ACCENT, 0.5, 0.03, 0);
      }
      o.sets.forEach(function (s) {
        var st = s.s, lk = locked(st) && !over, valid = st.kind !== "x";
        var moving = landing && landing.q === s, dx = 0, dy = 0, lift = 0;
        if (moving) { dx = dragging.x - dragging.ox - s.x0; dy = dragging.y - dragging.oy - s.y; lift = 0.35; }
        var hot = sel.set === s.k || hl === s.k || (overH && overH.k === s.k && (overH.type === "set" || overH.type === "tile") && !(dragging.src.type === "tile" && dragging.src.k === s.k));
        var mw = s.x1 - s.x0 + 2 * MATPAD, mh = GRIP + TH + MATBOT, mx = (s.x0 + s.x1) / 2 + dx, my2 = s.y - GRIP + mh / 2 + dy;
        items.push({t: "quad", x: mx, y: my2, z: lift, w: mw, h: mh, shape: "round", color: hot ? ACCENT : !valid ? BAD : c.mark,
                    alpha: hot ? 0.45 : !valid ? 0.3 : moving ? 0.16 : 0.08});
        if (!valid) edge(items, mx, my2, mw, mh, BAD, 0.85, 0.045, lift);
        else if (st.changed) edge(items, mx, my2, mw, mh, ACCENT, 0.55, 0.03, lift);
        items.push({t: "quad", x: mx, y: s.y - GRIP / 2 + 0.02 + dy, z: lift, w: Math.min(0.6, mw * 0.4), h: 0.07, shape: "round", color: c.mark, alpha: lk ? 0.14 : 0.3});
        s.tiles.forEach(function (t) {
          var picked1 = sel.tile && sel.tile.k === t.k && sel.tile.p === t.p;
          if (dragging && dragging.src.type === "tile" && dragging.src.k === t.k && dragging.src.p === t.p) return;
          if (picked1) items.push({t: "quad", x: t.x, y: t.y, w: TW + 0.14, h: TH + 0.14, shape: "round", color: ACCENT, alpha: 0.85});
          drawTile(t, t.x + dx, t.y + dy - (picked1 ? 0.1 : 0), lift + (picked1 ? 0.12 : 0), {dim: lk});
        });
      });
      // sets out of the window (a zoomed or spread table): a small arrowhead at the window's edge, towards each
      if (!z.all) {
        var wn = z.win, seen = {};
        o.sets.forEach(function (s) {
          var cy = s.y + TH / 2, cx = (s.x0 + s.x1) / 2, side = null, ax, ay;
          if (s.x0 > wn.x + wn.w - 0.3) { side = "▶"; ax = wn.x + wn.w - 0.3; ay = clamp(cy, wn.y + 0.5, wn.y + wn.h - 0.5); }
          else if (s.x1 < wn.x + 0.3) { side = "◀"; ax = wn.x + 0.3; ay = clamp(cy, wn.y + 0.5, wn.y + wn.h - 0.5); }
          else if (cy - TH / 2 > wn.y + wn.h - 0.3) { side = "▼"; ay = wn.y + wn.h - 0.3; ax = clamp(cx, wn.x + 0.5, wn.x + wn.w - 0.5); }
          else if (cy + TH / 2 < wn.y + 0.3) { side = "▲"; ay = wn.y + 0.3; ax = clamp(cx, wn.x + 0.5, wn.x + wn.w - 0.5); }
          var key2 = side + Math.round(ax * 2) + ":" + Math.round(ay * 2);
          if (!side || seen[key2]) return;
          seen[key2] = 1;
          items.push({t: "text", x: ax, y: ay, z: 0.3, text: side, size: 0.36, color: c.mark, alpha: 0.55});
        });
      }
      // the place for a new set; tiles dragged over a free part of the table show where their new set would go
      var nz = o.newZone, nzHot = (sel.rack.length || sel.tile) && mine();
      if (overH && overH.type === "new") nzHot = 2;
      items.push({t: "quad", x: nz.x, y: nz.y, w: nz.w, h: nz.h, shape: "round", color: nzHot ? ACCENT : c.mark, alpha: nzHot === 2 ? 0.5 : nzHot ? 0.25 : 0.08});
      items.push({t: "text", x: nz.x, y: nz.y - 0.14, text: "+", size: 0.5, color: nzHot ? ACCENT : c.mark, alpha: 0.85});
      items.push({t: "text", x: nz.x, y: nz.y + 0.24, text: "new set", size: 0.26, color: nzHot ? ACCENT : c.mark, alpha: 0.85});
      if (overH && overH.type === "table" && mine()) {
        var cell = cellAt(o, overH), cx0 = PADX + cell.c * HS, cy0 = o.top + cell.r * RH;
        items.push({t: "quad", x: cx0 + TW / 2, y: cy0 + TH / 2, w: TW + 0.2, h: TH + 0.2, shape: "round", color: ACCENT, alpha: 0.3});
      }
      // the bar: how the turn stands, and the buttons
      var words = barWords();
      if (words) {
        var good = v.tp && v.tp.code === "ok", bad = mine() && v.tp && v.tp.code !== "ok" && v.tp.code !== "none";
        items.push({t: "text", x: o.barText.x, y: o.barText.y, text: words, size: Math.min(0.34, o.barText.maxW / Math.max(8, words.length) * 1.35),
                    color: good ? GOOD : bad ? "#ffb3ab" : c.mark, align: "left"});
      }
      o.btns.forEach(function (b) { pill(items, b, c); });
      // the rack: a flat tray
      if (my && o.tray) {
        items.push({t: "text", x: PADX, y: o.rlabelY, text: "Your rack, " + plural(v.rack.length, "tile"), size: 0.3, color: c.mark, align: "left"});
        var trayHot = dragging && dragging.src.type === "tile" && overH && (overH.type === "rack" || overH.type === "tray");
        items.push({t: "box", x: o.tray.x, y: o.tray.y, w: o.tray.w, h: o.tray.h, d: 0.06, r: 0.1, color: trayHot ? PNGfx.shade(c.tray, 0.2) : c.tray, spec: 0.04});
        o.rack.forEach(function (t) {
          if (draggedRack.indexOf(t.i) >= 0) return;
          var on = sel.rack.indexOf(t.i) >= 0;
          if (on) items.push({t: "quad", x: t.x, y: t.y - 0.14, z: 0.06, w: TW + 0.14, h: TH + 0.14, shape: "round", color: ACCENT, alpha: 0.85});
          drawTile(t, t.x, t.y - (on ? 0.14 : 0), 0.06 + (on ? 0.1 : 0));
        });
      }
      // tiles being dragged
      if (dragging && dragging.src.type !== "set") {
        var faces = dragging.src.type === "rack" ? draggedRack.map(function (i) { return shown()[i]; }) : [setOf(dragging.src.k).tiles[dragging.src.p - 1]];
        faces.forEach(function (f, i) {
          var x = dragging.x + (i - (faces.length - 1) / 2) * STEP * 0.9;
          items.push({t: "shadow", x: x + 0.2, y: dragging.y + 0.25, r: 0.5, alpha: 0.4});
          items.push(tileItem(c, f, x, dragging.y, 0.5));
        });
      }
      // the keyboard's cursor, while the board has the focus
      if (kbd && document.activeElement === board() && cursor >= 0) {
        var ss = spots(), cs = ss[Math.min(cursor, ss.length - 1)];
        if (cs && cs.box) edge(items, cs.box.x, cs.box.y, cs.box.w + 0.1, cs.box.h + 0.1, ACCENT, 0.95, 0.06, 0.3);
        else if (cs) items.push({t: "quad", x: cs.x, y: cs.y, z: 0.3, w: cs.w + 0.18, h: cs.hh + 0.18, shape: "frame", color: ACCENT, alpha: 0.95});
      }
      // the end
      if (over) {
        var sc = api.common().scores || {}, rs = o.result, bw = Math.min(W - 2 * PADX, 9);
        items.push({t: "quad", x: W / 2, y: rs.y + rs.h / 2, w: bw, h: rs.h, shape: "round", color: "#101317", alpha: 0.85});
        items.push({t: "text", x: W / 2, y: rs.y + 0.45, text: "Game over: " + winnerLine(api.common().status || ""), size: 0.44, color: ACCENT});
        v.counts.forEach(function (cnt, i) {
          var l = api.name(i) + "  " + (sc[i] != null ? (sc[i] > 0 ? "+" : "") + sc[i] : "") + "  (" + plural(cnt, "tile") + " left)";
          items.push({t: "text", x: W / 2, y: rs.y + 0.98 + i * 0.5, text: l, size: 0.32, color: "#ffffff"});
        });
      }
      return {view: view, items: items, bg: "#121519"};
    }
    function winnerLine(st) {
      var m = /seat (\d+) wins/.exec(st);
      if (m) return api.name(+m[1]) + " wins";
      return /draw/i.test(st) ? "A draw" : st;
    }

    // for checks from outside the page (a headless browser): where things are, in board units
    function spot(what) {
      var o = layout(), m;
      buttons(o);
      if ((m = /^rack:(\d+)$/.exec(what))) return o.rack[+m[1]] ? {x: o.rack[+m[1]].x, y: o.rack[+m[1]].y, z: 0.1} : null;
      if ((m = /^tile:(\d+)\.(\d+)$/.exec(what))) { var t = o.tiles.filter(function (q) { return q.where === "t" && q.k === +m[1] && q.p === +m[2]; })[0]; return t ? {x: t.x, y: t.y, z: 0.05} : null; }
      if ((m = /^set:(\d+)$/.exec(what))) { var s = setAt(o, +m[1]); return s ? {x: s.x0 + 0.3, y: s.y - GRIP / 2, z: 0} : null; }
      if (what === "new") return {x: o.newZone.x, y: o.newZone.y, z: 0};
      if ((m = /^btn:(.+)$/.exec(what))) { var b = o.btns.filter(function (q) { return q.id === m[1]; })[0]; return b ? {x: b.x, y: b.y, z: 0.02} : null; }
      // the grid place c, r: where a set's mat is taken (as set:k gives it) to land the set there, and where tiles
      // dropped make a new set there
      if ((m = /^cell:(\d+),(\d+)$/.exec(what))) return {x: PADX + +m[1] * HS + 0.3, y: o.top + +m[2] * RH - GRIP / 2, z: 0};
      return null;
    }
    // each set's place on the grid (columns of half a tile, rows) and in board units, in table order
    function places() {
      var o = layout();
      return o.sets.map(function (s) { return {k: s.k, id: s.id, c: s.c, r: s.r, n: s.n, x0: s.x0, x1: s.x1, y: s.y, tiles: s.s.tiles.join(" ")}; });
    }
    function zoomState() {
      var z = cam();
      return {s: z.s, minS: z.minS, fitS: z.fitS, home: z.home, max: z.max, win: z.win, all: z.all, user: !!zoom, board: {w: L.BW, h: L.H}};
    }
    panel();
    return {view: view, event: event, describe: describe, frame: frame, pointer: pointer, key: key, wheel: wheel, status: status, spot: spot,
            destroy: destroy, places: places, zoomState: zoomState, zoomBy: zoomBy, fit: fit,
            at: function (x, y) { return hit({x: x, y: y}); },
            animating: function () { return animating || !!(L && L.tiles.some(function (t) { return t.anim; })) || !!(drag && drag.moved); },
            paletteChanged: function () { layoutKey = ""; panel(); api.redraw(); },
            rack: function () { return shown(); }, sets: function () { return v.sets; }, turnState: function () { return v.tp; },
            picked: function () { return {rack: picked(), tile: sel.tile, set: sel.set}; }, melded: function () { return v.melded; },
            pool: function () { return v.pool; }, counts: function () { return v.counts; }};
  }

  PN.register("rummytile/1", {name: "Rummy Tile Classic", create: create, paintTile: function (ctx, w, h, t, pal) { paintTile(ctx, w, h, t, pal, false); }});
})();
