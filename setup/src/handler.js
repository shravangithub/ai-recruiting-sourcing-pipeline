// Setup API: runs inside the recruiter's own n8n (Code node, "Run once for all items").
// Every action needs the recruiter's n8n API key, so nobody else can use this endpoint.
// Execution saving is switched off for this workflow, so keys never land in n8n's logs.

const TEMPLATE_URL = '__TEMPLATE_URL__';
const EMBEDDED_TEMPLATE = __EMBEDDED_TEMPLATE__; // null = download TEMPLATE_URL

const req = $input.first().json;
let body = req.body;
if (typeof body === 'string') { try { body = JSON.parse(body); } catch (e) { body = {}; } }
body = body || {};
const h = req.headers || {};
const host = h['x-forwarded-host'] || h.host || '';
let proto = String(h['x-forwarded-proto'] || '').split(',')[0].trim();
if (!proto) proto = /^(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$/.test(host) ? 'http' : 'https';
const BASE = proto + '://' + host;
const KEY = String(body.n8nKey || '').trim();

const http = async (o) => {
  try {
    const r = await this.helpers.httpRequest(Object.assign({
      json: true, returnFullResponse: true, ignoreHttpStatusErrors: true, timeout: 30000,
    }, o));
    return { status: r.statusCode, body: r.body };
  } catch (e) {
    return { status: 0, body: { message: e.message } };
  }
};
// Where to reach this n8n's own API. Usually the address in the browser; but when Docker maps
// n8n to another port (e.g. -p 5679:5678) that address doesn't exist inside the container, so fall
// back to n8n's own port.
let API_BASE = null;
const apiLike = (r) => r.status > 0 && r.body && typeof r.body === 'object';
const apiBase = async () => {
  if (API_BASE) return API_BASE;
  const tries = [BASE, 'http://127.0.0.1:5678', 'http://localhost:5678'];
  for (const b of tries) {
    const r = await http({ method: 'GET', url: b + '/api/v1/workflows?limit=1', headers: { 'X-N8N-API-KEY': KEY } });
    if (apiLike(r)) { API_BASE = b; return b; }
  }
  API_BASE = BASE;
  return BASE;
};
const api = async (method, path, payload) => http({
  method, url: (await apiBase()) + '/api/v1' + path,
  headers: { 'X-N8N-API-KEY': KEY, 'Content-Type': 'application/json' },
  body: payload,
});
const msg = (r) => {
  const b = r.body;
  if (b && typeof b === 'object') return b.message || b.error?.message || b.error || JSON.stringify(b).slice(0, 200);
  return String(b || ('HTTP ' + r.status)).slice(0, 200);
};
const done = (response, extra) => [{ json: Object.assign({ response }, extra || {}) }];
const fail = (error) => done({ ok: false, error });

// ---------- shared definitions ----------
const CRED_NAMES = {
  OPENAI: 'Sourcing · OpenAI', SERPER: 'Sourcing · Serper', APOLLO: 'Sourcing · Apollo',
  GITHUB: 'Sourcing · GitHub', GITLAB: 'Sourcing · GitLab', KAGGLE: 'Sourcing · Kaggle',
  SHEETS: 'Sourcing · Google Sheets', DRIVE: 'Sourcing · Google Drive', GMAIL: 'Sourcing · Gmail',
  SLACK: 'Sourcing · Slack',
};
const OAUTH = ['SHEETS', 'DRIVE', 'GMAIL', 'SLACK'];
const PIPELINE_NAME = 'AI Recruiting Sourcing Pipeline';
const SHEET_COLUMNS = ['Rank', 'Name', 'Role', 'Fit Score', 'Summary', 'Key Skills', 'Verified Gender', 'Current Company',
  'Verified Title', 'Location', 'Location Review', 'Email', 'Phone', 'GitHub', 'Work History', 'Education', 'LinkedIn',
  'Resume Link', 'Date Added', 'Source Platform', 'Resume Source', 'Contact Source', 'Search Role', 'Search Location',
  'Search Geography', 'Pipeline Status', 'Outreach Status', 'Dedupe Key', 'Send Email?', 'Confidence', 'Profile URL', 'Experience Check'];
const LOG_COLUMNS = ['Run ID', 'Status', 'Role', 'Location', 'Candidates', 'Rows', 'Started At', 'Completed At'];
const USAGE_TAB_ID = 2134273428; // the pipeline's Sheets nodes point at this tab id
const LOG_TAB_ID = 1;

// Colours and layout for the results sheet and Search Log (Google Sheets batchUpdate requests).
// Rule order matters: the first matching rule wins for a cell.
function styleRequests(logTabId) {
  const rgb = (hex) => ({ red: parseInt(hex.slice(1, 3), 16) / 255, green: parseInt(hex.slice(3, 5), 16) / 255, blue: parseInt(hex.slice(5, 7), 16) / 255 });
  const col = (name) => SHEET_COLUMNS.indexOf(name);
  const all = { sheetId: 0, startRowIndex: 1, startColumnIndex: 0, endColumnIndex: SHEET_COLUMNS.length };
  const one = (name) => ({ sheetId: 0, startRowIndex: 1, startColumnIndex: col(name), endColumnIndex: col(name) + 1 });
  const fmt = (bg, fg, bold) => ({ backgroundColor: rgb(bg), textFormat: Object.assign({ bold: !!bold }, fg ? { foregroundColor: rgb(fg) } : {}) });
  const rule = (index, range, condition, format) => ({ addConditionalFormatRule: { index, rule: { ranges: [range], booleanRule: { condition, format } } } });
  const letter = (i) => String.fromCharCode(65 + i);
  const headerRow = (sheetId, n, bg, fg) => ({ repeatCell: { range: { sheetId, startRowIndex: 0, endRowIndex: 1, startColumnIndex: 0, endColumnIndex: n },
    cell: { userEnteredFormat: { backgroundColor: rgb(bg), textFormat: { bold: true, foregroundColor: rgb(fg || '#FFFFFF') }, verticalAlignment: 'MIDDLE', wrapStrategy: 'WRAP' } },
    fields: 'userEnteredFormat(backgroundColor,textFormat,verticalAlignment,wrapStrategy)' } });
  const lr = '$' + letter(col('Location Review')) + '2';
  const rowRule = (index, formula, bg, fg, bold) => rule(index, all, { type: 'CUSTOM_FORMULA', values: [{ userEnteredValue: formula }] }, fmt(bg, fg, bold));
  const reqs = [
    headerRow(0, SHEET_COLUMNS.length, '#E2E8F0', '#0F172A'),
    { updateSheetProperties: { properties: { sheetId: 0, gridProperties: { frozenRowCount: 1, frozenColumnCount: 2 }, tabColorStyle: { rgbColor: rgb('#A5B4FC') } },
      fields: 'gridProperties.frozenRowCount,gridProperties.frozenColumnCount,tabColorStyle' } },
    // light colours, whole row. First match wins, so the requisition row comes first.
    rowRule(0, '=$A2="REQ"', '#E0E7FF', '#312E81', true),                                  // requisition: light indigo, bold
    rowRule(1, '=ISNUMBER(SEARCH("Review:",' + lr + '))', '#FFEDD5', '#7C2D12', false),    // recruiter must review: light orange
    rowRule(2, '=LEFT(' + lr + ',1)="✅"', '#ECFDF5', null, false),                         // in the city: light green
    rowRule(3, '=AND(LEN(' + lr + ')>0,NOT(LEFT(' + lr + ',1)="✅"))', '#FEFCE8', null, false), // country only / not shown: light yellow
  ];
  if (logTabId !== undefined && logTabId !== null) {
    const logAll = { sheetId: logTabId, startRowIndex: 1, startColumnIndex: 0, endColumnIndex: LOG_COLUMNS.length };
    const logRule = (index, word, bg) => rule(index, logAll, { type: 'CUSTOM_FORMULA', values: [{ userEnteredValue: '=ISNUMBER(SEARCH("' + word + '",$B2))' }] }, fmt(bg, null, false));
    reqs.push(headerRow(logTabId, LOG_COLUMNS.length, '#CCFBF1', '#134E4A'),
      { updateSheetProperties: { properties: { sheetId: logTabId, gridProperties: { frozenRowCount: 1 }, tabColorStyle: { rgbColor: rgb('#5EEAD4') } },
        fields: 'gridProperties.frozenRowCount,tabColorStyle' } },
      logRule(0, 'Complete', '#ECFDF5'), logRule(1, 'Processing', '#EFF6FF'), logRule(2, 'Paused', '#FEF2F2'));
  }
  return reqs;
}


async function checkKey() {
  if (!KEY) return 'Paste your n8n API key first.';
  const r = await api('GET', '/workflows?limit=1');
  if (r.status === 200) return null;
  if (r.status === 401) return "That n8n API key didn't work. Create a new one in n8n under Settings → n8n API.";
  if (r.status === 403) return "Your n8n plan doesn't include the n8n API (it isn't available on the free trial). Upgrade, or run n8n yourself.";
  return 'Could not reach the n8n API: ' + msg(r);
}

// ---------- vendor key tests ----------
async function testService(service, v) {
  v = v || {};
  const need = (x) => String(x || '').trim();
  let r;
  switch (service) {
    case 'openai':
      if (!need(v.key)) return 'Paste a key first.';
      // a 1-token request: proves the key works AND the account has credits
      r = await http({ method: 'POST', url: 'https://api.openai.com/v1/chat/completions', headers: { Authorization: 'Bearer ' + need(v.key) },
        body: { model: 'gpt-4o-mini', max_tokens: 1, messages: [{ role: 'user', content: 'hi' }] } });
      if (r.status === 429 && /quota|credit|billing/i.test(msg(r))) return 'This key works, but the OpenAI account has no credits. Add credit at platform.openai.com/settings/organization/billing, then test again.';
      break;
    case 'serper':
      if (!need(v.key)) return 'Paste a key first.';
      r = await http({ method: 'POST', url: 'https://google.serper.dev/search', headers: { 'X-API-KEY': need(v.key) }, body: { q: 'recruiting', num: 1 } });
      break;
    case 'apollo':
      if (!need(v.key)) return 'Paste a key first.';
      r = await http({ method: 'GET', url: 'https://api.apollo.io/v1/auth/health', headers: { 'x-api-key': need(v.key), 'Cache-Control': 'no-cache' } });
      if (r.status === 200 && r.body && r.body.is_logged_in === false) return 'Apollo did not accept this key.';
      break;
    case 'github':
      if (!need(v.key)) return 'Paste a token first.';
      r = await http({ method: 'GET', url: 'https://api.github.com/user', headers: { Authorization: 'Bearer ' + need(v.key), 'User-Agent': 'ai-recruiting-sourcing-pipeline' } });
      break;
    case 'gitlab':
      if (!need(v.key)) return 'Paste a token first.';
      r = await http({ method: 'GET', url: 'https://gitlab.com/api/v4/user', headers: { 'PRIVATE-TOKEN': need(v.key) } });
      break;
    case 'kaggle':
      if (!need(v.user) || !need(v.key)) return 'Enter your Kaggle username and key.';
      // kernels/list needs a valid login (datasets/list answers even without one)
      r = await http({ method: 'GET', url: 'https://www.kaggle.com/api/v1/kernels/list?pageSize=1&search=python', auth: { username: need(v.user), password: need(v.key) } });
      if (r.status === 401 && /^KGAT_/i.test(need(v.key))) return 'This looks like a new-style Kaggle API token. Create a legacy API key instead (Kaggle > Settings > API > Create Legacy API Key) and paste that.';
      break;
    default:
      return 'Unknown service.';
  }
  if (r.status >= 200 && r.status < 300) return null;
  if (r.status === 401 || r.status === 403) return 'The key was rejected (' + r.status + '). Check it and try again.';
  if (r.status === 0) return 'Could not reach the service: ' + msg(r);
  return 'The service answered ' + r.status + ': ' + msg(r);
}

// ---------- credentials ----------
function credentialDefs(keys, oauth) {
  keys = keys || {}; oauth = oauth || {};
  const s = (x) => String(x || '').trim();
  const unset = 'not-configured';
  const google = s(oauth.googleClientId) ? { clientId: s(oauth.googleClientId), clientSecret: s(oauth.googleClientSecret) } : {};
  const slack = s(oauth.slackClientId) ? { clientId: s(oauth.slackClientId), clientSecret: s(oauth.slackClientSecret) } : {};
  return {
    OPENAI: { type: 'openAiApi', data: { apiKey: s(keys.openai) } },
    SERPER: { type: 'httpHeaderAuth', data: { name: 'X-API-KEY', value: s(keys.serper) } },
    APOLLO: { type: 'httpHeaderAuth', data: { name: 'x-api-key', value: s(keys.apollo) || unset } },
    GITHUB: { type: 'githubApi', data: { server: 'https://api.github.com', user: '', accessToken: s(keys.github) || unset } },
    GITLAB: { type: 'gitlabApi', data: { server: 'https://gitlab.com', accessToken: s(keys.gitlab) || unset } },
    KAGGLE: { type: 'httpBasicAuth', data: { user: s(keys.kaggleUser) || unset, password: s(keys.kaggleKey) || unset } },
    SHEETS: { type: 'googleSheetsOAuth2Api', data: Object.assign({}, google) },
    DRIVE: { type: 'googleDriveOAuth2Api', data: Object.assign({}, google) },
    GMAIL: { type: 'gmailOAuth2', data: Object.assign({}, google) },
    SLACK: { type: 'slackOAuth2Api', data: Object.assign({}, slack) },
  };
}

async function listCredentials() {
  const out = [];
  let cursor = '';
  for (let i = 0; i < 20; i++) {
    const r = await api('GET', '/credentials?limit=100' + (cursor ? '&cursor=' + encodeURIComponent(cursor) : ''));
    if (r.status !== 200) return null; // listing not supported on this n8n version
    out.push(...(r.body.data || []));
    cursor = r.body.nextCursor;
    if (!cursor) break;
  }
  return out;
}

async function connect() {
  if (!String(body.keys?.openai || '').trim() || !String(body.keys?.serper || '').trim()) {
    return fail('OpenAI and Serper keys are required.');
  }
  const defs = credentialDefs(body.keys, body.oauth);
  const wantSlack = !!String(body.details?.slackChannel || '').trim();
  const existing = (await listCredentials()) || [];
  const known = body.ids || {};
  const ids = {};
  for (const k of Object.keys(defs)) {
    if (k === 'SLACK' && !wantSlack) continue;
    const name = CRED_NAMES[k];
    const d = defs[k];
    const found = existing.find((c) => c.name === name && c.type === d.type);
    const oldId = found ? found.id : known[k];
    if (oldId) {
      // OAuth credentials keep their sign-in; only re-save API keys.
      if (OAUTH.includes(k)) { ids[k] = oldId; continue; }
      const u = await api('PATCH', '/credentials/' + oldId, { name, type: d.type, data: d.data });
      if (u.status >= 200 && u.status < 300) { ids[k] = oldId; continue; }
      await api('DELETE', '/credentials/' + oldId); // fall back to re-creating it
    }
    const r = await api('POST', '/credentials', { name, type: d.type, data: d.data });
    if (r.status < 200 || r.status >= 300) {
      let hint = '';
      if (OAUTH.includes(k)) hint = ' On self-hosted n8n you need your own Google / Slack OAuth app: fill in "Advanced: your own sign-in apps" and try again.';
      return fail('Could not create "' + name + '": ' + msg(r) + hint);
    }
    ids[k] = r.body.id;
  }
  const signIn = OAUTH.filter((k) => ids[k]).map((k) => ({ key: k, name: CRED_NAMES[k], url: BASE + '/home/credentials/' + ids[k] }));
  return done({ ok: true, ids, signIn });
}

// ---------- results sheet + Drive folder (via a short-lived helper workflow) ----------
function monthLabel() {
  return new Date().toLocaleString('en-US', { month: 'long', year: 'numeric' });
}
function sheetBody(company) {
  const str = (v) => ({ userEnteredValue: { stringValue: String(v) } });
  const num = (v) => ({ userEnteredValue: { numberValue: v } });
  const header = (cols) => ({ startRow: 0, startColumn: 0, rowData: [{ values: cols.map(str) }] });
  return {
    properties: { title: (company || 'Recruiting') + ' – Sourcing Pipeline' },
    sheets: [
      { properties: { sheetId: 0, title: 'Sheet1', gridProperties: { frozenRowCount: 1 } }, data: [header(SHEET_COLUMNS)] },
      { properties: { sheetId: USAGE_TAB_ID, title: 'Usage Tracker' }, data: [{ startRow: 0, startColumn: 0, rowData: [
        { values: ['Month', 'Serper_Used', 'PDL_Used'].map(str) },
        { values: [str(monthLabel()), num(0), num(0)] },
      ] }] },
      { properties: { sheetId: LOG_TAB_ID, title: 'Search Log', gridProperties: { frozenRowCount: 1 } }, data: [header(LOG_COLUMNS)] },
    ],
  };
}

async function createSheetAndFolder(ids, company) {
  const hook = 'sourcing-bootstrap-' + Math.random().toString(36).slice(2, 12);
  const wf = {
    name: 'Sourcing setup helper (deletes itself)',
    settings: { executionOrder: 'v1', saveDataSuccessExecution: 'none', saveDataErrorExecution: 'none', saveManualExecutions: false },
    nodes: [
      { id: 'h1', name: 'Webhook', type: 'n8n-nodes-base.webhook', typeVersion: 2, position: [0, 0], webhookId: hook,
        parameters: { httpMethod: 'POST', path: hook, responseMode: 'responseNode', options: {} } },
      { id: 'h2', name: 'Create sheet', type: 'n8n-nodes-base.httpRequest', typeVersion: 4.2, position: [220, 0], onError: 'continueRegularOutput',
        parameters: { method: 'POST', url: 'https://sheets.googleapis.com/v4/spreadsheets', authentication: 'predefinedCredentialType',
          nodeCredentialType: 'googleSheetsOAuth2Api', sendBody: true, specifyBody: 'json', jsonBody: JSON.stringify(sheetBody(company)), options: {} },
        credentials: { googleSheetsOAuth2Api: { id: ids.SHEETS, name: CRED_NAMES.SHEETS } } },
      { id: 'h5', name: 'Add checkbox', type: 'n8n-nodes-base.httpRequest', typeVersion: 4.2, position: [330, 0], onError: 'continueRegularOutput',
        parameters: { method: 'POST', url: "={{ 'https://sheets.googleapis.com/v4/spreadsheets/' + $('Create sheet').first().json.spreadsheetId + ':batchUpdate' }}",
          authentication: 'predefinedCredentialType', nodeCredentialType: 'googleSheetsOAuth2Api', sendBody: true, specifyBody: 'json',
          jsonBody: JSON.stringify({ requests: [{ setDataValidation: { range: { sheetId: 0, startRowIndex: 1,
            startColumnIndex: SHEET_COLUMNS.indexOf('Send Email?'), endColumnIndex: SHEET_COLUMNS.indexOf('Send Email?') + 1 },
            rule: { condition: { type: 'BOOLEAN' }, showCustomUi: true } } }].concat(styleRequests(LOG_TAB_ID)) }), options: {} },
        credentials: { googleSheetsOAuth2Api: { id: ids.SHEETS, name: CRED_NAMES.SHEETS } } },
      { id: 'h3', name: 'Create folder', type: 'n8n-nodes-base.httpRequest', typeVersion: 4.2, position: [440, 0], onError: 'continueRegularOutput',
        parameters: { method: 'POST', url: 'https://www.googleapis.com/drive/v3/files', authentication: 'predefinedCredentialType',
          nodeCredentialType: 'googleDriveOAuth2Api', sendBody: true, specifyBody: 'json',
          jsonBody: JSON.stringify({ name: (company || 'Recruiting') + ' – Sourcing CVs & JDs', mimeType: 'application/vnd.google-apps.folder' }), options: {} },
        credentials: { googleDriveOAuth2Api: { id: ids.DRIVE, name: CRED_NAMES.DRIVE } } },
      { id: 'h4', name: 'Respond', type: 'n8n-nodes-base.respondToWebhook', typeVersion: 1.1, position: [660, 0],
        parameters: { respondWith: 'json', responseBody: "={{ JSON.stringify({ sheet: $('Create sheet').first().json, folder: $('Create folder').first().json }) }}", options: {} } },
    ],
    connections: {
      Webhook: { main: [[{ node: 'Create sheet', type: 'main', index: 0 }]] },
      'Create sheet': { main: [[{ node: 'Add checkbox', type: 'main', index: 0 }]] },
      'Add checkbox': { main: [[{ node: 'Create folder', type: 'main', index: 0 }]] },
      'Create folder': { main: [[{ node: 'Respond', type: 'main', index: 0 }]] },
    },
  };
  const c = await api('POST', '/workflows', wf);
  if (c.status < 200 || c.status >= 300) return { error: 'Could not prepare your sheet: ' + msg(c) };
  const id = c.body.id;
  try {
    const a = await api('POST', '/workflows/' + id + '/activate');
    if (a.status < 200 || a.status >= 300) return { error: 'Could not prepare your sheet: ' + msg(a) };
    let r;
    for (let i = 0; i < 4; i++) {
      r = await http({ method: 'POST', url: (await apiBase()) + '/webhook/' + hook, body: {} });
      if (r.status !== 404) break;
      await new Promise((ok) => setTimeout(ok, 1500));
    }
    const out = r.body || {};
    const sheet = out.sheet || {};
    const folder = out.folder || {};
    if (!sheet.spreadsheetId) {
      const e = sheet.error?.message || (typeof sheet.error === 'string' ? sheet.error : '');
      return { error: "Google Sheets isn't signed in yet" + (e ? ' (' + String(e).slice(0, 160) + ')' : '') + '. Open the Google Sheets sign-in link, click Sign in, then click Configure again.' };
    }
    return { sheetId: sheet.spreadsheetId, folderId: folder.id || '' , folderError: folder.id ? '' : 'Your Drive folder could not be created, so CVs and job descriptions will be saved to the top of your Google Drive. Sign in to Google Drive in n8n to fix this.' };
  } finally {
    await api('POST', '/workflows/' + id + '/deactivate');
    await api('POST', '/workflows/' + id + '/archive'); // newer n8n versions only delete archived workflows
    await api('DELETE', '/workflows/' + id);
  }
}

// ---------- install the pipeline ----------
function escHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function fillTokens(obj, map) {
  if (typeof obj === 'string') {
    for (const [k, v] of Object.entries(map)) obj = obj.split(k).join(v);
    return obj;
  }
  if (Array.isArray(obj)) return obj.map((x) => fillTokens(x, map));
  if (obj && typeof obj === 'object') {
    const o = {};
    for (const [k, v] of Object.entries(obj)) o[k] = fillTokens(v, map);
    return o;
  }
  return obj;
}
function uuid() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = Math.random() * 16 | 0; return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
  });
}

async function findWorkflow(name) {
  let cursor = '';
  for (let i = 0; i < 20; i++) {
    const r = await api('GET', '/workflows?limit=100' + (cursor ? '&cursor=' + encodeURIComponent(cursor) : ''));
    if (r.status !== 200) return null;
    const hit = (r.body.data || []).find((w) => w.name === name);
    if (hit) return hit;
    cursor = r.body.nextCursor;
    if (!cursor) return null;
  }
  return null;
}

async function install() {
  const ids = body.ids || {};
  for (const k of ['OPENAI', 'SERPER', 'APOLLO', 'GITHUB', 'GITLAB', 'KAGGLE', 'SHEETS', 'DRIVE', 'GMAIL']) {
    if (!ids[k]) return fail('Connections are missing. Go back one step and click "Create connections".');
  }
  const d = body.details || {};
  const company = String(d.company || '').trim();
  if (!company) return fail('Please enter your company name.');
  const signature = String(d.signature || '').trim() || company;
  let channel = String(d.slackChannel || '').trim();
  if (channel && !channel.startsWith('#')) channel = '#' + channel;
  const slackUser = String(d.slackUser || '').trim();

  // 1. results sheet + Drive folder (reuse if a previous attempt already made them)
  let sheetId = String(body.sheetId || '').trim();
  let folderId = String(body.folderId || '').trim();
  let note = '';
  if (!sheetId) {
    const s = await createSheetAndFolder(ids, company);
    if (s.error) return fail(s.error);
    sheetId = s.sheetId; folderId = s.folderId; note = s.folderError || '';
  }

  // 2. pipeline from the published template
  let wf = EMBEDDED_TEMPLATE ? JSON.parse(JSON.stringify(EMBEDDED_TEMPLATE)) : null;
  if (!wf) {
    const t = await http({ method: 'GET', url: TEMPLATE_URL, json: false });
    if (t.status !== 200) return fail('Could not download the pipeline template (' + t.status + ').');
    try { wf = typeof t.body === 'string' ? JSON.parse(t.body) : t.body; } catch (e) { return fail('The pipeline template is not valid JSON.'); }
  }

  const existing = await findWorkflow(PIPELINE_NAME);
  const formId = String(body.formId || '').trim() || uuid();
  const map = {
    __SHEET_ID__: sheetId,
    __DRIVE_FOLDER_ID__: folderId || 'root', // My Drive if the folder couldn't be made
    __COMPANY__: company,
    __SIGNATURE__: escHtml(signature).replace(/\n/g, '<br/>'),
    __SLACK_CHANNEL__: channel,
    __SLACK_USER__: slackUser,
    __FORM_ID__: formId,
  };
  for (const k of Object.keys(CRED_NAMES)) map['__CRED_' + k + '__'] = ids[k] || '';
  wf = fillTokens(wf, map);

  for (const n of wf.nodes) {
    if (!n.type.endsWith('.slack')) continue;
    if (!channel || !ids.SLACK) { n.disabled = true; continue; }
    if (n.parameters.select === 'user' && !slackUser) {
      n.parameters.select = 'channel';
      delete n.parameters.user;
      n.parameters.channelId = { __rl: true, value: channel, mode: 'name' };
    }
  }

  const payload = { name: wf.name, nodes: wf.nodes, connections: wf.connections, settings: wf.settings || { executionOrder: 'v1' } };
  let wid;
  if (existing) {
    await api('POST', '/workflows/' + existing.id + '/deactivate');
    const u = await api('PUT', '/workflows/' + existing.id, payload);
    if (u.status < 200 || u.status >= 300) return fail('Could not update your pipeline: ' + msg(u));
    wid = existing.id;
  } else {
    const c = await api('POST', '/workflows', payload);
    if (c.status < 200 || c.status >= 300) return fail('Could not install the pipeline: ' + msg(c));
    wid = c.body.id;
  }
  const a = await api('POST', '/workflows/' + wid + '/activate');
  const base = { sheetId, folderId, formId, workflowUrl: BASE + '/workflow/' + wid };
  if (a.status < 200 || a.status >= 300) {
    const m = msg(a);
    const hint = /gmail/i.test(m) ? ' Gmail is probably not signed in yet: open the Gmail sign-in link, click Sign in, then click Configure again.' : '';
    return done(Object.assign({ ok: false, error: 'The pipeline is installed but could not switch on: ' + m + hint }, base));
  }
  return done(Object.assign({
    ok: true,
    note,
    formUrl: BASE + '/form/' + formId,
    sheetUrl: 'https://docs.google.com/spreadsheets/d/' + sheetId + '/edit',
  }, base), { switchOff: true, apiBase: await apiBase(), k: KEY });
}

// ---------- keys never stay in n8n's execution history ----------
// n8n's "Import from File" drops a workflow's settings, so the "don't save executions" setting this
// file ships with can be lost. Put it back, then delete any runs of this setup that were saved.
async function keepKeysOutOfLogs() {
  const NO_LOGS = { saveDataSuccessExecution: 'none', saveDataErrorExecution: 'none', saveManualExecutions: false, saveExecutionProgress: false };
  try {
    const me = await api('GET', '/workflows/' + $workflow.id);
    const s = (me.body && me.body.settings) || {};
    if (me.status === 200 && Object.keys(NO_LOGS).some((k) => s[k] !== NO_LOGS[k])) {
      await api('PUT', '/workflows/' + $workflow.id, { name: me.body.name, nodes: me.body.nodes, connections: me.body.connections,
        settings: Object.assign({ executionOrder: 'v1' }, s, NO_LOGS) });
    }
    const ex = await api('GET', '/executions?workflowId=' + $workflow.id + '&limit=250');
    for (const e of ((ex.body && ex.body.data) || [])) await api('DELETE', '/executions/' + e.id);
  } catch (e) { /* best effort */ }
}

// ---------- router ----------
const action = String(body.action || '');
const keyError = await checkKey();
if (keyError) return fail(keyError);
await keepKeysOutOfLogs();

if (action === 'ping') return done({ ok: true });
if (action === 'test') {
  const e = await testService(String(body.service || ''), body.values);
  return done(e ? { ok: false, error: e } : { ok: true });
}
if (action === 'connect') return await connect();
if (action === 'install') return await install();
return fail('Unknown action.');
