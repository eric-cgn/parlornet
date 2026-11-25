// ParlorNet Play: the board renderer. Hand-written WebGL, with a 2D canvas fallback that draws the same
// scene from above. No libraries.
//
// A game module describes its board each frame as a list of items in board units (x right, y down the
// screen, z up from the table); the renderer draws them:
//   {t: "board", w, h, d, frame, paint, key}     a slab with a painted top (paint(ctx, w, h) on a 2D canvas)
//   {t: "box", x, y, z, w, h, d, r, color, rot}    a rounded box (walls, the bar, trays); rot turns it about
//                                                  the vertical, in radians, counterclockwise on the screen
//   {t: "terrain", x, y, z, h, d, a, b, band, bottom, key}   a side-on landscape (Cannon Fire): h is a list of
//                                                  heights, one per unit column, its surface through the
//                                                  columns' centers; y is where height 0 is; a slab d deep
//                                                  from the board up, its surface and a band of that
//                                                  depth below it color a, the rest b, down to -bottom;
//                                                  key names the heights (a new key, a new mesh)
//   {t: "disc", x, y, z, r, h, a, b, flip, axis}   a disc or checker: top color a, bottom b; flip turns it
//   {t: "tile", x, y, z, w, h, d, color, ink, text, sub, subInk, lift}   a lettered tile; or with paint(ctx, w, h)
//                                                  and key (naming what it paints), a face of the game's own
//   {t: "die", x, y, z, s, q, face, ink, kind}     a die (kind "cube": the doubling cube), q a quaternion
//   {t: "quad", x, y, z, w, h, color, alpha, shape}   a flat mark: "rect", "round", "dot", "ring"
//   {t: "text", x, y, z, text, size, color, align} a flat label
//   {t: "shadow", x, y, r, alpha}                  a soft shadow on the board
// Colors are "#rrggbb". Text on the board and tiles is drawn into textures at the screen's resolution.
"use strict";

var PNGfx = (function () {
  // ---- small math -------------------------------------------------------------------------------
  function hex(c) {
    var n = parseInt((c || "#888888").slice(1), 16);
    return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
  }
  function ident() { var m = new Float32Array(16); m[0] = m[5] = m[10] = m[15] = 1; return m; }
  function mul(a, b) {
    var o = new Float32Array(16);
    for (var c = 0; c < 4; c++) for (var r = 0; r < 4; r++) {
      var s = 0;
      for (var k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k];
      o[c * 4 + r] = s;
    }
    return o;
  }
  function persp(fovy, asp, n, f) {
    var t = 1 / Math.tan(fovy / 2), m = new Float32Array(16);
    m[0] = t / asp; m[5] = t; m[10] = (f + n) / (n - f); m[11] = -1; m[14] = 2 * f * n / (n - f);
    return m;
  }
  function sub3(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
  function cross(a, b) { return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]; }
  function norm(a) { var l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; }
  function lookAt(e, c, up) {
    var z = norm(sub3(e, c)), x = norm(cross(up, z)), y = cross(z, x), m = ident();
    m[0] = x[0]; m[4] = x[1]; m[8] = x[2];
    m[1] = y[0]; m[5] = y[1]; m[9] = y[2];
    m[2] = z[0]; m[6] = z[1]; m[10] = z[2];
    m[12] = -(x[0] * e[0] + x[1] * e[1] + x[2] * e[2]);
    m[13] = -(y[0] * e[0] + y[1] * e[1] + y[2] * e[2]);
    m[14] = -(z[0] * e[0] + z[1] * e[1] + z[2] * e[2]);
    return m;
  }
  function invert(m) {
    var a = m, o = new Float32Array(16);
    var b00 = a[0] * a[5] - a[1] * a[4], b01 = a[0] * a[6] - a[2] * a[4], b02 = a[0] * a[7] - a[3] * a[4];
    var b03 = a[1] * a[6] - a[2] * a[5], b04 = a[1] * a[7] - a[3] * a[5], b05 = a[2] * a[7] - a[3] * a[6];
    var b06 = a[8] * a[13] - a[9] * a[12], b07 = a[8] * a[14] - a[10] * a[12], b08 = a[8] * a[15] - a[11] * a[12];
    var b09 = a[9] * a[14] - a[10] * a[13], b10 = a[9] * a[15] - a[11] * a[13], b11 = a[10] * a[15] - a[11] * a[14];
    var det = b00 * b11 - b01 * b10 + b02 * b09 + b03 * b08 - b04 * b07 + b05 * b06;
    if (!det) return ident();
    det = 1 / det;
    o[0] = (a[5] * b11 - a[6] * b10 + a[7] * b09) * det; o[1] = (a[2] * b10 - a[1] * b11 - a[3] * b09) * det;
    o[2] = (a[13] * b05 - a[14] * b04 + a[15] * b03) * det; o[3] = (a[10] * b04 - a[9] * b05 - a[11] * b03) * det;
    o[4] = (a[6] * b08 - a[4] * b11 - a[7] * b07) * det; o[5] = (a[0] * b11 - a[2] * b08 + a[3] * b07) * det;
    o[6] = (a[14] * b02 - a[12] * b05 - a[15] * b01) * det; o[7] = (a[8] * b05 - a[10] * b02 + a[11] * b01) * det;
    o[8] = (a[4] * b10 - a[5] * b08 + a[7] * b06) * det; o[9] = (a[1] * b08 - a[0] * b10 - a[3] * b06) * det;
    o[10] = (a[12] * b04 - a[13] * b02 + a[15] * b00) * det; o[11] = (a[9] * b02 - a[8] * b04 - a[11] * b00) * det;
    o[12] = (a[5] * b07 - a[4] * b09 - a[6] * b06) * det; o[13] = (a[0] * b09 - a[1] * b07 + a[2] * b06) * det;
    o[14] = (a[13] * b01 - a[12] * b03 - a[14] * b00) * det; o[15] = (a[8] * b03 - a[9] * b01 + a[10] * b00) * det;
    return o;
  }
  function xform(m, p) {
    var x = p[0], y = p[1], z = p[2], w = m[3] * x + m[7] * y + m[11] * z + m[15];
    return [(m[0] * x + m[4] * y + m[8] * z + m[12]) / w, (m[1] * x + m[5] * y + m[9] * z + m[13]) / w,
            (m[2] * x + m[6] * y + m[10] * z + m[14]) / w];
  }
  function translate(x, y, z) { var m = ident(); m[12] = x; m[13] = y; m[14] = z; return m; }
  function scale(x, y, z) { var m = ident(); m[0] = x; m[5] = y; m[10] = z; return m; }
  function axisAngle(ax, ay, az, a) {
    var l = Math.hypot(ax, ay, az) || 1, x = ax / l, y = ay / l, z = az / l, c = Math.cos(a), s = Math.sin(a), t = 1 - c;
    var m = ident();
    m[0] = t * x * x + c; m[1] = t * x * y + s * z; m[2] = t * x * z - s * y;
    m[4] = t * x * y - s * z; m[5] = t * y * y + c; m[6] = t * y * z + s * x;
    m[8] = t * x * z + s * y; m[9] = t * y * z - s * x; m[10] = t * z * z + c;
    return m;
  }
  // quaternions [x, y, z, w] (for the dice)
  function quat(ax, ay, az, a) {
    var l = Math.hypot(ax, ay, az) || 1, s = Math.sin(a / 2);
    return [ax / l * s, ay / l * s, az / l * s, Math.cos(a / 2)];
  }
  function qmul(a, b) {
    return [a[3] * b[0] + a[0] * b[3] + a[1] * b[2] - a[2] * b[1], a[3] * b[1] - a[0] * b[2] + a[1] * b[3] + a[2] * b[0],
            a[3] * b[2] + a[0] * b[1] - a[1] * b[0] + a[2] * b[3], a[3] * b[3] - a[0] * b[0] - a[1] * b[1] - a[2] * b[2]];
  }
  function slerp(a, b, t) {
    var d = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3], s = 1;
    if (d < 0) { d = -d; s = -1; }
    var k0, k1;
    if (d > 0.9995) { k0 = 1 - t; k1 = t * s; } else {
      var th = Math.acos(d), sn = Math.sin(th);
      k0 = Math.sin((1 - t) * th) / sn; k1 = s * Math.sin(t * th) / sn;
    }
    var q = [a[0] * k0 + b[0] * k1, a[1] * k0 + b[1] * k1, a[2] * k0 + b[2] * k1, a[3] * k0 + b[3] * k1];
    var l = Math.hypot(q[0], q[1], q[2], q[3]) || 1;
    return [q[0] / l, q[1] / l, q[2] / l, q[3] / l];
  }
  function qmat(q) {
    var x = q[0], y = q[1], z = q[2], w = q[3], m = ident();
    m[0] = 1 - 2 * (y * y + z * z); m[1] = 2 * (x * y + z * w); m[2] = 2 * (x * z - y * w);
    m[4] = 2 * (x * y - z * w); m[5] = 1 - 2 * (x * x + z * z); m[6] = 2 * (y * z + x * w);
    m[8] = 2 * (x * z + y * w); m[9] = 2 * (y * z - x * w); m[10] = 1 - 2 * (x * x + y * y);
    return m;
  }
  // The orientation that shows face n (1-6) on top of a die (faces: 1 +z, 6 -z, 2 +x, 5 -x, 3 +y, 4 -y in
  // the die's own axes; world y is up the screen), turned by spin about the vertical.
  function dieFace(n, spin) {
    var q = [[0, 0, 0, 1], [0, 0, 0, 1], quat(0, 1, 0, -Math.PI / 2), quat(1, 0, 0, Math.PI / 2),
             quat(1, 0, 0, -Math.PI / 2), quat(0, 1, 0, Math.PI / 2), quat(1, 0, 0, Math.PI)][n];
    return qmul(quat(0, 0, 1, spin || 0), q);
  }

  // ---- meshes --------------------------------------------------------------------------------------
  // Each vertex: position (3), normal (3), uv (2), tag (1): 9 floats.
  // A rounded box: the half sizes and the corner radius; tag 1 on the top face. uvs: each face 0-1, or for a
  // die, its place in a 3 by 2 atlas (faces 1-6 as above).
  function roundedBox(hx, hy, hz, r, n, atlas) {
    var v = [], idx = [];
    var faces = [ // normal, u axis, v axis, die face
      [[0, 0, 1], [1, 0, 0], [0, -1, 0], 1], [[0, 0, -1], [1, 0, 0], [0, 1, 0], 6],
      [[1, 0, 0], [0, 1, 0], [0, 0, -1], 2], [[-1, 0, 0], [0, -1, 0], [0, 0, -1], 5],
      [[0, 1, 0], [-1, 0, 0], [0, 0, -1], 3], [[0, -1, 0], [1, 0, 0], [0, 0, -1], 4]];
    var h = [hx, hy, hz], inner = [hx - r, hy - r, hz - r];
    faces.forEach(function (f) {
      var base = v.length / 9;
      for (var j = 0; j <= n; j++) for (var i = 0; i <= n; i++) {
        var u = i / n * 2 - 1, w = j / n * 2 - 1, p = [], q = [], nn = [];
        for (var k = 0; k < 3; k++) p[k] = (f[0][k] + f[1][k] * u + f[2][k] * w) * h[k];
        for (k = 0; k < 3; k++) q[k] = Math.max(-inner[k], Math.min(inner[k], p[k]));
        nn = norm(sub3(p, q));
        if (!(Math.abs(p[0] - q[0]) + Math.abs(p[1] - q[1]) + Math.abs(p[2] - q[2]))) nn = f[0];
        var pos = [q[0] + nn[0] * r, q[1] + nn[1] * r, q[2] + nn[2] * r];
        var uu = i / n, vv = j / n;
        if (atlas) { var d = f[3] - 1; uu = ((d % 3) + uu) / 3; vv = (Math.floor(d / 3) + vv) / 2; }
        v.push(pos[0], pos[1], pos[2], nn[0], nn[1], nn[2], uu, vv, f[3] === 1 ? 1 : 0);
      }
      for (j = 0; j < n; j++) for (i = 0; i < n; i++) {
        var a = base + j * (n + 1) + i, b = a + 1, c = a + n + 1, d2 = c + 1;
        idx.push(a, c, b, b, c, d2);
      }
    });
    return {v: v, i: idx};
  }
  // A disc of radius 1 and height 1 with rounded edges (bevel b): tag 1 on its upper half, so a disc may be
  // two colors (Reversi). Top uvs over the cap.
  function disc(seg, b) {
    var prof = [], A = 6, k;
    prof.push([0, 0, 0, -1, 0]);
    for (k = 0; k <= A; k++) { var a = -Math.PI / 2 + k / A * Math.PI / 2; prof.push([1 - b + b * Math.cos(a), b + b * Math.sin(a), Math.cos(a), Math.sin(a), 0]); }
    prof.push([1, 0.5, 1, 0, 0]);
    prof.push(null);
    prof.push([1, 0.5, 1, 0, 1]);
    for (k = 0; k <= A; k++) { a = k / A * Math.PI / 2; prof.push([1 - b + b * Math.cos(a), 1 - b + b * Math.sin(a), Math.cos(a), Math.sin(a), 1]); }
    prof.push([0, 1, 0, 1, 1]);
    var v = [], idx = [], strips = [[]];
    prof.forEach(function (p) { if (p) strips[strips.length - 1].push(p); else strips.push([]); });
    strips.forEach(function (s) {
      var base = v.length / 9;
      s.forEach(function (p) {
        for (var i = 0; i <= seg; i++) {
          var t = i / seg * Math.PI * 2, c = Math.cos(t), sn = Math.sin(t);
          var nz = p[3], nr = p[2];
          v.push(p[0] * c, p[0] * sn, p[1], nr * c, nr * sn, nz, 0.5 + 0.5 * p[0] * c, 0.5 - 0.5 * p[0] * sn, p[4]);
        }
      });
      for (var j = 0; j < s.length - 1; j++) for (var i = 0; i < seg; i++) {
        var a2 = base + j * (seg + 1) + i, b2 = a2 + 1, c2 = a2 + seg + 1, d2 = c2 + 1;
        idx.push(a2, b2, c2, b2, d2, c2);
      }
    });
    return {v: v, i: idx};
  }
  // A landscape seen side-on: the surface runs through (i + 0.5, hs[i]) and out to the two edges; a front face
  // (z = d) with the band below the surface tagged 1, the surface itself (tag 1) from z = 0 to d, and the two
  // ends. Heights are y up; the item's y places height 0.
  function terrainMesh(hs, d, band, bottom) {
    var n = hs.length, pts = [[0, hs[0]]], v = [], idx = [], i;
    for (i = 0; i < n; i++) pts.push([i + 0.5, hs[i]]);
    pts.push([n, hs[n - 1]]);
    function quad(p, nn, tag) {     // four corners, counterclockwise seen from outside
      var base = v.length / 9;
      p.forEach(function (q) { v.push(q[0], q[1], q[2], nn[0], nn[1], nn[2], 0, 0, tag); });
      idx.push(base, base + 1, base + 2, base, base + 2, base + 3);
    }
    var bot = -bottom;
    for (i = 0; i < pts.length - 1; i++) {
      var a = pts[i], b = pts[i + 1], ab = Math.max(bot, a[1] - band), bb = Math.max(bot, b[1] - band);
      quad([[a[0], bot, d], [b[0], bot, d], [b[0], bb, d], [a[0], ab, d]], [0, 0, 1], 0);
      quad([[a[0], ab, d], [b[0], bb, d], [b[0], b[1], d], [a[0], a[1], d]], [0, 0, 1], 1);
      var nn = norm([-(b[1] - a[1]), b[0] - a[0], 0]);
      quad([[a[0], a[1], d], [b[0], b[1], d], [b[0], b[1], 0], [a[0], a[1], 0]], nn, 1);
    }
    quad([[0, bot, d], [0, pts[0][1], d], [0, pts[0][1], 0], [0, bot, 0]], [-1, 0, 0], 0);
    quad([[n, bot, 0], [n, pts[n + 1][1], 0], [n, pts[n + 1][1], d], [n, bot, d]], [1, 0, 0], 0);
    return {v: v, i: idx};
  }
  function quadMesh() {
    return {v: [-0.5, -0.5, 0, 0, 0, 1, 0, 1, 1, 0.5, -0.5, 0, 0, 0, 1, 1, 1, 1, 0.5, 0.5, 0, 0, 0, 1, 1, 0, 1,
                -0.5, 0.5, 0, 0, 0, 1, 0, 0, 1], i: [0, 1, 2, 0, 2, 3]};
  }

  // ---- textures painted on 2D canvases -------------------------------------------------------------------
  function canvas(w, h) {
    var c = document.createElement("canvas");
    c.width = Math.max(1, Math.round(w)); c.height = Math.max(1, Math.round(h));
    return c;
  }
  var FONT = "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", Helvetica, Arial, sans-serif";
  function paintPips(ctx, x, y, s, n, ink, face) {
    ctx.fillStyle = face;
    ctx.fillRect(x, y, s, s);
    var g = ctx.createRadialGradient(x + s * 0.5, y + s * 0.5, s * 0.1, x + s * 0.5, y + s * 0.5, s * 0.75);
    g.addColorStop(0, "rgba(255,255,255,0.0)"); g.addColorStop(1, "rgba(0,0,0,0.10)");
    ctx.fillStyle = g; ctx.fillRect(x, y, s, s);
    var P = {1: [[.5, .5]], 2: [[.27, .27], [.73, .73]], 3: [[.25, .25], [.5, .5], [.75, .75]],
             4: [[.27, .27], [.73, .27], [.27, .73], [.73, .73]], 5: [[.25, .25], [.75, .25], [.5, .5], [.25, .75], [.75, .75]],
             6: [[.27, .23], [.73, .23], [.27, .5], [.73, .5], [.27, .77], [.73, .77]]}[n];
    P.forEach(function (p) {
      ctx.beginPath(); ctx.arc(x + p[0] * s, y + p[1] * s, s * 0.085, 0, Math.PI * 2);
      ctx.fillStyle = ink; ctx.fill();
      ctx.beginPath(); ctx.arc(x + p[0] * s - s * 0.012, y + p[1] * s - s * 0.012, s * 0.06, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(0,0,0,0.25)"; ctx.fill();
    });
  }
  function paintCubeFace(ctx, x, y, s, n, ink, face) {
    ctx.fillStyle = face; ctx.fillRect(x, y, s, s);
    ctx.strokeStyle = ink; ctx.lineWidth = s * 0.03; ctx.strokeRect(x + s * 0.1, y + s * 0.1, s * 0.8, s * 0.8);
    ctx.fillStyle = ink; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.font = "700 " + Math.round(s * (n > 9 ? 0.42 : 0.55)) + "px " + FONT;
    ctx.fillText(String(n), x + s / 2, y + s * 0.54);
  }
  function dieAtlas(kind, ink, face) {
    var s = 256, c = canvas(s * 3, s * 2), ctx = c.getContext("2d");
    for (var d = 0; d < 6; d++) {
      var x = (d % 3) * s, y = Math.floor(d / 3) * s;
      if (kind === "cube") paintCubeFace(ctx, x, y, s, [64, 2, 4, 32, 16, 8][d], ink, face);
      else paintPips(ctx, x, y, s, d + 1, ink, face);
    }
    return c;
  }
  function textCanvas(text, px, color, weight, sub, subColor, w, h) {
    // a tile's or a label's face: text centered, an optional small number in the corner (a tile's value)
    var c = canvas(w, h), ctx = c.getContext("2d");
    ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillStyle = color;
    ctx.font = (weight || 700) + " " + Math.round(px) + "px " + FONT;
    ctx.fillText(text, w * (sub ? 0.46 : 0.5), h * 0.54);
    if (sub) {
      ctx.font = "600 " + Math.round(px * 0.36) + "px " + FONT;
      ctx.fillStyle = subColor || color; ctx.textAlign = "right"; ctx.textBaseline = "alphabetic";
      ctx.fillText(sub, w * 0.9, h * 0.88);
    }
    return c;
  }

  // ---- the camera, shared by both renderers -----------------------------------------------------------------
  // view: {w, h, tilt (degrees), pad (board units round the board), z (the height to fit), win}; win, when
  // given, is the part of the board to show, {x, y, w, h} in board units (a zoomed board): it fills the canvas
  // exactly, with no pad
  function Camera() { this.vp = ident(); this.inv = ident(); this.eye = [0, 0, 10]; }
  Camera.prototype.fit = function (view, cw, ch) {
    var tilt = (view.tilt || 0) * Math.PI / 180, fov = 28 * Math.PI / 180, asp = cw / ch, win = view.win;
    var pad = win ? 0 : view.pad || 0.4, zt = win ? 0 : view.z || 0.6, lim = win ? 1 : 0.985;
    var x0 = win ? win.x : 0, y0 = win ? win.y : 0, x1 = win ? win.x + win.w : view.w, y1 = win ? win.y + win.h : view.h;
    var cx = (x0 + x1) / 2, cy = -(y0 + y1) / 2;
    var pts = [], P = [[x0 - pad, y0 - pad], [x1 + pad, y0 - pad], [x0 - pad, y1 + pad], [x1 + pad, y1 + pad]];
    P.forEach(function (p) { pts.push([p[0], -p[1], 0], [p[0], -p[1], zt]); });
    var proj = persp(fov, asp, 0.5, 400), lo = 1, hi = 300, vp;
    for (var it = 0; it < 30; it++) {
      var d = (lo + hi) / 2;
      var eye = [cx, cy - d * Math.sin(tilt), d * Math.cos(tilt)];
      vp = mul(proj, lookAt(eye, [cx, cy, 0], [0, 1, 0]));
      var ok = pts.every(function (p) { var q = xform(vp, p); return Math.abs(q[0]) <= lim && Math.abs(q[1]) <= lim; });
      if (ok) hi = d; else lo = d;
    }
    var dd = hi;
    this.eye = [cx, cy - dd * Math.sin(tilt), dd * Math.cos(tilt)];
    this.vp = mul(persp(fov, asp, Math.max(0.5, dd * 0.3), dd * 3), lookAt(this.eye, [cx, cy, 0], [0, 1, 0]));
    this.inv = invert(this.vp);
    // the vertical centering: the board's middle at the middle of the view
    return this;
  };
  // A point on the screen (pixels in the canvas) -> board units on the plane at height z.
  Camera.prototype.unproject = function (px, py, cw, ch, z) {
    var nx = px / cw * 2 - 1, ny = 1 - py / ch * 2;
    var a = xform(this.inv, [nx, ny, -1]), b = xform(this.inv, [nx, ny, 1]);
    var t = ((z || 0) - a[2]) / (b[2] - a[2]);
    return {x: a[0] + (b[0] - a[0]) * t, y: -(a[1] + (b[1] - a[1]) * t)};
  };
  Camera.prototype.project = function (x, y, z, cw, ch) {
    var q = xform(this.vp, [x, -y, z || 0]);
    return {x: (q[0] + 1) / 2 * cw, y: (1 - q[1]) / 2 * ch};
  };

  // ---- the WebGL renderer -------------------------------------------------------------------------------------
  var VS = [
    "attribute vec3 aPos; attribute vec3 aNorm; attribute vec2 aUV; attribute float aTag;",
    "uniform mat4 uModel; uniform mat4 uVP;",
    "varying vec3 vN; varying vec2 vUV; varying float vTag; varying vec3 vW;",
    "void main() { vec4 w = uModel * vec4(aPos, 1.0); vW = w.xyz;",
    "  vN = (uModel * vec4(aNorm, 0.0)).xyz; vUV = aUV; vTag = aTag; gl_Position = uVP * w; }"].join("\n");
  var FS = [
    "precision mediump float;",
    "uniform vec3 uColA; uniform vec3 uColB; uniform float uSplit; uniform sampler2D uTex; uniform float uTexWhere;",
    "uniform vec3 uLight; uniform vec3 uEye; uniform float uSpec; uniform float uShine; uniform float uAlpha;",
    "uniform float uUnlit;",
    "varying vec3 vN; varying vec2 vUV; varying float vTag; varying vec3 vW;",
    "void main() {",
    "  vec3 base = uSplit > 0.5 ? mix(uColB, uColA, step(0.5, vTag)) : uColA;",
    "  float a = uAlpha;",
    "  if (uTexWhere > 1.5 || (uTexWhere > 0.5 && vTag > 0.5)) { vec4 t = texture2D(uTex, vUV);",
    "    if (uUnlit > 0.5) { base = t.rgb * uColA; a = t.a * uAlpha; } else { base = mix(base, t.rgb, t.a); } }",
    "  if (uUnlit > 0.5) { gl_FragColor = vec4(base, a); return; }",
    "  vec3 n = normalize(vN); vec3 l = normalize(uLight); vec3 v = normalize(uEye - vW);",
    "  float diff = max(dot(n, l), 0.0); float hemi = 0.5 + 0.5 * n.z;",
    "  float spec = pow(max(dot(n, normalize(l + v)), 0.0), uShine) * uSpec;",
    "  float rim = pow(1.0 - max(dot(n, v), 0.0), 3.0) * 0.12;",
    "  vec3 col = base * (0.30 + 0.22 * hemi + 0.62 * diff) + vec3(spec + rim);",
    "  gl_FragColor = vec4(col, a); }"].join("\n");

  function GL(canvasEl) {
    var gl = canvasEl.getContext("webgl", {antialias: true, alpha: false, premultipliedAlpha: false,
                                           preserveDrawingBuffer: true});
    if (!gl) throw new Error("no WebGL");
    this.gl = gl; this.cv = canvasEl; this.kind = "webgl";
    function sh(type, src) {
      var s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
      return s;
    }
    var p = gl.createProgram();
    gl.attachShader(p, sh(gl.VERTEX_SHADER, VS)); gl.attachShader(p, sh(gl.FRAGMENT_SHADER, FS));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    gl.useProgram(p);
    this.p = p; this.u = {}; this.a = {};
    var self = this;
    ["uModel", "uVP", "uColA", "uColB", "uSplit", "uTex", "uTexWhere", "uLight", "uEye", "uSpec", "uShine",
     "uAlpha", "uUnlit"].forEach(function (n) { self.u[n] = gl.getUniformLocation(p, n); });
    ["aPos", "aNorm", "aUV", "aTag"].forEach(function (n) { self.a[n] = gl.getAttribLocation(p, n); });
    this.ext = gl.getExtension("OES_element_index_uint");
    this.aniso = gl.getExtension("EXT_texture_filter_anisotropic") || gl.getExtension("WEBKIT_EXT_texture_filter_anisotropic");
    this.meshes = {}; this.tex = {}; this.texUse = {}; this.frameNo = 0;
    this.cam = new Camera();
    this.mesh("quad", quadMesh());
    this.mesh("disc", disc(48, 0.28));
    this.mesh("die", roundedBox(0.5, 0.5, 0.5, 0.11, 6, true));
    this.white = this.texture("white", function () { var c = canvas(2, 2), x = c.getContext("2d"); x.fillStyle = "#fff"; x.fillRect(0, 0, 2, 2); return c; });
    this.shadowTex = this.texture("shadow", function () {
      var c = canvas(128, 128), x = c.getContext("2d"), g = x.createRadialGradient(64, 64, 4, 64, 64, 64);
      g.addColorStop(0, "rgba(0,0,0,0.55)"); g.addColorStop(0.55, "rgba(0,0,0,0.22)"); g.addColorStop(1, "rgba(0,0,0,0)");
      x.fillStyle = g; x.fillRect(0, 0, 128, 128); return c;
    });
    gl.enable(gl.DEPTH_TEST); gl.enable(gl.CULL_FACE);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
  }
  GL.prototype.mesh = function (name, m) {
    var gl = this.gl, vb = gl.createBuffer(), ib = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, vb); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(m.v), gl.STATIC_DRAW);
    var big = m.v.length / 9 > 65535;
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ib);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, big ? new Uint32Array(m.i) : new Uint16Array(m.i), gl.STATIC_DRAW);
    this.meshes[name] = {vb: vb, ib: ib, n: m.i.length, type: big ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT};
    return this.meshes[name];
  };
  GL.prototype.boxMesh = function (hx, hy, hz, r) {
    var key = "box:" + [hx, hy, hz, r].map(function (n) { return n.toFixed(3); }).join(",");
    return this.meshes[key] || this.mesh(key, roundedBox(hx, hy, hz, Math.min(r, hx, hy, hz), 4, false));
  };
  // A texture made once by make() (a canvas) and kept while it is used; textures unused for a while go.
  GL.prototype.texture = function (key, make) {
    var t = this.tex[key];
    this.texUse[key] = this.frameNo;
    if (t) return t;
    var gl = this.gl, c = make();
    t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, c);
    var pot = !(c.width & (c.width - 1)) && !(c.height & (c.height - 1));
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    if (pot) { gl.generateMipmap(gl.TEXTURE_2D); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR); }
    else gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    if (this.aniso) gl.texParameterf(gl.TEXTURE_2D, this.aniso.TEXTURE_MAX_ANISOTROPY_EXT, 4);
    this.tex[key] = t;
    return t;
  };
  GL.prototype.sweep = function () {
    var gl = this.gl, self = this;
    Object.keys(this.tex).forEach(function (k) {
      if (k !== "white" && k !== "shadow" && self.frameNo - self.texUse[k] > 600) {
        gl.deleteTexture(self.tex[k]); delete self.tex[k]; delete self.texUse[k];
      }
    });
  };
  GL.prototype.resize = function () {
    var dpr = Math.min(window.devicePixelRatio || 1, 3), cv = this.cv;
    var w = Math.max(1, Math.round(cv.clientWidth * dpr)), h = Math.max(1, Math.round(cv.clientHeight * dpr));
    if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
    this.dpr = dpr;
    return [w, h];
  };
  // pixels per board unit at the board's middle (for sizing text textures)
  GL.prototype.ppu = function (view) {
    var a = this.cam.project(view.w / 2, view.h / 2, 0, this.cv.width, this.cv.height);
    var b = this.cam.project(view.w / 2 + 1, view.h / 2, 0, this.cv.width, this.cv.height);
    return Math.max(8, Math.abs(b.x - a.x));
  };
  GL.prototype.draw = function (m, model, o) {
    var gl = this.gl, u = this.u, a = this.a;
    gl.bindBuffer(gl.ARRAY_BUFFER, m.vb);
    gl.vertexAttribPointer(a.aPos, 3, gl.FLOAT, false, 36, 0); gl.enableVertexAttribArray(a.aPos);
    gl.vertexAttribPointer(a.aNorm, 3, gl.FLOAT, false, 36, 12); gl.enableVertexAttribArray(a.aNorm);
    gl.vertexAttribPointer(a.aUV, 2, gl.FLOAT, false, 36, 24); gl.enableVertexAttribArray(a.aUV);
    gl.vertexAttribPointer(a.aTag, 1, gl.FLOAT, false, 36, 32); gl.enableVertexAttribArray(a.aTag);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, m.ib);
    gl.uniformMatrix4fv(u.uModel, false, model);
    gl.uniform3fv(u.uColA, hex(o.a)); gl.uniform3fv(u.uColB, hex(o.b || o.a));
    gl.uniform1f(u.uSplit, o.b ? 1 : 0);
    gl.uniform1f(u.uTexWhere, o.texWhere || 0);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, o.tex || this.white); gl.uniform1i(u.uTex, 0);
    gl.uniform1f(u.uSpec, o.spec == null ? 0.25 : o.spec); gl.uniform1f(u.uShine, o.shine || 40);
    gl.uniform1f(u.uAlpha, o.alpha == null ? 1 : o.alpha); gl.uniform1f(u.uUnlit, o.unlit ? 1 : 0);
    gl.drawElements(gl.TRIANGLES, m.n, m.type, 0);
  };
  GL.prototype.render = function (view, items, bg) {
    var gl = this.gl, self = this;
    this.frameNo++;
    var sz = this.resize(), W = sz[0], H = sz[1];
    gl.viewport(0, 0, W, H);
    var c = hex(bg || "#1b1f24");
    gl.clearColor(c[0], c[1], c[2], 1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    this.cam.fit(view, W, H);
    gl.uniformMatrix4fv(this.u.uVP, false, this.cam.vp);
    gl.uniform3fv(this.u.uLight, norm([-0.45, 0.55, 0.9])); gl.uniform3fv(this.u.uEye, this.cam.eye);
    var ppu = this.ppu(view);
    var solid = [], flat = [], shadows = [];
    items.forEach(function (it) {
      if (it.t === "quad" || it.t === "text") flat.push(it);
      else if (it.t === "shadow") shadows.push(it);
      else solid.push(it);
    });
    gl.disable(gl.BLEND); gl.depthMask(true);
    solid.forEach(function (it) { if (it.t === "board" || it.t === "box" || it.t === "terrain") self.item(it, ppu); });
    // shadows on the board, then marks, then the pieces
    gl.enable(gl.BLEND); gl.depthMask(false);
    shadows.forEach(function (it) {
      var r = it.r * 2.2;
      self.draw(self.meshes.quad, mul(translate(it.x + 0.08 * r, -(it.y + 0.06 * r), (it.z || 0) + 0.003), scale(r, r, 1)),
                {a: "#000000", tex: self.shadowTex, texWhere: 2, unlit: true, alpha: it.alpha == null ? 0.8 : it.alpha});
    });
    flat.forEach(function (it) { self.item(it, ppu); });
    gl.disable(gl.BLEND); gl.depthMask(true);
    solid.forEach(function (it) { if (it.t !== "board" && it.t !== "box" && it.t !== "terrain") self.item(it, ppu); });
    if (this.frameNo % 300 === 0) this.sweep();
  };
  GL.prototype.item = function (it, ppu) {
    var self = this, m;
    if (it.t === "board") {
      var res = Math.min(4096, Math.max(512, Math.round(Math.max(it.w, it.h) * ppu)));
      var pw = Math.round(res * it.w / Math.max(it.w, it.h)), ph = Math.round(res * it.h / Math.max(it.w, it.h));
      var tex = this.texture("board:" + it.key + ":" + pw + "x" + ph, function () {
        var c = canvas(pw, ph); it.paint(c.getContext("2d"), pw, ph); return c;
      });
      var d = it.d || 0.3;
      m = this.boxMesh(it.w / 2, it.h / 2, d / 2, 0.02);
      this.draw(m, translate(it.w / 2, -it.h / 2, -d / 2 - 0.001), {a: it.frame || "#333333", spec: 0.08});
      this.draw(this.meshes.quad, mul(translate(it.w / 2, -it.h / 2, 0.0005), scale(it.w, it.h, 1)),
                {a: "#ffffff", tex: tex, texWhere: 2, spec: it.spec == null ? 0.06 : it.spec, shine: 18});
    } else if (it.t === "box") {
      m = this.boxMesh(it.w / 2, it.h / 2, it.d / 2, it.r == null ? 0.05 : it.r);
      var bt = null;
      if (it.paint) bt = this.texture("boxtop:" + it.key, function () { var c = canvas(512, 512); it.paint(c.getContext("2d"), 512, 512); return c; });
      var bm = translate(it.x, -it.y, (it.z || 0) + it.d / 2);
      if (it.rot) bm = mul(bm, axisAngle(0, 0, 1, it.rot));
      this.draw(m, bm, {a: it.color, tex: bt, texWhere: bt ? 1 : 0, spec: it.spec == null ? 0.15 : it.spec});
    } else if (it.t === "terrain") {
      var tk = "terrain:" + it.key;
      if (!this.meshes[tk]) {
        var gl2 = this.gl, ms = this.meshes;
        Object.keys(ms).forEach(function (k) {       // one landscape at a time: the old one's buffers go
          if (k.indexOf("terrain:") === 0) { gl2.deleteBuffer(ms[k].vb); gl2.deleteBuffer(ms[k].ib); delete ms[k]; }
        });
        this.mesh(tk, terrainMesh(it.h, it.d || 2, it.band == null ? 0.7 : it.band, it.bottom == null ? 1.5 : it.bottom));
      }
      this.draw(this.meshes[tk], translate(it.x || 0, -it.y, it.z || 0), {a: it.a, b: it.b, spec: 0.05, shine: 12});
    } else if (it.t === "disc") {
      var model = translate(it.x, -it.y, (it.z || 0) + (it.flip ? it.r * Math.abs(Math.sin(it.flip)) * 0.9 : 0));
      if (it.flip) {
        var ax = it.axis || [1, 0];
        model = mul(model, mul(translate(0, 0, it.h / 2), mul(axisAngle(-ax[1], -ax[0], 0, it.flip), translate(0, 0, -it.h / 2))));
      }
      model = mul(model, scale(it.r, it.r, it.h));
      var top = null;
      if (it.mark) top = this.texture("mark:" + it.mark + it.markInk, function () {
        var c = canvas(256, 256), x = c.getContext("2d");
        x.strokeStyle = it.markInk; x.lineWidth = 7; x.globalAlpha = 0.55;
        x.beginPath(); x.arc(128, 128, 78, 0, Math.PI * 2); x.stroke();
        x.globalAlpha = 0.35; x.beginPath(); x.arc(128, 128, 44, 0, Math.PI * 2); x.stroke();
        return c;
      });
      this.draw(this.meshes.disc, model, {a: it.a, b: it.b, tex: top, texWhere: top ? 1 : 0, spec: it.spec == null ? 0.45 : it.spec, shine: 60});
    } else if (it.t === "tile") {
      var hz = (it.d || 0.18) / 2;
      m = this.boxMesh(it.w / 2, it.h / 2, hz, Math.min(it.w, it.h) * 0.12);
      var tpx = Math.min(512, Math.max(32, Math.pow(2, Math.ceil(Math.log2(Math.max(it.w, it.h) * ppu * 1.2)))));
      var tt = it.paint ? this.texture("tilep:" + it.key + "|" + tpx, function () {
        var c = canvas(tpx, Math.round(tpx * it.h / it.w)); it.paint(c.getContext("2d"), c.width, c.height); return c;
      }) : it.text ? this.texture("tile:" + [it.text, it.ink, it.sub, it.subInk, tpx].join("|"), function () {
        return textCanvas(it.text, tpx * 0.62, it.ink, 700, it.sub, it.subInk, tpx, tpx);
      }) : null;
      var tm = translate(it.x, -it.y, (it.z || 0) + hz + (it.lift || 0));
      if (it.rot) tm = mul(tm, axisAngle(0, 0, 1, it.rot));
      this.draw(m, tm, {a: it.color, tex: tt, texWhere: tt ? 1 : 0, spec: 0.18, shine: 30});
    } else if (it.t === "die") {
      var s = it.s;
      var dt = this.texture("die:" + (it.kind || "pips") + it.ink + it.face, function () { return dieAtlas(it.kind, it.ink, it.face); });
      this.draw(this.meshes.die, mul(translate(it.x, -it.y, (it.z || 0) + s / 2), mul(qmat(it.q), scale(s, s, s))),
                {a: "#ffffff", tex: dt, texWhere: 2, spec: 0.35, shine: 50});
    } else if (it.t === "quad") {
      var shape = it.shape || "rect";
      var qt = shape === "rect" ? this.white : this.texture("shape:" + shape, function () {
        var c = canvas(256, 256), x = c.getContext("2d");
        x.fillStyle = "#fff"; x.strokeStyle = "#fff";
        if (shape === "dot") { x.beginPath(); x.arc(128, 128, 120, 0, Math.PI * 2); x.fill(); }
        else if (shape === "ring") { x.lineWidth = 18; x.beginPath(); x.arc(128, 128, 112, 0, Math.PI * 2); x.stroke(); }
        else if (shape === "frame") { x.lineWidth = 20; x.strokeRect(10, 10, 236, 236); }
        else { roundRect(x, 4, 4, 248, 248, 40); x.fill(); }
        return c;
      });
      this.draw(this.meshes.quad, mul(translate(it.x, -it.y, (it.z || 0) + 0.004), scale(it.w, it.h, 1)),
                {a: it.color, tex: qt, texWhere: 2, unlit: true, alpha: it.alpha == null ? 1 : it.alpha});
    } else if (it.t === "text") {
      var size = it.size || 0.4, tw = Math.max(1, it.text.length) * size * 0.7 + size * 0.4;
      var hpx = Math.min(256, Math.max(16, Math.round(size * ppu * 1.3)));
      var wpx = Math.round(hpx * tw / size);
      var xt = this.texture("text:" + [it.text, it.color, hpx, it.weight].join("|"), function () {
        return textCanvas(it.text, hpx * 0.78, it.color, it.weight || 700, null, null, wpx, hpx);
      });
      var off = it.align === "left" ? tw / 2 : it.align === "right" ? -tw / 2 : 0;
      this.draw(this.meshes.quad, mul(translate(it.x + off, -it.y, (it.z || 0) + 0.006), scale(tw, size, 1)),
                {a: "#ffffff", tex: xt, texWhere: 2, unlit: true, alpha: it.alpha == null ? 1 : it.alpha});
    }
  };

  function roundRect(x, l, t, w, h, r) {
    x.beginPath(); x.moveTo(l + r, t); x.arcTo(l + w, t, l + w, t + h, r); x.arcTo(l + w, t + h, l, t + h, r);
    x.arcTo(l, t + h, l, t, r); x.arcTo(l, t, l + w, t, r); x.closePath();
  }

  // ---- the 2D canvas renderer: the same scene from straight above -------------------------------------------
  function C2D(canvasEl) {
    this.cv = canvasEl; this.kind = "canvas";
    this.ctx = canvasEl.getContext("2d");
    this.cache = {};
    var self = this;
    this.cam = {
      unproject: function (px, py) { return {x: (px - self.ox) / self.k, y: (py - self.oy) / self.k}; },
      project: function (x, y) { return {x: self.ox + x * self.k, y: self.oy + y * self.k}; }
    };
  }
  C2D.prototype.resize = GL.prototype.resize;
  C2D.prototype.render = function (view, items, bg) {
    var sz = this.resize(), W = sz[0], H = sz[1], x = this.ctx, pad = view.pad || 0.4, wn = view.win;
    if (wn) {
      this.k = Math.min(W / wn.w, H / wn.h);
      this.ox = W / 2 - (wn.x + wn.w / 2) * this.k; this.oy = H / 2 - (wn.y + wn.h / 2) * this.k;
    } else {
      this.k = Math.min(W / (view.w + 2 * pad), H / (view.h + 2 * pad));
      this.ox = (W - view.w * this.k) / 2; this.oy = (H - view.h * this.k) / 2;
    }
    x.setTransform(1, 0, 0, 1, 0, 0);
    x.fillStyle = bg || "#1b1f24"; x.fillRect(0, 0, W, H);
    x.setTransform(this.k, 0, 0, this.k, this.ox, this.oy);
    var self = this;
    var order = {board: 0, terrain: 0.5, box: 1, shadow: 2, quad: 3, text: 3};
    items.slice().sort(function (a, b) {
      var oa = order[a.t] == null ? 4 : order[a.t], ob = order[b.t] == null ? 4 : order[b.t];
      return oa - ob || (a.z || 0) - (b.z || 0);
    }).forEach(function (it) { self.item(it); });
  };
  C2D.prototype.item = function (it) {
    var x = this.ctx, k = this.k, self = this;
    if (it.t === "board") {
      var pw = Math.round(it.w * k), ph = Math.round(it.h * k), key = it.key + pw + "x" + ph;
      if (!this.cache[key]) { var c = canvas(pw, ph); it.paint(c.getContext("2d"), pw, ph); this.cache = {}; this.cache[key] = c; }
      x.fillStyle = it.frame || "#333"; x.fillRect(-0.06, -0.06, it.w + 0.12, it.h + 0.12);
      x.drawImage(this.cache[key], 0, 0, it.w, it.h);
    } else if (it.t === "terrain") {
      var hs = it.h, n = hs.length, ty = it.y, bot = ty + (it.bottom == null ? 1.5 : it.bottom), x0 = it.x || 0;
      x.beginPath(); x.moveTo(x0, bot); x.lineTo(x0, ty - hs[0]);
      for (var ti = 0; ti < n; ti++) x.lineTo(x0 + ti + 0.5, ty - hs[ti]);
      x.lineTo(x0 + n, ty - hs[n - 1]); x.lineTo(x0 + n, bot); x.closePath();
      var tg2 = x.createLinearGradient(0, ty - 64, 0, bot);
      tg2.addColorStop(0, shade(it.b, 0.12)); tg2.addColorStop(1, shade(it.b, -0.35));
      x.fillStyle = tg2; x.fill();
      x.beginPath(); x.moveTo(x0, ty - hs[0]);
      for (ti = 0; ti < n; ti++) x.lineTo(x0 + ti + 0.5, ty - hs[ti]);
      x.lineTo(x0 + n, ty - hs[n - 1]);
      x.strokeStyle = it.a; x.lineWidth = (it.band == null ? 0.7 : it.band) * 0.8; x.lineJoin = "round"; x.stroke();
    } else if (it.t === "box") {
      var g = x.createLinearGradient(it.x - it.w / 2, it.y - it.h / 2, it.x + it.w / 2, it.y + it.h / 2);
      g.addColorStop(0, shade(it.color, 0.18)); g.addColorStop(1, shade(it.color, -0.2));
      if (it.rot) {
        x.save(); x.translate(it.x, it.y); x.rotate(-it.rot);
        g = x.createLinearGradient(-it.w / 2, -it.h / 2, it.w / 2, it.h / 2);
        g.addColorStop(0, shade(it.color, 0.18)); g.addColorStop(1, shade(it.color, -0.2));
        x.fillStyle = g; roundRect(x, -it.w / 2, -it.h / 2, it.w, it.h, Math.min(it.r == null ? 0.05 : it.r, it.w / 2, it.h / 2)); x.fill();
        x.restore();
        return;
      }
      x.fillStyle = g; roundRect(x, it.x - it.w / 2, it.y - it.h / 2, it.w, it.h, Math.min(it.r == null ? 0.05 : it.r, it.w / 2, it.h / 2)); x.fill();
      if (it.paint) {
        var bk = "box" + it.key;
        if (!this.cache[bk]) { var bc = canvas(256, 256); it.paint(bc.getContext("2d"), 256, 256); this.cache[bk] = bc; }
        x.drawImage(this.cache[bk], it.x - it.w / 2, it.y - it.h / 2, it.w, it.h);
      }
    } else if (it.t === "shadow") {
      var r = it.r * 1.1, sg = x.createRadialGradient(it.x + r * 0.12, it.y + r * 0.1, 0, it.x + r * 0.12, it.y + r * 0.1, r);
      sg.addColorStop(0, "rgba(0,0,0," + (0.45 * (it.alpha == null ? 0.8 : it.alpha)) + ")"); sg.addColorStop(1, "rgba(0,0,0,0)");
      x.fillStyle = sg; x.fillRect(it.x - r * 1.2, it.y - r * 1.2, r * 2.6, r * 2.6);
    } else if (it.t === "disc") {
      var f = it.flip || 0, sy = Math.cos(f), top = sy >= 0 ? it.a : (it.b || it.a), lift = 1 + (it.z || 0) * 0.15;
      var ax = it.axis || [1, 0], ang = Math.atan2(ax[1], ax[0]);
      x.save(); x.translate(it.x, it.y); x.rotate(ang); x.scale(lift, Math.max(0.06, Math.abs(sy)) * lift); x.rotate(-ang);
      var rg = x.createRadialGradient(-it.r * 0.35, -it.r * 0.35, it.r * 0.1, 0, 0, it.r);
      rg.addColorStop(0, shade(top, 0.25)); rg.addColorStop(0.7, top); rg.addColorStop(1, shade(top, -0.3));
      x.beginPath(); x.arc(0, 0, it.r, 0, Math.PI * 2); x.fillStyle = rg; x.fill();
      x.lineWidth = it.r * 0.06; x.strokeStyle = shade(top, -0.35); x.stroke();
      if (it.mark) { x.globalAlpha = 0.5; x.strokeStyle = it.markInk; x.lineWidth = it.r * 0.06; x.beginPath(); x.arc(0, 0, it.r * 0.6, 0, Math.PI * 2); x.stroke(); x.globalAlpha = 1; }
      x.restore();
    } else if (it.t === "tile") {
      var w = it.w, h = it.h, lf = 1 + ((it.lift || 0) + (it.z || 0)) * 0.25;
      x.save(); x.translate(it.x, it.y); if (it.rot) x.rotate(it.rot); x.scale(lf, lf);
      var tg = x.createLinearGradient(0, -h / 2, 0, h / 2);
      tg.addColorStop(0, shade(it.color, 0.12)); tg.addColorStop(1, shade(it.color, -0.12));
      x.fillStyle = "rgba(0,0,0,0.35)"; roundRect(x, -w / 2 + 0.03, -h / 2 + 0.05, w, h, w * 0.14); x.fill();
      x.fillStyle = tg; roundRect(x, -w / 2, -h / 2, w, h, w * 0.14); x.fill();
      if (it.paint) {
        var pw = Math.max(8, Math.round(w * k * lf)), ph = Math.max(8, Math.round(h * k * lf)), pk = "tilep" + it.key + pw + "x" + ph;
        if (!this.cache[pk]) { var pc = canvas(pw, ph); it.paint(pc.getContext("2d"), pw, ph); this.cache[pk] = pc; }
        x.save(); roundRect(x, -w / 2, -h / 2, w, h, w * 0.14); x.clip(); x.drawImage(this.cache[pk], -w / 2, -h / 2, w, h); x.restore();
      } else if (it.text) {
        // text at its own pixel size, not scaled: sharper
        var kk = k * lf;
        x.scale(1 / kk, 1 / kk);
        x.fillStyle = it.ink; x.textAlign = "center"; x.textBaseline = "middle";
        x.font = "700 " + Math.round(h * 0.62 * kk) + "px " + FONT; x.fillText(it.text, (it.sub ? -w * 0.04 : 0) * kk, h * 0.04 * kk);
        if (it.sub) { x.font = "600 " + Math.round(h * 0.22 * kk) + "px " + FONT; x.textAlign = "right"; x.textBaseline = "alphabetic"; x.fillStyle = it.subInk || it.ink; x.fillText(it.sub, w * 0.4 * kk, h * 0.38 * kk); }
      }
      x.restore();
    } else if (it.t === "die") {
      var s = it.s, q = it.q, ang2 = 2 * Math.atan2(q[2], q[3]);
      // the face nearest the top, from the orientation
      var m = qmat(q), up = [m[8], m[9], m[10]], faceN = topFace(m);
      x.save(); x.translate(it.x, it.y); x.rotate(-ang2);
      var tilt = Math.min(1, Math.hypot(up[0], up[1]) * 1.4);
      x.scale(1 + tilt * 0.1, 1 + tilt * 0.1);
      x.fillStyle = "rgba(0,0,0,0.3)"; roundRect(x, -s / 2 + 0.04, -s / 2 + 0.06, s, s, s * 0.18); x.fill();
      var dk = "die" + (it.kind || "") + it.ink + it.face + faceN;
      if (!this.cache[dk]) {
        var dc = canvas(128, 128), dx = dc.getContext("2d");
        roundRect(dx, 0, 0, 128, 128, 22); dx.clip();
        if (it.kind === "cube") paintCubeFace(dx, 0, 0, 128, [0, 64, 2, 4, 32, 16, 8][faceN], it.ink, it.face);
        else paintPips(dx, 0, 0, 128, faceN, it.ink, it.face);
        this.cache[dk] = dc;
      }
      x.drawImage(this.cache[dk], -s / 2, -s / 2, s, s);
      x.restore();
    } else if (it.t === "quad") {
      x.save(); x.globalAlpha = it.alpha == null ? 1 : it.alpha; x.fillStyle = it.color; x.strokeStyle = it.color;
      var sh = it.shape || "rect";
      if (sh === "dot") { x.beginPath(); x.ellipse(it.x, it.y, it.w / 2, it.h / 2, 0, 0, Math.PI * 2); x.fill(); }
      else if (sh === "ring") { x.lineWidth = it.w * 0.07; x.beginPath(); x.ellipse(it.x, it.y, it.w * 0.44, it.h * 0.44, 0, 0, Math.PI * 2); x.stroke(); }
      else if (sh === "frame") { x.lineWidth = it.w * 0.08; x.strokeRect(it.x - it.w * 0.46, it.y - it.h * 0.46, it.w * 0.92, it.h * 0.92); }
      else if (sh === "round") { roundRect(x, it.x - it.w / 2, it.y - it.h / 2, it.w, it.h, Math.min(it.w, it.h) * 0.16); x.fill(); }
      else x.fillRect(it.x - it.w / 2, it.y - it.h / 2, it.w, it.h);
      x.restore();
    } else if (it.t === "text") {
      x.save(); x.globalAlpha = it.alpha == null ? 1 : it.alpha; x.fillStyle = it.color;
      x.setTransform(1, 0, 0, 1, 0, 0);
      x.font = (it.weight || 700) + " " + Math.round((it.size || 0.4) * 0.78 * k) + "px " + FONT;
      x.textAlign = it.align || "center"; x.textBaseline = "middle";
      x.fillText(it.text, this.ox + it.x * k, this.oy + (it.y + (it.size || 0.4) * 0.04) * k);
      x.restore();
    }
    void self;
  };
  function topFace(m) {
    // which of the die's faces points most nearly up: faces 1 +z, 6 -z, 2 +x, 5 -x, 3 +y, 4 -y
    var c = [[1, m[10]], [6, -m[10]], [2, m[2]], [5, -m[2]], [3, m[6]], [4, -m[6]]];
    c.sort(function (a, b) { return b[1] - a[1]; });
    return c[0][0];
  }
  function shade(c, t) {
    var v = hex(c).map(function (x) { return t > 0 ? x + (1 - x) * t : x * (1 + t); });
    return "rgb(" + v.map(function (x) { return Math.round(x * 255); }).join(",") + ")";
  }

  function make(canvasEl, prefer) {
    if (prefer !== "canvas") {
      try { return new GL(canvasEl); } catch (e) { if (window.console) console.warn("WebGL not available:", e.message); }
    }
    return new C2D(canvasEl);
  }

  return {make: make, quat: quat, qmul: qmul, slerp: slerp, dieFace: dieFace, shade: shade, hex: hex,
          roundRect: roundRect, FONT: FONT, canvas: canvas};
})();
