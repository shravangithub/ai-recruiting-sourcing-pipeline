// Structural checks on the generated workflow: wiring, references, sheet columns.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { WF, NODES } = require('./harness');

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const handler = fs.readFileSync(path.join(__dirname, '..', 'setup', 'src', 'handler.js'), 'utf8');
const SHEET_COLUMNS = eval(handler.match(/const SHEET_COLUMNS = (\[[\s\S]*?\]);/)[1]);
const LOG_COLUMNS = eval(handler.match(/const LOG_COLUMNS = (\[[\s\S]*?\]);/)[1]);

test('every Code node compiles', () => {
  for (const n of WF.nodes.filter((x) => x.type.endsWith('.code'))) {
    assert.doesNotThrow(() => new AsyncFunction('$input', '$', '$json', '$now', '$execution', '$workflow', n.parameters.jsCode), n.name);
  }
});

test('connections only point at real nodes', () => {
  for (const [src, v] of Object.entries(WF.connections)) {
    assert.ok(NODES[src], 'missing source ' + src);
    for (const br of v.main || []) for (const t of br || []) assert.ok(NODES[t.node], src + ' -> missing ' + t.node);
  }
});

test('no step runs twice: only Merge nodes take more than one incoming branch', () => {
  const inc = {};
  for (const [src, v] of Object.entries(WF.connections)) for (const br of v.main || []) for (const t of br || []) (inc[t.node] = inc[t.node] || new Set()).add(src);
  const EITHER_OR = { 'Prep JD': ['Extract JD PDF', 'Prep Word JD'],          // mutually exclusive branches of "JD Is Word?"
    'Log Search Complete': ['Results Gate', 'Sourcing Summary'] };            // Results Gate only fires when nothing was written
  for (const [node, srcs] of Object.entries(inc)) {
    if (srcs.size < 2 || NODES[node].type.endsWith('.merge')) continue;
    assert.deepEqual([...srcs].sort(), (EITHER_OR[node] || []).slice().sort(), node + ' has fan-in from ' + [...srcs].join(', '));
  }
});

test("every $('Node') reference exists", () => {
  const txt = JSON.stringify(WF);
  for (const m of txt.matchAll(/\$\('([^']+)'\)/g)) assert.ok(NODES[m[1]], 'unknown node referenced: ' + m[1]);
});

test('all 11 sources feed the single "Wait For All Sources" merge', () => {
  const merge = NODES['Wait For All Sources'];
  assert.equal(merge.parameters.numberInputs, 11);
  const feeding = Object.entries(WF.connections).filter(([, v]) => (v.main || []).some((br) => (br || []).some((t) => t.node === 'Wait For All Sources')));
  assert.equal(feeding.length, 11);
});

test('results sheet writes only to columns the setup creates', () => {
  const cols = Object.keys(NODES['Append row in sheet'].parameters.columns.value);
  for (const c of cols) assert.ok(SHEET_COLUMNS.includes(c), 'column not in sheet: ' + c);
  for (const n of ['Mark Contacted', 'Mark Replied']) for (const c of Object.keys(NODES[n].parameters.columns.value)) assert.ok(SHEET_COLUMNS.includes(c), n + ': ' + c);
  for (const n of ['Log Search Start', 'Log Search Complete']) for (const c of Object.keys(NODES[n].parameters.columns.value)) assert.ok(LOG_COLUMNS.includes(c), n + ': ' + c);
});

test('form fields read by the workflow exist on the form', () => {
  const labels = NODES['On form submission'].parameters.formFields.values.map((f) => f.fieldLabel).filter(Boolean);
  const code = NODES['Search Criteria'].parameters.jsCode + NODES['Prep JD'].parameters.jsCode;
  for (const m of code.matchAll(/form\['([^']+)'\]|json\['([^']+)'\]/g)) {
    const label = m[1] || m[2];
    if (/^\d|Natural Language|Job Description/.test(label)) assert.ok(labels.includes(label), 'form field missing: ' + label);
  }
});

test('no account-specific values or vendor placeholders ship in the template', () => {
  const txt = JSON.stringify(WF);
  for (const bad of ['YOUR_', 'REPLACE_WITH', 'TalentRubric', 'recruitment-alerts', 'n8n credits']) assert.ok(!txt.includes(bad), bad);
  for (const n of WF.nodes) for (const c of Object.values(n.credentials || {})) assert.match(c.id, /^__CRED_[A-Z]+__$/, n.name);
});

test('outreach email is professional (no n8n footer, no hard-coded firm in subject)', () => {
  const p = NODES['Send Outreach Email'].parameters;
  assert.notEqual(p.options && p.options.appendAttribution, true, 'n8n attribution footer would be appended to candidate emails');
});

test('outreach poller does not run every minute', () => {
  const rule = NODES['Outreach Schedule'].parameters.rule.interval[0];
  assert.ok(!(rule.field === 'minutes' && !rule.minutesInterval), 'polls the sheet 1,440 times a day');
});

test('UAT: a zero-result search still marks the Search Log row Complete', () => {
  const targets = (WF.connections['Results Gate'].main[0] || []).map((t) => t.node);
  assert.ok(targets.includes('Log Search Complete'));
});

test('UAT: the sheet write never overwrites "Send Email?" with a constant', () => {
  assert.notEqual(NODES['Append row in sheet'].parameters.columns.value['Send Email?'], 'FALSE');
});

test('UAT: confirmation page does not promise Slack posts', () => {
  const t = NODES['On form submission'].parameters.options.respondWithOptions.values.formSubmittedText;
  assert.ok(!/also post to the recruitment Slack channel/.test(t));
});

test('UAT: new rows get a "Send Email?" checkbox after every search (rule only, no values, so no gaps)', () => {
  const targets = (WF.connections['Append row in sheet'].main[0] || []).map((t) => t.node);
  assert.ok(targets.includes('Tick Boxes'));
  const n = NODES['Tick Boxes'];
  assert.equal(n.executeOnce, true);
  assert.equal(n.onError, 'continueRegularOutput');
  const body = JSON.parse(n.parameters.jsonBody);
  const req = body.requests[0].setDataValidation;
  assert.equal(req.rule.condition.type, 'BOOLEAN');
  assert.equal(req.range.endRowIndex, undefined, 'a fixed end row can exceed the sheet and fail');
  assert.equal(req.range.startColumnIndex, SHEET_COLUMNS.indexOf('Send Email?'));
});

test('UAT: setup checkbox rule is open-ended too', () => {
  assert.ok(!/endRowIndex: 5000/.test(handler));
});
