// ParlorNet Play: Reversi (reversi/1, docs/types/reversi-1.md). Discs that drop onto the felt and flip over,
// one after another outward from the disc placed; the legal squares marked on your turn.
"use strict";

(function () {
  var COL = {felt: "#1d6b4e", felt2: "#14533b", line: "#0c2f22", frame: "#3a2617", seat: ["#1b1c1f", "#f3efe6"],
             hint: "#f3c56b"};
  var CELL = 1, BORDER = 0.55, R = 0.42, HD = 0.16;

  function ease(t) { return t < 0 ? 0 : t > 1 ? 1 : 1 - Math.pow(1 - t, 3); }

  function create(api) {
    var el = api.el, clear = api.clear;
    var n = 8, cells = [], moves = [], counts = [2, 2], turn = -1, last = null;
    var anim = {};          // "x,y" -> {kind: "drop"|"flip", start, dur, from (seat shown before), axis}
    var hover = null, cursor = null, nextAt = 0;
    function W() { return n * CELL + 2 * BORDER; }
    function sq(x, y) { return String.fromCharCode(97 + x) + (y + 1); }
    function parseSq(s) { return [s.charCodeAt(0) - 97, parseInt(s.slice(1), 10) - 1]; }
    function centre(x, y) { return {x: BORDER + (x + 0.5) * CELL, y: BORDER + (y + 0.5) * CELL}; }

    function view(lines) {
      var rows = [], mv = [];
      lines.forEach(function (l) {
        var f = l.split(" ");
        if (f[0] === "BRD") n = +f[1];
        else if (f[0] === "R") rows[+f[1] - 1] = f[2];
        else if (f[0] === "C") counts = [+f[1], +f[2]];
        else if (f[0] === "W") turn = +f[1];
        else if (f[0] === "M") mv.push(f[1]);
      });
      if (rows.length) {
        cells = rows.map(function (r) { return (r || "").split("").map(function (c) { return c === "." ? -1 : +c; }); });
        n = cells.length;
      }
      moves = mv;
      if (!cursor && moves.length) cursor = parseSq(moves[0]);
      panel();
    }
    function at(x, y) { return cells[y] ? cells[y][x] : -1; }
    function event(text, quiet) {
      var f = text.split(" ");
      if (f[0] !== "PUT") return;
      var seat = +f[1], p = parseSq(f[2]);
      last = p;
      if (quiet || !api.motion()) return;
      var now = performance.now(), delay = Math.max(0, nextAt - now), t0 = now + delay;
      anim[p[0] + "," + p[1]] = {kind: "drop", start: t0, dur: 320, seat: seat};
      var flips = f.slice(f.indexOf("FLP") + 1).map(parseSq), longest = 0;
      flips.forEach(function (q) {
        var dist = Math.max(Math.abs(q[0] - p[0]), Math.abs(q[1] - p[1]));
        var d = 260 + dist * 110;
        longest = Math.max(longest, d + 420);
        anim[q[0] + "," + q[1]] = {kind: "flip", start: t0 + d, dur: 420, from: 1 - seat,
                                   axis: [q[0] - p[0], q[1] - p[1]]};
        setTimeout(function () { api.sound("flip"); }, delay + d + 120);
      });
      nextAt = t0 + Math.max(longest, 400);
      setTimeout(function () { api.sound("place"); }, delay + 260);
      // the board as it was before the move, until the view after it comes: the placed disc and the flips are drawn
      // from the animations
      api.redraw();
    }
    function describe(text) {
      var f = text.split(" ");
      if (f[0] === "PUT") {
        var k = f.length - f.indexOf("FLP") - 1;
        return api.name(+f[1]) + " plays " + f[2] + ", flipping " + k + " disc" + (k === 1 ? "" : "s");
      }
      if (f[0] === "PASS") return api.name(+f[1]) + " has no move and passes";
      if (f[0] === "END") return f[1] === "win" ? "Game over: " + api.name(+f[2]) + " wins, " + f[3] : "Game over: a draw, " + f[2];
      return null;
    }
    function legal(x, y) { return moves.indexOf(sq(x, y)) >= 0; }
    function play(x, y) {
      if (!api.myTurn() || !legal(x, y)) return;
      hover = null;
      api.mov(sq(x, y));
    }
    function cellAt(p) {
      var x = Math.floor((p.x - BORDER) / CELL), y = Math.floor((p.y - BORDER) / CELL);
      return x >= 0 && y >= 0 && x < n && y < n ? [x, y] : null;
    }
    function pointer(kind, p) {
      var c = cellAt(p);
      if (kind === "move" || kind === "leave") {
        var h = kind === "move" && c && api.myTurn() && legal(c[0], c[1]) ? c : null;
        if (String(h) !== String(hover)) { hover = h; api.redraw(); }
        document.getElementById("board").style.cursor = h ? "pointer" : "default";
      } else if (kind === "down" && c) { cursor = c; play(c[0], c[1]); api.redraw(); }
    }
    function key(e) {
      var d = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]}[e.key];
      if (!cursor) cursor = [Math.floor(n / 2), Math.floor(n / 2)];
      if (d) {
        cursor = [Math.max(0, Math.min(n - 1, cursor[0] + d[0])), Math.max(0, Math.min(n - 1, cursor[1] + d[1]))];
        var s = at(cursor[0], cursor[1]);
        api.announce(sq(cursor[0], cursor[1]) + ": " + (s < 0 ? (legal(cursor[0], cursor[1]) ? "empty, a legal move" : "empty") : s === api.seat() ? "your disc" : "opponent's disc"));
        return true;
      }
      if (e.key === "Enter" || e.key === " ") { play(cursor[0], cursor[1]); return true; }
      return false;
    }
    function panel() {
      var p = clear(api.panel), me = api.seat();
      p.appendChild(el("h2", {text: "Reversi"}));
      p.appendChild(el("p", {}, [el("b", {text: api.name(0)}), " (dark) " + counts[0] + ", ", el("b", {text: api.name(1)}), " (light) " + counts[1]]));
      if (me >= 0 && api.myTurn()) {
        p.appendChild(el("p", {cls: "small muted", text: moves.length ? "Click a marked square, or use the arrow keys and Enter on the board. Or pick one:" : "No move: the server passes for you."}));
        var ms = el("div", {cls: "moves", role: "group", "aria-label": "Legal moves"});
        moves.forEach(function (m) {
          var b = el("button", {type: "button", text: m, on: {click: function () { var q = parseSq(m); play(q[0], q[1]); }}});
          b.addEventListener("mouseenter", function () { hover = parseSq(m); api.redraw(); });
          b.addEventListener("focus", function () { hover = parseSq(m); api.redraw(); });
          ms.appendChild(b);
        });
        p.appendChild(ms);
      }
    }
    function paint(ctx, w, h) {
      var k = w / W();
      var g = ctx.createLinearGradient(0, 0, w, h);
      g.addColorStop(0, "#4a3020"); g.addColorStop(1, "#2c1c11");
      ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);
      var b = BORDER * k, s = CELL * k;
      var fg = ctx.createRadialGradient(w / 2, h / 2, s, w / 2, h / 2, w * 0.7);
      fg.addColorStop(0, "#23805c"); fg.addColorStop(1, COL.felt2);
      ctx.fillStyle = fg; ctx.fillRect(b, b, n * s, n * s);
      ctx.globalAlpha = 0.05;
      for (var i = 0; i < 4000; i++) { ctx.fillStyle = i % 2 ? "#fff" : "#000"; ctx.fillRect(b + ((i * 131) % 997) / 997 * n * s, b + ((i * 71) % 991) / 991 * n * s, 1.2, 1.2); }
      ctx.globalAlpha = 1;
      ctx.strokeStyle = COL.line; ctx.lineWidth = Math.max(1, s * 0.03);
      for (i = 0; i <= n; i++) {
        ctx.beginPath(); ctx.moveTo(b + i * s, b); ctx.lineTo(b + i * s, b + n * s); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(b, b + i * s); ctx.lineTo(b + n * s, b + i * s); ctx.stroke();
      }
      if (n === 8) [[2, 2], [6, 2], [2, 6], [6, 6]].forEach(function (d) {
        ctx.fillStyle = COL.line; ctx.beginPath(); ctx.arc(b + d[0] * s, b + d[1] * s, s * 0.07, 0, Math.PI * 2); ctx.fill();
      });
      ctx.fillStyle = "#d9bf8c"; ctx.font = "600 " + Math.round(s * 0.28) + "px " + PNGfx.FONT; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      for (i = 0; i < n; i++) {
        ctx.fillText(String.fromCharCode(97 + i), b + (i + 0.5) * s, b / 2);
        ctx.fillText(String(i + 1), b / 2, b + (i + 0.5) * s);
      }
    }
    var animatingNow = false;
    function frame(now) {
      var items = [{t: "board", w: W(), h: W(), d: 0.45, frame: COL.frame, key: "rv" + n, paint: paint}];
      var mine = api.myTurn(), animating = false;
      if (mine) moves.forEach(function (m) {
        var q = parseSq(m), c = centre(q[0], q[1]), hl = hover && hover[0] === q[0] && hover[1] === q[1];
        items.push({t: "quad", x: c.x, y: c.y, w: hl ? 0.8 : 0.26, h: hl ? 0.8 : 0.26, shape: hl ? "ring" : "dot", color: COL.hint, alpha: hl ? 0.95 : 0.55});
      });
      if (last && cells[last[1]] && at(last[0], last[1]) >= 0) {
        var lc = centre(last[0], last[1]);
        items.push({t: "quad", x: lc.x, y: lc.y, w: 0.96, h: 0.96, shape: "frame", color: "#e3a83c", alpha: 0.7});
      }
      if (cursor && document.activeElement === document.getElementById("board")) {
        var cc = centre(cursor[0], cursor[1]);
        items.push({t: "quad", x: cc.x, y: cc.y, w: 1, h: 1, shape: "frame", color: "#ffd27a", alpha: 0.95});
      }
      for (var y = 0; y < n; y++) for (var x = 0; x < n; x++) {
        var s = at(x, y), a = anim[x + "," + y], c = centre(x, y), z = 0, flip = 0, axis = null;
        if (a) {
          var t = (now - a.start) / a.dur;
          if (t >= 1) delete anim[x + "," + y];
          else {
            animating = true;
            if (a.kind === "drop") {
              if (t < 0) continue;          // not placed yet
              s = a.seat; z = (1 - ease(t)) * 1.6;
            } else {
              // shown with the old color on top, turning over to the new one
              var e = ease(Math.max(0, t));
              s = a.from; flip = e * Math.PI; axis = a.axis;
            }
          }
        }
        if (s < 0) continue;
        items.push({t: "shadow", x: c.x, y: c.y, r: R * (1 + z * 0.2), alpha: 0.7 - Math.min(0.4, z * 0.25)});
        var disc = {t: "disc", x: c.x, y: c.y, z: z, r: R, h: HD, a: COL.seat[s], b: COL.seat[1 - s]};
        if (flip) { disc.flip = flip; disc.axis = axis; }
        items.push(disc);
      }
      animatingNow = animating;
      return {view: {w: W(), h: W(), tilt: 16, pad: 0.2, z: 0.6}, items: items, bg: "#121519"};
    }
    function status() {
      return "Dark " + counts[0] + ", light " + counts[1] + "." + (api.myTurn() && moves.length ? " Legal moves: " + moves.join(", ") + "." : "");
    }
    return {view: view, event: event, describe: describe, frame: frame, pointer: pointer, key: key, status: status,
            animating: function () { return animatingNow || Object.keys(anim).length > 0; },
            seatColour: function (s) { return COL.seat[s]; },
            spot: function (s) { var q = parseSq(s); return centre(q[0], q[1]); },
            cursor: function () { return cursor ? sq(cursor[0], cursor[1]) : null; }};
  }

  PN.register("reversi/1", {name: "Reversi", create: create});
})();
