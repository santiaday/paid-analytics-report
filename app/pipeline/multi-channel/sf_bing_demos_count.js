#!/usr/bin/env node
// sf_bing_demos_count.js — READ-ONLY Salesforce count of Bing demos, used by
// ms_conversion_check.py to tell a GENUINE-zero day apart from a broken Zapier pipe.
//
// The Demo_Scheduled offline-conversion pipe is: demo booked in Salesforce → Zapier →
// Google Sheet → Microsoft Ads import. Salesforce is the pipe's UPSTREAM source. So if
// Microsoft shows 0 Demo_Scheduled conversions for a window, there are two possibilities:
//   • BROKEN pipe: SF has Bing demos, but they never reached Microsoft (sf count > 0).
//   • GENUINE zero: no Bing demos existed to sync (sf count == 0) — NOT a broken pipe.
// This script returns the count so the health check can suppress the false alarm on a
// genuine-zero day (David 2026-07-13). SELECT-only — Salesforce is HARD READ-ONLY
// (see Master Context/SalesForce/CLAUDE.md). Never add anything that writes.
//
//   node sf_bing_demos_count.js <START YYYY-MM-DD> <END YYYY-MM-DD>
//   → prints JSON to stdout: {"source":"salesforce","start","end","total":N,"byday":{...}}
//   Login/diagnostic lines go to stderr so stdout stays clean JSON for the caller.
//
// "demos" = original demos scheduled (Original_Demo_Scheduled_At__c populated), source bing
// (UTM_Source__c='bing'), bucketed by NY calendar day — the same demo definition the rest of
// the pipeline uses. Credentials come from ~/.claude.json (the `salesforce` MCP env block).
const fs = require('fs');
const os = require('os');
const path = require('path');

function loadJsforce() {
  try { return require('jsforce'); } catch (e) {
    return require('/opt/mcp/MCP/salesforce-mcp/node_modules/jsforce');
  }
}
const jsforce = loadJsforce();

const TZ = 'America/New_York';
const nyDateOf = dt => new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(dt));
function addDays(iso, n) { const d = new Date(iso + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); }

async function queryAll(conn, soql) {
  let res = await conn.query(soql);
  const out = res.records;
  while (!res.done) { res = await conn.queryMore(res.nextRecordsUrl); out.push(...res.records); }
  return out;
}

(async () => {
  const start = process.argv[2], end = process.argv[3];
  if (!start || !end) { console.error('Usage: node sf_bing_demos_count.js <START YYYY-MM-DD> <END YYYY-MM-DD>'); process.exit(1); }
  const cfg = JSON.parse(fs.readFileSync(path.join(os.homedir(), '.claude.json'), 'utf8'));
  const env = cfg.mcpServers && cfg.mcpServers.salesforce && cfg.mcpServers.salesforce.env;
  if (!env || !env.SALESFORCE_USERNAME) { console.error('❌ Salesforce creds not found in ~/.claude.json'); process.exit(1); }
  const conn = new jsforce.Connection({ loginUrl: env.SALESFORCE_INSTANCE_URL || 'https://login.salesforce.com' });
  await conn.login(env.SALESFORCE_USERNAME, env.SALESFORCE_PASSWORD + env.SALESFORCE_TOKEN);
  console.error(`Logged in (read-only). Bing demos ${start} → ${end}`);

  // Pad the UTC window by a day each side, then bucket by NY calendar day so DST can't skew it.
  const lo = addDays(start, -1) + 'T00:00:00Z', hi = addDays(end, 1) + 'T00:00:00Z';
  const soql = `SELECT Id, Original_Demo_Scheduled_At__c FROM Lead WHERE UTM_Source__c = 'bing' `
    + `AND Original_Demo_Scheduled_At__c >= ${lo} AND Original_Demo_Scheduled_At__c < ${hi}`;
  const recs = await queryAll(conn, soql);

  const byday = {};
  for (const r of recs) {
    const v = r.Original_Demo_Scheduled_At__c;
    if (!v) continue;
    const d = nyDateOf(v);
    if (d >= start && d <= end) byday[d] = (byday[d] || 0) + 1;
  }
  const total = Object.values(byday).reduce((a, b) => a + b, 0);
  process.stdout.write(JSON.stringify({ source: 'salesforce', start, end, total, byday }));
})().catch(e => { console.error('SF error:', e.message || e); process.exit(2); });
