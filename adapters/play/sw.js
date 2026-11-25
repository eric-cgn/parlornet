// ParlorNet Play: the service worker, for push notifications only (docs/play-notifications.md). It caches
// nothing and handles no requests: Play needs the game server to be any use, so there is no offline mode.
// A message carries the kind, the table, the game's name and the other player's name, for a join what that
// player plays with ("with GameMaster 1.0.0 on a Macintosh Plus"), and nothing about the game itself
// (adapters/playpush.py).
"use strict";

self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (e) { e.waitUntil(self.clients.claim()); });

function text(d) {
  var g = d.g || "a game", w = d.w || "", t = d.t ? "Table " + d.t + "." : "";
  if (d.k === "turn") return {title: "Your turn in " + g, body: (w ? "Against " + w + ". " : "") + t};
  if (d.k === "end") return {title: "Your " + g + " game has ended", body: (w ? "Against " + w + ". " : "") + t};
  if (d.k === "join") {
    var v = d.v ? String(d.v) : "";
    return {title: (w || "Someone") + " sat down at your " + g + " table",
            body: (v ? v.charAt(0).toUpperCase() + v.slice(1) + ". " : "") + t};
  }
  return {title: "ParlorNet Play", body: t};
}

self.addEventListener("push", function (e) {
  var d = {};
  try { d = e.data ? e.data.json() : {}; } catch (x) { d = {}; }
  var m = text(d), table = /^\d{1,9}$/.test(String(d.t || "")) ? String(d.t) : "";
  e.waitUntil(self.registration.showNotification(m.title, {
    body: m.body, icon: "icon-192.png", tag: table ? "pn" + table : "pn", renotify: true,
    data: {url: new URL(table ? "./#/table/" + table : "./", self.registration.scope).href}
  }));
});

self.addEventListener("notificationclick", function (e) {
  e.notification.close();
  var url = (e.notification.data && e.notification.data.url) || self.registration.scope;
  e.waitUntil(self.clients.matchAll({type: "window", includeUncontrolled: true}).then(function (list) {
    var mine = list.filter(function (c) { return c.url.indexOf(self.registration.scope) === 0; })[0];
    if (mine) {
      return mine.focus().then(function (c) { return c && c.navigate ? c.navigate(url).catch(function () { return c; }) : c; });
    }
    return self.clients.openWindow(url);
  }));
});
