#!/usr/bin/env node
// sf_cohort.js — READ-ONLY Salesforce fallback for the Brain Day-0 cohort demo pull.
//
// Used ONLY when the Brain MCP is unreachable (NordVPN off). Pulls the same Day-0
// cohort demos straight from Salesforce (Lead records; David's CDF1 formula computed
// client-side because SOQL WHERE can't compare two fields) and writes a BRAIN-SHAPED
// raw file, so cohort_demos.py's aggregation runs unchanged:
//
//   node sf_cohort.js daily   <ANCHOR>      # YYYY-MM-DD (default: yesterday, NY)
//   node sf_cohort.js weekly  <WEEK_END>    # the Sunday ending the Mon–Sun week
//   node sf_cohort.js monthly <YYYY-MM>     # calendar month
//   → writes data/<dir>/cohort_raw_sf.json  ({"origin":"salesforce","result":"{\"rows\":[…]}"})
//   then run: python3 cohort_demos.py data/<dir>/cohort_raw_sf.json <period> <key>
//
// Credentials come from ~/.claude.json (the `salesforce` MCP server's env block) —
// never from this folder. EVERY Salesforce call in this file is a SELECT (SOQL).
// Salesforce is HARD READ-ONLY — do not add anything else (see
// Master Context/SalesForce/CLAUDE.md).
//
// Basis note: SF Day-0 (UTM first-click attribution, demo-scheduled-date == lead-
// created-date) runs ABOVE the Brain Day-0 cohort (~10-25%) — different attribution
// basis, documented, not a bug. The report labels which basis was used.

const fs = require('fs');
const os = require('os');
const path = require('path');

const CHANNEL = 'bing';          // Lead.UTM_Source__c filter (Google copy: 'google-ads')
const SOURCE  = 'bing';          // SOURCE value cohort_demos.py filters on (Google copy: 'google-ads')

function loadJsforce() {
  try { return require('jsforce'); } catch (e) {
    return require('/opt/mcp/MCP/salesforce-mcp/node_modules/jsforce');
  }
}
const jsforce = loadJsforce();

const TZ = 'America/New_York';
const nyDateOf = dt => new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(dt));
const nyToday = () => nyDateOf(new Date());
function addDays(iso, n) {
  const d = new Date(iso + 'T12:00:00Z');
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}
const dow = iso => new Date(iso + 'T12:00:00Z').getUTCDay(); // 0=Sun
function lastCompletedSunday() {
  let d = addDays(nyToday(), -1);
  while (dow(d) !== 0) d = addDays(d, -1);
  return d;
}
function lastCompletedMonth() {
  const t = nyToday();
  let [y, m] = [parseInt(t.slice(0, 4)), parseInt(t.slice(5, 7))];
  m -= 1; if (m === 0) { m = 12; y -= 1; }
  return `${y}-${String(m).padStart(2, '0')}`;
}
function monthEnd(ym) {
  const [y, m] = [parseInt(ym.slice(0, 4)), parseInt(ym.slice(5, 7))];
  return new Date(Date.UTC(y, m, 0, 12)).toISOString().slice(0, 10);
}
function addMonths(ym, n) { // YYYY-MM + n months -> YYYY-MM
  let [y, m] = [parseInt(ym.slice(0, 4)), parseInt(ym.slice(5, 7))];
  m += n;
  y += Math.floor((m - 1) / 12);
  m = ((m - 1) % 12 + 12) % 12 + 1;
  return `${y}-${String(m).padStart(2, '0')}`;
}

async function queryAll(conn, soql) { // queryMore loop (monthly window ≈ 5k records)
  let res = await conn.query(soql);
  const out = res.records;
  while (!res.done) { res = await conn.queryMore(res.nextRecordsUrl); out.push(...res.records); }
  return out;
}

(async () => {
  const period = process.argv[2];
  if (!['daily', 'weekly', 'monthly'].includes(period)) {
    console.error('Usage: node sf_cohort.js <daily|weekly|monthly> [key]'); process.exit(1);
  }
  let key = process.argv[3];
  let start, end, dir;
  if (period === 'daily') {
    key = key || addDays(nyToday(), -1);
    start = addDays(key, -29); end = key;                  // 30-day chart window (build.CLICKS_DAYS)
    dir = key;
  } else if (period === 'weekly') {
    key = key || lastCompletedSunday();
    if (dow(key) !== 0) { console.error(`weekly key must be a Sunday, got ${key}`); process.exit(1); }
    start = addDays(key, -83); end = key;                  // 12 Mon–Sun weeks (cohort_demos.weekly_grid)
    dir = `weekly-${key}`;
  } else {
    key = key || lastCompletedMonth();
    start = `${addMonths(key, -11)}-01`; end = monthEnd(key); // 12 calendar months
    dir = `monthly-${key}`;
  }

  const cfg = JSON.parse(fs.readFileSync(path.join(os.homedir(), '.claude.json'), 'utf8'));
  const env = cfg.mcpServers && cfg.mcpServers.salesforce && cfg.mcpServers.salesforce.env;
  if (!env || !env.SALESFORCE_USERNAME) { console.error('❌ Salesforce creds not found in ~/.claude.json'); process.exit(1); }
  const conn = new jsforce.Connection({ loginUrl: env.SALESFORCE_INSTANCE_URL || 'https://login.salesforce.com' });
  await conn.login(env.SALESFORCE_USERNAME, env.SALESFORCE_PASSWORD + env.SALESFORCE_TOKEN);
  console.log(`Logged in (read-only). Day-0 cohort window ${start} → ${end} (${period} ${key}, ${CHANNEL})`);

  // WHERE bounds padded ±1 day (UTC offset safety); cohort_demos.py re-filters to the
  // exact window, so over-pulling at the edges is harmless.
  const lo = addDays(start, -1) + 'T00:00:00Z';
  const hi = addDays(end, 2) + 'T00:00:00Z';
  const recs = await queryAll(conn,
    `SELECT Lead_Created_Date__c, Original_Demo_Scheduled_At__c, Demo_Scheduled_At__c, campaign_ID__c ` +
    `FROM Lead WHERE Test_Account__c = false AND UTM_Source__c = '${CHANNEL}' AND ` +
    `((Original_Demo_Scheduled_At__c >= ${lo} AND Original_Demo_Scheduled_At__c < ${hi}) OR ` +
    `(Original_Demo_Scheduled_At__c = null AND Demo_Scheduled_At__c >= ${lo} AND Demo_Scheduled_At__c < ${hi}))`);

  // Day-0 (CDF1): the demo-scheduled date (Original, falling back to Demo_Scheduled_At__c
  // only when Original is blank) equals the lead-created date — both as NY calendar days.
  const agg = new Map(); // "DT|CAMPAIGN_ID" -> count
  let day0 = 0;
  for (const r of recs) {
    const sched = r.Original_Demo_Scheduled_At__c || r.Demo_Scheduled_At__c;
    if (!sched || !r.Lead_Created_Date__c) continue;
    const sd = nyDateOf(sched);
    if (sd !== nyDateOf(r.Lead_Created_Date__c)) continue; // not same-day → not Day-0 cohort
    day0++;
    const cid = r.campaign_ID__c || null;
    const k = `${sd}|${cid}`;
    agg.set(k, (agg.get(k) || 0) + 1);
  }
  const rows = [...agg.entries()].sort().map(([k, n]) => {
    const [dt, cid] = k.split('|');
    return { DT: dt, SOURCE, CAMPAIGN_ID: cid === 'null' ? null : cid, DEMO_SCHEDULED_AT_DAY0: n };
  });

  const outDir = path.join(__dirname, 'data', dir);
  fs.mkdirSync(outDir, { recursive: true });
  const outPath = path.join(outDir, 'cohort_raw_sf.json');
  fs.writeFileSync(outPath, JSON.stringify({
    origin: 'salesforce',
    channel: CHANNEL,
    window: [start, end],
    pulled_at: new Date().toISOString(),
    result: JSON.stringify({ rows }),
  }));
  console.log(`Saved ${outPath} (${recs.length} demo leads → ${day0} Day-0 cohort, ${rows.length} day×campaign rows)`);
  const lastDay = rows.filter(r => r.DT === end).reduce((a, r) => a + r.DEMO_SCHEDULED_AT_DAY0, 0);
  console.log(`SANITY [${CHANNEL} ${period} ${key}]: Day-0 demos on ${end} = ${lastDay}`);
  console.log(`Next: python3 cohort_demos.py "data/${dir}/cohort_raw_sf.json" ${period} ${key}`);
})().catch(e => { console.error('FATAL :: ' + (e && e.message || e)); process.exit(1); });
