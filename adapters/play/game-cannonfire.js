// ParlorNet Play: Cannon Fire (cannonfire/1, docs/types/cannonfire-1.md). The landscape side-on, the tanks on
// it and the wind; your aim set with the keys, by dragging from your tank, or in the panel; a drive left or
// right before the shot. Every flight comes from the server's events (FIRE, TRJ, HIT or OUT, then DMG, ZCH,
// FALL, DIE): the shell follows the path the server sent and the crater opens where it says. Nothing is
// worked out here.
"use strict";

(function () {
  var SEAT = ["#e0574f", "#4a90d9", "#e8b33a", "#5cb85c", "#a36bd1", "#2bb5a5", "#ee8a3c", "#ef7fb2"];
  var COL = {grass: "#79b04d", dirt: "#7b5434", rock: "#3a3633", wreck: "#4a4d52", shell: "#22252a",
             fire: "#ffb347", ink: "#e9eef3", dim: "#9aa6b2", hint: "#f3c56b"};
  var SKY = 8, BOT = 2, TD = 4;           // sky above the highest ground; below height 0; the landscape's depth
  var TZ = TD * 0.55;                     // the height of the tanks' middle above the board

  function ease(t) { return t < 0 ? 0 : t > 1 ? 1 : 1 - Math.pow(1 - t, 3); }
  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }
  function letter(s) { return String.fromCharCode(65 + s); }
  function decode(chars) { return chars.split("").map(function (c) { return c.charCodeAt(0) - 0x30; }); }
  function clone(m) { return JSON.parse(JSON.stringify(m)); }

  function create(api) {
    var el = api.el;
    var W = 72, HM = 63;
    var srv = null, disp = null;       // the last view; what is drawn (behind srv while a shot is shown)
    var queue = [], cur = null, building = null, fresh = false, version = 0;
    var aim = null, aimSeat = -1, touched = false, dragging = false, busy = false;
    var dist = 0, preview = {L: null, R: null, why: ""}, previewFor = "";
    var falls = {}, flashes = {}, lastNow = 0;

    function GY() { return HM + SKY; }
    function k() { return clamp(W / 48, 1.4, 3.2); }         // tanks grow on wide arenas, to stay in sight
    function kl() { return clamp(W / 40, 1.6, 4.5); }        // and labels, to stay readable
    function me() { return api.seat(); }
    function alive(m, s) { return m && m.tanks[s] && m.tanks[s][1] > 0 && !(m.gone && m.gone[s]); }
    function mine() { return api.myTurn() && srv && alive(srv, me()); }

    // ---- the view -------------------------------------------------------------------------------------------
    function view(lines) {
      var m = {h: [], tanks: [], aim: [], moves: [], wind: 0, hit: null, turn: -1, budget: 0, moved: false, gone: [], score: []};
      var any = false;
      lines.forEach(function (l) {
        var f = l.split(" ");
        if (f[0] === "ARN") { W = +f[1]; HM = +f[2]; }
        else if (f[0] === "Z") { any = true; decode(f[2] || "").forEach(function (h, i) { m.h[+f[1] + i] = h; }); }
        else if (f[0] === "K") m.tanks[+f[1]] = [+f[2], +f[3]];
        else if (f[0] === "A") m.aim[+f[1]] = [+f[2], +f[3]];
        else if (f[0] === "L") m.moves[+f[1]] = +f[2];
        else if (f[0] === "N") m.wind = +f[1];
        else if (f[0] === "H") m.hit = [+f[1], +f[2]];
        else if (f[0] === "W") m.turn = +f[1];
        else if (f[0] === "B") { m.budget = +f[1]; m.moved = f[2] === "1"; }
        else if (f[0] === "G") m.gone = f.slice(1).map(Number);
        else if (f[0] === "T" && f.length === 3) m.score[+f[1]] = +f[2];
      });
      if (!any) { panel(); return; }            // before the game starts: no landscape yet
      if (m.h.length && m.h.length !== W) W = m.h.length;
      srv = m;
      fresh = true;
      if (!disp || (!cur && !queue.length)) { disp = clone(srv); version++; }
      var s = me();
      if (s >= 0 && m.aim[s] && (!aim || !touched || aimSeat !== s)) { aim = m.aim[s].slice(); aimSeat = s; }
      if (!mine()) touched = false;
      if (!dist && m.budget) dist = Math.ceil(m.budget / 10);
      panel();
      checkDrive();
    }

    // ---- events: what the server says happened, shown in order ---------------------------------------------
    function event(text, quiet) {
      var f = text.split(" ");
      if (quiet || !api.motion() || !srv) return;     // the view says it all
      if (f[0] === "FIRE") {
        building = {k: "shot", seat: +f[1], a: +f[2], p: +f[3], pts: [], hit: null, out: null, after: []};
        queue.push(building); fresh = false;
      } else if (f[0] === "TRJ" && building) {
        for (var i = 1; i + 1 < f.length; i += 2) building.pts.push([+f[i], +f[i + 1]]);
      } else if (f[0] === "HIT" && building) building.hit = [+f[1], +f[2], +f[3]];
      else if (f[0] === "OUT" && building) building.out = +f[1];
      else if (f[0] === "MOVE") {
        building = null; fresh = false;
        queue.push({k: "drive", seat: +f[1], from: +f[2], to: +f[3], left: +f[5]});
      } else if (/^(DMG|ZCH|FALL|DIE|N|GONE)$/.test(f[0])) {
        if (building) building.after.push(f);
        else { queue.push({k: "apply", evs: [f]}); fresh = false; }
      } else if (f[0] === "TURN" || f[0] === "END") building = null;
      api.redraw();
    }
    function apply(f, now) {
      var t = disp.tanks;
      if (f[0] === "DMG" && t[+f[1]]) { t[+f[1]][1] = +f[2]; flashes[+f[1]] = now; }
      else if (f[0] === "ZCH") { decode(f[2] || "").forEach(function (h, i) { disp.h[+f[1] + i] = h; }); version++; }
      else if (f[0] === "FALL" && t[+f[1]]) { t[+f[1]][0] = +f[2]; t[+f[1]][1] = +f[3]; }
      else if (f[0] === "DIE" && t[+f[1]]) t[+f[1]][1] = 0;
      else if (f[0] === "N") disp.wind = +f[1];
      else if (f[0] === "GONE") disp.gone[+f[1]] = 1;
    }
    function ground(m, x) { return m.h[clamp(x, 0, W - 1)] || 0; }
    // The path of a shot in board units: from the muzzle through the server's points to where it stopped.
    function path(step) {
      var t = disp.tanks[step.seat] || [0, 0], out = [[t[0] + 0.5, ground(disp, t[0]) + 2]];
      step.pts.forEach(function (p) { out.push([p[0] + 0.5, Math.max(0, p[1])]); });
      if (step.hit) out.push([step.hit[0] + 0.5, step.hit[1]]);
      else if (step.out != null) { var l = out[out.length - 1]; out.push([step.out === 0 ? -2 : W + 2, l[1]]); }
      return out;
    }
    function begin(step, now) {
      step.t0 = now;
      if (step.k === "shot") {
        step.path = path(step);
        var len = 0;
        for (var i = 1; i < step.path.length; i++) len += Math.hypot(step.path[i][0] - step.path[i - 1][0], step.path[i][1] - step.path[i - 1][1]);
        step.turn = 260; step.fly = clamp(380 + len * 16, 520, 2100); step.boom = step.hit ? 650 : 120;
        step.from = (disp.aim[step.seat] || [90, 50]).slice();
        step.dur = step.turn + step.fly + step.boom;
        setTimeout(function () { api.sound("place"); }, step.turn);
      } else if (step.k === "drive") step.dur = clamp(Math.abs(step.to - step.from) * 140, 250, 1400);
      else step.dur = 0;
    }
    function burst(step, now) {
      // the crater, the damage and the falls, as the server said, when the shell bursts
      var before = disp.tanks.map(function (t) { return t ? ground(disp, t[0]) : 0; });
      step.after.forEach(function (f) { apply(f, now); });
      step.after.forEach(function (f) {
        if (f[0] === "FALL") falls[+f[1]] = {from: before[+f[1]], t0: now};
      });
      disp.hit = step.hit ? [step.hit[0], step.hit[1]] : null;
      if (step.hit) api.sound("dice");
      step.burst = true;
    }
    function finish(step, now) {
      if (step.k === "shot") {
        if (!step.burst) burst(step, now);
        disp.aim[step.seat] = [step.a, step.p];
      } else if (step.k === "drive") {
        if (disp.tanks[step.seat]) disp.tanks[step.seat][0] = step.to;
        disp.moves[step.seat] = step.left;
      } else step.evs.forEach(function (f) { apply(f, now); });
    }
    // Moves the queue on to the time now; true while something is still shown.
    function advance(now) {
      lastNow = now;
      for (;;) {
        if (!cur) {
          if (!queue.length) {
            if (fresh && srv && disp !== srv && JSON.stringify(disp) !== JSON.stringify(srv)) { disp = clone(srv); version++; }
            return Object.keys(falls).length > 0 || Object.keys(flashes).length > 0 || dragging;
          }
          cur = queue.shift();
          begin(cur, now);
        }
        if (cur.k === "shot" && !cur.burst && now - cur.t0 >= cur.turn + cur.fly) burst(cur, now);
        if (now - cur.t0 < cur.dur) return true;
        finish(cur, now);
        cur = null;
      }
    }

    // ---- aiming, driving, firing -----------------------------------------------------------------------------
    function setAim(a, p, quiet) {
      if (!aim) return;
      aim = [clamp(Math.round(a), 0, 180), clamp(Math.round(p), 0, 100)];
      touched = true;
      syncInputs();
      if (!quiet) api.announce("Angle " + aim[0] + ", power " + aim[1]);
      api.redraw();
    }
    function fire() {
      if (!mine() || busy || !aim) return;
      busy = true;
      api.mov("FIRE " + aim[0] + " " + aim[1]).then(function () { busy = false; touched = false; panel(); });
    }
    function drive(dir) {
      if (!mine() || busy) return;
      if (srv.moved) { api.toast("You have driven this turn: fire now."); return; }
      if (!srv.moves[me()]) { api.toast("No moves left this game."); return; }
      busy = true;
      api.mov("MOVE " + dir + " " + driveCols()).then(function () { busy = false; previewFor = ""; panel(); });
    }
    function maxCols() { return Math.max(1, Math.ceil((srv ? srv.budget : 70) / 10)); }
    function driveCols() { return dist >= maxCols() ? 999 : clamp(dist, 1, 999); }
    // Where a drive would end, from the server (CHK), for the marks on the board and the buttons.
    function checkDrive() {
      var s = me();
      if (!mine() || srv.moved || !srv.moves[s]) { preview = {L: null, R: null, why: ""}; return; }
      var key = [api.tid, srv.tanks[s][0], driveCols(), srv.moves[s], version].join(":");
      if (key === previewFor) return;
      previewFor = key;
      ["L", "R"].forEach(function (d) {
        api.chk("MOVE " + d + " " + driveCols()).then(function (r) {
          if (previewFor !== key) return;
          var m = /^OK from (\d+) to (\d+) used (\d+) of (\d+)/.exec(r.final);
          preview[d] = m ? {to: +m[2], used: +m[3], of: +m[4]} : null;
          if (!m && /^ERR/.test(r.final)) preview[d + "why"] = r.final.replace(/^ERR \S+ /, "");
          panel(); api.redraw();
        });
      });
    }

    // ---- the panel ------------------------------------------------------------------------------------------------
    var ui = null;
    function build() {
      var p = api.clear(api.panel);
      ui = {};
      p.appendChild(el("h2", {text: "Cannon Fire"}));
      ui.turn = p.appendChild(el("p", {}));
      ui.tanks = p.appendChild(el("ul", {cls: "list small"}));
      ui.wind = p.appendChild(el("p", {cls: "small"}));
      ui.ctl = p.appendChild(el("div", {}));
      function slider(label, max, idx) {
        var r = el("input", {type: "range", min: 0, max: max, step: 1, "aria-label": label});
        var n = el("input", {type: "number", min: 0, max: max, step: 1, "aria-label": label + ", as a number", inputmode: "numeric"});
        r.style.flex = "1"; r.style.minHeight = "0"; r.style.padding = "0"; n.style.width = "4.6em";
        var on = function (v) { var a = aim.slice(); a[idx] = +v; setAim(a[0], a[1], true); };
        r.addEventListener("input", function () { on(r.value); });
        n.addEventListener("change", function () { if (n.value !== "") on(n.value); });
        var row = el("label", {cls: "row"}, [el("span", {text: label}), r, n]);
        row.firstChild.style.minWidth = "3.6em";
        ui.ctl.appendChild(row);
        return [r, n];
      }
      ui.angle = slider("Angle", 180, 0);
      ui.power = slider("Power", 100, 1);
      ui.fire = el("button", {type: "button", cls: "primary", text: "Fire", on: {click: fire}});
      ui.ctl.appendChild(el("p", {}, [ui.fire]));
      ui.drive = ui.ctl.appendChild(el("div", {}));
      ui.left = el("button", {type: "button", text: "Drive left", on: {click: function () { drive("L"); }}});
      ui.right = el("button", {type: "button", text: "Drive right", on: {click: function () { drive("R"); }}});
      ui.dist = el("input", {type: "range", min: 1, max: 8, step: 1, "aria-label": "How far to drive, in columns"});
      ui.dist.style.flex = "1"; ui.dist.style.minHeight = "0"; ui.dist.style.padding = "0";
      ui.dist.addEventListener("input", function () { dist = +ui.dist.value; refresh(); checkDrive(); api.redraw(); });
      ui.distText = el("span", {cls: "small"});
      ui.drive.appendChild(el("div", {cls: "row"}, [ui.left, ui.right]));
      ui.drive.appendChild(el("label", {cls: "row"}, [el("span", {cls: "small", text: "Distance"}), ui.dist, ui.distText]));
      ui.driveNote = ui.drive.appendChild(el("p", {cls: "small muted"}));
      ui.keys = p.appendChild(el("p", {cls: "small muted", text: "On the board: Left and Right turn the barrel, Up and Down set the power " +
        "(with Shift, by 5), Space or Enter fires, A or [ drives left and D or ] drives right. Or drag from your tank: " +
        "the direction is the angle and the length the power; then Fire."}));
    }
    function syncInputs() {
      if (!ui || !aim) return;
      [[ui.angle, 0], [ui.power, 1]].forEach(function (x) {
        if (document.activeElement !== x[0][1]) x[0][1].value = aim[x[1]];
        x[0][0].value = aim[x[1]];
      });
    }
    function windText(w) {
      return w === 0 ? "No wind" : "Wind " + Math.abs(w) + (w > 0 ? " to the right" : " to the left");
    }
    function refresh() {
      if (!ui) build();
      var m = disp || srv, s = me(), c = api.common();
      if (!m) {
        ui.turn.textContent = "Waiting for players.";
        api.clear(ui.tanks); ui.wind.textContent = ""; ui.ctl.hidden = true; ui.keys.hidden = true;
        return;
      }
      ui.keys.hidden = s < 0;
      var t = srv.turn;
      ui.turn.textContent = api.over() ? (c.status || "Game over") : mine() ? "Your shot." :
        t >= 0 ? "Tank " + letter(t) + " (" + api.name(t) + ") to fire." : "";
      api.clear(ui.tanks);
      m.tanks.forEach(function (tk, i) {
        if (!tk) return;
        var chip = el("span", {cls: "chip", "aria-hidden": "true"});
        chip.style.display = "inline-block"; chip.style.width = "0.8em"; chip.style.height = "0.8em";
        chip.style.borderRadius = "50%"; chip.style.background = SEAT[i % 8]; chip.style.marginRight = "0.4em";
        var state = (m.gone && m.gone[i]) ? "left the game" : tk[1] <= 0 ? "destroyed" : tk[1] + " hp";
        var li = ui.tanks.appendChild(el("li", {}, [el("span", {}, [chip, el("b", {text: letter(i) + " "}), api.name(i) +
          (i === s ? " (you)" : "") + ": " + state + ", " + (m.moves[i] || 0) + " move" + (m.moves[i] === 1 ? "" : "s") + " left"])]));
        li.style.justifyContent = "flex-start";
      });
      ui.wind.textContent = windText(m.wind) + ".";
      var my = mine();
      ui.ctl.hidden = !(s >= 0 && srv && alive(srv, s) && !api.over());
      [ui.angle, ui.power].forEach(function (x) { x[0].disabled = x[1].disabled = !my; });
      ui.fire.disabled = !my || busy;
      syncInputs();
      var canDrive = my && !srv.moved && srv.moves[s] > 0;
      ui.drive.hidden = !(s >= 0 && srv.moves[s] > 0);
      ui.left.disabled = !canDrive || busy || !preview.L;
      ui.right.disabled = !canDrive || busy || !preview.R;
      var mc = maxCols();
      ui.dist.max = mc;
      dist = clamp(dist || mc, 1, mc);
      ui.dist.value = dist;
      ui.distText.textContent = dist >= mc ? "all" : dist + " col.";
      ui.dist.disabled = !canDrive;
      var note = srv.moves[s] + " of 4 moves left this game. One drive a turn, before the shot.";
      if (my && srv.moved) note = "You have driven this turn: fire now.";
      else if (canDrive && (preview.L || preview.R)) {
        note += (preview.L ? " Left goes to column " + preview.L.to + "." : " You cannot drive left.") +
                (preview.R ? " Right goes to column " + preview.R.to + "." : " You cannot drive right.");
      }
      ui.driveNote.textContent = note;
    }
    function panel() { refresh(); }

    // ---- input ---------------------------------------------------------------------------------------------------
    function muzzle(m, s) {
      var t = m.tanks[s], kk = k();
      return {x: t[0] + 0.5, y: GY() - ground(m, t[0]) - 1.15 * kk};
    }
    function fireButton() { var kk = kl(); return {x: W - 3.4 * kk, y: 3.4 * kk, w: 5.6 * kk, h: 1.9 * kk}; }
    function pointer(kind, p) {
      if (!disp || !mine() || !aim) { dragging = false; return; }
      var q = p.at ? p.at(TZ) : p, qb = p.at ? p.at(TD + 0.3) : p, b = fireButton();
      if (kind === "down") {
        if (Math.abs(qb.x - b.x) <= b.w / 2 && Math.abs(qb.y - b.y) <= b.h / 2) { fire(); return; }
        dragging = true;
      }
      if ((kind === "down" || kind === "move") && dragging) {
        var c = muzzle(disp, me()), dx = q.x - c.x, dy = c.y - q.y;
        var a = Math.atan2(dy, dx) * 180 / Math.PI;
        if (dy < 0) a = dx >= 0 ? 0 : 180;
        setAim(a, Math.hypot(dx, dy) * 100 / Math.min(32, Math.max(18, W * 0.35)), true);
      }
      if (kind === "up" || kind === "leave" || kind === "cancel") {
        if (dragging) api.announce("Angle " + aim[0] + ", power " + aim[1]);
        dragging = false;
      }
      document.getElementById("board").style.cursor = dragging ? "crosshair" : "default";
    }
    function key(e) {
      if (!mine() || !aim) return false;
      var step = e.shiftKey ? 5 : 1;
      switch (e.key) {
        case "ArrowLeft": setAim(aim[0] + step, aim[1]); return true;
        case "ArrowRight": setAim(aim[0] - step, aim[1]); return true;
        case "ArrowUp": setAim(aim[0], aim[1] + step); return true;
        case "ArrowDown": setAim(aim[0], aim[1] - step); return true;
        case "PageUp": setAim(aim[0], aim[1] + 10); return true;
        case "PageDown": setAim(aim[0], aim[1] - 10); return true;
        case " ": case "Enter": fire(); return true;
        case "a": case "A": case "[": drive("L"); return true;
        case "d": case "D": case "]": drive("R"); return true;
      }
      return false;
    }

    // ---- drawing --------------------------------------------------------------------------------------------------
    function paint(ctx, w, h) {
      var g = ctx.createLinearGradient(0, 0, 0, h);
      g.addColorStop(0, "#0b1624"); g.addColorStop(0.55, "#22405e"); g.addColorStop(1, "#5d6f7c");
      ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);
      for (var i = 0; i < 160; i++) {       // stars, the same every time
        var sx = ((i * 7919) % 1009) / 1009 * w, sy = ((i * 104729) % 1013) / 1013 * h * 0.5;
        ctx.globalAlpha = 0.25 + ((i * 31) % 7) / 12; ctx.fillStyle = "#ffffff";
        ctx.fillRect(sx, sy, Math.max(1, w / 900), Math.max(1, w / 900));
      }
      ctx.globalAlpha = 0.35; ctx.fillStyle = "#1b2a38";      // far hills
      ctx.beginPath(); ctx.moveTo(0, h);
      for (i = 0; i <= 48; i++) ctx.lineTo(i / 48 * w, h * (0.58 + 0.08 * Math.sin(i * 0.7) + 0.05 * Math.sin(i * 1.9 + 1)));
      ctx.lineTo(w, h); ctx.closePath(); ctx.fill();
      ctx.globalAlpha = 1;
    }
    function tankItems(items, m, s, now, x, g) {
      var kk = k(), t = m.tanks[s], dead = t[1] <= 0 || (m.gone && m.gone[s]), col = dead ? COL.wreck : SEAT[s % 8];
      var cx = x + 0.5, base = GY() - g, fl = flashes[s] != null && now - flashes[s] < 500;
      if (flashes[s] != null && now - flashes[s] >= 500) delete flashes[s];
      if (fl && Math.floor((now - flashes[s]) / 90) % 2 === 0) col = "#ffffff";
      items.push({t: "box", x: cx, y: base - 0.3 * kk, z: TZ - 0.8, w: 2.7 * kk, h: 0.6 * kk, d: 1.6, r: 0.25 * kk, color: "#2b2e33"});
      items.push({t: "box", x: cx, y: base - 0.85 * kk, z: TZ - 0.6, w: 2.3 * kk, h: 0.7 * kk, d: 1.2, r: 0.2 * kk, color: col});
      var a = (s === me() && aim && mine() && !cur ? aim[0] : (m.aim[s] || [90])[0]);
      if (cur && cur.k === "shot" && cur.seat === s) {
        var tt = ease((now - cur.t0) / cur.turn);
        a = cur.from[0] + (cur.a - cur.from[0]) * tt;
      }
      if (dead) a = s % 2 ? 160 : 20;
      var r = a * Math.PI / 180, mz = {x: cx, y: base - 1.15 * kk};
      items.push({t: "box", x: mz.x + Math.cos(r) * 0.9 * kk, y: mz.y - Math.sin(r) * 0.9 * kk, z: TZ - 0.2, w: 1.7 * kk, h: 0.26 * kk,
                  d: 0.4, r: 0.1 * kk, rot: r, color: dead ? COL.wreck : "#c9ced4"});
      items.push({t: "disc", x: mz.x, y: mz.y, z: TZ - 0.5, r: 0.55 * kk, h: 1.0, a: col});
      // the hit points and the letter, above
      var bw = 2.8 * kk, by = base - 2.4 * kk;
      if (!dead) {
        items.push({t: "quad", x: cx, y: by, z: TD + 1, w: bw, h: 0.36 * kk, color: "#000000", alpha: 0.55});
        var hp = t[1] / 100;
        items.push({t: "quad", x: cx - bw / 2 + bw * hp / 2, y: by, z: TD + 1.01, w: bw * hp, h: 0.26 * kk,
                    color: hp > 0.5 ? "#6cc04a" : hp > 0.25 ? "#e8b33a" : "#e0574f"});
      }
      items.push({t: "text", x: cx, y: by - 1.1 * kk, z: TD + 1, text: letter(s) + " " + api.name(s), size: 0.75 * kl(),
                  color: dead ? COL.dim : SEAT[s % 8]});
      if (!dead && !api.over() && srv && srv.turn === s && !cur && !queue.length) {
        var bob = api.motion() ? Math.sin(now / 260) * 0.25 : 0;
        items.push({t: "text", x: cx, y: by - 1.6 * kk - 0.9 * kl() + bob, z: TD + 1, text: "▼", size: 0.8 * kl(), color: SEAT[s % 8]});
      }
    }
    function frame(now) {
      if (!disp) {
        return {view: {w: 40, h: 20, tilt: 0, pad: 0.4}, bg: "#0e1620", items: [
          {t: "board", w: 40, h: 20, d: 0.3, frame: "#1a2029", key: "cfwait", paint: paint},
          {t: "text", x: 20, y: 10, text: "Waiting for players", size: 1.6, color: COL.ink}]};
      }
      advance(now);
      var gy = GY(), kk = k(), H = gy + BOT, items = [];
      items.push({t: "board", w: W, h: H, d: 0.4, frame: "#121820", key: "cf" + W + "x" + H, paint: paint, spec: 0.02});
      items.push({t: "terrain", x: 0, y: gy, h: disp.h, d: TD, a: COL.grass, b: COL.dirt, band: 0.8, bottom: BOT - 0.05,
                  key: version + ":" + disp.h.join(",")});
      items.push({t: "box", x: W / 2, y: gy + (BOT - 0.4) / 2 + 0.2, z: 0, w: W, h: BOT - 0.4 + 0.1, d: TD + 0.15, r: 0.05, color: COL.rock});
      // where the last shell burst
      if (disp.hit && !(cur && cur.k === "shot")) {
        items.push({t: "quad", x: disp.hit[0] + 0.5, y: gy - disp.hit[1], z: TD + 0.05, w: 1.6, h: 1.6, shape: "ring", color: COL.fire, alpha: 0.45});
      }
      // the tanks: on their ground, driving, or falling into a crater
      disp.tanks.forEach(function (t, s) {
        if (!t) return;
        var x = t[0], g = ground(disp, x);
        if (cur && cur.k === "drive" && cur.seat === s) {
          var dt = ease((now - cur.t0) / cur.dur), fx = cur.from + (cur.to - cur.from) * dt, xa = Math.floor(fx), xb = Math.min(W - 1, xa + 1);
          g = ground(disp, xa) + (ground(disp, xb) - ground(disp, xa)) * (fx - xa); x = fx;
        }
        var fall = falls[s];
        if (fall) {
          var ft = (now - fall.t0) / 450;
          if (ft >= 1) delete falls[s];
          else g = fall.from + (g - fall.from) * (ft * ft);
        }
        tankItems(items, disp, s, now, x, g);
      });
      // the drives the server would allow (CHK), as faint marks
      if (mine() && !cur && !queue.length && srv && !srv.moved) {
        ["L", "R"].forEach(function (d) {
          var p = preview[d];
          if (!p) return;
          items.push({t: "quad", x: p.to + 0.5, y: gy - ground(disp, p.to) - 0.6 * kk, z: TD + 0.1, w: 2.5 * kk, h: 1.2 * kk,
                      shape: "round", color: SEAT[me() % 8], alpha: 0.4});
          items.push({t: "text", x: p.to + 0.5, y: gy - ground(disp, p.to) + 0.9 * kl(), z: TD + 0.1, text: d === "L" ? "◀ A" : "D ▶",
                      size: 0.6 * kl(), color: COL.ink});
        });
      }
      // your aim: a line of dots from the muzzle, as long as the power (not the flight: the server works that out)
      if (mine() && aim && !cur && !queue.length && disp.tanks[me()]) {
        var mz = muzzle(disp, me()), ra = aim[0] * Math.PI / 180, n = Math.max(1, Math.round(aim[1] / 7));
        for (var i = 1; i <= n; i++) {
          var dd = 1.2 * kk + i * 0.9 * kk;
          items.push({t: "quad", x: mz.x + Math.cos(ra) * dd, y: mz.y - Math.sin(ra) * dd, z: TD + 0.2, w: 0.38 * kk, h: 0.38 * kk,
                      shape: "dot", color: COL.hint, alpha: 0.9 - 0.5 * i / n});
        }
        var b = fireButton();
        items.push({t: "quad", x: b.x, y: b.y, z: TD + 0.3, w: b.w, h: b.h, shape: "round", color: "#c0392b", alpha: busy ? 0.5 : 0.95});
        items.push({t: "text", x: b.x, y: b.y, z: TD + 0.35, text: "FIRE", size: 0.9 * kl(), color: "#ffffff"});
        items.push({t: "text", x: 1.2, y: 1.2 * kl() + 2.2 * kl(), z: TD + 0.3, align: "left", text: "Angle " + aim[0] + "°  Power " + aim[1],
                    size: 0.85 * kl(), color: COL.ink});
      }
      // the shell in flight, and the burst
      if (cur && cur.k === "shot") {
        var el2 = now - cur.t0 - cur.turn;
        if (el2 >= 0 && el2 < cur.fly) {
          var pts = cur.path, u = (el2 / cur.fly) * (pts.length - 1), j = Math.min(pts.length - 2, Math.floor(u)), fr = u - j;
          var sx = pts[j][0] + (pts[j + 1][0] - pts[j][0]) * fr, sy = pts[j][1] + (pts[j + 1][1] - pts[j][1]) * fr;
          for (var q = 0; q <= j; q++) {
            items.push({t: "quad", x: pts[q][0], y: gy - pts[q][1], z: TD + 0.15, w: 0.5, h: 0.5, shape: "dot", color: "#ffe2a8",
                        alpha: 0.35 + 0.55 * q / Math.max(1, j)});
          }
          if (gy - sy < 0.5) {      // above the top of the screen: a mark where it will come down
            items.push({t: "text", x: clamp(sx, 1, W - 1), y: 0.9, z: TD + 0.3, text: "▲", size: 1.1 * kk, color: COL.fire});
          } else {
            items.push({t: "disc", x: sx, y: gy - sy, z: TZ, r: 0.42 * kk, h: 0.6, a: COL.shell});
            items.push({t: "quad", x: sx, y: gy - sy, z: TD + 0.25, w: 1.1 * kk, h: 1.1 * kk, shape: "dot", color: COL.fire, alpha: 0.35});
          }
        } else if (el2 >= cur.fly && cur.hit) {
          var bt = (el2 - cur.fly) / cur.boom, r = cur.hit[2] || 7, hx = cur.hit[0] + 0.5, hy = gy - cur.hit[1];
          items.push({t: "quad", x: hx, y: hy, z: TD + 0.4, w: 2 * r * ease(bt * 1.6), h: 2 * r * ease(bt * 1.6), shape: "dot",
                      color: bt < 0.3 ? "#fff1c1" : COL.fire, alpha: Math.max(0, 0.9 * (1 - bt))});
          items.push({t: "quad", x: hx, y: hy, z: TD + 0.45, w: 2.6 * r * ease(bt), h: 2.6 * r * ease(bt), shape: "ring",
                      color: "#ffffff", alpha: Math.max(0, 0.7 * (1 - bt))});
        }
      }
      // the wind, at the top
      var w = disp.wind, wt = w === 0 ? "No wind" : (w < 0 ? new Array(Math.abs(w) + 1).join("◀") + "  Wind " + Math.abs(w)
                                                           : "Wind " + w + "  " + new Array(w + 1).join("▶"));
      items.push({t: "text", x: 1.2, y: 1.2 * kl(), z: TD + 0.3, align: "left", text: wt, size: 0.85 * kl(), color: w === 0 ? COL.dim : "#cfe6ff"});
      return {view: {w: W, h: H, tilt: -5, pad: 0.4, z: TD + 1}, items: items, bg: "#0b121a"};
    }
    function describe(text) {
      var f = text.split(" "), nm = function (s) { return "Tank " + letter(+s) + " (" + api.name(+s) + ")"; };
      switch (f[0]) {
        case "FIRE": return nm(f[1]) + " fires: angle " + f[2] + ", power " + f[3];
        case "HIT": return "The shell bursts at column " + f[1];
        case "OUT": return "The shell leaves the arena";
        case "DMG": return nm(f[1]) + " is hit: " + f[2] + " hp left";
        case "DIE": return nm(f[1]) + " is destroyed";
        case "FALL": return nm(f[1]) + " drops into the crater";
        case "N": return windText(+f[1]);
        case "MOVE": return nm(f[1]) + " drives from column " + f[2] + " to " + f[3] + " (" + f[5] + " move" + (f[5] === "1" ? "" : "s") + " left)";
        case "END":
          if (f[1] === "win") return "Game over: " + nm(f[2]) + " wins, " + (f[3] || "");
          if (f[1] === "draw") return "Game over: the last tanks destroyed each other, " + (f[2] || "");
          return null;
      }
      return null;
    }
    function status() {
      var m = disp || srv;
      if (!m) return "Waiting for players.";
      var t = m.tanks.map(function (tk, s) {
        return tk ? letter(s) + " " + api.name(s) + " at column " + tk[0] + ", " + (tk[1] > 0 ? tk[1] + " hp" : "destroyed") : "";
      }).filter(Boolean).join("; ");
      return t + ". " + windText(m.wind) + "." + (mine() && aim ? " Your shot: angle " + aim[0] + ", power " + aim[1] + "." : "");
    }
    panel();
    return {view: view, event: event, describe: describe, frame: frame, pointer: pointer, key: key, status: status,
            animating: function (now) { return advance(now || lastNow); },
            seatColour: function (s) { return SEAT[s % 8]; },
            spot: function (s) { var t = disp && disp.tanks[+s]; return t ? {x: t[0] + 0.5, y: GY() - ground(disp, t[0])} : null; },
            destroy: function () { queue = []; cur = null; }};
  }

  PN.register("cannonfire/1", {name: "Cannon Fire", create: create});
})();
