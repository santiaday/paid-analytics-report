#!/usr/bin/env node
// pipeline_sf.js — READ-ONLY Salesforce pull for the Meta daily report's booked-on-day
// Opportunities / Accounts / ARR (the Metric Grid pipeline tiles).
//
//   node pipeline_sf.js <ANCHOR>     # YYYY-MM-DD (default: yesterday, NY) → 30-day window
//   → writes data/<ANCHOR>/pipeline_30d.json  (SAME shape pipeline_funnel.py emits, so
//     build.py's load_pipeline + clicks_daily_data consume it unchanged; data_source=salesforce)
//
// WHY Salesforce (not the Brain) for Meta: the Brain has NO booked-on-day Meta opps/accounts/ARR
// (its Meta funnel `ma-funnel-daily` only carries a Day-14 cohort `opp_14d`, no accounts/ARR). So
// Salesforce — the source of truth — IS the booked-on-day source here, exactly the verification
// David asked for. Basis (matches David's SF report templates, verified 2026-06-11):
//   • Opportunities = Opportunity records, booked by CreatedDate (Opportunity-created date).
//   • Accounts + ARR = Account records, booked by Stripe_Subscription_First_Invoice_At__c;
//     ARR = SUM(Stripe_Subscription_ARR__c).
//   • Meta attribution = Account.UTM_Source__c IN (fb,ig,fb_broken,'{{site_source_name}}'),
//     FIRST-CLICK UTM (= the same first-touch basis the Brain uses for Meta).
// Campaign attribution = UTM_Campaign__c matched to the report's active campaigns (spend_cur.json);
// anything else (retired/other Meta campaigns) → "unattributed" and listed in mismatch_ids (flagged).
//
// Credentials come from ~/.claude.json (the `salesforce` MCP env). EVERY call here is a SELECT.
// Salesforce is HARD READ-ONLY — never add a write (see Master Context/SalesForce/CLAUDE.md).

const fs = require('fs');
const os = require('os');
const path = require('path');

const CHANNELS = ['fb', 'ig', 'fb_broken', '{{site_source_name}}'];
const N_DAYS = 30;

function loadJsforce() {
  try { return require('jsforce'); } catch (e) {
    return require('/opt/mcp/MCP/salesforce-mcp/node_modules/jsforce');
  }
}
const jsforce = loadJsforce();

const TZ = 'America/New_York';
const nyDateOf = dt => new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(dt));
const nyToday = () => nyDateOf(new Date());
function addDays(iso, n) { const d = new Date(iso + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); }

async function queryAll(conn, soql) {
  let res = await conn.query(soql); const out = res.records;
  while (!res.done) { res = await conn.queryMore(res.nextRecordsUrl); out.push(...res.records); }
  return out;
}

(async () => {
  const anchor = process.argv[2] || addDays(nyToday(), -1);
  const start = addDays(anchor, -(N_DAYS - 1)), end = anchor;
  const days = []; for (let i = 0; i < N_DAYS; i++) days.push(addDays(start, i));
  const didx = new Map(days.map((d, i) => [d, i]));

  // active report campaigns (so UTM_Campaign__c can be matched to a chart campaign)
  const spendPath = path.join(__dirname, 'data', anchor, 'spend_cur.json');
  const activeNames = new Map(); // lower -> canonical
  if (fs.existsSync(spendPath)) {
    for (const r of JSON.parse(fs.readFileSync(spendPath, 'utf8'))) {
      const n = r['campaign.name'] || r.campaign_name;
      if (n) activeNames.set(String(n).toLowerCase(), n);
    }
  }

  const cfg = JSON.parse(fs.readFileSync(path.join(os.homedir(), '.claude.json'), 'utf8'));
  const env = cfg.mcpServers && cfg.mcpServers.salesforce && cfg.mcpServers.salesforce.env;
  if (!env || !env.SALESFORCE_USERNAME) { console.error('❌ Salesforce creds not in ~/.claude.json'); process.exit(1); }
  const conn = new jsforce.Connection({ loginUrl: env.SALESFORCE_INSTANCE_URL || 'https://login.salesforce.com' });
  await conn.login(env.SALESFORCE_USERNAME, env.SALESFORCE_PASSWORD + env.SALESFORCE_TOKEN);
  console.log(`Logged in (read-only). Meta pipeline window ${start} → ${end}`);

  const lo = addDays(start, -1) + 'T00:00:00Z', hi = addDays(end, 2) + 'T00:00:00Z';
  const inList = CHANNELS.map(c => `'${c}'`).join(',');

  const opps = await queryAll(conn,
    `SELECT CreatedDate, Account.UTM_Campaign__c FROM Opportunity WHERE Test_Account__c = false ` +
    `AND Account.UTM_Source__c IN (${inList}) AND CreatedDate >= ${lo} AND CreatedDate < ${hi}`);
  const accts = await queryAll(conn,
    `SELECT Stripe_Subscription_First_Invoice_At__c, UTM_Campaign__c, Stripe_Subscription_ARR__c FROM Account ` +
    `WHERE Test_Account__c = false AND UTM_Source__c IN (${inList}) ` +
    `AND Stripe_Subscription_First_Invoice_At__c >= ${lo} AND Stripe_Subscription_First_Invoice_At__c < ${hi}`);

  const campaigns = {};                                   // canonical name -> {opps,accts,arr}
  const unatt = { opps: arr0(), accts: arr0(), arr: arr0() };
  const mism = {};                                        // raw campaign -> {opps,accts,arr}
  function arr0() { return new Array(N_DAYS).fill(0); }
  function cell(name) { return campaigns[name] || (campaigns[name] = { opps: arr0(), accts: arr0(), arr: arr0() }); }
  function route(rawCamp, dt, key, val) {
    const i = didx.get(dt); if (i === undefined) return;
    const canon = activeNames.get(String(rawCamp || '').toLowerCase());
    if (canon) { cell(canon)[key][i] += val; }
    else {
      unatt[key][i] += val;
      const k = rawCamp || '(none)';
      (mism[k] || (mism[k] = { label: k, opps: 0, accts: 0, arr: 0 }))[key] += val;
    }
  }
  for (const o of opps) route(o.Account && o.Account.UTM_Campaign__c, nyDateOf(o.CreatedDate), 'opps', 1);
  for (const a of accts) {
    const dt = nyDateOf(a.Stripe_Subscription_First_Invoice_At__c);
    route(a.UTM_Campaign__c, dt, 'accts', 1);
    route(a.UTM_Campaign__c, dt, 'arr', +(a.Stripe_Subscription_ARR__c || 0));
  }

  const round = a => a.map(v => Math.round(v * 100) / 100);
  for (const c of Object.values(campaigns)) { c.opps = round(c.opps); c.accts = round(c.accts); c.arr = round(c.arr); }
  unatt.opps = round(unatt.opps); unatt.accts = round(unatt.accts); unatt.arr = round(unatt.arr);
  const sum = a => a.reduce((x, y) => x + y, 0);
  const out = {
    metric_source: 'salesforce', anchor, days,
    campaigns, unattributed: unatt,
    mismatch_ids: Object.entries(mism).sort((a, b) => b[1].opps - a[1].opps)
      .map(([id, v]) => ({ id, label: v.label, opps: v.opps, accts: v.accts, arr: Math.round(v.arr * 100) / 100 })),
    totals: {
      opps: sum(Object.values(campaigns).map(c => sum(c.opps))),
      accts: sum(Object.values(campaigns).map(c => sum(c.accts))),
      arr: Math.round(sum(Object.values(campaigns).map(c => sum(c.arr))) * 100) / 100,
      unattr_opps: sum(unatt.opps), unattr_accts: sum(unatt.accts), unattr_arr: Math.round(sum(unatt.arr) * 100) / 100,
    },
  };
  const outDir = path.join(__dirname, 'data', anchor);
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(path.join(outDir, 'pipeline_30d.json'), JSON.stringify(out));
  const t = out.totals;
  console.log(`Saved data/${anchor}/pipeline_30d.json  (source=salesforce, ${Object.keys(campaigns).length} campaigns)`);
  console.log(`  attributed: opps=${t.opps} accts=${t.accts} arr=$${t.arr.toLocaleString()}`);
  console.log(`  unattributed: opps=${t.unattr_opps} accts=${t.unattr_accts} arr=$${t.unattr_arr.toLocaleString()} (${out.mismatch_ids.length} other Meta campaigns)`);
})().catch(e => { console.error('FATAL :: ' + (e && e.message || e)); process.exit(1); });
