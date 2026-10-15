#!/usr/bin/env python3
"""Build the hosted copy of the dashboard: dividend-calendar.html.

Differences from index.html, all for a page that is opened by link rather
than served from this folder:
  * the data is read from the artifact's shared store, falling back to the
    snapshot baked into the file, so a refresh reaches every open copy;
  * calendar ticks are stored per viewer, so the same account sees one set
    of ticks on a laptop and a phone;
  * controls that need this folder (import/export, save-to-server)
    and the Counters tab are hidden.
"""
import re
import sys

SYNC_JS = r'''
// ---------------------------------------------------------------- sync -----
// Ticks belong to the person, not the browser: tick on a laptop and the same
// account sees it on a phone. The dividend data is read from the same store,
// so a refresh reaches every open copy without republishing this page.
// Everything below is additive -- with no store (signed out, or opened as a
// plain file) the page keeps working on localStorage alone.
let SYNC = {db:null, uid:null, ticks:null, state:"local"};
let tickWrite = Promise.resolve();

function paintSync(state){
  SYNC.state = state;
  const el = document.getElementById("syncChip");
  if(!el) return;
  const copy = {
    live:   ["● Synced",      "Ticks and data follow your account across devices."],
    local:  ["○ This device", "Sign in to claude.ai to carry ticks between devices."],
  }[state] || ["○ This device",""];
  el.textContent = copy[0];
  el.title = copy[1];
  el.classList.toggle("on", state==="live");
}

// Writes are coalesced: one in flight per document, and only on a real change.
function pushTicks(){
  if(!SYNC.ticks) return;
  tickWrite = tickWrite.then(()=>SYNC.ticks.set({ticked: checks, at: new Date().toISOString()}))
                       .catch(()=>{ /* refused or offline: localStorage still holds it */ });
}

async function initSync(){
  if(!(window.claude && typeof window.claude.use === "function")) return;
  let db = null;
  try{ db = await claude.use("db"); }catch(e){ return; }
  if(!db) return;
  SYNC.db = db;

  // Shared dividend snapshot. Newer than the file? Take it.
  db.doc("snapshot/current").onSnapshot(snap=>{
    if(!snap.exists) return;
    const d = snap.data();
    if(!d || !Array.isArray(d.counters) || !d.counters.length) return;
    if(DATA && DATA.lastUpdated && d.lastUpdated && d.lastUpdated <= DATA.lastUpdated) return;
    DATA = {schema:d.schema, lastUpdated:d.lastUpdated,
            counterCount:d.counters.length, counters:d.counters};
    buildFilterOptions();
    render();
  }, ()=>{});

  let user = null;
  try{ user = await claude.use("user"); }catch(e){}
  const uid = user ? await user.id() : null;
  if(!uid){ paintSync("local"); return; }   // no private subtree for this viewer
  SYNC.uid = uid;
  SYNC.ticks = db.doc("data/users/" + uid + "/ticks");

  SYNC.ticks.onSnapshot(snap=>{
    const incoming = (snap.exists && (snap.data()||{}).ticked) || {};
    for(const k in checks) delete checks[k];
    for(const k in incoming){
      const v = incoming[k];
      if(v && typeof v === "object") checks[k] = {cal:!!v.cal, rec:!!v.rec};
    }
    saveChecksLocal();
    paintSync("live");
    render();
  }, ()=>paintSync("local"));
}
'''


def build(src: str) -> str:
    out = src

    # Data ships inside the page; the store supplies anything newer.
    old = re.search(r"async function loadData\(\)\{.*?\n\}\n", out, re.S)
    out = out.replace(old.group(0), '''async function loadData(){
  // Hosted copy: the snapshot is embedded, and initSync() upgrades it from the
  // shared store when this viewer can reach one.
  DATA = EMBEDDED_DATA;
  SOURCE = "snapshot";
  if(!DATA){ $("#board").innerHTML='<div class="empty">No data available.</div>'; return; }
  collapseAll();
  buildFilterOptions();
  render();
  initSync();
}
''', 1)

    # Keep the local write, add the synced one.
    out = out.replace(
        'function saveChecks(){ try{ localStorage.setItem(CHECKS_KEY, JSON.stringify(checks)); }catch(e){} }',
        'function saveChecksLocal(){ try{ localStorage.setItem(CHECKS_KEY, JSON.stringify(checks)); }catch(e){} }\n'
        'function saveChecks(){ saveChecksLocal(); pushTicks(); }', 1)

    # Controls that need the project folder, and the roster, are not for viewers.
    for a, b in [
        ('<label class="chip" title="Open an updated dividends.json (when viewing without a server)">',
         '<label class="chip hidden" title="Open an updated dividends.json (when viewing without a server)">'),
        ('<button class="ghost" id="expBtn"', '<button class="ghost hidden" id="expBtn"'),
        ('<label class="chip" title="Load a previously exported overrides.json">',
         '<label class="chip hidden" title="Load a previously exported overrides.json">'),
        ('<button class="ghost" id="saveBtn"', '<button class="ghost hidden" id="saveBtn"'),
        ('<div class="tabs" role="tablist">', '<div class="tabs hidden" role="tablist">'),
    ]:
        if a not in out:
            raise SystemExit("build_shared: anchor missing -> " + a[:60])
        out = out.replace(a, b, 1)

    # Header: data age plus whether this view is syncing.
    head = ('<div class="updated">Data: <b id="srcLabel">—</b> · '
            'Last updated <b id="updatedAt">—</b></div>')
    out = out.replace(head,
        '<div class="updated">Data as of <b id="updatedAt">—</b> '
        '<span id="syncChip" class="syncchip">○ This device</span>'
        '<span id="srcLabel" class="hidden"></span></div>', 1)

    out = out.replace('  .hidden{display:none !important}',
        '  .syncchip{margin-left:8px; font-size:11px; padding:2px 8px; border-radius:999px;\n'
        '            border:1px solid var(--line); color:var(--muted); white-space:nowrap}\n'
        '  .syncchip.on{background:var(--green-bg); color:var(--green); border-color:transparent}\n'
        '  .hidden{display:none !important}', 1)

    foot = re.search(r'\$\("#foot"\)\.innerHTML=.*?;\n', out, re.S)
    out = out.replace(foot.group(0),
        '$("#foot").innerHTML="Ticks are saved to your Claude account, so the same account sees "\n'
        '  +"them on every device. Signed out, they stay in this browser.";\n', 1)

    out = out.replace('\nloadData();\n', SYNC_JS + '\nloadData();\n', 1)

    # Unwrap: the publish skeleton supplies doctype, head and body.
    out = re.sub(r'^<!DOCTYPE html>\s*<html lang="en">\s*<head>\s*', '', out)
    out = re.sub(r'<meta charset="utf-8">\s*', '', out)
    out = re.sub(r'<meta name="viewport"[^>]*>\s*', '', out)
    out = out.replace('</head>\n<body>\n', '', 1)
    out = re.sub(r'\s*</body>\s*</html>\s*$', '\n', out)
    out = out.replace('<title>Dividend Tracker</title>', '<title>Dividend Calendar</title>', 1)
    return out


if __name__ == "__main__":
    dest = sys.argv[1] if len(sys.argv) > 1 else "dividend-calendar.html"
    with open("index.html") as fh:
        html = build(fh.read())
    with open(dest, "w") as fh:
        fh.write(html)
    assert "<html" not in html and "</head>" not in html
    print("wrote %s  %.0f KB" % (dest, len(html) / 1024))
