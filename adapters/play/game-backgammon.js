// ParlorNet Play: Backgammon (backgammon/1, docs/types/backgammon-1.md). A wooden board with felt, checkers that
// slide along an arc, dice that tumble, and the doubling cube. Your home board is at the bottom right, or at the
// bottom left when the player chose that side (PRF bghome left): then the drawing and the pointer are mirrored
// left to right, and everything else (the moves, the keys, the panel) stays the same.
"use strict";

(function () {
  var W = 16, H = 11.6, FIELD_L = 0.5, BAR_L = 6.5, BAR_R = 7.6, TRAY_L = 13.85, TRAY_R = 15.45;
  var TOP = 0.5, BOT = 11.1, PLEN = 4.6, R = 0.46, CH = 0.2;
  var COL = {felt: "#1f5a47", pointA: "#eadcc0", pointB: "#183a2f", wood: "#5b3b23", wood2: "#6d4a2d",
             tray: "#163d31", seat: ["#f4efe5", "#b3261e"], rim: ["#cfc6b4", "#7e1a14"], label: "#e6c98f"};
  var CUBE_FACE = {1: 1, 64: 1, 2: 2, 4: 3, 32: 4, 16: 5, 8: 6};

  function ease(t) { return t < 0 ? 0 : t > 1 ? 1 : 1 - Math.pow(1 - t, 3); }

  function create(api) {
    var el = api.el, clear = api.clear;
    var v = {pts: [], bar: [0, 0], off: [0, 0], dice: [], rolled: null, cube: 1, owner: -1, turn: -1, phase: "",
             moves: [], need: 0, cubeOk: [false, false], match: 0, score: [0, 0], game: 1, offer: null};
    for (var i = 0; i < 24; i++) v.pts.push(0);
    var model = null;               // the board as shown: changed by events at once, then by each view
    var arrivals = {};              // stack key -> {x, y, z, start, dur} the top checker flies in from
    var dice = {start: 0, dur: 0, seat: 0, vals: [], from: []};
    var sel = null, drag = null, hover = null, kbd = null, done = false, nextAt = 0;
    var flipView = function () { return api.seat() === 1; };
    var mirror = function () { return api.bghome && api.bghome() === "left"; };

    // ---- where things are, in board units ---------------------------------------------------------------
    function disp(p) { return flipView() ? 25 - p : p; }
    function column(p) { var d = disp(p); return d <= 12 ? 12 - d : d - 13; }
    function colX(c) { return c < 6 ? FIELD_L + c + 0.5 : BAR_R + (c - 6) + 0.5; }
    function isTop(p) { return disp(p) >= 13; }
    // A point or the bar squeezes a tall stack closer together, down to half a checker apart (MAX checkers);
    // past that it shows MAX - 1 and +n in the last place for the rest, as the other clients do.
    var MAX = 9;
    function shown(key, n) { return key.indexOf("off") === 0 || n <= MAX ? n : MAX - 1; }
    function spacing(n) { n = Math.min(n, MAX); return n <= 5 ? 2 * R : Math.min(2 * R, (PLEN - 2 * R) / (n - 1)); }
    function stackPos(key, i, n) {
      // the i-th place (0 at the edge) of a stack of n
      if (key.charAt(0) === "p") {
        var p = +key.slice(1), x = colX(column(p)), s = spacing(n);
        i = Math.min(i, MAX - 1);
        return isTop(p) ? {x: x, y: TOP + R + i * s, z: 0} : {x: x, y: BOT - R - i * s, z: 0};
      }
      if (key.indexOf("bar") === 0) {
        var mine = +key.slice(3) === viewer();
        var bs = Math.min(2 * R * 0.95, 4.2 / Math.max(1, Math.min(n, MAX)));
        i = Math.min(i, MAX - 1);
        return {x: (BAR_L + BAR_R) / 2, y: mine ? 4.7 - R - i * bs : 6.9 + R + i * bs, z: 0.12};
      }
      var own = +key.slice(3) === viewer();       // borne off: slabs in the tray
      return {x: (TRAY_L + TRAY_R) / 2, y: own ? BOT - 0.2 - i * 0.29 : TOP + 0.2 + i * 0.29, z: 0, slab: true};
    }
    function viewer() { return flipView() ? 1 : 0; }
    function stacks(m) {
      var out = [];
      for (var p = 1; p <= 24; p++) {
        var n = m.pts[p - 1];
        if (n) out.push({key: "p" + p, seat: n > 0 ? 0 : 1, n: Math.abs(n)});
      }
      [0, 1].forEach(function (s) {
        if (m.bar[s]) out.push({key: "bar" + s, seat: s, n: m.bar[s]});
        if (m.off[s]) out.push({key: "off" + s, seat: s, n: m.off[s]});
      });
      return out;
    }
    function count(m, key) {
      if (key.charAt(0) === "p") return Math.abs(m.pts[+key.slice(1) - 1]);
      return key.indexOf("bar") === 0 ? m.bar[+key.slice(3)] : m.off[+key.slice(3)];
    }
    function topPos(m, key) { var n = count(m, key); return stackPos(key, Math.max(0, shown(key, n) - 1), Math.max(1, n)); }
    function keyOf(seat, where) {
      if (where === "bar") return "bar" + seat;
      if (where === "off") return "off" + seat;
      return "p" + where;
    }
    function copy(m) { return {pts: m.pts.slice(), bar: m.bar.slice(), off: m.off.slice()}; }
    function take(m, key, seat) {
      if (key.charAt(0) === "p") { var i = +key.slice(1) - 1; m.pts[i] -= seat === 0 ? 1 : -1; }
      else if (key.indexOf("bar") === 0) m.bar[seat]--; else m.off[seat]--;
    }
    function put(m, key, seat) {
      if (key.charAt(0) === "p") { var i = +key.slice(1) - 1; m.pts[i] += seat === 0 ? 1 : -1; }
      else if (key.indexOf("bar") === 0) m.bar[seat]++; else m.off[seat]++;
    }
    // A checker moves: from the top of one stack to the top of another, flying along an arc.
    function fly(seat, fromKey, toKey, delay) {
      if (!model) return;
      var a = topPos(model, fromKey);
      take(model, fromKey, seat);
      put(model, toKey, seat);
      if (!api.motion()) return;
      arrivals[toKey] = {x: a.x, y: a.y, start: performance.now() + (delay || 0), dur: 420};
    }

    // ---- the view and events -----------------------------------------------------------------------------
    function view(lines, common) {
      var nv = {pts: v.pts.slice(), bar: [0, 0], off: [0, 0], dice: [], rolled: null, cube: 1, owner: -1, turn: -1, phase: "",
                moves: [], need: 0, cubeOk: [false, false], match: 0, score: [0, 0], game: 1, offer: null, started: false};
      lines.forEach(function (l) {
        var f = l.split(" ");
        switch (f[0]) {
          case "P": for (var i = 2; i < f.length; i++) nv.pts[+f[1] - 1 + i - 2] = +f[i]; nv.started = true; break;
          case "B": nv.bar = [+f[1], +f[2]]; break;
          case "O": nv.off = [+f[1], +f[2]]; break;
          case "D": nv.dice = f.slice(1).map(Number); break;
          case "DR": nv.rolled = [+f[1], +f[2]]; break;
          case "C": nv.cube = +f[1]; nv.owner = f[2] === "-" ? -1 : +f[2]; break;
          case "W": nv.turn = f[1] === "-" ? -1 : +f[1]; break;
          case "Q": nv.phase = f[1]; break;
          case "M": nv.moves.push(f[1]); break;
          case "N": nv.need = +f[1]; break;
          case "CA": nv.cubeOk = [f[1] === "1", f[2] === "1"]; break;
          case "L": nv.match = +f[1]; break;
          case "T": if (f.length === 3) nv.score[+f[1]] = +f[2]; break;
          case "G": nv.game = +f[1]; nv.crawford = f.indexOf("crawford") > 1; break;
          case "RS": nv.offer = {seat: +f[1], points: +f[2], kind: f[3]}; break;
        }
      });
      if (!nv.started) nv.pts = v.pts;
      var newTurn = nv.turn !== v.turn || nv.phase !== v.phase;
      v = nv;
      model = copy(v);
      if (newTurn) { sel = null; kbd = null; }
      if (!(v.phase === "move" && api.myTurn())) done = false;
      if (v.phase === "move" && api.myTurn() && !v.moves.length && v.dice.length === 0) done = true;
      if (!dice.vals.length && v.rolled) dice = {start: 0, dur: 0, seat: v.turn, vals: v.rolled.slice(), from: []};
      panel();
    }
    function event(text, quiet) {
      var f = text.split(" "), now = performance.now();
      if (f[0] === "ROLL" || f[0] === "OPEN") {
        var seat = f[0] === "OPEN" ? -1 : +f[1], vals = f[0] === "OPEN" ? [+f[1], +f[2]] : [+f[2], +f[3]];
        dice = {start: now, dur: quiet || !api.motion() ? 0 : 950, seat: seat, vals: vals, spin: [Math.random() * 6, Math.random() * 6],
                axis: [[Math.random() - 0.5, Math.random() - 0.5, Math.random()], [Math.random() - 0.5, Math.random() - 0.5, Math.random()]]};
        if (!quiet) api.sound("dice");
      } else if ((f[0] === "STEP" || f[0] === "UNDO") && !quiet) {
        // steps that come together are played one after another
        var s = +f[1], delay = Math.max(0, nextAt - now);
        nextAt = now + delay + 460;
        if (f[0] === "UNDO") fly(s, keyOf(s, f[3]), keyOf(s, f[2]), delay);
        else {
          if (f[4] === "HIT") fly(1 - s, keyOf(1 - s, f[3]), "bar" + (1 - s), delay + 250);
          fly(s, keyOf(s, f[2]), keyOf(s, f[3]), delay);
        }
        setTimeout(function () { api.sound("place"); }, delay + 380);
      }
      api.redraw();
    }
    function describe(text) {
      var f = text.split(" "), nm = function (s) { return api.name(+s); };
      var w = function (x) { return x === "bar" ? "the bar" : x === "off" ? "off" : x; };
      switch (f[0]) {
        case "OPEN": return "Opening roll: " + nm(0) + " " + f[1] + ", " + nm(1) + " " + f[2];
        case "ROLL": return nm(f[1]) + " rolls " + f[2] + " and " + f[3];
        case "STEP": return nm(f[1]) + " moves " + w(f[2]) + " to " + w(f[3]) + (f[4] === "HIT" ? ", a hit" : "");
        case "UNDO": return nm(f[1]) + " takes back " + w(f[2]) + " to " + w(f[3]);
        case "PASS": return nm(f[1]) + " cannot move";
        case "DBL": return nm(f[1]) + " doubles to " + f[2];
        case "TAKE": return nm(f[1]) + " takes; the cube is at " + f[2];
        case "DROP": return nm(f[1]) + " drops";
        case "OFFER": return nm(f[1]) + " offers to resign (" + f[3] + ", " + f[2] + " point" + (f[2] === "1" ? "" : "s") + ")";
        case "ACCEPT": return nm(f[1]) + " accepts the resignation";
        case "REJECT": return nm(f[1]) + " rejects the resignation";
        case "WON": return nm(f[1]) + " wins the game, " + f[2] + " point" + (f[2] === "1" ? "" : "s") + " (" + f[3] + ")";
        case "SCORE": return "Match score: " + f[1] + " to " + f[2];
        case "GAME": return "Game " + f[1] + (f[2] ? " (Crawford)" : "");
        case "STEPS": return null;
        case "END": return f[1] === "win" ? "Game over: " + nm(f[2]) + " wins " + f[3] + " point" + (f[3] === "1" ? "" : "s") + (f[4] && f[4] !== "single" ? " (" + f[4] + ")" : "") : null;
      }
      return null;
    }

    // ---- moving -------------------------------------------------------------------------------------------
    function movesFrom(where) { return v.moves.filter(function (m) { return m.split("-")[0] === where; }); }
    function canMove() { return api.myTurn() && v.phase === "move" && v.moves.length > 0; }
    function play(tok) {
      sel = null; kbd = null; drag = null;
      return api.mov(tok).then(function (final) {
        if (final === "OK done") { done = true; panel(); api.announce("All steps played: confirm the move, or undo."); }
      });
    }
    function sourceAt(p) {
      // the stack the pointer is on: "bar", a point number as text, or null
      if (p.x > BAR_L && p.x < BAR_R && p.y > TOP && p.y < BOT) return "bar";
      var c = null;
      if (p.x >= FIELD_L && p.x < BAR_L) c = Math.floor(p.x - FIELD_L);
      else if (p.x >= BAR_R && p.x < BAR_R + 6) c = 6 + Math.floor(p.x - BAR_R);
      if (c === null || p.y < TOP || p.y > BOT) return null;
      var top = p.y < H / 2, d = top ? 13 + c : 12 - c;
      return String(flipView() ? 25 - d : d);
    }
    function targetAt(p) {
      if (p.x >= TRAY_L - 0.1 && p.x <= TRAY_R + 0.2) return "off";
      var s = sourceAt(p);
      return s === "bar" ? null : s;
    }
    function pointer(kind, p0) {
      // the board's own coordinates: the pointer mirrored back when the drawing is
      var p = p0;
      if (mirror()) p = {x: W - p0.x, y: p0.y, at: p0.at && function (z) { var q = p0.at(z); return {x: W - q.x, y: q.y}; }};
      var here = sourceAt(p);
      if (kind === "move") {
        var h = canMove() && here && movesFrom(here).length ? here : null;
        if (drag) { var hp = p.at ? p.at(0.7) : p; drag.x = hp.x; drag.y = hp.y; api.redraw(); }
        if (h !== hover) { hover = h; api.redraw(); }
        document.getElementById("board").style.cursor = drag ? "grabbing" : (h || (sel && targetAt(p))) ? "pointer" : "default";
        return;
      }
      if (!canMove()) return;
      if (kind === "down") {
        if (sel) {
          var t = targetAt(p), tok = sel + "-" + t;
          if (t && v.moves.indexOf(tok) >= 0) { play(tok); return; }
        }
        if (here && movesFrom(here).length) {
          var ms = movesFrom(here);
          sel = here; drag = {from: here, x: p.x, y: p.y, moved: false, sx: p.x, sy: p.y};
          api.redraw();
          void ms;
        } else { sel = null; api.redraw(); }
      } else if (kind === "up" && drag) {
        // where the checker held up is: the point under it
        var moved = Math.hypot(p.x - drag.sx, p.y - drag.sy) > 0.4, t2 = targetAt(p.at ? p.at(0.7) : p), tok2 = drag.from + "-" + t2;
        drag = null;
        if (moved && t2 && v.moves.indexOf(tok2) >= 0) { play(tok2); return; }
        if (!moved) {
          var only = movesFrom(sel || "");
          // a single click on a checker with one way to go plays it
          if (only.length === 1 && sel) { play(only[0]); return; }
        }
        api.redraw();
      } else if (kind === "leave") { hover = null; api.redraw(); }
    }
    function key(e) {
      if (e.key === "r" && api.myTurn() && v.phase === "roll") { cmd("roll"); return true; }
      if (e.key === "u" && api.myTurn() && v.phase === "move") { api.und(); done = false; return true; }
      if (e.key === "Enter" && done) { confirm(); return true; }
      if (!canMove()) return false;
      var srcs = [], seen = {};
      v.moves.forEach(function (m) { var s = m.split("-")[0]; if (!seen[s]) { seen[s] = 1; srcs.push(s); } });
      if (e.key === "ArrowLeft" || e.key === "ArrowRight" || e.key === "ArrowUp" || e.key === "ArrowDown") {
        var dir = e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 1;
        if (!sel) { kbd = srcs[(srcs.indexOf(kbd) + dir + srcs.length) % srcs.length] || srcs[0]; api.announce("From " + kbd + ": " + movesFrom(kbd).join(", ")); }
        else { var ts = movesFrom(sel); kbd = ts[(ts.indexOf(kbd) + dir + ts.length) % ts.length] || ts[0]; api.announce("Move " + kbd); }
        return true;
      }
      if (e.key === "Enter" || e.key === " ") {
        if (!sel && kbd) { sel = kbd; kbd = movesFrom(sel)[0]; api.announce("Selected " + sel + ". Arrows choose where; Enter moves. " + kbd); return true; }
        if (sel && kbd) { play(kbd); return true; }
      }
      if (e.key === "Escape" && sel) { sel = null; kbd = null; return true; }
      return false;
    }
    function cmd(tok) { return api.mov(tok); }
    function confirm() { done = false; return api.end(); }

    // ---- the panel beside the board -------------------------------------------------------------------------
    function panel() {
      var p = clear(api.panel), mine = api.myTurn(), me = api.seat(), opp = 1 - (me < 0 ? 0 : me);
      p.appendChild(el("h2", {text: "Backgammon"}));
      if (v.match) p.appendChild(el("p", {cls: "small", text: "Match to " + v.match + ": " + api.name(0) + " " + v.score[0] + ", " + api.name(1) + " " + v.score[1] + ". Game " + v.game + (v.crawford ? " (Crawford: no doubling)" : "") + "."}));
      var info = [];
      if (v.dice.length) info.push("Dice to play: " + v.dice.join(", ") + ".");
      if (v.cube > 1) info.push("Cube at " + v.cube + (v.owner >= 0 ? ", " + (v.owner === me ? "yours" : api.name(v.owner) + "'s") : "") + ".");
      if (info.length) p.appendChild(el("p", {text: info.join(" ")}));
      var row = el("div", {cls: "panel-buttons"});
      if (mine && v.phase === "roll") {
        row.appendChild(el("button", {type: "button", cls: "primary", text: "Roll (R)", on: {click: function () { cmd("roll"); }}}));
        if (v.cubeOk[me]) row.appendChild(el("button", {type: "button", text: "Double to " + v.cube * 2, on: {click: function () { cmd("double"); }}}));
      } else if (mine && v.phase === "take") {
        p.appendChild(el("p", {text: api.name(opp) + " doubles to " + v.cube * 2 + "."}));
        row.appendChild(el("button", {type: "button", cls: "primary", text: "Take", on: {click: function () { cmd("take"); }}}));
        row.appendChild(el("button", {type: "button", cls: "danger", text: "Drop", on: {click: function () { cmd("drop"); }}}));
      } else if (mine && v.phase === "resign" && v.offer) {
        p.appendChild(el("p", {text: api.name(v.offer.seat) + " offers to resign: " + v.offer.kind + ", " + v.offer.points + " point" + (v.offer.points === 1 ? "" : "s") + "."}));
        row.appendChild(el("button", {type: "button", cls: "primary", text: "Accept", on: {click: function () { cmd("accept"); }}}));
        row.appendChild(el("button", {type: "button", text: "Reject", on: {click: function () { cmd("reject"); }}}));
      } else if (mine && v.phase === "move") {
        if (done) row.appendChild(el("button", {type: "button", cls: "primary", text: "Confirm move (Enter)", on: {click: confirm}}));
        row.appendChild(el("button", {type: "button", text: "Undo step (U)", on: {click: function () { done = false; api.und(); }}}));
      }
      if (row.children.length) p.appendChild(row);
      if (mine && v.phase === "move" && v.moves.length) {
        p.appendChild(el("p", {cls: "small muted", text: "Drag a checker, or click it and then where it goes. Or pick a step:"}));
        var ms = el("div", {cls: "moves", role: "group", "aria-label": "Legal steps"});
        v.moves.forEach(function (m) { ms.appendChild(el("button", {type: "button", text: m, on: {click: function () { play(m); }}})); });
        p.appendChild(ms);
      }
      if (me >= 0 && v.phase && v.phase !== "over" && v.phase !== "resign" && v.phase !== "take") {
        var d = el("details", {}, [el("summary", {text: "Offer to resign"})]);
        var r2 = el("div", {cls: "panel-buttons"});
        [["single", "A single game"], ["gammon", "A gammon"], ["backgammon", "A backgammon"]].forEach(function (k) {
          r2.appendChild(el("button", {type: "button", text: k[1], on: {click: function () { cmd("resign-" + k[0]); }}}));
        });
        d.appendChild(r2); p.appendChild(d);
      }
      if (!v.started && !v.phase) p.appendChild(el("p", {cls: "muted", text: "The board is set when the second player sits down."}));
    }

    // ---- drawing -------------------------------------------------------------------------------------------
    function paint(ctx, w, h) {
      var k = w / W;
      ctx.save();
      if (mirror()) { ctx.translate(w, 0); ctx.scale(-1, 1); }
      ctx.scale(k, k);
      // the wooden frame, with a grain
      var g = ctx.createLinearGradient(0, 0, W, H);
      g.addColorStop(0, COL.wood2); g.addColorStop(1, COL.wood);
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
      ctx.globalAlpha = 0.09; ctx.strokeStyle = "#2a1608"; ctx.lineWidth = 0.02;
      for (var i = 0; i < 140; i++) {
        var y = (i / 140) * H + Math.sin(i * 1.7) * 0.05;
        ctx.beginPath(); ctx.moveTo(0, y);
        for (var x = 0; x <= W; x += 0.5) ctx.lineTo(x, y + Math.sin(x * 0.7 + i) * 0.04);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
      // the two fields of felt
      [[FIELD_L, BAR_L], [BAR_R, BAR_R + 6]].forEach(function (f) {
        var fg = ctx.createRadialGradient((f[0] + f[1]) / 2, H / 2, 0.5, (f[0] + f[1]) / 2, H / 2, 6.5);
        fg.addColorStop(0, "#24694f"); fg.addColorStop(1, "#174636");
        ctx.fillStyle = fg; ctx.fillRect(f[0], TOP, f[1] - f[0], BOT - TOP);
        ctx.strokeStyle = "rgba(0,0,0,0.45)"; ctx.lineWidth = 0.05; ctx.strokeRect(f[0], TOP, f[1] - f[0], BOT - TOP);
      });
      // felt speckle
      ctx.globalAlpha = 0.06;
      for (i = 0; i < 2600; i++) {
        var sx = FIELD_L + ((i * 97) % 1301) / 1301 * (BAR_R + 6 - FIELD_L), sy = TOP + ((i * 61) % 977) / 977 * (BOT - TOP);
        if (sx > BAR_L && sx < BAR_R) continue;
        ctx.fillStyle = i % 2 ? "#fff" : "#000"; ctx.fillRect(sx, sy, 0.025, 0.025);
      }
      ctx.globalAlpha = 1;
      // the points
      for (var c = 0; c < 12; c++) {
        var cx = colX(c);
        [true, false].forEach(function (top) {
          var dark = (c + (top ? 1 : 0)) % 2 === 0;
          var base = top ? TOP : BOT, tip = top ? TOP + PLEN : BOT - PLEN;
          var pg = ctx.createLinearGradient(0, base, 0, tip);
          var col = dark ? COL.pointB : COL.pointA;
          pg.addColorStop(0, col); pg.addColorStop(1, PNGfx.shade(col, dark ? 0.08 : -0.1));
          ctx.fillStyle = pg;
          ctx.beginPath(); ctx.moveTo(cx - 0.48, base); ctx.lineTo(cx + 0.48, base); ctx.lineTo(cx, tip); ctx.closePath(); ctx.fill();
          ctx.strokeStyle = "rgba(0,0,0,0.25)"; ctx.lineWidth = 0.02; ctx.stroke();
        });
      }
      // the bar and the trays
      var bg2 = ctx.createLinearGradient(BAR_L, 0, BAR_R, 0);
      bg2.addColorStop(0, "#4a2f1b"); bg2.addColorStop(0.5, COL.wood2); bg2.addColorStop(1, "#4a2f1b");
      ctx.fillStyle = bg2; ctx.fillRect(BAR_L, 0, BAR_R - BAR_L, H);
      [[TOP, H / 2 - 0.15], [H / 2 + 0.15, BOT]].forEach(function (t) {
        ctx.fillStyle = COL.tray; ctx.fillRect(TRAY_L, t[0], TRAY_R - TRAY_L, t[1] - t[0]);
        ctx.strokeStyle = "rgba(0,0,0,0.5)"; ctx.lineWidth = 0.05; ctx.strokeRect(TRAY_L, t[0], TRAY_R - TRAY_L, t[1] - t[0]);
      });
      ctx.restore();
      // point numbers, from this player's side (in pixels: small text is sharper drawn at its own size)
      var mx = function (x) { return (mirror() ? W - x : x) * k; };
      ctx.fillStyle = COL.label; ctx.font = "600 " + Math.round(0.24 * k) + "px " + PNGfx.FONT; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      for (c = 0; c < 12; c++) {
        var top2 = 13 + c, bot2 = 12 - c;
        ctx.fillText(String(flipView() ? 25 - top2 : top2), mx(colX(c)), TOP / 2 * k);
        ctx.fillText(String(flipView() ? 25 - bot2 : bot2), mx(colX(c)), (BOT + (H - BOT) / 2) * k);
      }
    }
    function frame(now) {
      var items = [{t: "board", w: W, h: H, d: 0.5, frame: "#3c2615", key: "bg" + viewer() + (mirror() ? "m" : ""), paint: paint}];
      // the frame's raised rim and the bar, for depth
      items.push({t: "box", x: W / 2, y: 0.06, w: W, h: 0.12, d: 0.3, color: COL.wood, r: 0.05});
      items.push({t: "box", x: W / 2, y: H - 0.06, w: W, h: 0.12, d: 0.3, color: COL.wood, r: 0.05});
      items.push({t: "box", x: 0.06, y: H / 2, w: 0.12, h: H, d: 0.3, color: COL.wood, r: 0.05});
      items.push({t: "box", x: W - 0.06, y: H / 2, w: 0.12, h: H, d: 0.3, color: COL.wood, r: 0.05});
      items.push({t: "box", x: (BAR_L + BAR_R) / 2, y: H / 2, w: BAR_R - BAR_L, h: H, d: 0.3, color: COL.wood2, r: 0.06});
      items.push({t: "box", x: (BAR_R + 6 + TRAY_L) / 2, y: H / 2, w: TRAY_L - BAR_R - 6, h: H, d: 0.3, color: COL.wood, r: 0.04});
      var m = model || v, mine = canMove();
      // marks: where the picked checker may go, and which checkers can move
      if (mine) {
        var srcs = {};
        v.moves.forEach(function (t) { srcs[t.split("-")[0]] = true; });
        Object.keys(srcs).forEach(function (s) {
          var key2 = s === "bar" ? "bar" + api.seat() : "p" + s, tp = topPos(m, key2);
          var hl = s === sel || s === hover || s === kbd;
          items.push({t: "quad", x: tp.x, y: tp.y, z: (tp.z || 0) + CH + 0.01, w: 2 * R * 1.25, h: 2 * R * 1.25, shape: "ring", color: hl ? "#ffd27a" : "#e3a83c", alpha: hl ? 0.95 : 0.55});
        });
        if (sel) movesFrom(sel).forEach(function (t) {
          var to = t.split("-")[1];
          if (to === "off") {
            var own = viewer();
            items.push({t: "quad", x: (TRAY_L + TRAY_R) / 2, y: own === viewer() ? (H / 2 + BOT) / 2 + 0.1 : (TOP + H / 2) / 2, z: 0.02, w: TRAY_R - TRAY_L - 0.1, h: BOT - H / 2 - 0.3, shape: "round", color: "#ffd27a", alpha: t === kbd ? 0.5 : 0.28});
          } else {
            var tk = "p" + to, n = count(m, tk), pos = stackPos(tk, n, n + 1);
            items.push({t: "quad", x: pos.x, y: pos.y, z: 0.01, w: 2 * R, h: 2 * R, shape: "dot", color: "#ffd27a", alpha: t === kbd ? 0.95 : 0.6});
          }
        });
      }
      var animating = false, targets = {};
      if (mine && sel) movesFrom(sel).forEach(function (t) { targets[t.split("-")[1]] = true; });
      stacks(m).forEach(function (st) {
        var k = shown(st.key, st.n);
        for (var i = 0; i < k; i++) {
          var pos = stackPos(st.key, i, st.n), z = pos.z || 0, top = i === k - 1;
          var a = top ? arrivals[st.key] : null;
          if (a) {
            var t = (now - a.start) / a.dur;
            if (t >= 1) { delete arrivals[st.key]; }
            else {
              animating = true;
              var e = ease(Math.max(0, t));
              pos = {x: a.x + (pos.x - a.x) * e, y: a.y + (pos.y - a.y) * e, slab: pos.slab};
              z = Math.sin(Math.PI * Math.max(0, t)) * 1.1;
            }
          }
          if (drag && drag.from && top && ((drag.from === "bar" && st.key === "bar" + api.seat()) || st.key === "p" + drag.from) &&
              Math.hypot(drag.x - drag.sx, drag.y - drag.sy) > 0.3) {
            pos = {x: drag.x, y: drag.y}; z = 0.7;
          }
          if (pos.slab) {
            items.push({t: "box", x: pos.x, y: pos.y, z: 0, w: 1.15, h: 0.25, d: 0.3, r: 0.1, color: COL.seat[st.seat]});
          } else {
            items.push({t: "shadow", x: pos.x, y: pos.y, r: R * (1 + z * 0.15), alpha: 0.75 - Math.min(0.4, z * 0.3)});
            items.push({t: "disc", x: pos.x, y: pos.y, z: z, r: R, h: CH, a: COL.seat[st.seat], mark: "rings", markInk: COL.rim[st.seat]});
          }
        }
        if (k < st.n) {               // +n in the last place: the checkers not drawn (yellow where the picked one may go)
          var lp = stackPos(st.key, MAX - 1, st.n), target = st.key.charAt(0) === "p" && targets[st.key.slice(1)];
          items.push({t: "tile", x: lp.x, y: lp.y, z: (lp.z || 0) + CH, w: 0.8, h: 0.8, d: 0.06,
                      color: target ? "#ffd27a" : COL.label, ink: "#2a2420", text: "+" + (st.n - k)});
        }
      });
      // the dice
      var dv = dice.vals || [], dt = dice.dur ? (now - dice.start) / dice.dur : 1;
      if (dt < 1) animating = true;
      var seatSide = dice.seat === -1 ? null : dice.seat === viewer() ? "right" : "left";
      var used = v.rolled && v.dice ? (v.rolled[0] === v.rolled[1] ? 4 : 2) - v.dice.length : 0;
      var show = dv.length === 2 && dv[0] === dv[1] && dice.seat !== -1 ? [dv[0], dv[0], dv[0], dv[0]] : dv;
      show.forEach(function (n, i) {
        var cx = seatSide === "left" ? (FIELD_L + BAR_L) / 2 : seatSide === "right" ? (BAR_R + BAR_R + 6) / 2 : (i ? (BAR_R + BAR_R + 6) / 2 : (FIELD_L + BAR_L) / 2);
        var off = show.length === 4 ? (i - 1.5) * 0.8 : seatSide ? (i - 0.5) * 0.95 : 0;
        var tx = cx + off, ty = H / 2, size = show.length === 4 ? 0.58 : 0.66;
        var q = PNGfx.dieFace(n, (i % 2 ? 0.25 : -0.18));
        var x = tx, y = ty, z = 0;
        if (dt < 1 && i < 2) {
          var e = ease(dt), ax = dice.axis[i];
          var spin = PNGfx.quat(ax[0], ax[1], ax[2], (1 - e) * (8 + dice.spin[i]));
          q = PNGfx.qmul(spin, q);
          x = tx + (1 - e) * (seatSide === "left" ? -2.5 : 2.5); y = ty + (1 - e) * (i ? 1.2 : -1.0);
          z = Math.abs(Math.sin(dt * Math.PI * 3)) * (1 - dt) * 1.4;
        } else if (dt < 1) return;
        var dim = v.rolled && dice.seat !== -1 && i < used;
        items.push({t: "shadow", x: x, y: y, r: size * 0.6, alpha: 0.7});
        items.push({t: "die", x: x, y: y, z: z, s: size, q: q, ink: "#1d1d1d", face: dim ? "#9d978b" : "#f7f3ea"});
      });
      // the doubling cube
      var cv = v.cube || 1, face = CUBE_FACE[cv] || 1;
      var cy = v.owner < 0 ? H / 2 : v.owner === viewer() ? BOT - 0.9 : TOP + 0.9;
      var cx2 = (BAR_L + BAR_R) / 2;
      if (v.owner < 0 && dice.vals && dice.vals.length) cx2 = (BAR_L + BAR_R) / 2;
      items.push({t: "shadow", x: cx2, y: cy, r: 0.4});
      items.push({t: "die", kind: "cube", x: cx2, y: cy, z: 0.3, s: 0.7, q: PNGfx.dieFace(face, v.owner === viewer() ? 0 : v.owner < 0 ? Math.PI / 2 : Math.PI), ink: "#2a1a0e", face: "#efe3c8"});
      if (animating) api.redraw();
      animatingNow = animating;
      if (mirror()) items.forEach(function (it) { if (it.x != null) it.x = W - it.x; });
      return {view: {w: W, h: H, tilt: 20, pad: 0.15, z: 0.8}, items: items, bg: "#121519"};
    }
    var animatingNow = false;
    function status() {
      if (!api.myTurn()) return "";
      if (v.phase === "roll") return "Roll the dice, or double.";
      if (v.phase === "take") return "Take or drop the double.";
      if (v.phase === "move") return v.moves.length ? "Dice " + v.dice.join(" and ") + ". Steps: " + v.moves.join(", ") + "." : done ? "Confirm your move." : "";
      return "";
    }
    // where a stack's top checker is ("13", "bar"), or with next, where the next checker on it would go ("off":
    // the tray): for keyboard focus marks and for checks from outside the page
    function spot(where, next) {
      var m = model || v, s;
      if (where === "off") s = {x: (TRAY_L + TRAY_R) / 2, y: (H / 2 + BOT) / 2};
      else {
        var k = where === "bar" ? "bar" + api.seat() : "p" + where, n = count(m, k);
        s = next ? stackPos(k, n, n + 1) : topPos(m, k);
      }
      return mirror() ? {x: W - s.x, y: s.y, z: s.z} : s;
    }
    return {view: view, event: event, describe: describe, frame: frame, pointer: pointer, key: key, status: status, spot: spot,
            animating: function () { return animatingNow || Object.keys(arrivals).length > 0; },
            seatColour: function (s) { return COL.seat[s]; }, paletteChanged: function () { api.redraw(); }};
  }

  PN.register("backgammon/1", {name: "Backgammon", create: create});
})();
