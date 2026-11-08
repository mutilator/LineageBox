// The photo frame (photo_frame.html, routes/frame.py): asks for its list of photos, shows each in turn with a slow
// fade and its date and caption, small, and keeps going whatever happens to the server. A tap or click anywhere (or →)
// shows the next one now, to look through them all before the link is shared.
//
// Written for old browsers on purpose (an old tablet, a TV, a dashboard): no arrow functions, let, fetch or promises
// of its own. While the server doesn't answer (restarting, the network down) the frame keeps cycling through the
// photos it has; pictures that don't load are passed over. The list is asked for again now and then, and the page
// itself is loaded again twice a day (a new version), but only once the server is seen to answer.
(function () {
  "use strict";
  var body = document.body;
  // the frame's words in the page's language (i18n.py puts them in #lineagebox-text; none in English), as app.js t()
  var text = {};
  try {
    text = JSON.parse((document.getElementById("lineagebox-text") || {}).textContent || "{}").text || {};
  } catch (e) {}
  function t(s) { return typeof text[s] === "string" && text[s] ? text[s] : s; }
  if (!body || !body.getAttribute("data-photos")) return;

  var listUrl = body.getAttribute("data-photos");
  var base = body.getAttribute("data-base") || "";
  var seconds = Number(body.getAttribute("data-seconds")) || 20;
  var refresh = (Number(body.getAttribute("data-refresh")) || 1800) * 1000;
  var RELOAD = 12 * 3600 * 1000;  // the page itself, now and then: an update reaches the frame
  var VIDEO_MOST = 90 * 1000;     // a video plays this long at most, muted
  var started = new Date().getTime();

  var stage = body.querySelector(".stage");
  var pictures = stage.querySelectorAll("img.picture");
  var video = stage.querySelector("video.picture");
  var when = body.querySelector(".when");
  var caption = body.querySelector(".caption");
  var note = body.querySelector(".note");
  var full = body.querySelector(".full");

  var photos = [];
  var at = -1;          // the photo shown now
  var front = 0;        // which of the two pictures is in front
  var timer = null;
  var failedInARow = 0;
  var wakeLock = null;

  function say(text) {
    note.textContent = text || "";
    note.style.display = text ? "block" : "none";
  }

  function get(url, done, failed) {
    var xhr = new XMLHttpRequest();
    xhr.open("GET", url, true);
    xhr.timeout = 30000;
    xhr.onreadystatechange = function () {
      if (xhr.readyState !== 4) return;
      if (xhr.status === 200) {
        var data = null;
        try { data = JSON.parse(xhr.responseText); } catch (e) { data = null; }
        if (data) { done(data); return; }
      }
      failed(xhr.status);
    };
    try { xhr.send(); } catch (e) { failed(0); }
  }

  // the list of photos: kept as it was when the server doesn't answer
  function load(first) {
    get(listUrl, function (data) {
      var fresh = data.photos || [];
      if (data.seconds) seconds = Number(data.seconds) || seconds;
      if (data.title) document.title = data.title + " · LineageBox";
      var wasEmpty = !photos.length;
      photos = fresh;
      if (at >= photos.length) at = -1;
      if (!photos.length) {
        clearTimeout(timer);
        hideAll();
        say(t("There are no photos to show here yet."));
      } else if (wasEmpty) {
        say("");
        next();
      }
      if (new Date().getTime() - started > RELOAD) { location.reload(); return; }
      setTimeout(function () { load(false); }, refresh);
    }, function (status) {
      if (status === 404 || status === 401 || status === 403) {
        photos = [];
        clearTimeout(timer);
        hideAll();
        say(t("This photo frame's link was turned off. Ask whoever set it up for a new one."));
        setTimeout(function () { load(false); }, refresh);
        return;
      }
      if (!photos.length) say(t("The photos can't be reached just now. Trying again in a minute…"));
      setTimeout(function () { load(false); }, first || !photos.length ? 60000 : refresh);
    });
  }

  function hideAll() {
    for (var i = 0; i < pictures.length; i++) pictures[i].className = "picture";
    video.className = "picture";
    when.textContent = caption.textContent = "";
  }

  function words(p) {
    when.textContent = p.when || "";
    caption.textContent = p.caption || "";
  }

  function later(wait) {
    clearTimeout(timer);
    timer = setTimeout(next, wait);
  }

  function next() {
    if (!photos.length) return;
    at = (at + 1) % photos.length;
    var p = photos[at];
    if (p.video && video.canPlayType && video.canPlayType("video/mp4")) play(p);
    else picture(p);
  }

  // A picture is shown once it has loaded, over the one before, which then fades out.
  function picture(p) {
    var img = pictures[1 - front];
    img.onload = function () {
      failedInARow = 0;
      img.className = "picture shown";
      pictures[front].className = "picture";
      video.className = "picture";
      stopVideo();
      front = 1 - front;
      words(p);
      later(seconds * 1000);
    };
    img.onerror = function () {
      failedInARow++;
      // none of them loads: the server is away. The last picture stays; try again in a minute.
      later(failedInARow >= Math.min(photos.length, 5) ? 60000 : 1000);
    };
    img.src = base + (p.video ? p.poster : p.src);
  }

  function stopVideo() {
    if (!video.getAttribute("src")) return;
    try { video.pause(); } catch (e) { /* nothing to stop */ }
    video.removeAttribute("src");
    try { video.load(); } catch (e) { /* an old browser */ }
  }

  function play(p) {
    var done = false;
    function end() {
      if (done) return;
      done = true;
      later(0);
    }
    video.onended = end;
    video.onerror = function () { done = true; picture(p); };
    video.onplaying = function () {
      failedInARow = 0;
      video.className = "picture shown";
      for (var i = 0; i < pictures.length; i++) pictures[i].className = "picture";
      words(p);
      later(VIDEO_MOST);
    };
    video.poster = base + p.poster;
    video.src = base + p.src;
    var playing = video.play && video.play();
    if (playing && playing["catch"]) playing["catch"](function () { done = true; picture(p); });
    later(VIDEO_MOST);
  }

  // the screen kept on, where the browser can (the Wake Lock API, over HTTPS); asked again when the page shows again
  function keepAwake() {
    if (!navigator.wakeLock || document.hidden) return;
    navigator.wakeLock.request("screen").then(function (lock) { wakeLock = lock; }, function () { wakeLock = null; });
  }
  document.addEventListener("visibilitychange", function () { if (!document.hidden) keepAwake(); });

  // Full screen: a button shown for a few seconds, and again whenever the screen is touched or the mouse moves
  var fullTimer = null;
  function showButton() {
    var can = document.documentElement.requestFullscreen || document.documentElement.webkitRequestFullscreen;
    if (!can || document.fullscreenElement || document.webkitFullscreenElement) return;
    full.style.display = "block";
    clearTimeout(fullTimer);
    fullTimer = setTimeout(function () { full.style.display = "none"; }, 6000);
  }
  full.addEventListener("click", function () {
    var el = document.documentElement;
    var go = el.requestFullscreen || el.webkitRequestFullscreen;
    if (go) go.call(el);
    full.style.display = "none";
    keepAwake();
  });
  document.addEventListener("mousemove", showButton);
  document.addEventListener("touchstart", showButton);
  document.addEventListener("keydown", function (e) {
    if (e.key === "ArrowRight" || e.keyCode === 39) { later(0); }
  });
  // a tap or click anywhere but the Full screen button: the next photo now
  document.addEventListener("click", function (e) {
    if (e.target === full || !photos.length) return;
    later(0);
  });

  say(t("Getting the photos…"));
  showButton();
  keepAwake();
  load(true);
})();
