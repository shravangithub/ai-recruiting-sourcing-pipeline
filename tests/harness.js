// Runs a Code node from setup/pipeline.workflow.json outside n8n, with the same
// globals n8n gives it ($input, $(), $json, $now, $execution, $workflow).
const path = require('path');
const WF = require(path.join(__dirname, '..', 'setup', 'pipeline.workflow.json'));
const NODES = Object.fromEntries(WF.nodes.map((n) => [n.name, n]));
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

let DateTime = null;
try { DateTime = require('luxon').DateTime; } catch (e) { /* optional */ }
const now = DateTime ? DateTime.fromISO('2026-10-27T10:30:00', { zone: 'Asia/Kolkata' })
  : { toFormat: (f) => (f === 'LLLL yyyy' ? 'October 2026' : '2026-10-27 10:30') };

function wrap(items) { return items.map((j) => (j && j.json ? j : { json: j })); }

async function runNode(name, input = [], refs = {}, opts = {}) {
  const node = NODES[name];
  if (!node) throw new Error('no node ' + name);
  const code = node.parameters.jsCode;
  const inItems = wrap(input);
  const refItems = Object.fromEntries(Object.entries(refs).map(([k, v]) => [k, wrap(v)]));
  const itemIndex = opts.itemIndex || 0;
  const $ = (n) => {
    if (!refItems[n]) throw new Error('node "' + n + '" has not run');
    return { first: () => refItems[n][0], all: () => refItems[n], item: refItems[n][itemIndex] || refItems[n][0] };
  };
  const ctx = { helpers: {} };
  const mode = node.parameters.mode || 'runOnceForAllItems';
  const f = new AsyncFunction('$input', '$', '$json', '$now', '$execution', '$workflow', '$runIndex', 'DateTime', code);
  if (mode === 'runOnceForEachItem') {
    const out = [];
    for (let i = 0; i < inItems.length; i++) {
      const $i = (n) => ({ first: () => refItems[n][0], all: () => refItems[n], item: (refItems[n] || [])[i] || (refItems[n] || [])[0] });
      const r = await f.call(ctx, { all: () => inItems, first: () => inItems[0], item: inItems[i] }, $i, inItems[i].json, now, { id: '999' }, { id: 'wf' }, 0, DateTime);
      out.push(r);
    }
    return out.map((r) => (r && r.json ? r.json : r));
  }
  const r = await f.call(ctx, { all: () => inItems, first: () => inItems[0] }, $, inItems[0] && inItems[0].json, now, { id: '999' }, { id: 'wf' }, 0, DateTime);
  return (r || []).map((x) => (x && x.json ? x.json : x));
}

// a realistic Search Criteria item (what the form + AI parser produce)
function criteria(over = {}) {
  return Object.assign({
    'Target Role': 'Java backend developer', 'Key Skills & Criteria': 'Java, Spring Boot, Microservices',
    'Primary Skills': 'Java, Spring Boot, Microservices', 'Secondary Skills': '', 'Use Secondary Skills': true,
    'Location': 'Bangalore', 'Preferred Industry': '', 'Current Employer': '', 'Keywords to Exclude': '',
    'Education': 'Any', 'Expand Job Title Synonyms': 'No', 'Experience Range (Years)': '3-5 years',
    'Gender Preference': 'Any', 'Target Platforms': ['LinkedIn', 'Github'], 'Search Region': '', nl_used: true,
  }, over);
}

function aiOutput(arr) { return { output: [{ content: [{ text: typeof arr === 'string' ? arr : JSON.stringify(arr) }] }] }; }

module.exports = { WF, NODES, runNode, criteria, aiOutput };
