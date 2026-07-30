/* boot.js — data loader for the fetch()-based port.
   Loads ONLY the default All-Sources payload up front (index + daily-history + changelog +
   quality ≈ 185 KB gz), re-creates the four <script id> blobs engine.js reads, then loads
   engine.js. The heavy platform files (google/microsoft/meta.json) are lazy-loaded by
   engine.js on first tab open (see the port-frontend.py lazy patch), so google.json's
   ~850 KB gz never hits the default page load.

   `mc-data` is injected WITHOUT google/microsoft/meta; engine.js fills DATA[platform] on
   demand. index.json is exactly mc-data minus those three objects. */
(function () {
  "use strict";
  var DATA_BASE = "data/";

  function getText(file) {
    return fetch(DATA_BASE + file).then(function (r) {
      if (!r.ok) throw new Error(file + " -> HTTP " + r.status);
      return r.text();
    });
  }

  function inject(id, text) {
    var s = document.createElement("script");
    s.type = "application/json";
    s.id = id;
    s.textContent = text;
    document.body.appendChild(s);
  }

  Promise.all([
    getText("index.json"),        // mc-data minus the platform objects
    getText("daily-history.json"),
    getText("changelog.json"),
    getText("quality.json"),
  ]).then(function (r) {
    inject("mc-data", r[0]);
    inject("daily-history", r[1]);
    inject("mc-changelog", r[2]);
    inject("mc-quality", r[3]);
    var e = document.createElement("script");
    e.src = "engine.js";
    document.body.appendChild(e);
  }).catch(function (err) {
    document.body.insertAdjacentHTML(
      "afterbegin",
      '<div style="background:#fee;color:#900;padding:12px;font:14px system-ui,sans-serif">' +
        "Failed to load report data: " + String(err) + "</div>"
    );
    console.error(err);
  });
})();
