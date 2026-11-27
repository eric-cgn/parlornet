// ParlorNet Play: Grid Words (gridwords/1, docs/types/gridwords-1.md). The board in the player's colors (Settings,
// docs/design/gridwords-colours.md), the rack under it: drag tiles onto the board, or click a tile and then a square,
// or type on the board. The score of the tiles laid is asked of the server (CHK) as they are laid.
"use strict";

(function () {
  var B = 0.55, CELL = 1, GAP = 0.45, RACK_H = 1.5, TS = 0.9, SLOT = 1.12;
  var LABEL = {TW: "3W", DW: "2W", TL: "3L", DL: "2L"};
  var MEANING = {TW: "triple word", DW: "double word", TL: "triple letter", DL: "double letter"};

  function ease(t) { return t < 0 ? 0 : t > 1 ? 1 : 1 - Math.pow(1 - t, 3); }

  function create(api) {
    var el = api.el, clear = api.clear;
    var size = 15, ctr = [8, 8], prm = {}, til = {}, tiles = {}, rack = null, counts = [], bag = 0, last = null, turn = -1;
    var order = [];             // the rack as shown: [{c, id}] (the player may shuffle and reorder it)
    var pending = [];           // tiles laid this turn, not yet played: {x, y, c (letter), blank, id}
    var drag = null, picked = null, cursor = [8, 8], dir = "H", exch = null, preview = null, chkTimer = null, chkSeq = 0;
    var drops = {};             // "x,y" -> {start, dur}: tiles another player has just played, falling into place
    var nextId = 1;
    function W() { return size * CELL + 2 * B; }
    function rackY() { return W() + GAP + RACK_H / 2; }
    function H() { return W() + GAP + RACK_H + 0.1; }
    function sqName(x, y) { return "abcdefghijklmno".charAt(x - 1) + y; }
    function cell(x, y) { return {x: B + (x - 0.5) * CELL, y: B + (y - 0.5) * CELL}; }
    function slotX(i, n) { return W() / 2 + (i - (n - 1) / 2) * SLOT; }
    function points(c) { return til[c.toUpperCase()] || 0; }     // the table's own values (its TIL lines)
    function pal() { return api.palette(); }
    function key(x, y) { return x + "," + y; }
    function pendingAt(x, y) { for (var i = 0; i < pending.length; i++) if (pending[i].x === x && pending[i].y === y) return pending[i]; return null; }
    function free(x, y) { return x >= 1 && y >= 1 && x <= size && y <= size && !tiles[key(x, y)] && !pendingAt(x, y); }
    function myTurn() { return api.myTurn(); }

    // ---- the view and events -------------------------------------------------------------------------------
    function view(lines) {
      var t = {}, r = null, k = [], l = null, gotX = false, p = null, v = null;    // the geometry comes whole
      lines.forEach(function (line) {
        var f = line.split(" ");
        switch (f[0]) {
          case "BRD": size = +f[1]; break;
          case "CTR": ctr = [+f[1], +f[2]]; break;
          case "PRM": p = p || {}; for (var i = 2; i + 1 < f.length; i += 2) p[key(+f[i], +f[i + 1])] = f[1]; break;
          case "TIL": v = v || {}; v[f[1]] = +f[2]; break;
          case "X": gotX = true; for (var j = 1; j + 2 < f.length; j += 3) t[key(+f[j], +f[j + 1])] = f[j + 2]; break;
          case "R": r = f[1] === "-" ? "" : (f[1] || ""); break;
          case "K": k[+f[1]] = +f[2]; break;
          case "BAG": bag = +f[1]; break;
          case "W": turn = +f[1]; break;
          case "LAST": l = {seat: +f[1], x: +f[2], y: +f[3], d: f[4], word: f[5], pts: +f[6]}; break;
        }
      });
      tiles = t; counts = k; last = l; void gotX;
      if (p) prm = p;
      if (v) til = v;
      if (r !== null) setRack(r);
      // a tile laid where one has now been played goes back to the rack
      pending = pending.filter(function (p) { return !tiles[key(p.x, p.y)]; });
      panel();
      schedulePreview();
    }
    function setRack(r) {
      rack = r;
      // keep the player's order for the letters still there
      var left = r.split(""), out = [];
      order.concat(pending.map(function (p) { return {c: p.blank ? "_" : p.c, id: p.id}; })).forEach(function (o) {
        var i = left.indexOf(o.c);
        if (i >= 0) { left.splice(i, 1); out.push(o); }
      });
      left.forEach(function (c) { out.push({c: c, id: nextId++}); });
      var ids = {};
      out.forEach(function (o) { ids[o.id] = true; });
      pending = pending.filter(function (p) { return ids[p.id]; });
      var inPlay = {};
      pending.forEach(function (p) { inPlay[p.id] = true; });
      order = out.filter(function (o) { return !inPlay[o.id]; });
    }
    function event(text, quiet) {
      var f = text.split(" ");
      if (f[0] === "X" && !quiet && api.motion()) {
        var now = performance.now();
        for (var i = 1, n = 0; i + 2 < f.length; i += 3, n++) {
          drops[key(+f[i], +f[i + 1])] = {start: now + n * 110, dur: 380};
        }
        setTimeout(function () { api.sound("tile"); }, 300);
        api.redraw();
      }
      if (f[0] === "PLAY" && +f[1] === api.seat()) { pending = []; preview = null; }
    }
    function describe(text) {
      var f = text.split(" "), nm = function (s) { return api.name(+s); };
      switch (f[0]) {
        case "PLAY": return nm(f[1]) + " plays " + f[5] + " at " + sqName(+f[2], +f[3]) + (f[4] === "H" ? " across" : " down") + " for " + f[6];
        case "EXCH": return nm(f[1]) + " exchanges " + f[2] + " tile" + (f[2] === "1" ? "" : "s");
        case "PASS": return nm(f[1]) + " passes";
        case "ADJ": return nm(f[1]) + " " + f[2] + (f[3] && f[3] !== "-" ? " (" + f[3] + " left)" : " (went out)");
        case "END": return "Game over: " + (f[1] === "win" ? nm(f[2]) + " wins, " + f[3] : "a draw, " + f[2]);
        case "X": case "DRAW": return null;
      }
      return null;
    }

    // ---- making the move from the tiles laid -----------------------------------------------------------------
    function letterAt(x, y) {
      var t = tiles[key(x, y)];
      if (t) return t.toUpperCase();
      var p = pendingAt(x, y);
      return p ? (p.blank ? "_" + p.c : p.c) : null;
    }
    function build() {
      // -> {tok: "PLAY x y D WORD"} or {err: reason}
      if (!pending.length) return {err: "Lay some tiles first."};
      var xs = pending.map(function (p) { return p.x; }), ys = pending.map(function (p) { return p.y; });
      var d;
      if (pending.length === 1) {
        var p0 = pending[0];
        d = (tiles[key(p0.x - 1, p0.y)] || tiles[key(p0.x + 1, p0.y)]) ? "H" : (tiles[key(p0.x, p0.y - 1)] || tiles[key(p0.x, p0.y + 1)]) ? "V" : dir;
      } else if (ys.every(function (y) { return y === ys[0]; })) d = "H";
      else if (xs.every(function (x) { return x === xs[0]; })) d = "V";
      else return {err: "The tiles must be in one row or one column."};
      var dx = d === "H" ? 1 : 0, dy = d === "V" ? 1 : 0;
      var lo = d === "H" ? Math.min.apply(null, xs) : Math.min.apply(null, ys), hi = d === "H" ? Math.max.apply(null, xs) : Math.max.apply(null, ys);
      var fx = d === "H" ? null : xs[0], fy = d === "H" ? ys[0] : null;
      var at = function (i) { return d === "H" ? letterAt(i, fy) : letterAt(fx, i); };
      while (lo > 1 && at(lo - 1)) lo--;
      while (hi < size && at(hi + 1)) hi++;
      var word = "";
      for (var i = lo; i <= hi; i++) {
        var c = at(i);
        if (!c) return {err: "The word has a gap in it."};
        word += c;
      }
      void dx; void dy;
      var sx = d === "H" ? lo : fx, sy = d === "H" ? fy : lo;
      return {tok: "PLAY " + sx + " " + sy + " " + d + " " + word, d: d};
    }
    function schedulePreview() {
      clearTimeout(chkTimer);
      if (!pending.length || api.seat() < 0) { preview = null; showPreview(); return; }
      var b = build();
      if (b.err) { preview = {bad: true, text: b.err}; showPreview(); return; }
      var mine = ++chkSeq;
      chkTimer = setTimeout(function () {
        api.chk(b.tok).then(function (r) {
          if (mine !== chkSeq) return;
          if (r.final.indexOf("OK") === 0) {
            var f = r.final.split(" ");
            preview = {good: true, text: f[1] + " points: " + f.slice(2).join(", ").toUpperCase(), pts: +f[1]};
          } else preview = {bad: true, text: PNNet.errText(r.final)};
          showPreview(); api.redraw();
        });
      }, 220);
    }
    function play() {
      var b = build();
      if (b.err) { api.toast(b.err); return; }
      api.mov(b.tok).then(function (final) {
        if (final.indexOf("OK") === 0) { api.sound("tile"); pending = []; preview = null; panel(); }
      });
    }
    function recall() { pending.forEach(function (p) { order.push({c: p.blank ? "_" : p.c, id: p.id}); }); pending = []; schedulePreview(); api.redraw(); }
    function shuffle() {
      for (var i = order.length - 1; i > 0; i--) { var j = Math.floor(Math.random() * (i + 1)), t = order[i]; order[i] = order[j]; order[j] = t; }
      api.redraw();
    }
    function lay(tile, x, y) {
      if (!free(x, y)) return Promise.resolve(false);
      var ask = tile.c === "_" ? api.askLetter() : Promise.resolve(null);
      return ask.then(function (letter) {
        if (tile.c === "_" && !letter) return false;
        order = order.filter(function (o) { return o.id !== tile.id; });
        pending.push({x: x, y: y, c: tile.c === "_" ? letter : tile.c, blank: tile.c === "_", id: tile.id});
        api.sound("tile");
        schedulePreview(); panel(); api.redraw();
        return true;
      });
    }
    function unlay(p, at) {
      pending = pending.filter(function (q) { return q !== p; });
      var o = {c: p.blank ? "_" : p.c, id: p.id};
      if (at == null || at > order.length) order.push(o); else order.splice(at, 0, o);
      schedulePreview(); panel(); api.redraw();
    }

    // ---- pointer and keys --------------------------------------------------------------------------------------
    function squareAt(p) {
      var x = Math.floor((p.x - B) / CELL) + 1, y = Math.floor((p.y - B) / CELL) + 1;
      return x >= 1 && y >= 1 && x <= size && y <= size ? [x, y] : null;
    }
    function rackIndexAt(p) {
      if (Math.abs(p.y - rackY()) > RACK_H / 2) return -1;
      var n = order.length;
      for (var i = 0; i < n; i++) if (Math.abs(p.x - slotX(i, n)) < SLOT / 2) return i;
      return -2;            // in the rack, not on a tile
    }
    function insertAt(p) {
      var n = order.length + 1, best = n - 1;
      for (var i = 0; i < n; i++) if (p.x < slotX(i, n)) { best = i; break; }
      return best;
    }
    function pointer(kind, p) {
      var cv = document.getElementById("board");
      if (kind === "down") {
        if (exch) {
          var ei = rackIndexAt(p);
          if (ei >= 0) { var id = order[ei].id; exch[id] = !exch[id]; panel(); api.redraw(); }
          return;
        }
        var ri = rackIndexAt(p);
        if (ri >= 0) { drag = {tile: order[ri], x: p.x, y: p.y, sx: p.x, sy: p.y}; return; }
        var s = squareAt(p);
        if (s) {
          var pd = pendingAt(s[0], s[1]);
          if (pd) { drag = {pend: pd, x: p.x, y: p.y, sx: p.x, sy: p.y}; return; }
          if (picked && free(s[0], s[1])) { var t = picked; picked = null; lay(t, s[0], s[1]); return; }
          if (!tiles[key(s[0], s[1])]) {
            if (cursor[0] === s[0] && cursor[1] === s[1]) dir = dir === "H" ? "V" : "H";
            cursor = s; api.redraw();
          }
        }
      } else if (kind === "move") {
        if (drag) { var hp = p.at(0.6); drag.x = hp.x; drag.y = hp.y; api.redraw(); }
        var over = rackIndexAt(p) >= 0 || (squareAt(p) && pendingAt(squareAt(p)[0], squareAt(p)[1]));
        cv.style.cursor = drag ? "grabbing" : over ? "grab" : "default";
      } else if (kind === "up" && drag) {
        var moved = Math.hypot(p.x - drag.sx, p.y - drag.sy) > 0.25, d = drag;
        drag = null;
        var sq = squareAt(p.at ? p.at(0.6) : p);     // the square under the tile held up
        if (d.tile) {
          if (!moved) { picked = picked && picked.id === d.tile.id ? null : d.tile; api.announce(picked ? "Picked " + (picked.c === "_" ? "the blank" : picked.c) + ": click a square" : "Put back"); api.redraw(); return; }
          if (sq && free(sq[0], sq[1])) { lay(d.tile, sq[0], sq[1]); return; }
          if (rackIndexAt(p) !== -1) { // reorder within the rack
            order = order.filter(function (o) { return o.id !== d.tile.id; });
            order.splice(insertAt(p), 0, d.tile);
          }
          api.redraw();
        } else if (d.pend) {
          if (!moved) { unlay(d.pend); return; }
          if (sq && free(sq[0], sq[1])) { d.pend.x = sq[0]; d.pend.y = sq[1]; schedulePreview(); api.redraw(); return; }
          if (rackIndexAt(p) !== -1) { unlay(d.pend, insertAt(p)); return; }
          api.redraw();
        }
      } else if (kind === "leave") { cv.style.cursor = "default"; }
    }
    function step(x, y, back) {
      var dx = dir === "H" ? 1 : 0, dy = dir === "V" ? 1 : 0, s = back ? -1 : 1;
      do { x += dx * s; y += dy * s; } while (x >= 1 && y >= 1 && x <= size && y <= size && tiles[key(x, y)]);
      return [Math.max(1, Math.min(size, x)), Math.max(1, Math.min(size, y))];
    }
    function keyDown(e) {
      var mv = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]}[e.key];
      if (mv) {
        cursor = [Math.max(1, Math.min(size, cursor[0] + mv[0])), Math.max(1, Math.min(size, cursor[1] + mv[1]))];
        var t = tiles[key(cursor[0], cursor[1])], kind = prm[key(cursor[0], cursor[1])];
        api.announce(sqName(cursor[0], cursor[1]) + ": " + (t ? t.toUpperCase() : pendingAt(cursor[0], cursor[1]) ? "your " + pendingAt(cursor[0], cursor[1]).c : kind ? MEANING[kind] : "empty"));
        return true;
      }
      if (e.key === " ") { dir = dir === "H" ? "V" : "H"; api.announce(dir === "H" ? "Across" : "Down"); return true; }
      if (e.key === "Enter") { if (pending.length && myTurn()) play(); else { dir = dir === "H" ? "V" : "H"; api.announce(dir === "H" ? "Across" : "Down"); } return true; }
      if (e.key === "Escape") { recall(); return true; }
      if (e.key === "Backspace") {
        var lastP = pending[pending.length - 1];
        if (lastP) { cursor = [lastP.x, lastP.y]; unlay(lastP); }
        return true;
      }
      if (/^[a-zA-Z]$/.test(e.key) && !e.ctrlKey && !e.metaKey && !e.altKey) {
        var c = e.key.toUpperCase(), x = cursor[0], y = cursor[1];
        if (!free(x, y)) { var n = step(x, y); x = n[0]; y = n[1]; if (!free(x, y)) return true; }
        var tile = order.filter(function (o) { return o.c === c; })[0];
        var blank = !tile && order.filter(function (o) { return o.c === "_"; })[0];
        if (!tile && !blank) { api.toast("No " + c + " on your rack."); return true; }
        var use = tile || blank;
        order = order.filter(function (o) { return o.id !== use.id; });
        pending.push({x: x, y: y, c: c, blank: !tile, id: use.id});
        api.sound("tile");
        cursor = step(x, y);
        schedulePreview(); panel();
        return true;
      }
      return false;
    }

    // ---- the panel --------------------------------------------------------------------------------------------
    var prevEl = null;
    function showPreview() {
      if (!prevEl) return;
      prevEl.textContent = preview ? preview.text : (pending.length ? "" : "");
      prevEl.className = "score-preview" + (preview ? (preview.good ? " good" : " bad") : "");
    }
    function panel() {
      var p = clear(api.panel), c = pal(), mine = myTurn(), me = api.seat();
      p.appendChild(el("h2", {text: "Grid Words"}));
      p.appendChild(el("p", {cls: "small", text: "Tiles in the bag: " + bag + "." + (last ? " Last: " + last.word + " for " + last.pts + "." : "")}));
      if (me >= 0) {
        if (exch) {
          var n = Object.keys(exch).filter(function (k) { return exch[k]; }).length;
          p.appendChild(el("p", {text: "Click the tiles to put back, then Exchange."}));
          var er = el("div", {cls: "panel-buttons"});
          er.appendChild(el("button", {type: "button", cls: "primary", text: "Exchange " + n, disabled: !n || !mine, on: {click: function () {
            var letters = order.filter(function (o) { return exch[o.id]; }).map(function (o) { return o.c; }).join("");
            exch = null;
            api.mov("EXCH " + letters).then(function () { panel(); api.redraw(); });
          }}}));
          er.appendChild(el("button", {type: "button", text: "Cancel", on: {click: function () { exch = null; panel(); api.redraw(); }}}));
          p.appendChild(er);
        } else {
          p.appendChild(el("p", {cls: "small muted", text: mine ? "Drag tiles from your rack to the board, or click a tile and then a square, or click a square and type. Space turns across or down; Enter plays." : "You can lay tiles and see their score while you wait."}));
          prevEl = el("p", {cls: "score-preview", "aria-live": "polite"});
          p.appendChild(prevEl);
          showPreview();
          var row = el("div", {cls: "panel-buttons"});
          row.appendChild(el("button", {type: "button", cls: "primary", text: "Play", disabled: !(mine && pending.length), on: {click: play}}));
          row.appendChild(el("button", {type: "button", text: "Recall", disabled: !pending.length, on: {click: recall}}));
          row.appendChild(el("button", {type: "button", text: "Shuffle", on: {click: shuffle}}));
          p.appendChild(row);
          var row2 = el("div", {cls: "panel-buttons"});
          row2.appendChild(el("button", {type: "button", text: "Exchange...", disabled: !(mine && bag >= 7), on: {click: function () { recall(); exch = {}; panel(); api.redraw(); }}}));
          row2.appendChild(el("button", {type: "button", text: "Pass", disabled: !mine, on: {click: function () { api.mov("PASS"); }}}));
          row2.style.marginTop = "8px";
          p.appendChild(row2);
          // the play typed, for keyboards and screen readers
          var f = el("form", {cls: "row", on: {submit: function (e) {
            e.preventDefault();
            var m = /^\s*([a-oA-O])(\d{1,2})\s+(a|across|h|d|down|v)\s+([A-Za-z_]{1,30})\s*$/.exec(inp.value);
            if (!m) { api.toast("Type a start square, a or d, and the word: h8 a HELLO (a blank is _ before its letter)."); return; }
            var x = m[1].toLowerCase().charCodeAt(0) - 96, y = +m[2], d = /^(d|down|v)$/i.test(m[3]) ? "V" : "H";
            api.mov("PLAY " + x + " " + y + " " + d + " " + m[4].toUpperCase().replace(/_([A-Z])/g, "_$1"));
          }}}, []);
          var inp = el("input", {"aria-label": "Type a play: the start square, a or d, and the whole word", placeholder: "h8 a WORD"});
          f.appendChild(inp); f.appendChild(el("button", {type: "submit", text: "Play typed", disabled: !mine}));
          var det = el("details", {}, [el("summary", {text: "Type a play"}), f]);
          det.style.marginTop = "8px";
          p.appendChild(det);
        }
      }
      var lg = el("div", {cls: "legend", role: "list", "aria-label": "Premium squares"});
      ["TW", "DW", "TL", "DL"].forEach(function (k) {
        var i = el("i", {cls: k.charAt(1) === "W" ? "word" : "", "aria-hidden": "true", text: LABEL[k]});
        i.style.background = c[k.toLowerCase()]; i.style.color = c["label_" + k.toLowerCase()];
        lg.appendChild(el("span", {role: "listitem"}, [i, LABEL[k] + " " + MEANING[k]]));
      });
      p.appendChild(lg);
      p.appendChild(el("p", {cls: "small muted"}, ["Colors: ", el("a", {href: "#", text: "Settings", on: {click: function (e) { e.preventDefault(); document.getElementById("b-settings").click(); }}}), "."]));
    }

    // ---- drawing -----------------------------------------------------------------------------------------------
    function paint(ctx, w, h) {
      var c = pal(), k = w / W(), s = CELL * k, b = B * k;
      ctx.fillStyle = PNGfx.shade(c.board, -0.25); ctx.fillRect(0, 0, w, h);
      ctx.fillStyle = c.grid; ctx.fillRect(b - 2, b - 2, size * s + 4, size * s + 4);
      for (var y = 1; y <= size; y++) for (var x = 1; x <= size; x++) {
        var kind = prm[key(x, y)], X = b + (x - 1) * s, Y = b + (y - 1) * s, inset = Math.max(1, s * 0.035);
        var col = kind ? c[kind.toLowerCase()] : c.board;
        ctx.fillStyle = col; ctx.fillRect(X + inset, Y + inset, s - 2 * inset, s - 2 * inset);
        var isCtr = x === ctr[0] && y === ctr[1];
        if (kind) {
          var ink = c["label_" + kind.toLowerCase()];
          if (kind.charAt(1) === "W") { ctx.strokeStyle = ink; ctx.globalAlpha = 0.7; ctx.lineWidth = Math.max(1, s * 0.04); ctx.strokeRect(X + s * 0.12, Y + s * 0.12, s * 0.76, s * 0.76); ctx.globalAlpha = 1; }
          if (isCtr) {
            ctx.fillStyle = ink; ctx.beginPath();
            for (var i = 0; i < 10; i++) { var a = -Math.PI / 2 + i * Math.PI / 5, r = i % 2 ? s * 0.13 : s * 0.3; ctx.lineTo(X + s / 2 + r * Math.cos(a), Y + s / 2 + r * Math.sin(a)); }
            ctx.closePath(); ctx.fill();
          } else {
            ctx.fillStyle = ink; ctx.font = "700 " + Math.round(s * 0.34) + "px " + PNGfx.FONT; ctx.textAlign = "center"; ctx.textBaseline = "middle";
            ctx.fillText(LABEL[kind], X + s / 2, Y + s * 0.53);
          }
        }
      }
      ctx.fillStyle = PNGfx.shade(c.board, lumOf(c.board) < 0.3 ? 0.65 : -0.6);
      ctx.font = "600 " + Math.round(b * 0.5) + "px " + PNGfx.FONT; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      for (i = 1; i <= size; i++) { ctx.fillText("abcdefghijklmno".charAt(i - 1), b + (i - 0.5) * s, b / 2); ctx.fillText(String(i), b / 2, b + (i - 0.5) * s); }
    }
    function lumOf(hex) { var v = PNGfx.hex(hex); return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; }
    function rackPaint(ctx, w, h) {
      var g = ctx.createLinearGradient(0, 0, 0, h); g.addColorStop(0, "#3a3f47"); g.addColorStop(1, "#23272d");
      ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);
    }
    function tileItem(x, y, z, c, blank, extra) {
      var p = pal(), it = {t: "tile", x: x, y: y, z: z || 0, w: TS, h: TS, d: 0.16, color: p.tile,
                           ink: blank ? p.blank : p.letter, text: c === "_" ? "" : c.toUpperCase(), sub: blank || c === "_" ? null : String(points(c)), subInk: p.points};
      Object.keys(extra || {}).forEach(function (k) { it[k] = extra[k]; });
      return it;
    }
    var animatingNow = false;
    function frame(now) {
      var p = pal(), items = [{t: "board", w: W(), h: W(), d: 0.4, frame: PNGfx.shade(p.board, -0.4), key: "gw" + size + JSON.stringify(p) + Object.keys(prm).length, paint: paint}];
      var animating = false;
      Object.keys(tiles).forEach(function (k2) {
        var xy = k2.split(","), c = cell(+xy[0], +xy[1]), ch = tiles[k2], z = 0, dr = drops[k2];
        if (dr) {
          var t = (now - dr.start) / dr.dur;
          if (t >= 1) delete drops[k2]; else { animating = true; if (t < 0) return; z = (1 - ease(t)) * 2.4; }
        }
        items.push({t: "shadow", x: c.x, y: c.y, r: 0.5, alpha: 0.5});
        items.push(tileItem(c.x, c.y, z, ch, ch !== ch.toUpperCase()));
      });
      if (last) {
        for (var i = 0; i < last.word.length; i++) {
          var lx = last.x + (last.d === "H" ? i : 0), ly = last.y + (last.d === "V" ? i : 0), lc = cell(lx, ly);
          items.push({t: "quad", x: lc.x, y: lc.y, w: 1, h: 1, shape: "frame", color: "#e3a83c", alpha: 0.55});
        }
      }
      pending.forEach(function (pd) {
        if (drag && drag.pend === pd) return;
        var c = cell(pd.x, pd.y);
        items.push({t: "quad", x: c.x, y: c.y, w: 1.02, h: 1.02, shape: "round", color: preview && preview.bad ? "#ea6d62" : "#ffd27a", alpha: 0.75});
        items.push(tileItem(c.x, c.y, 0.04, pd.c, pd.blank));
      });
      // the cursor and the direction typing goes
      var focused = document.activeElement === document.getElementById("board");
      if (focused || (api.seat() >= 0 && !pending.length && myTurn())) {
        var cc = cell(cursor[0], cursor[1]);
        items.push({t: "quad", x: cc.x, y: cc.y, w: 1.0, h: 1.0, shape: "frame", color: "#ffd27a", alpha: focused ? 0.95 : 0.45});
        items.push({t: "text", x: cc.x + (dir === "H" ? 0.36 : 0), y: cc.y + (dir === "V" ? 0.36 : 0), z: 0.2, text: dir === "H" ? ">" : "v", size: 0.34, color: "#ffd27a"});
      }
      // the rack
      if (api.seat() >= 0 && rack !== null) {
        items.push({t: "box", x: W() / 2, y: rackY(), w: Math.max(7, order.length + pending.length) * SLOT + 0.5, h: RACK_H, d: 0.12, r: 0.12, color: "#2b3037", spec: 0.05});
        var n = order.length;
        order.forEach(function (o, i) {
          if (drag && drag.tile === o && Math.hypot(drag.x - drag.sx, drag.y - drag.sy) > 0.25) return;
          var x = slotX(i, n), y = rackY(), z = 0.12, lift = 0;
          if (picked && picked.id === o.id) lift = 0.25;
          if (exch && exch[o.id]) { y -= 0.35; lift = 0.15; }
          items.push({t: "shadow", x: x, y: y, z: 0.12, r: 0.48, alpha: 0.5});
          items.push(tileItem(x, y, z, o.c, false, {lift: lift}));
          if (picked && picked.id === o.id) items.push({t: "quad", x: x, y: y, z: 0.13, w: 1.08, h: 1.08, shape: "round", color: "#ffd27a", alpha: 0.5});
        });
      }
      if (drag && Math.hypot(drag.x - drag.sx, drag.y - drag.sy) > 0.25) {
        var dt = drag.tile || {c: drag.pend.blank ? "_" : drag.pend.c};
        var sq = squareAt(drag);
        if (sq && free(sq[0], sq[1])) { var hc = cell(sq[0], sq[1]); items.push({t: "quad", x: hc.x, y: hc.y, w: 1, h: 1, shape: "round", color: "#ffd27a", alpha: 0.45}); }
        items.push({t: "shadow", x: drag.x + 0.25, y: drag.y + 0.3, r: 0.55, alpha: 0.45});
        items.push(tileItem(drag.x, drag.y, 0.6, drag.pend ? drag.pend.c : dt.c, drag.pend ? drag.pend.blank : false, {rot: -0.08}));
      }
      if (preview && preview.good && pending.length) {
        var lp = pending[pending.length - 1], pc = cell(lp.x, lp.y);
        items.push({t: "quad", x: pc.x + 0.5, y: pc.y - 0.5, z: 0.3, w: 0.78, h: 0.5, shape: "round", color: "#1d6b4e", alpha: 0.95});
        items.push({t: "text", x: pc.x + 0.5, y: pc.y - 0.5, z: 0.31, text: String(preview.pts), size: 0.36, color: "#ffffff"});
      }
      animatingNow = animating;
      return {view: {w: W(), h: H(), tilt: 7, pad: 0.15, z: 0.3}, items: items, bg: "#121519"};
    }
    function status() {
      var r = order.map(function (o) { return o.c === "_" ? "blank" : o.c; }).join(" ");
      return (rack !== null ? "Your rack: " + r + ". " : "") + "Bag " + bag + "." + (last ? " Last play " + last.word + " for " + last.pts + "." : "");
    }
    function spot(what) {
      // checks from outside the page: "rack:N" (the N-th tile on the rack) or a square name like "h8"
      if (what.indexOf("rack:") === 0) { var i = +what.slice(5); return {x: slotX(i, order.length), y: rackY(), z: 0.2}; }
      var x = what.charCodeAt(0) - 96, y = +what.slice(1);
      return cell(x, y);
    }
    return {view: view, event: event, describe: describe, frame: frame, pointer: pointer, key: keyDown, status: status,
            spot: spot, animating: function () { return animatingNow || Object.keys(drops).length > 0; },
            paletteChanged: function () { panel(); api.redraw(); },
            rack: function () { return order.map(function (o) { return o.c; }).join(""); },
            pending: function () { return pending.length; },
            board: function () { return tiles; }, preview: function () { return preview; }};
  }

  PN.register("gridwords/1", {name: "Grid Words", create: create});
})();
