#!/usr/bin/env python3
"""Port the self-contained multi-channel report into a fetch()-based static bundle.

Phase 0 of the DeployBay re-platform. The existing pipeline (`multi-channel/build.py`)
emits ONE self-contained `Paid-Analytics.html` with four inlined JSON blobs
(`mc-data`, `daily-history`, `mc-changelog`, `mc-quality`). The frontend (`engine.js`)
reads those blobs by id via `getElementById(...).textContent`. Nothing else in the page
is coupled to the data at runtime.

This script performs the mechanical transform that unlocks the whole re-platform:
  1. Split the four inlined blobs OUT of the HTML into fetchable files under public/data/.
     `mc-data` is further split per-platform (index + google + microsoft + meta) so the
     frontend can load the default All-Sources tab (~185 KB gz) and lazy-load each
     platform only when its tab is opened (google.json alone is ~850 KB gz).
  2. Externalise the inlined `engine.js` block to public/engine.js (byte-identical to the
     source module `multi-channel/engine.js`).
  3. Emit public/index.html = the ORIGINAL page shell (head + CSS + DOM + adapters JS),
     byte-identical EXCEPT the data blobs and engine.js are removed and a single
     <script src="boot.js"> loader is appended. boot.js fetches the data, re-creates the
     four <script id> tags the engine expects, then loads engine.js.

The output is provably faithful because everything but data-delivery is preserved verbatim
from a known-good `build.py` render. Phase 1 replaces the `--baseline` input's data with
the middleware's S3 output; the shell/engine extraction is unchanged.

Usage:
  python3 port-frontend.py --baseline <Paid-Analytics.html> --engine <engine.js> --out <public/>
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys

BLOB_IDS = ["mc-data", "daily-history", "mc-changelog", "mc-quality"]
ENGINE_SIGNATURE = "engine.js — client-side engine for the multi-channel unified report"


def unescape_blob(s: str) -> str:
    """Reverse build.py's `</` -> `<\\/` script-tag-safety escaping."""
    return s.replace("<\\/", "</")


def extract_blob(html: str, blob_id: str) -> tuple[str, str]:
    """Return (inner_text, full_tag) for <script type=application/json id=blob_id>."""
    pat = re.compile(
        r'<script type="application/json" id="%s">(.*?)</script>' % re.escape(blob_id),
        re.S,
    )
    m = pat.search(html)
    if not m:
        sys.exit(f"ERROR: blob '{blob_id}' not found in baseline HTML")
    return unescape_blob(m.group(1)), m.group(0)


def find_engine_block(html: str) -> str:
    """Return the full <script>...</script> block that contains the engine.js IIFE.

    engine.js has no literal '</script>' (or build.py could not have inlined it), so the
    first '</script>' after the signature is its true closer.
    """
    sig = html.find(ENGINE_SIGNATURE)
    if sig == -1:
        sys.exit("ERROR: engine.js block not found in baseline HTML")
    open_tag = html.rfind("<script", 0, sig)
    close = html.find("</script>", sig)
    if open_tag == -1 or close == -1:
        sys.exit("ERROR: could not delimit the engine.js <script> block")
    return html[open_tag:close + len("</script>")]


def write_json_str(path: str, text: str) -> int:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return len(text.encode("utf-8"))


# Injected into showTab() so a platform tab fetches its own data (google/microsoft/meta.json)
# the first time it is opened, then re-runs showTab to init normally. Keeps the default page
# load to the ~185 KB gz All-Sources payload. Applied by string-replace on two unique anchors.
LAZY_GUARD = """      /* lazy platform load (paid-analytics-ui port): fetch DATA[key] on first open */
      if ((key === "google" || key === "microsoft" || key === "meta") && !DATA[key]) {
        if (!loadingTab[key]) {
          loadingTab[key] = true;
          var _pane = document.getElementById("tab-" + key);
          if (_pane && !_pane.querySelector(".tab-loading")) {
            var _ld = document.createElement("div");
            _ld.className = "tab-loading";
            _ld.style.cssText = "padding:48px;text-align:center;color:#64748B;font:15px system-ui,sans-serif";
            _ld.textContent = "Loading " + key + " data\\u2026";
            _pane.appendChild(_ld);
          }
          fetch("data/" + key + ".json").then(function (r) {
            if (!r.ok) throw new Error(key + ".json HTTP " + r.status);
            return r.json();
          }).then(function (obj) {
            DATA[key] = obj;
            loadingTab[key] = false;
            var _l = document.querySelector("#tab-" + key + " .tab-loading");
            if (_l) _l.remove();
            showTab(key);
          }).catch(function (err) {
            loadingTab[key] = false;
            var _l = document.querySelector("#tab-" + key + " .tab-loading");
            if (_l) { _l.textContent = "Failed to load " + key + " data: " + err.message; _l.style.color = "#dc2626"; }
            console.error(err);
          });
        }
        return;
      }
"""


def apply_lazy_patch(engine_src: str) -> str:
    """Two surgical edits so platform data lazy-loads on first tab open. Each anchor must be
    unique, or we abort rather than silently mis-patch."""
    decl = "var inited = {};"
    guard_anchor = "if (!inited[key]) {"
    for anchor in (decl, guard_anchor):
        n = engine_src.count(anchor)
        if n != 1:
            sys.exit(f"ERROR: lazy-patch anchor {anchor!r} found {n} times (expected 1) — aborting")
    engine_src = engine_src.replace(decl, "var inited = {}, loadingTab = {};", 1)
    engine_src = engine_src.replace(guard_anchor, guard_anchor + "\n" + LAZY_GUARD, 1)
    return engine_src


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True, help="known-good Paid-Analytics.html")
    ap.add_argument("--engine", required=True, help="source multi-channel/engine.js")
    ap.add_argument("--out", required=True, help="public/ output dir")
    ap.add_argument("--no-lazy", action="store_true",
                    help="emit engine.js verbatim (no lazy patch); use with an eager boot.js")
    args = ap.parse_args()

    html = open(args.baseline, encoding="utf-8").read()
    data_dir = os.path.join(args.out, "data")
    os.makedirs(data_dir, exist_ok=True)

    # 1. Pull the four blobs out of the HTML.
    blobs: dict[str, str] = {}
    for bid in BLOB_IDS:
        inner, full = extract_blob(html, bid)
        blobs[bid] = inner
        html = html.replace(full, "")

    # 1a. Split mc-data per-platform: index = everything except the heavy platform objects.
    data = json.loads(blobs["mc-data"])
    platforms = ["google", "microsoft", "meta"]
    index = {k: v for k, v in data.items() if k not in platforms}
    written = []
    written.append(("index.json", write_json_str(
        os.path.join(data_dir, "index.json"), json.dumps(index, separators=(",", ":")))))
    for p in platforms:
        if p in data:
            written.append((f"{p}.json", write_json_str(
                os.path.join(data_dir, f"{p}.json"),
                json.dumps(data[p], separators=(",", ":")))))
    # 1b. Aux blobs kept whole (small; all load with the default tab).
    for bid, fname in (("daily-history", "daily-history.json"),
                       ("mc-changelog", "changelog.json"),
                       ("mc-quality", "quality.json")):
        written.append((fname, write_json_str(os.path.join(data_dir, fname), blobs[bid])))

    # 2. Externalise engine.js (copy the SOURCE module — identical to the inlined block,
    #    but the canonical source avoids any stray inlining artefacts).
    engine_block = find_engine_block(html)
    html = html.replace(engine_block, "")
    engine_src = open(args.engine, encoding="utf-8").read()
    if not args.no_lazy:
        engine_src = apply_lazy_patch(engine_src)
    write_json_str(os.path.join(args.out, "engine.js"), engine_src)

    # 3. Append the boot loader where the data + engine used to be (just before </body>).
    if "<script src=\"boot.js\"></script>" not in html:
        html = html.replace("</body>", '<script src="boot.js"></script>\n</body>', 1)
    with open(os.path.join(args.out, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

    # 3a. Copy the static boot loader so --out is a COMPLETE, servable site (index.html +
    #     engine.js + boot.js + data/). boot.js is hand-authored (doesn't vary by build).
    boot_src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "public", "boot.js")
    if os.path.exists(boot_src):
        with open(boot_src, encoding="utf-8") as bf:
            boot_js = bf.read()
        write_json_str(os.path.join(args.out, "boot.js"), boot_js)

    print(f"wrote {args.out}/index.html  ({len(html.encode()):,} B shell, data removed)")
    print(f"wrote {args.out}/engine.js   ({len(engine_src.encode()):,} B, from source)")
    for name, n in written:
        print(f"wrote {args.out}/data/{name}  ({n:,} B)")


if __name__ == "__main__":
    main()
