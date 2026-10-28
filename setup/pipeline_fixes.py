"""Quality fixes applied on top of ai-recruiting-pipeline.template.json.

Each fix addresses a problem seen in real runs:

 1. One ranking wave. The 11 source branches used to fan straight into a Code
    node, so n8n ran the AI ranking once PER SOURCE: ranks restarted (1-8, 1-2,
    1-10) and each wave kept only its own top 10. A Merge node now waits for
    every source, then ALL candidates are ranked together.
 2. Keep everyone. No top-10 cap and no hard experience cut: every real person
    is scored 1-10 and written to the sheet; the recruiter decides.
 3. Batched AI scoring (25 candidates per call) so large pools fit the model.
 4. No invented facts: the AI may only use what the source text says
    (no guessed years of experience).
 5. Location: every candidate gets a location (AI-extracted, Apollo, GitHub);
    profiles clearly in another country are dropped, the rest are ranked
    in-city first, then unknown, then same-country-other-city.
 6. Better searches: LinkedIn/Serper role terms are no longer one exact
    phrase; Bangalore/Bengaluru-style aliases; "Open Web" no longer switches
    off the LinkedIn filter; GitHub user search uses language/location
    qualifiers (free-text skill words matched user NAMES before) and fetches
    each user's real profile before ranking; flagship-repo contributors
    (global maintainers) are skipped when a location is requested and are
    picked by language.
 7. Real names + enrichment: Apollo search hits (first names only) are
    enriched by Apollo id; GitHub logins are replaced with the profile name.
    Only the top 25 are sent to Apollo (credits).
 8. Resume search for the top 20 only (Serper credits), now with real names.
 9. Column fixes: "PDL Skills" wrongly held the profile URL -> new
    "Profile URL" column; Search Log counts and "Started At" fixed.
"""
import json
import re

APOLLO_ENRICH_TOP = 25
RESUME_SEARCH_TOP = 20
LLM_BATCH = 25

CITY_ALIASES_JS = r"""
const CITY_ALIASES = [
  ['bangalore','bengaluru','blr'], ['mumbai','bombay','navi mumbai','thane'], ['delhi','new delhi','ncr','delhi ncr'],
  ['gurgaon','gurugram'], ['noida','greater noida'], ['chennai','madras'], ['kolkata','calcutta'],
  ['hyderabad','secunderabad'], ['pune','pimpri'], ['kochi','cochin','ernakulam'], ['thiruvananthapuram','trivandrum'],
  ['mysore','mysuru'], ['vizag','visakhapatnam'], ['ahmedabad','gandhinagar'], ['coimbatore'], ['jaipur'], ['chandigarh','mohali'],
  ['san francisco','sf bay area','bay area','san jose','palo alto','mountain view','sunnyvale'], ['new york','nyc','brooklyn','manhattan'],
  ['london'], ['singapore'], ['dubai'], ['abu dhabi'], ['berlin'], ['amsterdam'], ['toronto'], ['sydney'], ['melbourne']
];
const COUNTRY_OF_CITY = {
  bangalore:'india', mumbai:'india', delhi:'india', gurgaon:'india', noida:'india', chennai:'india', kolkata:'india',
  hyderabad:'india', pune:'india', kochi:'india', thiruvananthapuram:'india', mysore:'india', vizag:'india',
  ahmedabad:'india', coimbatore:'india', jaipur:'india', chandigarh:'india',
  'san francisco':'united states', 'new york':'united states', london:'united kingdom', singapore:'singapore',
  dubai:'united arab emirates', 'abu dhabi':'united arab emirates', berlin:'germany', amsterdam:'netherlands',
  toronto:'canada', sydney:'australia', melbourne:'australia'
};
const COUNTRY_WORDS = {
  'india':['india','karnataka','maharashtra','tamil nadu','telangana','kerala','haryana','uttar pradesh','west bengal','gujarat','rajasthan','andhra pradesh','punjab'],
  'united states':['united states','usa','u.s.','california','new york','texas','washington','massachusetts','illinois','florida'],
  'united kingdom':['united kingdom',' uk ','england','scotland','wales'],
  'singapore':['singapore'], 'united arab emirates':['united arab emirates','uae'], 'germany':['germany','deutschland'],
  'netherlands':['netherlands','holland'], 'canada':['canada'], 'australia':['australia'], 'belgium':['belgium'],
  'france':['france'], 'spain':['spain'], 'italy':['italy'], 'ireland':['ireland'], 'japan':['japan'], 'brazil':['brazil'],
  'pakistan':['pakistan'], 'bangladesh':['bangladesh'], 'sri lanka':['sri lanka'], 'nepal':['nepal'], 'china':['china'],
  'russia':['russia'], 'ukraine':['ukraine'], 'poland':['poland'], 'portugal':['portugal'], 'sweden':['sweden'],
  'switzerland':['switzerland'], 'austria':['austria'], 'israel':['israel'], 'nigeria':['nigeria'], 'kenya':['kenya'],
  'philippines':['philippines'], 'indonesia':['indonesia'], 'vietnam':['vietnam'], 'egypt':['egypt'], 'turkey':['turkey'],
  'argentina':['argentina'], 'mexico':['mexico'], 'new zealand':['new zealand'], 'south africa':['south africa'],
  'saudi arabia':['saudi arabia','ksa','riyadh','jeddah']
};
function aliasesFor(loc) {
  const l = String(loc || '').toLowerCase().trim();
  if (!l) return [];
  for (const g of CITY_ALIASES) { if (g.some(function(a){ return l === a || l.indexOf(a) !== -1; })) return g; }
  return [l];
}
 function countryOf(text) {
  if (/,\s*(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\b/.test(String(text || ''))) return 'united states';
  const t = ' ' + String(text || '').toLowerCase().replace(/[(),]/g, ' ') + ' ';
  for (const g of CITY_ALIASES) {
    if (g.some(function(a){ return t.indexOf(' ' + a + ' ') !== -1 || t.indexOf(' ' + a + ',') !== -1; })) { if (COUNTRY_OF_CITY[g[0]]) return COUNTRY_OF_CITY[g[0]]; }
  }
  for (const c of Object.keys(COUNTRY_WORDS)) {
    if (COUNTRY_WORDS[c].some(function(w){ return t.indexOf(w.length <= 4 ? w : (' ' + w.trim())) !== -1; })) return c;
  }
  return '';
}
"""

# ---------------------------------------------------------------- code nodes

BUILD_SEARCH_QUERY = CITY_ALIASES_JS + r"""
const form = $('Search Criteria').first().json;
const role = String(form['Target Role'] || '').trim();
const skillsRaw = form['Key Skills & Criteria'] || '';
const loc = String(form['Location'] || '').split('-')[0].trim();
const excludeRaw = form['Keywords to Exclude'] || '';
const employer = String(form['Current Employer'] || '').trim();
const education = String(form['Education'] || '').trim();
const expand = String(form['Expand Job Title Synonyms'] || '').toLowerCase();

function q(s) { return /\s/.test(s) ? '"' + s + '"' : s; }
function looksBoolean(s) { s = String(s); return /\s(OR|AND)\s/i.test(s) || s.indexOf('"') !== -1 || /(^|\s)-\w/.test(s); }

const synMap = {
  'software engineer': ['software engineer','software developer','sde','backend engineer','full stack developer'],
  'backend engineer': ['backend engineer','backend developer','back end developer','software engineer'],
  'backend developer': ['backend developer','backend engineer','back end developer','software engineer'],
  'frontend engineer': ['frontend engineer','frontend developer','ui engineer','react developer'],
  'full stack developer': ['full stack developer','fullstack developer','full stack engineer','software engineer'],
  'data scientist': ['data scientist','machine learning engineer','ml engineer','ai engineer'],
  'data engineer': ['data engineer','big data engineer','etl developer','analytics engineer'],
  'data analyst': ['data analyst','business analyst','bi analyst'],
  'product manager': ['product manager','product owner','technical product manager'],
  'devops engineer': ['devops engineer','site reliability engineer','sre','platform engineer'],
  'qa engineer': ['qa engineer','sdet','test engineer','automation engineer'],
  'designer': ['designer','ux designer','ui designer','product designer'],
  'mobile developer': ['mobile developer','android developer','ios developer','react native developer'],
  'recruiter': ['recruiter','talent acquisition','technical recruiter','sourcer'],
  'hr': ['hr','human resources','talent acquisition','hrbp','people operations']
};
// Role: a long exact phrase ("python backend developer") almost never matches a
// real headline. Use the core title (last two words) as the phrase and the
// rest as separate required words, plus synonyms for the core title.
let roleTerm = '';
if (role) {
  if (looksBoolean(role)) {
    roleTerm = '(' + role + ')';
  } else {
    const words = role.toLowerCase().split(/\s+/).filter(Boolean);
    const core = words.length > 2 ? words.slice(-2).join(' ') : words.join(' ');
    const extra = words.length > 2 ? words.slice(0, -2) : [];
    let variants = [core];
    if (synMap[core] && expand !== 'no' && expand !== 'false') variants = synMap[core];
    else if (synMap[role.toLowerCase()]) variants = synMap[role.toLowerCase()];
    const v = variants.map(function(x){ return '"' + x + '"'; });
    roleTerm = (v.length > 1 ? '(' + v.join(' OR ') + ')' : v[0]) + (extra.length ? ' ' + extra.join(' ') : '');
  }
}

// Skills: must-have OR-group only (secondary skills are used for ranking, not search,
// so they do not shrink the result set).
function orGroup(raw){
  if (!String(raw).trim()) return '';
  if (looksBoolean(raw)) return '(' + String(raw).trim() + ')';
  const arr = String(raw).split(',').map(function(s){return s.trim();}).filter(Boolean).slice(0, 5);
  if (!arr.length) return '';
  const quoted = arr.map(q);
  return quoted.length > 1 ? '(' + quoted.join(' OR ') + ')' : quoted[0];
}
let skillsTerm = orGroup(form['Primary Skills'] || '') || orGroup(skillsRaw);
// don't repeat a skill that is already in the role words
if (skillsTerm && role) {
  const rl = role.toLowerCase();
  const arr = String(form['Primary Skills'] || skillsRaw).split(',').map(function(s){return s.trim();}).filter(function(s){ return s && rl.indexOf(s.toLowerCase()) === -1; }).slice(0, 5);
  skillsTerm = arr.length ? (arr.length > 1 ? '(' + arr.map(q).join(' OR ') + ')' : q(arr[0])) : '';
}

const excludes = String(excludeRaw).split(',').map(function(s){return s.trim();}).filter(Boolean);
const excludeTerm = excludes.map(function(s){ return '-' + q(s); }).join(' ');
const employerTerm = employer ? q(employer) : '';

const eduMap = {
  'bachelors / degree': ['bachelor','bachelors','"b.e"','"b.tech"','btech','bsc','degree'],
  'masters': ['master','masters','"m.tech"','mtech','msc'],
  'phd': ['phd','"ph.d"','doctorate'],
  'mba': ['mba']
};
let eduTerm = '';
if (education && !/^any$/i.test(education)) {
  const variants = eduMap[education.toLowerCase()];
  eduTerm = variants ? '(' + variants.join(' OR ') + ')' : q(education);
}

// Location: city + its aliases (Bangalore OR Bengaluru)
const locAliases = aliasesFor(loc);
const locTerm = locAliases.length ? (locAliases.length > 1 ? '(' + locAliases.filter(function(a){ return a.length > 3; }).map(q).join(' OR ') + ')' : q(locAliases[0])) : '';

let platforms = form['Target Platforms'];
if (!Array.isArray(platforms)) platforms = platforms ? String(platforms).split(',').map(function(s){return s.trim();}).filter(Boolean) : [];
const siteMap = {
  'LinkedIn': ['site:linkedin.com/in'],
  'X (Twitter)': ['site:x.com','site:twitter.com'],
  'Dribbble': ['site:dribbble.com'],
  'Behance': ['site:behance.net']
};
const openWeb = platforms.some(function(pl){ return /google|open web/i.test(pl); });
let sites = [];
for (const pl of platforms) { if (siteMap[pl]) sites = sites.concat(siteMap[pl]); }

const regionMap = {
  'india':'in','united states':'us','usa':'us','us':'us','united kingdom':'gb','uk':'gb','canada':'ca','australia':'au',
  'germany':'de','france':'fr','netherlands':'nl','singapore':'sg','united arab emirates':'ae','uae':'ae','dubai':'ae',
  'ireland':'ie','spain':'es','italy':'it','japan':'jp','brazil':'br','saudi arabia':'sa'
};
const regionRaw = String(form['Search Region'] || '').trim();
let gl = '';
if (regionRaw && !/^global|^all/i.test(regionRaw)) gl = regionMap[regionRaw.toLowerCase().replace(/\s*\(.*\)\s*/, '').trim()] || '';
if (!gl) { const c = countryOf(loc); const byCountry = {'india':'in','united states':'us','united kingdom':'gb','singapore':'sg','united arab emirates':'ae','germany':'de','netherlands':'nl','canada':'ca','australia':'au'}; gl = byCountry[c] || ''; }

const queries = [];
// 1) profile-site search (LinkedIn / X / Dribbble / Behance)
if (sites.length) {
  const parts = [sites.length > 1 ? '(' + sites.join(' OR ') + ')' : sites[0], roleTerm, skillsTerm, employerTerm, eduTerm, locTerm, excludeTerm, '-intitle:jobs -inurl:jobs'];
  queries.push({ kind: 'profiles', q: parts.filter(Boolean).join(' ').trim(), pages: 3 });
}
// 2) open-web search (personal sites, portfolios, CV pages) - in ADDITION, never instead
if (openWeb) {
  const noise = ['intitle:jobs','inurl:jobs','inurl:careers','"job description"','"apply now"','"we are hiring"','site:linkedin.com/jobs','site:naukri.com','site:indeed.com','site:glassdoor.com','site:foundit.in','site:instahyre.com','site:cutshort.io','site:wellfound.com/jobs'];
  const intent = '("resume" OR "curriculum vitae" OR "portfolio" OR "about me" OR inurl:resume OR inurl:cv)';
  const parts = [roleTerm, skillsTerm, intent, locTerm, excludeTerm, noise.map(function(t){ return '-' + t; }).join(' ')];
  queries.push({ kind: 'open-web', q: parts.filter(Boolean).join(' ').trim(), pages: 2 });
}
const out = [];
for (const qq of queries) {
  for (let pg = 1; pg <= qq.pages; pg++) {
    const body = { q: qq.q, page: pg, num: 10 };
    if (gl) body.gl = gl;
    out.push({ json: { q: qq.q, kind: qq.kind, page: pg, gl: gl || 'global', serper_body: body, platforms_used: qq.kind } });
  }
}
// keep the pipeline flowing even when no web platform was ticked
if (!out.length) return [];
return out;
"""

FORMAT_BATCHES = r"""
// Split the merged candidate pool into batches so every candidate is scored,
// however many sources were searched.
const all = ($input.first().json.organic || []);
const BATCH = %d;
const lines = all.map(function(o){ return (o.title || '') + ' | ' + String(o.snippet || '').replace(/\s+/g, ' ').slice(0, 700) + ' | ' + (o.link || ''); });
const out = [];
for (let i = 0; i < lines.length; i += BATCH) {
  out.push({ json: { page_text: lines.slice(i, i + BATCH).join('\n'), batch: out.length + 1, batch_size: Math.min(BATCH, lines.length - i), pool_size: lines.length } });
}
if (!out.length) out.push({ json: { page_text: '', batch: 1, batch_size: 0, pool_size: 0 } });
return out;
""" % LLM_BATCH

SPLIT_CANDIDATES = r"""
const out = [];
const seen = {};
for (const item of $input.all()) {
  let raw;
  if (item.json.output && item.json.output[0] && item.json.output[0].content && item.json.output[0].content[0]) raw = item.json.output[0].content[0].text;
  else if (typeof item.json.text === 'string') raw = item.json.text;
  else raw = '[]';
  raw = String(raw || '').trim().replace(/^```(?:json)?/i, '').replace(/```$/, '').trim();
  let parsed;
  try { parsed = JSON.parse(raw); } catch (e) { parsed = []; }
  if (parsed && !Array.isArray(parsed) && Array.isArray(parsed.candidates)) parsed = parsed.candidates;
  if (!Array.isArray(parsed)) parsed = [parsed];
  for (const c of parsed) {
    if (!c) continue;
    const url = String(c.profile_url || '').trim();
    const key = url.toLowerCase().replace(/\/+$/, '') || String(c.candidate_name || '').toLowerCase();
    if (!key || seen[key]) continue;
    seen[key] = true;
    out.push({ json: {
      candidate_name: c.candidate_name || '',
      is_person: c.is_person !== false && !!c.candidate_name,
      current_role: c.current_role || '',
      fit_score: typeof c.fit_score === 'number' ? c.fit_score : Number(c.fit_score) || 0,
      match_summary: c.match_summary || '',
      key_skills: Array.isArray(c.key_skills) ? c.key_skills : [],
      candidate_location: String(c.candidate_location || '').trim(),
      experience_evidence: String(c.experience_evidence || '').trim(),
      profile_url: url
    } });
  }
}
return out;
"""

RANK_ALL = r"""
// Rank EVERY candidate (no top-10 cap). Rank here is provisional; the final
// rank is set after enrichment + location check.
const items = $input.all().map(function(it, idx) { return { json: it.json, pairedItem: { item: idx } }; });
items.sort(function(a, b) { return (Number(b.json.fit_score) || 0) - (Number(a.json.fit_score) || 0); });
return items.map(function(it, i) { return { json: Object.assign({}, it.json, { rank: i + 1 }), pairedItem: it.pairedItem }; });
"""

LOCATION_CHECK = CITY_ALIASES_JS + r"""
// Final step before the sheet: decide where each person is, drop people clearly
// in another country, and set the final rank across ALL sources.
const form = $('Search Criteria').first().json;
const wanted = String(form['Location'] || '').split('-')[0].trim();
const wantAliases = aliasesFor(wanted);
const wantCountry = countryOf(wanted) || countryOf(form['Search Region'] || '');
const kept = [];
let dropped = 0;
$input.all().forEach(function(it, idx) {
  const c = Object.assign({}, it.json);
  const locText = [c.location, c.candidate_location].filter(Boolean).join(' | ');
  if (!c.location && c.candidate_location) c.location = c.candidate_location;
  let tier = 1, label = 'location unknown';
  if (!wanted) { tier = 0; label = ''; }
  else if (locText) {
    const lt = ' ' + locText.toLowerCase() + ' ';
    const inCity = wantAliases.some(function(a){ return lt.indexOf(a) !== -1; });
    const country = countryOf(locText);
    if (inCity) { tier = 0; label = 'in ' + wanted; }
    else if (wantCountry && country && country !== wantCountry) { tier = 9; label = 'outside ' + wantCountry; }
    else if (wantCountry && country === wantCountry) { tier = 2; label = 'same country, different city'; }
    else { tier = 1; label = 'location unclear'; }
  }
  if (tier === 9) { dropped++; return; }
  c.location_tier = tier;
  c.location_check = label;
  kept.push({ json: c, pairedItem: { item: idx } });
});
kept.sort(function(a, b) {
  if (a.json.location_tier !== b.json.location_tier) return a.json.location_tier - b.json.location_tier;
  return (Number(b.json.fit_score) || 0) - (Number(a.json.fit_score) || 0);
});
kept.forEach(function(k, i) { k.json.rank = i + 1; k.json.location_dropped_total = dropped; });
return kept;
"""

GH_BUILD = CITY_ALIASES_JS + r"""
const form = $('Search Criteria').first().json;
let platforms = form['Target Platforms'];
if (!Array.isArray(platforms)) platforms = platforms ? String(platforms).split(',').map(function(s){return s.trim();}).filter(Boolean) : [];
if (!platforms.some(function(p){ return /^git\s*hub$/i.test(String(p).trim()); })) return [];
const role = String(form['Target Role'] || '').toLowerCase();
const skills = String(form['Key Skills & Criteria'] || '').toLowerCase().split(',').map(function(s){return s.trim();}).filter(Boolean);
const loc = String(form['Location'] || '').split('-')[0].trim();
const langMap = { 'java':'java','spring boot':'java','spring':'java','python':'python','django':'python','fastapi':'python','flask':'python','javascript':'javascript','react':'javascript','node.js':'javascript','node':'javascript','typescript':'typescript','go':'go','golang':'go','rust':'rust','c++':'cpp','ruby':'ruby','php':'php','swift':'swift','kotlin':'kotlin','scala':'scala','c#':'csharp','.net':'csharp' };
let lang = '';
for (const s of skills) { if (langMap[s]) { lang = langMap[s]; break; } }
if (!lang) { for (const k of Object.keys(langMap)) { if ((' ' + role + ' ').indexOf(' ' + k + ' ') !== -1) { lang = langMap[k]; break; } } }
// GitHub user search: free-text words match user NAMES, not skills, so use only
// qualifiers. One query per location alias (Bangalore, Bengaluru).
const locs = aliasesFor(loc).filter(function(a){ return a.length > 3; });
const base = (lang ? 'language:' + lang + ' ' : '') + 'repos:>3';
const qs = locs.length ? locs.map(function(a){ return base + ' location:' + (/\s/.test(a) ? '"' + a + '"' : a); }) : [base + ' followers:>20'];
return qs.slice(0, 2).map(function(q){ return { json: { gh_query: q, gh_url: 'https://api.github.com/search/users?per_page=30&sort=followers&order=desc&q=' + encodeURIComponent(q) } }; });
"""

GH_SPLIT = r"""
const seen = {};
const out = [];
for (const it of $input.all()) {
  for (const u of ((it.json && it.json.items) || [])) {
    const login = String(u.login || '').trim();
    if (!login || seen[login] || u.type === 'Organization') continue;
    seen[login] = true;
    out.push({ json: { login: login } });
    if (out.length >= 40) break;
  }
}
return out;
"""

GH_TO_ORGANIC = r"""
// GitHub users WITH their real profile (name, bio, company, location, repos),
// so the AI ranks on evidence instead of a bare username.
const splits = $('GH Split Users').all();
const out = [];
$input.all().forEach(function(it, i) {
  const p = it.json || {};
  const login = String(p.login || (splits[i] && splits[i].json.login) || '').trim();
  if (!login || !p.id) return;
  const name = String(p.name || '').trim();
  const bits = [];
  if (p.bio) bits.push('Bio: ' + String(p.bio).replace(/\s+/g, ' ').slice(0, 300));
  if (p.company) bits.push('Company: ' + p.company);
  if (p.location) bits.push('Location: ' + p.location);
  if (typeof p.public_repos === 'number') bits.push(p.public_repos + ' public repos');
  if (typeof p.followers === 'number') bits.push(p.followers + ' followers');
  if (p.blog) bits.push('Site: ' + p.blog);
  if (p.hireable) bits.push('open to hire');
  out.push({ title: (name ? name + ' (@' + login + ')' : login) + ' - GitHub developer', snippet: bits.join(' | ') || 'GitHub developer', link: p.html_url || ('https://github.com/' + login) });
});
return [{ json: { organic: out, source: 'github', results_count: out.length } }];
"""

REPO_BUILD_PREFIX = r"""
// Flagship-repo contributors are global maintainers: they ignore location, so
// skip this source whenever the recruiter asked for a location.
const formLoc = String($('Search Criteria').first().json['Location'] || '').trim();
if (formLoc) return [];
"""

PREP_APOLLO_PROFILE = r"""
// Apollo search returns first names only; the people/match call below needs the
// Apollo id (for Apollo-sourced rows) or the LinkedIn URL.
"""

COMBINE_EXTRA_FIELDS = """  candidate_location: cand.candidate_location || '',
  experience_evidence: cand.experience_evidence || '',
"""

# ---------------------------------------------------------------- helpers

def _node(name, ntype, version, pos, params, **extra):
    n = {"parameters": params, "id": "fix-" + name.lower().replace(" ", "-"), "name": name,
         "type": ntype, "typeVersion": version, "position": pos}
    n.update(extra)
    return n


def _main(conns, src, targets_per_output):
    conns[src] = {"main": [[{"node": t, "type": "main", "index": i} for (t, i) in outs] for outs in targets_per_output]}


def apply(nodes, conns):
    N = {n["name"]: n for n in nodes}

    def code(name):
        return N[name]["parameters"]

    # 1. Merge node waits for ALL sources, then one ranking wave ---------------
    sources = ["Serper Search", "HN To Organic", "GH To Organic", "SO To Organic", "Apollo To Organic",
               "Reddit To Organic", "DevTo To Organic", "GitLab To Organic", "HF To Organic",
               "Kaggle To Organic", "Repo To Organic"]
    mp = N["Merge Pages"]["position"]
    nodes.append(_node("Wait For All Sources", "n8n-nodes-base.merge", 3, [mp[0] - 220, mp[1]],
                       {"numberInputs": len(sources)}))
    for i, s in enumerate(sources):
        conns[s] = {"main": [[{"node": "Wait For All Sources", "type": "main", "index": i}]]}
    _main(conns, "Wait For All Sources", [[("Merge Pages", 0)]])

    # 2/3. batch the pool for the AI -------------------------------------------
    f = N["Format Search Results"]
    f["type"] = "n8n-nodes-base.code"; f["typeVersion"] = 2
    f["parameters"] = {"jsCode": FORMAT_BATCHES}

    # 4/5. prompt: keep every real person, no invented facts, extract location -
    msg = N["Message a model"]["parameters"]["responses"]["values"][0]
    c = msg["content"]
    c = c.replace("Gender Preference: {{ $('Search Criteria').first().json['Gender Preference'] }}",
                  "Gender Preference: {{ $('Search Criteria').first().json['Gender Preference'] }}\n"
                  "Target Location: {{ $('Search Criteria').first().json['Location'] || 'any' }}\n"
                  "Must-have skills: {{ $('Search Criteria').first().json['Primary Skills'] || $('Search Criteria').first().json['Key Skills & Criteria'] }}\n"
                  "Nice-to-have skills: {{ $('Search Criteria').first().json['Secondary Skills'] || 'none' }}")
    start = c.index("If a line is NOT an individual person")
    c = c[:start] + (
        "If a line is NOT an individual person (a job posting, company page, directory, list, article or hiring announcement), set is_person to false.\n"
        "EVERY real person MUST be returned with is_person true and a fit_score from 1 to 10. Never drop or zero-score a real person: the recruiter decides. "
        "Score must-have skills and role match highest, then seniority, then nice-to-have skills.\n"
        "EXPERIENCE: compare against the requested Experience Range only when the line actually states years, dates or seniority. "
        "If it does, lower the score for clearly too-junior people (students/interns -> 1-3) and say so. If it does not, write 'experience not stated' - NEVER guess a number of years.\n"
        "STRICT HONESTY: use ONLY facts present in the candidate's line. Do not invent employers, years, skills or locations. "
        "key_skills must be skills the line actually shows (a Java/Spring maintainer is not a Python developer).\n"
        "Also factor in the Gender Preference: if a specific gender is requested (Female or Male), prioritize candidates matching it; if 'Any' is selected, ignore gender entirely and rank purely on merit.\n"
        "Return a JSON ARRAY with one object for EVERY line (same order), each with exactly these fields:\n"
        "1. candidate_name (string) - the person's real full name as written; for a GitHub/GitLab/Kaggle line with no real name, use the username. Never invent a name. \"\" if no person.\n"
        "2. is_person (boolean)\n"
        "3. current_role (string, as stated, else \"\")\n"
        "4. fit_score (number 1-10 for people, 0 only when is_person is false)\n"
        "5. match_summary (string, 2 short sentences: why they fit and what is missing/unknown)\n"
        "6. key_skills (array of strings, only skills evidenced in the line)\n"
        "7. candidate_location (string - city/region/country exactly as stated in the line, else \"\")\n"
        "8. experience_evidence (string - years/seniority as stated, else \"not stated\")\n"
        "9. profile_url (string - the URL at the end of their line, copied exactly)\n"
        "Sort the array by fit_score descending. Return ONLY the JSON array.")
    msg["content"] = c

    code("Split Candidates")["jsCode"] = SPLIT_CANDIDATES
    # keep real people only (non-people are job posts etc.)
    fc = N["Filter Candidates"]["parameters"]["conditions"]
    fc["conditions"] = [
        {"id": "fix-person", "leftValue": "={{ $json.is_person }}", "rightValue": True,
         "operator": {"type": "boolean", "operation": "true", "singleValue": True}},
        {"id": "fix-score", "leftValue": "={{ $json.fit_score }}", "rightValue": 0,
         "operator": {"type": "number", "operation": "gt"}}]
    code("Rank Top 10")["jsCode"] = RANK_ALL

    # 7. Apollo enrichment: Apollo-sourced rows by id, LinkedIn rows by URL; top N only
    hl = N["Has LinkedIn"]["parameters"]["conditions"]
    hl["conditions"] = [
        {"id": "fix-li", "leftValue": "={{ $json.profile_url }}", "rightValue": "linkedin\\.com|apollo\\.io/people/",
         "operator": {"type": "string", "operation": "regex"}},
        {"id": "fix-top", "leftValue": "={{ $json.rank }}", "rightValue": APOLLO_ENRICH_TOP,
         "operator": {"type": "number", "operation": "lte"}}]
    hl["combinator"] = "and"
    ae = N["Apollo Enrich"]["parameters"]["queryParameters"]["parameters"]
    ae[:] = [
        {"name": "id", "value": "={{ ($json.profile_url.match(/apollo\\.io\\/people\\/([A-Za-z0-9]+)/) || [])[1] || '' }}"},
        {"name": "name", "value": "={{ /apollo\\.io/i.test($json.profile_url) ? '' : $json.candidate_name }}"},
        {"name": "linkedin_url", "value": "={{ /linkedin\\.com/i.test($json.profile_url) ? $json.profile_url : '' }}"},
        {"name": "reveal_personal_emails", "value": "false"}]
    ce = code("Combine Enrichment")
    ce["jsCode"] = ce["jsCode"].replace(
        "  candidate_name: cand.candidate_name || '',",
        "  candidate_name: (p && p.name && String(p.name).length > String(cand.candidate_name || '').length) ? p.name : (cand.candidate_name || ''),"
    ).replace("  profile_url: cand.profile_url || '',",
              "  profile_url: (/apollo\\.io/i.test(cand.profile_url || '') && p && p.linkedin_url) ? p.linkedin_url : (cand.profile_url || ''),\n"
              + COMBINE_EXTRA_FIELDS)
    cna = code("Combine No Apollo")
    cna["jsCode"] = cna["jsCode"].replace("  location: '',", "  location: cand.candidate_location || '',").replace(
        "  profile_url: url,", "  profile_url: url,\n" + COMBINE_EXTRA_FIELDS).replace(
        "enrichment_status: 'skipped - no linkedin (apollo not used)'",
        "enrichment_status: /linkedin\\.com|apollo\\.io/i.test(url) ? 'skipped - below top " + str(APOLLO_ENRICH_TOP) + " (Apollo credits)' : 'skipped - not a LinkedIn/Apollo profile'")
    ec = N["Enriched Candidates"]
    ec["type"] = "n8n-nodes-base.merge"; ec["typeVersion"] = 3; ec["parameters"] = {"numberInputs": 2}
    _main(conns, "Combine Enrichment", [[("Enriched Candidates", 0)]])
    _main(conns, "Combine No Apollo", [[("Enriched Candidates", 1)]])
    # GitHub: replace a bare login with the real profile name
    mg = code("Merge GitHub")
    mg["jsCode"] = mg["jsCode"].replace(
        "  merged.github_status = 'enriched';",
        "  const ghName = s(resp.name);\n"
        "  if (ghName && (!merged.candidate_name || String(merged.candidate_name).toLowerCase() === String(resp.login).toLowerCase())) merged.candidate_name = ghName;\n"
        "  if (!merged.current_role || /github (open-source )?(contributor|developer)/i.test(merged.current_role)) merged.current_role = bio ? bio.slice(0, 80) : (merged.current_role || 'Developer');\n"
        "  merged.github_status = 'enriched';")

    # 5. location check + final rank, then resume search for the top N only ----
    pos = N["Resume Search"]["position"]
    nodes.append(_node("Location Check & Rank", "n8n-nodes-base.code", 2, [pos[0] - 520, pos[1] + 200],
                       {"jsCode": LOCATION_CHECK}))
    nodes.append(_node("Resume Lookup?", "n8n-nodes-base.if", 2.2, [pos[0] - 300, pos[1] + 200], {
        "conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
                       "conditions": [{"id": "fix-resume-top", "leftValue": "={{ $json.rank }}", "rightValue": RESUME_SEARCH_TOP,
                                       "operator": {"type": "number", "operation": "lte"}}],
                       "combinator": "and"}, "options": {}}))
    nodes.append(_node("Rejoin Candidates", "n8n-nodes-base.merge", 3, [pos[0] + 80, pos[1] + 200], {"numberInputs": 2}))
    _main(conns, "Merge GitHub", [[("Location Check & Rank", 0)]])
    _main(conns, "Location Check & Rank", [[("Resume Lookup?", 0)]])
    _main(conns, "Resume Lookup?", [[("Resume Search", 0)], [("Rejoin Candidates", 1)]])
    _main(conns, "Resume Search", [[("Rejoin Candidates", 0)]])
    _main(conns, "Rejoin Candidates", [[("Attach Resume", 0)]])
    ar = code("Attach Resume")
    ar["jsCode"] = ar["jsCode"].replace(
        "const cand = $('Merge GitHub').item.json;\nconst resp = $json || {};",
        "const cand = $('Location Check & Rank').item.json;\nconst resp = ($json && ($json.organic || $json.searchParameters)) ? $json : {};")
    # resume search: quote the real name, drop the role words that over-narrow
    rs = N["Resume Search"]["parameters"]
    rs["jsonBody"] = rs["jsonBody"].replace(" {{ $json.current_role }}", "")

    # 6. search quality ---------------------------------------------------------
    code("Build Search Query")["jsCode"] = BUILD_SEARCH_QUERY
    code("Build GH Query")["jsCode"] = GH_BUILD
    gpos = N["GitHub Search"]["position"]
    nodes.append(_node("GH Split Users", "n8n-nodes-base.code", 2, [gpos[0] + 160, gpos[1] + 120], {"jsCode": GH_SPLIT}))
    nodes.append(_node("GH User Details", "n8n-nodes-base.httpRequest", 4.2, [gpos[0] + 320, gpos[1] + 120], {
        "url": "=https://api.github.com/users/{{ $json.login }}",
        "authentication": "predefinedCredentialType", "nodeCredentialType": "githubApi",
        "sendHeaders": True,
        "headerParameters": {"parameters": [{"name": "Accept", "value": "application/vnd.github+json"},
                                            {"name": "User-Agent", "value": "talentrubric-sourcing"}]},
        "options": {"timeout": 15000}},
        credentials=json.loads(json.dumps(N["GitHub Search"].get("credentials", {}))), onError="continueRegularOutput"))
    _main(conns, "GitHub Search", [[("GH Split Users", 0)]])
    _main(conns, "GH Split Users", [[("GH User Details", 0)]])
    _main(conns, "GH User Details", [[("GH To Organic", 0)]])
    code("GH To Organic")["jsCode"] = GH_TO_ORGANIC
    rq = code("Build Repo Query")
    rq["jsCode"] = rq["jsCode"].replace(
        "let picked = null;\nfor (const dm of domains) { if (dm.kw.some(function(k){ return blob.indexOf(k) !== -1; })) { picked = dm; break; } }",
        "// pick the domain with the MOST keyword hits (a 'python backend' search must not land on ML repos)\n"
        "let picked = null, best = 0;\nfor (const dm of domains) { const hits = dm.kw.filter(function(k){ return blob.indexOf(k) !== -1; }).length; if (hits > best) { best = hits; picked = dm; } }"
    ).replace("const ghSelected", REPO_BUILD_PREFIX + "const ghSelected", 1).replace(
        "{ name: 'backend', kw: ['backend','back end','java','spring','django','fastapi','node','golang',' go ','microservice','api developer','server'],\n    repos: ['spring-projects/spring-boot','django/django','gin-gonic/gin','fastapi/fastapi','nestjs/nest'] },",
        "{ name: 'backend_python', kw: ['python','django','fastapi','flask'], repos: ['django/django','fastapi/fastapi','pallets/flask'] },\n"
        "  { name: 'backend_java', kw: ['java','spring','kotlin'], repos: ['spring-projects/spring-boot','spring-projects/spring-framework'] },\n"
        "  { name: 'backend_go', kw: ['golang',' go '], repos: ['gin-gonic/gin','golang/go'] },\n"
        "  { name: 'backend_node', kw: ['node','nestjs','express'], repos: ['nestjs/nest','expressjs/express'] },\n"
        "  { name: 'backend', kw: ['backend','back end','microservice','api developer','server'],\n    repos: ['django/django','spring-projects/spring-boot','fastapi/fastapi'] },")

    # 9. sheet + log fixes --------------------------------------------------------
    cols = N["Append row in sheet"]["parameters"]["columns"]
    v = cols["value"]
    v.pop("PDL Skills", None)
    v["Profile URL"] = "={{ $json.profile_url }}"
    v["Rank"] = "={{ $json.rank }}"
    v["Location"] = "={{ $json.location }}{{ $json.location_check ? ' (' + $json.location_check + ')' : '' }}"
    v["Summary"] = "={{ $json.match_summary }}{{ $json.experience_evidence && $json.experience_evidence !== 'not stated' ? ' Experience: ' + $json.experience_evidence + '.' : '' }}"
    for s in cols["schema"]:
        if s["id"] == "PDL Skills":
            s["id"] = s["displayName"] = "Profile URL"
    ss = code("Sourcing Summary")
    ss["jsCode"] = ss["jsCode"].replace(
        "const rows = $input.all().map(function(i){ return i.json; });\nconst count = rows.length;",
        "const rows = $input.all().map(function(i){ return i.json; });\n"
        "let count = rows.length;\ntry { count = $('Location Check & Rank').all().length; } catch (e) {}")
    ss["jsCode"] = ss["jsCode"].replace("else { rangeText = 'the latest ' + count + ' rows'; }",
                                        "else { rangeText = count + ' candidates, ranked 1-' + count; }")
    lc = N["Log Search Complete"]["parameters"]["columns"]["value"]
    lc.pop("Started At", None)
    lc["Rows"] = "={{ $json.range_text }}"
    # confidence wording for rows that were never meant for Apollo
    pc = code("Parse Contact")
    pc["jsCode"] = pc["jsCode"].replace(
        "else if (String(merged.enrichment_status||'').indexOf('skipped') !== -1) reasons.push('not Apollo-verified (no LinkedIn)');",
        "else if (String(merged.enrichment_status||'').indexOf('below top') !== -1) reasons.push('not Apollo-enriched (outside top " + str(APOLLO_ENRICH_TOP) + ")');")
    pc["jsCode"] = pc["jsCode"].replace(
        "  if (!merged.candidate_name) { score -= 3; reasons.push('no identifiable name'); }",
        "  if (!merged.candidate_name) { score -= 3; reasons.push('no identifiable name'); }\n"
        "  if (merged.location_check === 'location unknown' || merged.location_check === 'location unclear') reasons.push('location not confirmed');\n"
        "  else if (merged.location_check === 'same country, different city') reasons.push('different city');")
    return nodes, conns


# ======================================================================
# Round 2 (after the second live run): no silent drops, use every free
# GitHub field, working resume search, LinkedIn always on.
# ======================================================================
BATCH2 = 12

FORMAT_BATCHES_V2 = r"""
// Number every line and remember which lines are always real people, so the
// next step can put back anyone the AI skipped.
const all = ($input.first().json.organic || []);
const BATCH = %d;
const PERSON_SRC = /(github\.com\/[^\/]+\/?$|gitlab\.com\/[^\/]+\/?$|kaggle\.com\/[^\/]+\/?$|huggingface\.co\/[^\/]+\/?$|stackoverflow\.com\/users\/|apollo\.io\/people\/|linkedin\.com\/in\/)/i;
const out = [];
for (let i = 0; i < all.length; i += BATCH) {
  const chunk = all.slice(i, i + BATCH);
  const lines = chunk.map(function(o, j){ return { n: j + 1, title: String(o.title || ''), link: String(o.link || ''), person: PERSON_SRC.test(String(o.link || '')) }; });
  const text = chunk.map(function(o, j){ return '[' + (j + 1) + '] ' + (o.title || '') + ' | ' + String(o.snippet || '').replace(/\s+/g, ' ').slice(0, 700) + ' | ' + (o.link || ''); }).join('\n');
  out.push({ json: { page_text: text, lines: lines, batch: out.length + 1, pool_size: all.length } });
}
if (!out.length) out.push({ json: { page_text: '', lines: [], batch: 1, pool_size: 0 } });
return out;
""" % BATCH2

SPLIT_V2 = r"""
// Parse the AI output per batch. Anyone from a people-only source (GitHub,
// GitLab, LinkedIn, Apollo, Stack Overflow, Kaggle, Hugging Face) that the AI
// skipped is put back with a neutral score, so no real profile is lost.
let batches = [];
try { batches = $('Format Search Results').all().map(function(i){ return i.json.lines || []; }); } catch (e) { batches = []; }
function nameFromTitle(t) {
  t = String(t || '');
  const m = t.match(/^(.+?) \(@[^)]+\)/); if (m) return m[1].trim();
  return t.replace(/\s*[-(].*$/, '').trim();
}
const out = [];
const seen = {};
let restored = 0;
$input.all().forEach(function(item, b) {
  let raw;
  if (item.json.output && item.json.output[0] && item.json.output[0].content && item.json.output[0].content[0]) raw = item.json.output[0].content[0].text;
  else if (typeof item.json.text === 'string') raw = item.json.text;
  else raw = '[]';
  raw = String(raw || '').trim().replace(/^```(?:json)?/i, '').replace(/```$/, '').trim();
  let parsed;
  try { parsed = JSON.parse(raw); } catch (e) { parsed = []; }
  if (parsed && !Array.isArray(parsed) && Array.isArray(parsed.candidates)) parsed = parsed.candidates;
  if (!Array.isArray(parsed)) parsed = [parsed];
  const lines = batches[b] || [];
  const gotLine = {}, gotUrl = {};
  for (const c of parsed) {
    if (!c) continue;
    let url = String(c.profile_url || '').trim();
    const ln = Number(c.line);
    if (ln && lines[ln - 1]) { gotLine[ln] = 1; if (!url) url = lines[ln - 1].link; }
    if (url) gotUrl[url.toLowerCase().replace(/\/+$/, '')] = 1;
    const key = url.toLowerCase().replace(/\/+$/, '') || String(c.candidate_name || '').toLowerCase();
    if (!key || seen[key]) continue;
    seen[key] = true;
    out.push({ json: {
      candidate_name: c.candidate_name || '',
      is_person: c.is_person !== false && !!c.candidate_name,
      current_role: c.current_role || '',
      fit_score: typeof c.fit_score === 'number' ? c.fit_score : Number(c.fit_score) || 0,
      match_summary: c.match_summary || '',
      key_skills: Array.isArray(c.key_skills) ? c.key_skills : [],
      candidate_location: String(c.candidate_location || '').trim(),
      experience_evidence: String(c.experience_evidence || '').trim(),
      profile_url: url
    } });
  }
  for (const l of lines) {
    const key = l.link.toLowerCase().replace(/\/+$/, '');
    if (!l.person || gotLine[l.n] || gotUrl[key] || seen[key]) continue;
    seen[key] = true; restored++;
    out.push({ json: { candidate_name: nameFromTitle(l.title), is_person: true, current_role: '', fit_score: 3,
      match_summary: 'Not scored by the AI (it skipped this profile) - please review manually.', key_skills: [],
      candidate_location: '', experience_evidence: 'not stated', profile_url: l.link, restored: true } });
  }
});
if (out.length) out[0].json.restored_total = restored;
return out;
"""

GH_TO_ORGANIC_V2 = r"""
// GitHub users WITH their real profile, plus the language they were found by.
const splits = $('GH Split Users').all();
let lang = '';
try { const m = String($('Build GH Query').first().json.gh_query || '').match(/language:(\S+)/); lang = m ? m[1] : ''; } catch (e) {}
const out = [];
$input.all().forEach(function(it, i) {
  const p = it.json || {};
  const login = String(p.login || (splits[i] && splits[i].json.login) || '').trim();
  if (!login || !p.id) return;
  const name = String(p.name || '').trim();
  const bits = [];
  if (lang) bits.push('Writes ' + lang + ' (matched by GitHub language search)');
  if (p.bio) bits.push('Bio: ' + String(p.bio).replace(/\s+/g, ' ').slice(0, 300));
  if (p.company) bits.push('Company: ' + p.company);
  if (p.location) bits.push('Location: ' + p.location);
  if (typeof p.public_repos === 'number') bits.push(p.public_repos + ' public repos');
  if (typeof p.followers === 'number') bits.push(p.followers + ' followers');
  if (p.created_at) bits.push('on GitHub since ' + String(p.created_at).slice(0, 4));
  if (p.blog) bits.push('Site: ' + p.blog);
  if (p.hireable) bits.push('open to hire');
  out.push({ title: (name ? name + ' (@' + login + ')' : login) + ' - GitHub developer', snippet: bits.join(' | ') || 'GitHub developer', link: p.html_url || ('https://github.com/' + login) });
});
return [{ json: { organic: out, source: 'github', results_count: out.length } }];
"""


def apply_round2(nodes, conns):
    N = {n["name"]: n for n in nodes}

    def code(name):
        return N[name]["parameters"]

    def R(text, old, new):
        assert old in text, "round2: anchor not found: " + old[:80]
        return text.replace(old, new)

    # LinkedIn + GitHub are always searched (they carry titles, emails, real names)
    sc = code("Search Criteria")
    sc["jsCode"] = R(sc["jsCode"], "if (!platforms.length) platforms = ['LinkedIn', 'Github'];",
        "if (!platforms.length) platforms = ['LinkedIn', 'Github'];\n"
        "// LinkedIn and GitHub are the core people sources: always include them\n"
        "['LinkedIn', 'Github'].forEach(function(must){ if (!platforms.some(function(p){ return String(p).toLowerCase() === must.toLowerCase(); })) platforms.push(must); });")
    # no skills typed? take the tech words from the role ("java backend developer" -> Java)
    sc["jsCode"] = R(sc["jsCode"], "if (!primary.length && !secondary.length) { primary = toArr(p.key_skills); }",
        "if (!primary.length && !secondary.length) { primary = toArr(p.key_skills); }\n"
        "if (!primary.length) {\n"
        "  const roleWords = ' ' + String(pick(form['1) Target Role'], p.target_role, '')).toLowerCase() + ' ';\n"
        "  const TECH = ['java','python','javascript','typescript','golang','go','rust','c++','c#','.net','ruby','php','kotlin','swift','scala','react','angular','vue','node.js','node','spring boot','spring','django','fastapi','flask','kafka','aws','azure','gcp','kubernetes','docker','sql','salesforce','sap','android','ios','flutter','data','ml','ai'];\n"
        "  primary = TECH.filter(function(t){ return roleWords.indexOf(' ' + t + ' ') !== -1; }).map(function(t){ return t === 'go' ? 'Go' : t.replace(/^./, function(c){ return c.toUpperCase(); }); });\n"
        "}")

    # smaller numbered batches + put back skipped people
    code("Format Search Results")["jsCode"] = FORMAT_BATCHES_V2
    msg = N["Message a model"]["parameters"]["responses"]["values"][0]
    msg["content"] = R(msg["content"], "9. profile_url (string - the URL at the end of their line, copied exactly)",
        "9. profile_url (string - the URL at the end of their line, copied exactly)\n"
        "10. line (number - the [n] at the start of that line)\n"
        "Each line starts with [n]. You MUST return exactly one object per line, for every line, including weak matches.")
    code("Split Candidates")["jsCode"] = SPLIT_V2

    # GitHub: language evidence, public email, personal site
    code("GH To Organic")["jsCode"] = GH_TO_ORGANIC_V2
    mg = code("Merge GitHub")
    mg["jsCode"] = R(mg["jsCode"], "  merged.github_status = 'enriched';",
        "  const ghEmail = s(resp.email);\n"
        "  if (ghEmail && !merged.email) { merged.email = ghEmail; merged.email_status = 'public on GitHub profile'; }\n"
        "  if (s(resp.twitter_username)) merged.twitter = 'https://x.com/' + s(resp.twitter_username);\n"
        "  merged.github_status = 'enriched';")
    pc = code("Parse Contact")
    pc["jsCode"] = R(pc["jsCode"], "merged.contact_source = merged.email\n  ? (merged.email_status.indexOf('resume') !== -1 ? 'resume' : (merged.enrichment_status === 'enriched' ? 'apollo' : ''))\n  : '';",
        "merged.contact_source = merged.email\n"
        "  ? (merged.email_status.indexOf('resume') !== -1 ? 'resume' : (merged.email_status.indexOf('GitHub') !== -1 ? 'GitHub profile' : (merged.enrichment_status === 'enriched' ? 'apollo' : '')))\n"
        "  : '';")
    pc["jsCode"] = R(pc["jsCode"], "// --- data confidence flag (helps recruiters trust each row) ---",
        "// --- no CV file found: fall back to the person's own website / portfolio ---\n"
        "if (!merged.resume_link && !merged.drive_cv_link && merged.github_blog) {\n"
        "  let site = String(merged.github_blog).trim();\n"
        "  if (site && !/^https?:\\/\\//i.test(site)) site = 'https://' + site;\n"
        "  merged.resume_link = site;\n"
        "  merged.resume_source = 'personal website (from GitHub)';\n"
        "  merged.resume_status = 'portfolio / personal site';\n"
        "}\n"
        "if (merged.twitter && !/x\\.com|twitter/i.test(merged.work_history || '')) merged.work_history = (merged.work_history ? merged.work_history + ' | ' : '') + 'X: ' + merged.twitter;\n"
        "// --- data confidence flag (helps recruiters trust each row) ---")
    # a "GitHub profile" email is real but unverified: stay Medium, never Low
    pc["jsCode"] = R(pc["jsCode"], "  else if (es.indexOf('resume') !== -1) reasons.push('email from resume (unverified)');",
        "  else if (es.indexOf('resume') !== -1) reasons.push('email from resume (unverified)');\n"
        "  else if (es.indexOf('GitHub') !== -1) { score += 1; reasons.push('email public on GitHub'); }")

    # resume search: name + CV words + city; the scorer still verifies the hit
    rs = N["Resume Search"]["parameters"]
    rs["jsonBody"] = ("={{ JSON.stringify({ q: '\"' + String($json.candidate_name || '').replace(/\"/g, '') + '\" (resume OR cv OR \"curriculum vitae\" OR portfolio) ' + "
                      "String($('Search Criteria').first().json['Location'] || '').split('-')[0].trim(), num: 10 }) }}")
    ar = code("Attach Resume")
    ar["jsCode"] = R(ar["jsCode"], "  if (link.indexOf('read.cv') !== -1 ||",
        "  // the candidate's own site (from GitHub) or a github.io page is a real portfolio/CV source\n"
        "  const ownSite = String(cand.github_blog || '').toLowerCase().replace(/^https?:\\/\\//, '').replace(/\\/.*$/, '');\n"
        "  if (ownSite && link.indexOf(ownSite) !== -1) score += 45;\n"
        "  if (/\\.github\\.io/.test(link)) score += 35;\n"
        "  if (link.indexOf('read.cv') !== -1 ||")
    return nodes, conns


# ======================================================================
# Round 3 (third live run, 159 rows): drop unknown-location rows from
# sources that cannot filter by city; Reddit via Google; clean platform names.
# ======================================================================
def apply_round3(nodes, conns):
    N = {n["name"]: n for n in nodes}

    def code(name):
        return N[name]["parameters"]

    def R(text, old, new):
        assert old in text, "round3: anchor not found: " + old[:80]
        return text.replace(old, new)

    lc = code("Location Check & Rank")
    lc["jsCode"] = R(lc["jsCode"], "  if (tier === 9) { dropped++; return; }",
        "  // Stack Overflow, GitLab, Dev.to, Hugging Face, Kaggle, Hacker News and Reddit cannot search by city:\n"
        "  // when a city was asked for, keep their people only if a location confirms it.\n"
        "  const globalSource = /(stackoverflow\\.com|gitlab\\.com|dev\\.to|huggingface\\.co|kaggle\\.com|ycombinator\\.com|reddit\\.com)/i.test(String(c.profile_url || ''));\n"
        "  if (wanted && globalSource && tier === 1) tier = 9;\n"
        "  if (tier === 9) { dropped++; return; }")
    # Reddit's API now refuses scripts (403): search it through Google instead
    code("Build Reddit Query")["jsCode"] = "// Reddit blocks direct API calls; Reddit is searched via Google (Build Search Query).\nreturn [];\n"
    bq = code("Build Search Query")
    bq["jsCode"] = R(bq["jsCode"], "  'Behance': ['site:behance.net']\n};",
        "  'Behance': ['site:behance.net'],\n  'Reddit': ['site:reddit.com/r/forhire', 'site:reddit.com/user']\n};")
    pc = code("Parse Contact")
    pc["jsCode"] = R(pc["jsCode"], "  try { return String(url).replace(/^https?:\\/\\//,'').split('/')[0]; } catch (e) { return 'Other'; }",
        "  return 'Open Web';")
    return nodes, conns


# ======================================================================
# Round 4 (SDET + recruiter UAT)
# ======================================================================
RESULTS_GATE_V2 = r"""
// Runs after the candidate branch has finished (n8n runs branches in order).
// If nothing reached the sheet - no results, or everyone filtered out - send
// the zero-result notice AND close the Search Log row, so it never stays
// "Processing".
try { if ($('Location Check & Rank').all().length > 0) return []; } catch (e) { /* step never ran */ }
let scanned = 0, valid = 0;
for (const it of $input.all()) {
  const j = (it && it.json) || {};
  let raw = (j.output && j.output[0] && j.output[0].content && j.output[0].content[0]) ? j.output[0].content[0].text : (typeof j.text === 'string' ? j.text : '[]');
  raw = String(raw || '').trim().replace(/^```(?:json)?/i, '').replace(/```$/, '').trim();
  let parsed; try { parsed = JSON.parse(raw); } catch (e) { parsed = []; }
  if (parsed && !Array.isArray(parsed) && Array.isArray(parsed.candidates)) parsed = parsed.candidates;
  if (!Array.isArray(parsed)) parsed = [parsed];
  scanned += parsed.length;
  for (const c of parsed) if (c && c.candidate_name && c.is_person !== false) valid++;
}
let role = '', loc = '', queryStr = '', platforms = '';
try { const f = $('Search Criteria').first().json; role = f['Target Role'] || ''; loc = f['Location'] || ''; } catch (e) {}
try { const g = $('Build Search Query').first().json; queryStr = g.q || ''; platforms = g.platforms_used || ''; } catch (e) {}
const reason = valid > 0 ? (valid + ' people found, but none in ' + (loc || 'the requested location')) : 'no matching profiles found';
return [{ json: { role: role, location: loc, platforms: platforms, query: queryStr, scanned: scanned,
  count: 0, range_text: reason, search_role: role, search_location: loc } }];
"""


def apply_round4(nodes, conns):
    N = {n["name"]: n for n in nodes}

    def code(name):
        return N[name]["parameters"]

    def R(text, old, new):
        assert old in text, "round4: anchor not found: " + old[:80]
        return text.replace(old, new)

    # GitHub is searched automatically only for tech roles (an HR search must
    # not return random developers in that city)
    gq = code("Build GH Query")
    gq["jsCode"] = R(gq["jsCode"], "const locs = aliasesFor(loc)",
        "// no programming language in the role/skills -> not a developer search\n"
        "if (!lang) return [];\nconst locs = aliasesFor(loc)")
    # zero results: notice + close the Search Log row
    code("Results Gate")["jsCode"] = RESULTS_GATE_V2
    conns["Results Gate"] = {"main": [[{"node": "No Results Alert", "type": "main", "index": 0},
                                       {"node": "Log Search Complete", "type": "main", "index": 0}]]}
    # outreach: no n8n footer, subject that fits agency recruiting, poll every 15 min
    oe = N["Send Outreach Email"]["parameters"]
    oe["options"] = dict(oe.get("options") or {}, appendAttribution=False)
    oe["subject"] = "={{ $json['Search Role'] }} role{{ $json['Search Location'] ? ' in ' + $json['Search Location'] : '' }} - quick chat?"
    N["Outreach Schedule"]["parameters"]["rule"] = {"interval": [{"field": "minutes", "minutesInterval": 15}]}
    return nodes, conns


def apply_round5(nodes, conns):
    """Re-running a search must not un-tick "Send Email?" on existing rows."""
    N = {n["name"]: n for n in nodes}
    mc = N["Merge CRM State"]["parameters"]
    old = "  merged.is_new = false;"
    assert old in mc["jsCode"]
    mc["jsCode"] = mc["jsCode"].replace(old,
        "  const tick = existing['Send Email?'];\n"
        "  merged.send_email = (tick === true || /^(true|yes|1)$/i.test(String(tick || '').trim())) ? 'TRUE' : 'FALSE';\n"
        "  merged.is_new = false;")
    old2 = "  merged.is_new = true;"
    assert old2 in mc["jsCode"]
    mc["jsCode"] = mc["jsCode"].replace(old2, "  merged.send_email = 'FALSE';\n  merged.is_new = true;")
    N["Append row in sheet"]["parameters"]["columns"]["value"]["Send Email?"] = "={{ $json.send_email || 'FALSE' }}"
    return nodes, conns


def apply_round6(nodes, conns):
    """UAT: Apollo people search over-narrowed; confirmation text promised Slack."""
    N = {n["name"]: n for n in nodes}
    aq = N["Build Apollo Query"]["parameters"]
    old = "  if (keywords) body.q_keywords = keywords;"
    assert old in aq["jsCode"]
    aq["jsCode"] = aq["jsCode"].replace(old,
        "  // keywords are ANDed by Apollo: only use them when there is no title to search on\n"
        "  if (keywords && !titles.length) body.q_keywords = keywords;")
    opt = N["On form submission"]["parameters"]["options"]["respondWithOptions"]["values"]
    old2 = "Ranked candidates also post to the recruitment Slack channel."
    assert old2 in opt["formSubmittedText"]
    opt["formSubmittedText"] = opt["formSubmittedText"].replace(old2,
        "Large searches across many sources can take up to 10 minutes. If Slack is connected, a summary is posted there too.")
    return nodes, conns


def apply_round7(nodes, conns):
    """'Send Email?' checkbox on every row the pipeline writes (validation only:
    writing values into empty rows would push later results below a gap)."""
    N = {n["name"]: n for n in nodes}
    ap = N["Append row in sheet"]
    col = list(ap["parameters"]["columns"]["value"].keys())
    sheet_cols = ['Rank', 'Name', 'Role', 'Fit Score', 'Summary', 'Key Skills', 'Verified Gender', 'Current Company',
                  'Verified Title', 'Location', 'Email', 'Phone', 'GitHub', 'Work History', 'Education', 'LinkedIn', 'Resume Link',
                  'Date Added', 'Source Platform', 'Resume Source', 'Contact Source', 'Search Role', 'Search Location',
                  'Pipeline Status', 'Outreach Status', 'Dedupe Key', 'Send Email?', 'Confidence', 'Profile URL']
    assert set(col) <= set(sheet_cols)
    idx = sheet_cols.index('Send Email?')
    body = {"requests": [{"setDataValidation": {
        "range": {"sheetId": 0, "startRowIndex": 1, "startColumnIndex": idx, "endColumnIndex": idx + 1},
        "rule": {"condition": {"type": "BOOLEAN"}, "showCustomUi": True}}}]}
    pos = ap["position"]
    nodes.append({"parameters": {"method": "POST", "url": "https://sheets.googleapis.com/v4/spreadsheets/__SHEET_ID__:batchUpdate",
                                 "authentication": "predefinedCredentialType", "nodeCredentialType": "googleSheetsOAuth2Api",
                                 "sendBody": True, "specifyBody": "json", "jsonBody": json.dumps(body), "options": {}},
                  "id": "fix-tick-boxes", "name": "Tick Boxes", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
                  "position": [pos[0] + 220, pos[1] + 160], "executeOnce": True, "onError": "continueRegularOutput",
                  "credentials": json.loads(json.dumps(ap["credentials"]))})
    conns["Append row in sheet"]["main"][0].append({"node": "Tick Boxes", "type": "main", "index": 0})
    return nodes, conns


def apply_round8(nodes, conns):
    """UAT: Apollo rows ranked below 25 were first-name-only. Apollo-sourced
    people (exact match by Apollo id) are always enriched; LinkedIn-sourced
    lookups stay limited to the top 25 to protect Apollo credits."""
    N = {n["name"]: n for n in nodes}
    hl = N["Has LinkedIn"]["parameters"]["conditions"]
    hl["conditions"] = [{"id": "fix-enrich", "leftValue":
        "={{ /apollo\\.io\\/people\\//i.test($json.profile_url || '') || (/linkedin\\.com/i.test($json.profile_url || '') && Number($json.rank) <= %d) }}" % APOLLO_ENRICH_TOP,
        "rightValue": True, "operator": {"type": "boolean", "operation": "true", "singleValue": True}}]
    hl["combinator"] = "and"
    return nodes, conns


# ======================================================================
# Round 9 (recruiter UAT: Senior ML Engineer, Bangalore, all sources)
# ======================================================================
CITY_PROFILES_QUERY = r"""
// 3) Kaggle / Stack Overflow / Hugging Face cannot be searched by city through their
// own APIs, so their people were always dropped for a city search. Google shows the
// city on their profile pages ("Bengaluru, Karnataka, India"), so search them there.
const cityPlatforms = { 'Kaggle': 'site:kaggle.com', 'Stack Overflow': 'site:stackoverflow.com/users', 'Hugging Face': 'site:huggingface.co' };
const citySites = platforms.filter(function(pl){ return cityPlatforms[pl]; }).map(function(pl){ return cityPlatforms[pl]; });
if (citySites.length && locTerm) {
  const topic = skillsTerm || roleTerm;
  const notProfiles = '-inurl:code -inurl:datasets -inurl:discussion -inurl:competitions -inurl:models -inurl:spaces -inurl:questions';
  const parts = [citySites.length > 1 ? '(' + citySites.join(' OR ') + ')' : citySites[0], locTerm, topic, excludeTerm, notProfiles];
  queries.push({ kind: 'city-profiles', q: parts.filter(Boolean).join(' ').trim(), pages: 2 });
}
const out = [];"""

RESUME_FILE_URL = ("={{ /\\.(pdf|docx?)(\\?|#|$)|drive\\.google\\.com|docs\\.google\\.com|dropbox\\.com|1drv\\.ms|onedrive/i"
                   ".test($json.resume_link || '') ? $json.resume_link : '' }}")


def apply_round9(nodes, conns):
    N = {n["name"]: n for n in nodes}

    def R(text, old, new):
        assert old in text, "round9: anchor not found: " + old[:80]
        return text.replace(old, new, 1)

    # 1. city-aware search for Kaggle / Stack Overflow / Hugging Face profiles
    bq = N["Build Search Query"]["parameters"]
    bq["jsCode"] = R(bq["jsCode"], "\nconst out = [];", CITY_PROFILES_QUERY)

    # 2. a resume hit must carry the person's name in its address or title, not just
    #    somewhere in a long PDF (college reports and bank lists were attached as CVs)
    ar = N["Attach Resume"]["parameters"]
    ar["jsCode"] = R(ar["jsCode"],
        "  // --- hard blocks: registry/court/news/job-board hosts, or directory-style pages ---",
        "  // the name must be in the link or the title (their own site from GitHub is exempt):\n"
        "  // a name that only appears inside a long document's text is a list, not their CV\n"
        "  const ownHost = String(cand.github_blog || '').toLowerCase().replace(/^https?:\\/\\//, '').replace(/\\/.*$/, '');\n"
        "  const head = (link + ' ' + String(o.title || '')).toLowerCase();\n"
        "  if (!(ownHost && link.indexOf(ownHost) !== -1) && !tokens.some(function(t){ return head.indexOf(t) !== -1; }))\n"
        "    return { link: o.link || '', score: -999, nameHit: true };\n\n"
        "  // --- hard blocks: registry/court/news/job-board hosts, or directory-style pages ---")
    ar["jsCode"] = R(ar["jsCode"], "|yumpu\\.com)/i;", "|yumpu\\.com|naac|annual[-_ ]?report)/i;")

    # 3. only download real files (PDF / Word / Drive / Dropbox); a web page link stays a
    #    link in the sheet. Shorter timeout so one slow server can't stall the run.
    dl = N["Download Resume"]["parameters"]
    dl["url"] = RESUME_FILE_URL
    dl["options"]["timeout"] = 10000
    return nodes, conns


# ======================================================================
# Round 10 (recruiter UAT re-run): the monthly Apollo allowance stopped the
# whole search, silently (the only notice went to Slack).
# ======================================================================
SERPER_CAP = 1000
APOLLO_CAP = 100
APOLLO_LEFT = "Number($('Budget Gate').first().json.PDL_Used || 0) < %d" % APOLLO_CAP


def apply_round10(nodes, conns):
    N = {n["name"]: n for n in nodes}

    # 1. only the Serper (Google search) allowance can stop a run
    cond = N["If"]["parameters"]["conditions"]
    cond["conditions"] = [c for c in cond["conditions"] if "PDL_Used" not in c["leftValue"]]
    assert len(cond["conditions"]) == 1 and "Serper_Used" in cond["conditions"][0]["leftValue"]

    # 2. Apollo allowance used up -> skip Apollo search and lookups, keep everything else
    aq = N["Build Apollo Query"]["parameters"]
    aq["jsCode"] = ("// Monthly Apollo allowance used up: skip Apollo, the other sources still run.\n"
                    "try { if (!(" + APOLLO_LEFT + ")) return []; } catch (e) {}\n" + aq["jsCode"])
    hl = N["Has LinkedIn"]["parameters"]["conditions"]["conditions"][0]
    assert hl["leftValue"].startswith("={{ ") and hl["leftValue"].endswith(" }}")
    hl["leftValue"] = "={{ (" + hl["leftValue"][4:-3] + ") && " + APOLLO_LEFT + " }}"

    # 3. a run stopped by the limit says so in the Search Log (Slack may not be connected)
    ls = N["Log Search Start"]
    stop = json.loads(json.dumps(ls))
    stop["name"] = "Log Budget Stop"
    stop["id"] = "fix-log-budget-stop"
    stop["position"] = [N["Send a message1"]["position"][0], N["Send a message1"]["position"][1] + 180]
    stop["parameters"]["columns"]["value"].update({
        "Status": "={{ '⛔ Paused - monthly Google search limit reached (' + $('Budget Gate').first().json.Serper_Used + '/%d). Resumes next month.' }}" % SERPER_CAP,
        "Completed At": "={{ $now.toFormat('yyyy-LL-dd HH:mm:ss') }}"})
    stop["onError"] = "continueRegularOutput"
    nodes.append(stop)
    conns["If"]["main"][1].append({"node": "Log Budget Stop", "type": "main", "index": 0})
    return nodes, conns


# ======================================================================
# Round 11 (recruiter UAT re-run): the zero-result check ran BEFORE the
# candidates reached the sheet whenever one branch had nothing in it (e.g.
# no Apollo lookups), posting a false "no candidates" alert. A Merge that
# waits for the end of the candidate branch fixes the order (verified on a
# live n8n: it runs last in every case, including truly zero results).
# ======================================================================
def apply_round11(nodes, conns):
    N = {n["name"]: n for n in nodes}
    gate = N["Results Gate"]
    nodes.append(_node("Candidates Done", "n8n-nodes-base.merge", 3,
                       [gate["position"][0] - 200, gate["position"][1]], {"numberInputs": 2}))
    mm = conns["Message a model"]["main"][0]
    assert any(t["node"] == "Results Gate" for t in mm)
    conns["Message a model"]["main"][0] = [t for t in mm if t["node"] != "Results Gate"] + [
        {"node": "Candidates Done", "type": "main", "index": 0}]
    conns["Append row in sheet"]["main"][0].append({"node": "Candidates Done", "type": "main", "index": 1})
    conns["Candidates Done"] = {"main": [[{"node": "Results Gate", "type": "main", "index": 0}]]}
    code = gate["parameters"]["jsCode"]
    old = "for (const it of $input.all()) {"
    assert old in code
    gate["parameters"]["jsCode"] = code.replace(
        "// Runs after the candidate branch has finished (n8n runs branches in order).",
        "// Runs after the candidate branch has finished: \"Candidates Done\" waits for the\n"
        "// sheet write (or, when nothing reaches the sheet, for the run to settle).").replace(
        old,
        "let aiItems; try { aiItems = $('Message a model').all(); } catch (e) { aiItems = $input.all(); }\n"
        "for (const it of aiItems) {")
    return nodes, conns


# ======================================================================
# Round 12 (recruiter UAT): "Senior Machine Learning Engineer" was searched as
# "learning engineer" senior machine - the core title was always the last two
# words. Recognise three-word titles built on a compound ("machine learning",
# "site reliability", "full stack", "human resources", ...).
# ======================================================================
def apply_round12(nodes, conns):
    N = {n["name"]: n for n in nodes}
    p = N["Build Search Query"]["parameters"]
    s = p["jsCode"]

    def R(old, new):
        nonlocal s
        assert old in s, "round12: anchor not found: " + old[:60]
        s = s.replace(old, new, 1)

    R("  'data scientist': [",
      "  'machine learning engineer': ['machine learning engineer','ml engineer','applied scientist','ai engineer','deep learning engineer'],\n"
      "  'site reliability engineer': ['site reliability engineer','sre','devops engineer','platform engineer'],\n"
      "  'data scientist': [")
    R("    const core = words.length > 2 ? words.slice(-2).join(' ') : words.join(' ');\n"
      "    const extra = words.length > 2 ? words.slice(0, -2) : [];",
      "    // titles built on a two-word compound keep all three words: \"machine learning engineer\"\n"
      "    const COMPOUND = /^(machine learning|deep learning|site reliability|full stack|data science|human resources|talent acquisition|business development|customer success|quality assurance|product marketing|technical program|business intelligence|natural language|computer vision)$/;\n"
      "    const take = (words.length >= 3 && (COMPOUND.test(words.slice(-3, -1).join(' ')) || synMap[words.slice(-3).join(' ')])) ? 3 : 2;\n"
      "    const core = words.length > take ? words.slice(-take).join(' ') : words.join(' ');\n"
      "    const extra = words.length > take ? words.slice(0, -take) : [];")
    p["jsCode"] = s
    return nodes, conns


# ======================================================================
# Round 13 (recruiter feedback): never drop a candidate just because their
# city is unknown; say what we know (country) or flag them for review when the
# platform can't filter by location. Each search gets a "REQ" header row above
# its results, a "Search Geography" column, and a "Location Review" column the
# sheet colours by value.
# ======================================================================
SHEET_COLUMNS_V2 = ['Rank', 'Name', 'Role', 'Fit Score', 'Summary', 'Key Skills', 'Verified Gender', 'Current Company',
                    'Verified Title', 'Location', 'Location Review', 'Email', 'Phone', 'GitHub', 'Work History', 'Education',
                    'LinkedIn', 'Resume Link', 'Date Added', 'Source Platform', 'Resume Source', 'Contact Source',
                    'Search Role', 'Search Location', 'Search Geography', 'Pipeline Status', 'Outreach Status',
                    'Dedupe Key', 'Send Email?', 'Confidence', 'Profile URL', 'Experience Check']

LOCATION_DECIDE = r"""
// Final step before the sheet: decide where each person is and set the final rank
// across ALL sources. Only people CLEARLY in another country are removed; anyone
// whose city isn't known stays, labelled with what we do know.
const form = $('Search Criteria').first().json;
const wanted = String(form['Location'] || '').split('-')[0].trim();
const wantAliases = aliasesFor(wanted);
const wantCountry = countryOf(wanted) || countryOf(form['Search Region'] || '');
const titleCase = function(s){ return String(s || '').replace(/\b\w/g, function(ch){ return ch.toUpperCase(); }); };
// sources whose own search cannot filter by city (Google results from them carry the city in the snippet)
const GLOBAL_SOURCES = [['stackoverflow.com','Stack Overflow'], ['gitlab.com','GitLab'], ['dev.to','Dev.to'], ['huggingface.co','Hugging Face'],
  ['kaggle.com','Kaggle'], ['ycombinator.com','Hacker News'], ['reddit.com','Reddit']];
const kept = [];
let dropped = 0;
$input.all().forEach(function(it, idx) {
  const c = Object.assign({}, it.json);
  const url = String(c.profile_url || '').toLowerCase();
  const locText = [c.location, c.candidate_location].filter(Boolean).join(' | ');
  if (!c.location && c.candidate_location) c.location = c.candidate_location;
  const gs = GLOBAL_SOURCES.find(function(g){ return url.indexOf(g[0]) !== -1; });
  let tier = 0, label = '', review = '';
  if (wanted) {
    const lt = ' ' + locText.toLowerCase() + ' ';
    const inCity = !!locText && wantAliases.some(function(a){ return lt.indexOf(a) !== -1; });
    const country = locText ? countryOf(locText) : '';
    const otherCity = !!locText && CITY_ALIASES.some(function(g){ return g !== wantAliases && g.some(function(a){ return a.length > 3 && lt.indexOf(a) !== -1; }); });
    if (inCity) { tier = 0; label = 'in ' + wanted; review = '✅ In ' + wanted; }
    else if (wantCountry && country && country !== wantCountry) { tier = 9; }
    else if (country) { tier = 2; label = 'same country, different city';
      review = '🟡 ' + titleCase(country) + (otherCity ? ' - other city' : ' - city not listed'); }
    else if (gs) { tier = 3; label = 'location unknown';
      review = '⚠️ Review: ' + gs[1] + " can't filter by location - check profile"; }
    else { tier = 1; label = 'location unknown';
      review = '🟡 Location not on profile (found by ' + wanted + ' search)'; }
  }
  if (tier === 9) { dropped++; return; }
  c.location_tier = tier;
  c.location_check = label;
  c.location_review = review;
  kept.push({ json: c, pairedItem: { item: idx } });
});
kept.sort(function(a, b) {
  if (a.json.location_tier !== b.json.location_tier) return a.json.location_tier - b.json.location_tier;
  return (Number(b.json.fit_score) || 0) - (Number(a.json.fit_score) || 0);
});
kept.forEach(function(k, i) { k.json.rank = i + 1; k.json.location_dropped_total = dropped; });
return kept;
"""

CRM_MATCH_V2 = r"""const cand = $json;
// One row per person PER REQ: the same person found for a different role or city
// gets their own row under that req's header.
function norm(s) { return String(s || '').toLowerCase().replace(/\s+/g, ' ').trim(); }
const reqOf = function(role, loc) { return '|req:' + norm(role) + '@' + norm(loc); };
const baseKey = norm(cand.dedupe_key);
const key = baseKey ? baseKey + reqOf(cand.search_role, cand.search_location) : '';
const liKey = String(cand.verified_linkedin || cand.profile_url || '').toLowerCase().trim();
let rows = [];
try { rows = $('Load CRM Rows').all().map(function(i){ return i.json; }); } catch (e) { rows = []; }
const sameReq = function(r) { return norm(r['Search Role']) === norm(cand.search_role) && norm(r['Search Location']) === norm(cand.search_location); };
let existing = null;
for (const r of rows) {
  const rk = norm(r['Dedupe Key']);
  if (key && rk && (rk === key || (rk === baseKey && sameReq(r)))) { existing = r; break; }
}
if (!existing && liKey) {
  for (const r of rows) {
    const rli = String(r['LinkedIn'] || '').toLowerCase().trim();
    if (rli && rli === liKey && sameReq(r)) { existing = r; break; }
  }
}
const merged = Object.assign({}, cand);
merged.dedupe_key = existing ? String(existing['Dedupe Key']) : key;
if (existing) {"""

REQ_HEADER = r"""
// One "REQ" row above each search's results, so every search reads as a block:
// what was searched, where, when, and how many people are listed below it.
const items = $input.all();
if (!items.length) return [];
const f = $('Search Criteria').first().json;
const role = f['Target Role'] || 'Search';
const geo = items[0].json.search_geography || 'Global';
let platforms = f['Target Platforms'];
if (!Array.isArray(platforms)) platforms = platforms ? String(platforms).split(',') : [];
const fresh = items.filter(function(i){ return i.json.is_new !== false; }).length;
const refreshed = items.length - fresh;
const when = $now.toFormat('dd LLL yyyy, HH:mm');
const header = {
  rank: 'REQ',
  candidate_name: '📋 ' + role,
  current_role: geo,
  fit_score: '',
  match_summary: 'Searched ' + when + ' on ' + (platforms.map(function(p){ return String(p).trim(); }).filter(Boolean).join(', ') || 'all sources') +
    '. ' + fresh + ' candidate(s) listed below' + (refreshed ? '; ' + refreshed + ' already in the sheet from an earlier run of this req were refreshed in place.' : '.'),
  location: '', location_review: '', search_role: role, search_location: f['Location'] || '', search_geography: geo,
  date_added: $now.toFormat('yyyy-MM-dd HH:mm'), pipeline_status: 'Req', outreach_status: '', send_email: 'FALSE',
  dedupe_key: 'REQ|' + $execution.id, profile_url: '', source_platform: '', confidence: ''
};
return [{ json: header, pairedItem: { item: 0 } }].concat(items.map(function(it, i){ return { json: it.json, pairedItem: { item: i } }; }));
"""

GEOGRAPHY_JS = ("\nmerged.search_geography = (function(){ const f = $('Search Criteria').first().json; "
                "const parts = [String(f['Location'] || '').trim(), String(f['Search Region'] || '').replace(/\\s*\\(.*\\)\\s*/, '').trim()]"
                ".filter(function(p){ return p && !/^global|^all/i.test(p); }); "
                "const out = []; parts.forEach(function(p){ if (!out.some(function(o){ return o.toLowerCase() === p.toLowerCase(); })) out.push(p); }); "
                "return out.length ? out.join(', ') : 'Global'; })();\n")


def apply_round13(nodes, conns):
    N = {n["name"]: n for n in nodes}

    def R(text, old, new):
        assert old in text, "round13: anchor not found: " + old[:80]
        return text.replace(old, new, 1)

    # 1. location decision: keep unknown-city people, label them
    lc = N["Location Check & Rank"]["parameters"]
    i = lc["jsCode"].index("// Final step before the sheet")
    lc["jsCode"] = lc["jsCode"][:i] + LOCATION_DECIDE.lstrip()

    # 2. geography searched, on every row
    pc = N["Parse Contact"]["parameters"]
    pc["jsCode"] = R(pc["jsCode"], "merged.pipeline_status = 'Sourced';", GEOGRAPHY_JS + "merged.pipeline_status = 'Sourced';")

    # 3. one row per person per req
    cm = N["Merge CRM State"]["parameters"]
    head, tail = cm["jsCode"].split("if (existing) {", 1)
    cm["jsCode"] = CRM_MATCH_V2 + tail

    # 4. REQ header row before each search's rows
    ap = N["Append row in sheet"]
    nodes.append(_node("Add Req Header", "n8n-nodes-base.code", 2, [ap["position"][0] - 120, ap["position"][1] - 160], {"jsCode": REQ_HEADER}))
    mc = conns["Merge CRM State"]["main"][0]
    conns["Merge CRM State"]["main"][0] = [t for t in mc if t["node"] != "Append row in sheet"] + [{"node": "Add Req Header", "type": "main", "index": 0}]
    conns["Add Req Header"] = {"main": [[{"node": "Append row in sheet", "type": "main", "index": 0}]]}

    # 5. sheet columns
    cols = ap["parameters"]["columns"]
    cols["value"]["Location"] = "={{ $json.location || '' }}"
    cols["value"]["Location Review"] = "={{ $json.location_review || '' }}"
    cols["value"]["Search Geography"] = "={{ $json.search_geography || '' }}"
    have = {s["id"] for s in cols["schema"]}
    for c in ["Location Review", "Search Geography", "Profile URL"]:
        if c not in have:
            cols["schema"].append({"id": c, "displayName": c, "required": False, "defaultMatch": False, "display": True,
                                   "type": "string", "canBeUsedToMatch": True, "removed": False})
    cols["schema"].sort(key=lambda s: SHEET_COLUMNS_V2.index(s["id"]) if s["id"] in SHEET_COLUMNS_V2 else 99)

    # 6. the checkbox column moved
    tb = N["Tick Boxes"]["parameters"]
    body = json.loads(tb["jsonBody"])
    idx = SHEET_COLUMNS_V2.index("Send Email?")
    body["requests"][0]["setDataValidation"]["range"].update({"startColumnIndex": idx, "endColumnIndex": idx + 1})
    tb["jsonBody"] = json.dumps(body)
    return nodes, conns


# ======================================================================
# Round 14 (recruiter feedback): the REQ row must carry the full requisition -
# what was asked for, where, and what came back - readable in one line.
# The details go in Summary with the cells to its right left empty, so the
# text flows across the row in Google Sheets.
# ======================================================================
REQ_HEADER_V2 = r"""
// One requisition row above each search's results: everything the recruiter
// asked for, where, and what came back. The details sit in Summary and the
// cells to its right stay empty so the text reads across the whole row.
const items = $input.all();
if (!items.length) return [];
const f = $('Search Criteria').first().json;
const role = f['Target Role'] || 'Search';
const geo = items[0].json.search_geography || 'Global';
const list = function(v){ return (Array.isArray(v) ? v : String(v || '').split(',')).map(function(s){ return String(s).trim(); }).filter(Boolean); };
const platforms = list(f['Target Platforms']);
let jd = 'no JD';
try { const j = $('Prep JD').first().json; if (j.jd_present) jd = 'JD ' + (j.jd_source === 'pasted' ? 'pasted' : 'uploaded'); } catch (e) {}
const fresh = items.filter(function(i){ return i.json.is_new !== false; }).length;
const refreshed = items.length - fresh;
const lr = function(re){ return items.filter(function(i){ return re.test(String(i.json.location_review || '')); }).length; };
const inCity = lr(/^✅/), country = lr(/^🟡/), review = lr(/Review:/);
const parts = [];
parts.push('Must-have: ' + (f['Primary Skills'] || 'from role'));
if (f['Use Secondary Skills'] !== false && f['Secondary Skills']) parts.push('Nice-to-have: ' + f['Secondary Skills']);
parts.push('Experience: ' + (f['Experience Range (Years)'] || 'any'));
if (f['Education'] && !/^any$/i.test(f['Education'])) parts.push('Education: ' + f['Education']);
if (f['Preferred Industry']) parts.push('Industry: ' + f['Preferred Industry']);
if (f['Current Employer']) parts.push('Employer: ' + f['Current Employer']);
if (f['Keywords to Exclude']) parts.push('Excluding: ' + f['Keywords to Exclude']);
if (f['Gender Preference'] && !/^any$|^auto/i.test(f['Gender Preference'])) parts.push('Gender: ' + f['Gender Preference']);
parts.push('Platforms: ' + (platforms.join(', ') || 'all'));
parts.push(jd);
let result = items.length + ' candidates';
if (geo !== 'Global') result += ' (' + inCity + ' in ' + (f['Location'] || geo) + ', ' + country + ' country/unknown, ' + review + ' to review)';
if (refreshed) result += '; ' + refreshed + ' already listed under an earlier run of this req were refreshed there';
parts.push(result);
parts.push('Searched ' + $now.toFormat('dd LLL yyyy, HH:mm'));
const header = {
  rank: 'REQ', candidate_name: role, current_role: '📍 ' + geo, fit_score: '',
  match_summary: parts.join('  |  '),
  key_skills: '', current_company: '', verified_title: '', location: '', location_review: '', email: '', phone: '',
  github_url: '', work_history: '', education: '', verified_linkedin: '', resume_link: '', drive_cv_link: '',
  date_added: $now.toFormat('yyyy-MM-dd HH:mm'), source_platform: '', resume_source: '', contact_source: '',
  search_role: role, search_location: f['Location'] || '', search_geography: geo,
  pipeline_status: 'Requisition', outreach_status: '', send_email: 'FALSE',
  dedupe_key: 'REQ|' + $execution.id, profile_url: '', confidence: '', experience_evidence: ''
};
return [{ json: header, pairedItem: { item: 0 } }].concat(items.map(function(it, i){ return { json: it.json, pairedItem: { item: i } }; }));
"""


def apply_round14(nodes, conns):
    N = {n["name"]: n for n in nodes}
    N["Add Req Header"]["parameters"]["jsCode"] = REQ_HEADER_V2
    return nodes, conns


# ======================================================================
# Round 15 (recruiter question: "Apollo used but no contacts? no Hacker News
# or GitLab people?")
# - The Confidence note said "not Apollo-enriched (outside top 25)" even when
#   Apollo was off for the month. Say why: monthly limit / not a LinkedIn row.
# - GitLab's user API only matches usernames ("Kubernetes" finds accounts named
#   kubernetes-*), so it never produced real people. Search GitLab profiles via
#   Google instead, like Kaggle / Stack Overflow.
# - Hacker News: link to the person's HN profile (not one comment), and only
#   the city is required in the search; skills are optional words, so posts
#   don't need to mention every skill.
# ======================================================================
def apply_round15(nodes, conns):
    N = {n["name"]: n for n in nodes}

    def R(name, old, new, field="jsCode"):
        p = N[name]["parameters"]
        assert old in p[field], "round15 %s: anchor not found: %s" % (name, old[:70])
        p[field] = p[field].replace(old, new, 1)

    # 1. honest Apollo note
    R("Combine No Apollo",
      "enrichment_status: /linkedin\\.com|apollo\\.io/i.test(url) ? 'skipped - below top 25 (Apollo credits)' : 'skipped - not a LinkedIn/Apollo profile'",
      "enrichment_status: !/linkedin\\.com|apollo\\.io/i.test(url) ? 'skipped - not a LinkedIn/Apollo profile'\n"
      "    : ((function(){ try { return Number($('Budget Gate').first().json.PDL_Used || 0) >= %d; } catch (e) { return false; } })()\n"
      "      ? 'skipped - Apollo monthly limit reached' : 'skipped - below top 25 (Apollo credits)')" % APOLLO_CAP)
    R("Parse Contact",
      "  else if (String(merged.enrichment_status||'').indexOf('below top') !== -1) reasons.push('not Apollo-enriched (outside top 25)');",
      "  else if (String(merged.enrichment_status||'').indexOf('monthly limit') !== -1) reasons.push('Apollo off - monthly limit reached');\n"
      "  else if (String(merged.enrichment_status||'').indexOf('below top') !== -1) reasons.push('not Apollo-enriched (outside top 25)');")

    # 2. GitLab through Google
    N["Build GitLab Query"]["parameters"]["jsCode"] = (
        "// GitLab's user search only matches usernames, so it can't find people by skill.\n"
        "// GitLab profiles are searched through Google instead (Build Search Query).\n"
        "return [];\n")
    R("Build Search Query",
      "const cityPlatforms = { 'Kaggle': 'site:kaggle.com', 'Stack Overflow': 'site:stackoverflow.com/users', 'Hugging Face': 'site:huggingface.co' };",
      "const cityPlatforms = { 'Kaggle': 'site:kaggle.com', 'Stack Overflow': 'site:stackoverflow.com/users', 'Hugging Face': 'site:huggingface.co', 'GitLab': 'site:gitlab.com' };")
    R("Build Search Query",
      "if (citySites.length && locTerm) {",
      "// GitLab has no usable people search of its own, so it always goes through Google (with or without a city)\n"
      "if (citySites.length && (locTerm || citySites.indexOf('site:gitlab.com') !== -1)) {")
    R("Build Search Query", "-inurl:spaces -inurl:questions';", "-inurl:spaces -inurl:questions -inurl:merge_requests -inurl:issues -inurl:-/';")

    # 3. Hacker News: person link + city-required search
    R("HN To Organic",
      "const link = 'https://news.ycombinator.com/item?id=' + (s.objectID || '');",
      "const link = 'https://news.ycombinator.com/user?id=' + encodeURIComponent(s.author || '');")
    R("Build HN Query",
      "const url = 'https://hn.algolia.com/api/v1/search?tags=comment&hitsPerPage=40&query=' + encodeURIComponent(query);",
      "// the city must match; skills are optional words so a post needn't list every skill\n"
      "const optional = loc ? terms.filter(function(t){ return t !== loc; }) : [];\n"
      "const url = 'https://hn.algolia.com/api/v1/search?tags=comment&hitsPerPage=40&query=' + encodeURIComponent(query)\n"
      "  + (optional.length ? '&optionalWords=' + encodeURIComponent(optional.join(' ')) : '');")
    R("Format Search Results", "stackoverflow\\.com\\/users\\/|", "stackoverflow\\.com\\/users\\/|news\\.ycombinator\\.com\\/user\\?id=|")
    return nodes, conns


# ======================================================================
# Round 16 (recruiter UAT): Google (via Serper) occasionally returns an empty
# page for a valid query - one run lost all three LinkedIn pages that returned
# 10 results each a few minutes later. Retry empty pages once after a pause.
# ======================================================================
EMPTY_PAGES = r"""
// Pages that came back empty (Google sometimes returns nothing for a valid
// query): send them once more after a short pause.
const out = [];
$input.all().forEach(function(it, i) {
  const j = it.json || {};
  const sp = j.searchParameters;
  if (sp && sp.q && !(j.organic && j.organic.length)) {
    const body = { q: sp.q, num: sp.num || 10, page: sp.page || 1 };
    if (sp.gl) body.gl = sp.gl;
    out.push({ json: { serper_body: body }, pairedItem: { item: i } });
  }
});
return out;
"""


def apply_round16(nodes, conns):
    N = {n["name"]: n for n in nodes}
    s = N["Serper Search"]
    x, y = s["position"]
    assert conns["Serper Search"]["main"][0] == [{"node": "Wait For All Sources", "type": "main", "index": 0}]
    nodes.append(_node("Empty Pages", "n8n-nodes-base.code", 2, [x + 120, y + 140], {"jsCode": EMPTY_PAGES}))
    nodes.append(_node("Pause Before Retry", "n8n-nodes-base.wait", 1.1, [x + 280, y + 140], {"resume": "timeInterval", "amount": 4, "unit": "seconds"}))
    retry = json.loads(json.dumps(s))
    retry["name"] = "Serper Retry"
    retry["id"] = "fix-serper-retry"
    retry["position"] = [x + 440, y + 140]
    retry["onError"] = "continueRegularOutput"
    nodes.append(retry)
    nodes.append(_node("Serper Pages", "n8n-nodes-base.merge", 3, [x + 600, y], {"numberInputs": 2}))
    conns["Serper Search"]["main"][0] = [{"node": "Serper Pages", "type": "main", "index": 0},
                                         {"node": "Empty Pages", "type": "main", "index": 0}]
    conns["Empty Pages"] = {"main": [[{"node": "Pause Before Retry", "type": "main", "index": 0}]]}
    conns["Pause Before Retry"] = {"main": [[{"node": "Serper Retry", "type": "main", "index": 0}]]}
    conns["Serper Retry"] = {"main": [[{"node": "Serper Pages", "type": "main", "index": 1}]]}
    conns["Serper Pages"] = {"main": [[{"node": "Wait For All Sources", "type": "main", "index": 0}]]}
    return nodes, conns


# ======================================================================
# Round 17 (channel-by-channel test): Google reads only the first 32 words of
# a query. The open-web query was ~44 words, so its job-site exclusions were
# silently ignored and half the results were job ads. Every Google query is
# now built from prioritised parts and trimmed to 32 words (exclusions go
# first, the site filter and the role go last). LinkedIn gets its own query
# instead of sharing one with X / Dribbble / Behance / Reddit. Open Web runs
# two short variants, and job-ad links are dropped after the search, which
# costs no query words. GitHub no longer skips searches whose skills aren't a
# programming language (DevOps: Kubernetes, Terraform, AWS).
# ======================================================================
QUERY_BUILD_V2 = r"""
// ---- Every Google query must fit in 32 words (Google ignores the rest). ----
// Parts are listed most important first; each part has richer -> leaner
// versions. While a query is too long, the least important part shrinks first.
const LIMIT = 32;
function wc(s) { return String(s || '').trim().split(/\s+/).filter(Boolean).length; }
function fit(parts) {
  const idx = parts.map(function(){ return 0; });
  const make = function(){ return parts.map(function(p, i){ return p[idx[i]]; }).filter(Boolean).join(' ').replace(/\s+/g, ' ').trim(); };
  let s = make();
  while (wc(s) > LIMIT) {
    let moved = false;
    for (let i = parts.length - 1; i >= 0; i--) { if (idx[i] < parts[i].length - 1) { idx[i]++; moved = true; break; } }
    if (!moved) break;
    s = make();
  }
  return s;
}
function orOf(list) { list = list.filter(Boolean); if (!list.length) return ''; return list.length > 1 ? '(' + list.join(' OR ') + ')' : list[0]; }
function shrink(list) { const out = []; for (let n = list.length; n >= 1; n--) out.push(orOf(list.slice(0, n))); return out; }

// Role: core title (last two words, or three for compounds) as a phrase, the
// rest as required words, plus at most two synonyms of the core title.
let roleRaw = '', roleVariants = [], roleExtra = '';
if (role) {
  if (looksBoolean(role)) {
    roleRaw = '(' + role + ')';
  } else {
    const words = role.toLowerCase().split(/\s+/).filter(Boolean);
    const COMPOUND = /^(machine learning|deep learning|site reliability|full stack|data science|human resources|talent acquisition|business development|customer success|quality assurance|product marketing|technical program|business intelligence|natural language|computer vision)$/;
    const take = (words.length >= 3 && (COMPOUND.test(words.slice(-3, -1).join(' ')) || synMap[words.slice(-3).join(' ')])) ? 3 : 2;
    const core = words.length > take ? words.slice(-take).join(' ') : words.join(' ');
    roleExtra = words.length > take ? words.slice(0, -take).join(' ') : '';
    let variants = [core];
    if (synMap[core] && expand !== 'no' && expand !== 'false') variants = synMap[core];
    else if (synMap[role.toLowerCase()]) variants = synMap[role.toLowerCase()];
    roleVariants = variants.slice(0, 3).map(function(x){ return '"' + x + '"'; });
  }
}
const roleOpts = roleRaw ? [roleRaw] : shrink(roleVariants).map(function(t){ return (t + ' ' + roleExtra).trim(); });
const roleShort = roleOpts[roleOpts.length - 1] || '';

// Skills: must-haves only (secondary skills are used for ranking, not search);
// a skill already in the role words isn't repeated.
let skillArr = [];
let skillRawBool = '';
const skillSrc = String(form['Primary Skills'] || '') || String(skillsRaw || '');
if (looksBoolean(skillSrc)) skillRawBool = '(' + skillSrc.trim() + ')';
else {
  const rl = role.toLowerCase();
  skillArr = skillSrc.split(',').map(function(s){ return s.trim(); }).filter(function(s){ return s && rl.indexOf(s.toLowerCase()) === -1; }).slice(0, 3).map(q);
}
const skillOpts = skillRawBool ? [skillRawBool] : (skillArr.length ? shrink(skillArr) : ['']);
const skillsDrop = skillOpts.concat(['']);

const excludes = String(excludeRaw).split(',').map(function(s){ return s.trim(); }).filter(Boolean);
const exclOpts = excludes.length ? [excludes.map(function(s){ return '-' + q(s); }).join(' '), excludes.slice(0, 2).map(function(s){ return '-' + q(s); }).join(' '), ''] : [''];
const employerOpts = employer ? [q(employer), ''] : [''];

const eduMap = {
  'bachelors / degree': ['bachelor','"b.tech"','degree'],
  'masters': ['masters','"m.tech"','msc'],
  'phd': ['phd','doctorate'],
  'mba': ['mba']
};
let eduOpts = [''];
if (education && !/^any$/i.test(education)) {
  const v = eduMap[education.toLowerCase()];
  eduOpts = v ? shrink(v).concat(['']) : [q(education), ''];
}

// Location: city + its aliases (Bangalore OR Bengaluru), trimmed to the city alone if needed
const locList = aliasesFor(loc).filter(function(a){ return a.length > 3; }).map(q);
const locOpts = locList.length ? shrink(locList.slice(0, 3)) : (loc ? [q(loc.toLowerCase())] : ['']);

let platforms = form['Target Platforms'];
if (!Array.isArray(platforms)) platforms = platforms ? String(platforms).split(',').map(function(s){return s.trim();}).filter(Boolean) : [];
const has = function(re){ return platforms.some(function(pl){ return re.test(String(pl)); }); };
const otherSites = [];
if (has(/twitter|^x\b/i)) otherSites.push('site:x.com', 'site:twitter.com');
if (has(/dribbble/i)) otherSites.push('site:dribbble.com');
if (has(/behance/i)) otherSites.push('site:behance.net');
if (has(/reddit/i)) otherSites.push('site:reddit.com/r/forhire', 'site:reddit.com/user');
const openWeb = has(/google|open web/i);

const regionMap = {
  'india':'in','united states':'us','usa':'us','us':'us','united kingdom':'gb','uk':'gb','canada':'ca','australia':'au',
  'germany':'de','france':'fr','netherlands':'nl','singapore':'sg','united arab emirates':'ae','uae':'ae','dubai':'ae',
  'ireland':'ie','spain':'es','italy':'it','japan':'jp','brazil':'br','saudi arabia':'sa'
};
const regionRaw = String(form['Search Region'] || '').trim();
let gl = '';
if (regionRaw && !/^global|^all/i.test(regionRaw)) gl = regionMap[regionRaw.toLowerCase().replace(/\s*\(.*\)\s*/, '').trim()] || '';
if (!gl) { const c = countryOf(loc); const byCountry = {'india':'in','united states':'us','united kingdom':'gb','singapore':'sg','united arab emirates':'ae','germany':'de','netherlands':'nl','canada':'ca','australia':'au'}; gl = byCountry[c] || ''; }

const queries = [];
// 1) LinkedIn profiles - its own query so other sites don't crowd it out
if (has(/linkedin/i)) {
  queries.push({ kind: 'profiles', pages: 3, q: fit([
    ['site:linkedin.com/in'], roleOpts, locOpts, skillOpts,
    employerOpts, eduOpts, exclOpts, ['-intitle:jobs -inurl:jobs', '']]) });
}
// 2) X / Dribbble / Behance / Reddit profiles
if (otherSites.length) {
  queries.push({ kind: 'profiles', pages: 2, q: fit([
    [orOf(otherSites)], roleOpts, locOpts, skillsDrop, exclOpts, ['-inurl:jobs', '']]) });
}
// 3) Open web: personal sites, CVs and portfolios. Two short variants; job
// pages are also removed after the search (Merge Pages), which costs no words.
if (openWeb) {
  const noise = ['-inurl:jobs -inurl:careers -inurl:job -"apply now" -hiring', '-inurl:jobs -inurl:careers', ''];
  queries.push({ kind: 'open-web', pages: 2, q: fit([
    [roleShort ? roleOpts[0] : ''], ['(resume OR cv OR "curriculum vitae")'], locOpts, skillsDrop, exclOpts, noise]) });
  queries.push({ kind: 'open-web', pages: 2, q: fit([
    [roleShort ? roleOpts[0] : ''], ['("about me" OR portfolio OR site:github.io)'], locOpts, skillsDrop, exclOpts, noise]) });
}
// 4) Kaggle / Stack Overflow / Hugging Face (by city) and GitLab (always) through Google
const cityPlatforms = { 'Kaggle': 'site:kaggle.com', 'Stack Overflow': 'site:stackoverflow.com/users', 'Hugging Face': 'site:huggingface.co', 'GitLab': 'site:gitlab.com' };
const citySites = platforms.filter(function(pl){ return cityPlatforms[pl]; }).map(function(pl){ return cityPlatforms[pl]; });
if (citySites.length && (locList.length || loc || citySites.indexOf('site:gitlab.com') !== -1)) {
  const topic = skillArr.length || skillRawBool ? skillOpts : roleOpts;
  const notProfiles = ['-inurl:datasets -inurl:discussion -inurl:competitions -inurl:models -inurl:spaces -inurl:questions -inurl:issues -inurl:merge_requests -inurl:-/',
    '-inurl:datasets -inurl:discussion -inurl:questions -inurl:issues', ''];
  queries.push({ kind: 'city-profiles', pages: 2, q: fit([[orOf(citySites)], locOpts, topic, exclOpts, notProfiles]) });
}
const out = [];
for (const qq of queries) {
  if (wc(qq.q) > LIMIT) throw new Error('query over ' + LIMIT + ' words: ' + qq.q);
  for (let pg = 1; pg <= qq.pages; pg++) {
    const body = { q: qq.q, page: pg, num: 10 };
    if (gl) body.gl = gl;
    out.push({ json: { q: qq.q, kind: qq.kind, page: pg, gl: gl || 'global', words: wc(qq.q), serper_body: body, platforms_used: qq.kind } });
  }
}
// keep the pipeline flowing even when no web platform was ticked
if (!out.length) return [];
return out;
"""

JOB_AD_FILTER = r"""
// Job ads, job boards and document dumps are never candidates: drop them here
// (after the search) instead of spending Google's 32 query words on exclusions.
const JOB_AD = /myworkdayjobs|greenhouse\.io|lever\.co|smartrecruiters|\/\/careers?\.|\/careers?\/|\/jobs?\/|\/job-|naukri\.com|indeed\.|glassdoor\.|foundit\.|instahyre\.|cutshort\.io|wellfound\.com\/jobs|codingjobboard|scribd\.com|jobschat|linkedin\.com\/jobs|shine\.com|timesjobs|monsterindia|iimjobs|hirist/i;
"""


def apply_round17(nodes, conns):
    N = {n["name"]: n for n in nodes}
    p = N["Build Search Query"]["parameters"]
    anchor = "// Role: a long exact phrase"
    assert anchor in p["jsCode"], "round17: Build Search Query anchor missing"
    p["jsCode"] = p["jsCode"][:p["jsCode"].index(anchor)] + QUERY_BUILD_V2.lstrip()

    m = N["Merge Pages"]["parameters"]
    old = "    if (seen[key]) continue;"
    assert old in m["jsCode"], "round17: Merge Pages anchor missing"
    m["jsCode"] = JOB_AD_FILTER.lstrip() + m["jsCode"].replace(
        old, "    if (seen[key]) continue;\n    if (o && o.link && JOB_AD.test(String(o.link))) { seen[key] = true; continue; }", 1)

    g = N["Build GH Query"]["parameters"]
    old = "let lang = '';"
    assert old in g["jsCode"], "round17: Build GH Query anchor missing"
    g["jsCode"] = g["jsCode"].replace(
        "const langMap = { ",
        "const langMap = { 'terraform':'hcl','kubernetes':'go','k8s':'go','docker':'go','helm':'go','ansible':'python','aws':'python','azure':'python','gcp':'python','pytorch':'python','tensorflow':'python','spark':'scala','swiftui':'swift','android':'kotlin','flutter':'dart','dart':'dart', ", 1)
    old_skip = "if (!lang) return [];"
    assert old_skip in g["jsCode"], "round17: GH skip anchor missing"
    g["jsCode"] = g["jsCode"].replace(
        old_skip,
        "// no language: technical roles still search active developers in the city;\n"
        "// non-technical roles (HR, sales, PM...) skip GitHub, which would only add random developers\n"
        "if (!lang && !/engineer|developer|devops|\\bsre\\b|architect|programmer|scientist|\\bcoder\\b/i.test(role)) return [];", 1)
    old_base = "const base = (lang ? 'language:' + lang + ' ' : '') + 'repos:>3';"
    assert old_base in g["jsCode"], "round17: GH base anchor missing"
    g["jsCode"] = g["jsCode"].replace(old_base, "const base = lang ? 'language:' + lang + ' repos:>3' : 'repos:>5 followers:>10';", 1)
    return nodes, conns


# ======================================================================
# Round 18 (after the channel-by-channel test):
# - Ticking one platform now searches only that platform. LinkedIn + GitHub
#   are added only when nothing is ticked.
# - Google Sheets calls retry on "too many requests" (several searches at
#   once hit the Sheets rate limit and one run wrote nothing).
# - X / Dribbble / Behance / Reddit and Kaggle / SO / HF / GitLab get a second,
#   differently-worded Google query (role vs skills), since few profiles on
#   these sites carry both the title and the skill words.
# ======================================================================
def apply_round18(nodes, conns):
    N = {n["name"]: n for n in nodes}

    def R(name, old, new):
        p = N[name]["parameters"]
        assert old in p["jsCode"], "round18 %s: anchor not found: %s" % (name, old[:70])
        p["jsCode"] = p["jsCode"].replace(old, new, 1)

    # 1. only what was ticked
    sc = N["Search Criteria"]["parameters"]["jsCode"]
    i = sc.index("// LinkedIn and GitHub are the core people sources: always include them")
    j = sc.index("platforms.push(must); });", i) + len("platforms.push(must); });")
    N["Search Criteria"]["parameters"]["jsCode"] = sc[:i] + "// only the platforms the recruiter ticked (LinkedIn + GitHub when none were ticked)" + sc[j:]

    # 2. Sheets: retry on rate limits
    for n in nodes:
        if n["type"].endswith("googleSheets"):
            n["retryOnFail"] = True
            n["maxTries"] = 5
            n["waitBetweenTries"] = 5000

    # 3. second wording for the low-yield sites
    R("Build Search Query",
      """    [orOf(otherSites)], roleOpts, locOpts, skillsDrop, exclOpts, ['-inurl:jobs', '']]) });
}""",
      """    [orOf(otherSites)], roleOpts, locOpts, skillsDrop, exclOpts, ['-inurl:jobs', '']]) });
  // people on these sites often show only a title or only their skills
  queries.push({ kind: 'profiles', pages: 1, q: fit([[orOf(otherSites)], roleOpts, locOpts, exclOpts]) });
  if (skillArr.length || skillRawBool) queries.push({ kind: 'profiles', pages: 1, q: fit([[orOf(otherSites)], skillOpts, locOpts, exclOpts]) });
}""")
    R("Build Search Query",
      """  queries.push({ kind: 'city-profiles', pages: 2, q: fit([[orOf(citySites)], locOpts, topic, exclOpts, notProfiles]) });""",
      """  queries.push({ kind: 'city-profiles', pages: 2, q: fit([[orOf(citySites)], locOpts, topic, exclOpts, notProfiles]) });
  // and once by job title, for profiles that don't list the skill words
  if (topic !== roleOpts && roleOpts.length) queries.push({ kind: 'city-profiles', pages: 1, q: fit([[orOf(citySites)], locOpts, roleOpts, exclOpts, notProfiles]) });""")
    return nodes, conns


# ======================================================================
# Round 19 (UX + speed + contacts):
# 1. Brief-first, two-page form. Page 1: describe the person (and JD).
#    Page 2: "Here's what we'll search for" - every field pre-filled from the
#    brief, editable, sources as tiles. Then a "Search started" page.
#    The old parser node becomes "Draft Criteria"; "Search Criteria" now reads
#    page 2, so every downstream node is unchanged.
# 2. AI scoring runs all batches in parallel (HTTP call to OpenAI instead of
#    the one-at-a-time model node). Same prompt, same output shape.
# 3. The requisition row lists candidates per source.
# 4. GitHub candidates without an email: their public commit email is used
#    (top 20 only, noreply addresses ignored).
# ======================================================================
FORM_CSS = r"""
:root { --ink:#161A2E; --muted:#5B6178; --line:#D3D6E2; --soft:#EEF0FD; --accent:#3B4BD6; --accent-dark:#2A37A8; --bg:#F4F5F9; }
body { background: var(--bg) !important; color: var(--ink); font-family: Inter, system-ui, -apple-system, "Segoe UI", sans-serif; }
.container { max-width: 760px !important; width: 100% !important; box-sizing: border-box; margin: 0 auto; padding: 40px 16px; }
.card { width: 100%; box-sizing: border-box; border: 1px solid #E3E5EE; border-radius: 18px; box-shadow: none; padding: 36px 40px; }
.form-header { text-align: left; margin-bottom: 8px; }
.form-header h1, h1 { font-size: 32px; font-weight: 800; letter-spacing: -0.5px; color: var(--ink); text-align: left; }
.form-header p { font-size: 15px; line-height: 1.55; color: var(--muted); text-align: left; }
.form-group { margin-bottom: 22px; }
.form-label, label { font-size: 14px; font-weight: 600; color: var(--ink); margin-bottom: 6px; display: block; }
input[type=text], input[type=email], input[type=number], textarea, select {
  width: 100%; box-sizing: border-box; min-height: 44px; border: 1px solid var(--line); border-radius: 10px;
  padding: 10px 14px; font-size: 15px; color: var(--ink); background: #fff; font-family: inherit !important; }
input:focus, textarea:focus, select:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(59,75,214,.15); outline: none; }
textarea { min-height: 110px; line-height: 1.5; resize: vertical; }
::placeholder { color: #8A8FA5; font-family: inherit; }
/* html blocks: a tip (has bold text) or a section heading */
.form-group.html div:has(b) { padding: 12px 14px; border-radius: 10px; background: var(--soft); color: #2A3170; font-size: 13.5px; line-height: 1.55; }
.form-group.html div:has(b) b { color: var(--accent-dark); }
.form-group.html div:not(:has(b)) { margin-top: 10px; padding-top: 18px; border-top: 1px solid #EEF0F5; font-size: 13px; font-weight: 700;
  letter-spacing: .6px; text-transform: uppercase; color: var(--muted); }
.form-group.html { margin-bottom: 10px; }
/* sources as tiles */
.multiselect { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.multiselect-option { display: flex; align-items: stretch; gap: 10px; min-height: 48px; box-sizing: border-box; padding: 0 0 0 12px; margin: 0;
  border: 1px solid var(--line); border-radius: 10px; background: #fff; font-size: 14px; font-weight: 500; cursor: pointer; min-width: 0; }
.multiselect-option:has(input:checked) { border: 2px solid var(--accent); background: #F7F8FF; }
.multiselect-option input { accent-color: var(--accent); width: 16px; height: 16px; margin: 0; flex: none; align-self: center; }
.multiselect-option label { flex: 1; display: flex; align-items: center; margin: 0; padding: 12px 12px 12px 0; font-weight: 500; cursor: pointer; color: var(--ink); overflow-wrap: anywhere; }
.header h1 { font-size: 28px !important; font-weight: 800; text-align: left; color: var(--ink); }
.header p { font-size: 15px !important; line-height: 1.55; text-align: left; color: var(--muted); }
button, button[type=submit] { width: 100%; min-height: 52px; border: 0; border-radius: 12px; background: var(--accent) !important; color: #fff;
  font-size: 16px; font-weight: 700; cursor: pointer; }
button:hover { background: var(--accent-dark) !important; }
.n8n-link { display: none !important; }
@media (max-width: 640px) {
  .card { padding: 24px 18px; border-radius: 14px; }
  .form-header h1, h1 { font-size: 26px; }
  .multiselect { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
"""

PAGE1_FIELDS = [
    {"fieldType": "html", "elementName": "tip",
     "html": "<div class=\"tr-tip\">Write it the way you'd brief a sourcer: <b>role, city, years, must-have skills</b>, anything to avoid. "
             "On the next page you'll see exactly what we read and can change any of it. Prefer filling fields yourself? Leave this empty and press Next.</div>"},
    {"fieldLabel": "Brief", "fieldType": "textarea",
     "placeholder": "e.g. Senior backend engineer in Bangalore, 5+ years of Java, Spring Boot and Kafka. No interns. Look on LinkedIn and GitHub."},
    {"fieldLabel": "Job description", "fieldType": "textarea",
     "placeholder": "Optional. Paste the JD and every candidate is scored against it."},
    {"fieldLabel": "Job description file", "fieldType": "file", "multipleFiles": False,
     "acceptFileTypes": ".pdf,.doc,.docx,.txt,.md,.markdown,.rtf,.html,.htm,.csv"},
]

PLATFORMS = ["LinkedIn", "GitHub", "Stack Overflow", "Google / Open Web", "Hacker News", "GitLab", "Kaggle",
             "Hugging Face", "Dev.to", "Reddit", "X (Twitter)", "Dribbble", "Behance"]
REGIONS = ["Global (all regions)", "India", "United States", "United Kingdom", "Canada", "Australia", "Germany", "France",
           "Netherlands", "Singapore", "United Arab Emirates", "Ireland"]


def _opts(xs):
    return {"values": [{"option": x} for x in xs]}


def _dd(name, label, options, default):
    return {"fieldName": name, "fieldLabel": label, "fieldType": "dropdown", "fieldOptions": _opts(options), "defaultValue": default}


def _tx(name, label, default, placeholder=""):
    f = {"fieldName": name, "fieldLabel": label, "fieldType": "text", "defaultValue": default}
    if placeholder:
        f["placeholder"] = placeholder
    return f


PAGE2_FIELDS = [
    {"fieldType": "html", "elementName": "s_role", "html": "<div class=\"tr-sec\">Role</div>"},
    _tx("role", "Job title", "={{ $json['Target Role'] || '' }}", "e.g. Backend Engineer"),
    _dd("similar", "Include similar titles (recommended)", ["Yes", "No"], "Yes"),
    _dd("experience", "Experience", ["Any", "0-2 years", "3-5 years", "5-8 years", "8+ years"],
        "={{ ['0-2 years','3-5 years','5-8 years','8+ years'].includes($json['Experience Range (Years)']) ? $json['Experience Range (Years)'] : 'Any' }}"),
    {"fieldType": "html", "elementName": "s_skills", "html": "<div class=\"tr-sec\">Skills</div>"},
    _tx("must", "Must-have skills (comma-separated)", "={{ $json['Primary Skills'] || '' }}", "e.g. Java, Spring Boot, Kafka"),
    _tx("nice", "Nice-to-have skills (only used for ranking)", "={{ $json['Secondary Skills'] || '' }}", "e.g. AWS, Microservices"),
    {"fieldType": "html", "elementName": "s_where", "html": "<div class=\"tr-sec\">Where</div>"},
    _tx("city", "City", "={{ $json['Location'] || '' }}", "e.g. Bangalore"),
    _dd("region", "Country / region", REGIONS,
        "={{ " + json.dumps(REGIONS) + ".includes($json['Search Region']) ? $json['Search Region'] : ($json['Location'] ? 'India' : 'Global (all regions)') }}"),
    {"fieldType": "html", "elementName": "s_more", "html": "<div class=\"tr-sec\">More filters (optional)</div>"},
    _tx("industry", "Industry", "={{ $json['Preferred Industry'] || '' }}", "e.g. Fintech, SaaS"),
    _tx("employer", "Current employer", "={{ $json['Current Employer'] || '' }}", "Only people at this company"),
    _tx("exclude", "Exclude words", "={{ $json['Keywords to Exclude'] || '' }}", "e.g. intern, trainee"),
    _dd("education", "Education", ["Any", "Bachelors / Degree", "Masters", "PhD", "MBA"],
        "={{ ['Bachelors / Degree','Masters','PhD','MBA'].includes($json['Education']) ? $json['Education'] : 'Any' }}"),
    _dd("gender", "Gender preference", ["Any", "Female", "Male"],
        "={{ ['Female','Male'].includes($json['Gender Preference']) ? $json['Gender Preference'] : 'Any' }}"),
    {"fieldType": "html", "elementName": "s_src", "html": "<div class=\"tr-sec\">Where to look</div>"},
    {"fieldName": "sources", "fieldLabel": "Sources", "fieldType": "checkbox", "fieldOptions": _opts(PLATFORMS),
     "defaultValue": "={{ ($json['Target Platforms'] || ['LinkedIn','GitHub']).map(function(p){ return /^github$/i.test(p) ? 'GitHub' : p; }).join(',') }}"},
]

FINAL_CRITERIA = r"""
// Page 2 of the form ("Here's what we'll search for"): what the recruiter
// confirmed. Same output shape as before, so every later step is unchanged.
const f = $input.first().json || {};
const d = (function(){ try { return $('Draft Criteria').first().json; } catch (e) { return {}; } })();
const val = function(k, label){ const v = f[k] !== undefined ? f[k] : f[label]; return v === undefined || v === null ? '' : v; };
const list = function(v){ return (Array.isArray(v) ? v : String(v || '').split(',')).map(function(s){ return String(s).trim(); }).filter(Boolean); };
const seen = {};
let primary = list(val('must', 'Must-have skills (comma-separated)')).filter(function(s){ const k = s.toLowerCase(); if (seen[k]) return false; seen[k] = 1; return true; });
let secondary = list(val('nice', 'Nice-to-have skills (only used for ranking)')).filter(function(s){ const k = s.toLowerCase(); if (seen[k]) return false; seen[k] = 1; return true; });
if (!primary.length) primary = list(d['Primary Skills']);
let platforms = list(val('sources', 'Sources'));
if (!platforms.length) platforms = ['LinkedIn', 'Github'];
const region = String(val('region', 'Country / region') || '');
return [{ json: {
  'Target Role': String(val('role', 'Job title') || d['Target Role'] || '').trim(),
  'Key Skills & Criteria': primary.concat(secondary).join(', '),
  'Primary Skills': primary.join(', '),
  'Secondary Skills': secondary.join(', '),
  'Use Secondary Skills': secondary.length > 0,
  'Location': String(val('city', 'City') || '').trim(),
  'Preferred Industry': String(val('industry', 'Industry') || '').trim(),
  'Current Employer': String(val('employer', 'Current employer') || '').trim(),
  'Keywords to Exclude': String(val('exclude', 'Exclude words') || '').trim(),
  'Education': val('education', 'Education') || 'Any',
  'Expand Job Title Synonyms': val('similar', 'Include similar titles (recommended)') || 'Yes',
  'Experience Range (Years)': val('experience', 'Experience') || 'Any',
  'Gender Preference': val('gender', 'Gender preference') || 'Any',
  'Target Platforms': platforms,
  'Search Region': /^global/i.test(region) ? '' : region,
  'nl_used': !!d.nl_used
} }];
"""

COMMIT_URL = ("={{ (function(j){ var m = String(j.github_url || j.profile_url || '').match(/github\\.com\\/([A-Za-z0-9-]+)\\/?$/i);"
              " return (m && !j.email && Number(j.rank) <= 20) ? 'https://api.github.com/search/commits?q=' + encodeURIComponent('author:' + m[1])"
              " + '&sort=author-date&order=desc&per_page=10' : 'https://api.github.com/rate_limit'; })($json) }}")

ATTACH_COMMIT = r"""
// A GitHub developer's public commit email, when their profile shows none.
// Only addresses that clearly belong to them (no noreply / bot / example).
const a = Object.assign({}, $('Attach Resume').item.json);
const r = $json || {};
const login = (String(a.github_url || a.profile_url || '').match(/github\.com\/([A-Za-z0-9-]+)\/?$/i) || [])[1] || '';
let email = '';
if (login && !a.email && Array.isArray(r.items)) {
  for (const it of r.items) {
    const e = String((it && it.commit && it.commit.author && it.commit.author.email) || '').trim();
    const who = String((it && it.author && it.author.login) || '').toLowerCase();
    if (who === login.toLowerCase() && /^[^@\s]+@[^@\s]+\.[a-z]{2,}$/i.test(e)
        && !/noreply|no-reply|localhost|\.local$|example\.(com|org)|invalid|\[bot\]|github\.com$/i.test(e)) { email = e; break; }
  }
}
a.commit_email = email;
return { json: a };
"""


def _template_to_js(t):
    assert t.startswith("=")
    t = t[1:]
    assert "`" not in t and "${" not in t
    t = t.replace("\\", "\\\\")
    t = re.sub(r"\{\{(.*?)\}\}", lambda m: "${" + m.group(1) + "}", t, flags=re.S)
    t = t.replace("$('Search Criteria').first().json", "S('Search Criteria')").replace("$('Prep JD').first().json", "S('Prep JD')")
    return t


def apply_round19(nodes, conns):
    N = {n["name"]: n for n in nodes}

    def R(name, old, new):
        p = N[name]["parameters"]
        assert old in p["jsCode"], "round19 %s: anchor not found: %s" % (name, old[:70])
        p["jsCode"] = p["jsCode"].replace(old, new, 1)

    # ---- 1. two-page form
    trig = N["On form submission"]
    tp = trig["parameters"]
    tp["formTitle"] = "Who are you hiring?"
    tp["formDescription"] = "Describe the person in a sentence or two. Next, you'll check what we understood before the search starts."
    tp["formFields"] = {"values": PAGE1_FIELDS}
    tp["options"]["buttonLabel"] = "Next: check the search"
    tp["options"]["customCss"] = FORM_CSS
    tp["options"].pop("respondWithOptions", None)
    tp["options"]["appendAttribution"] = False
    N["Parse NL Search"]["parameters"]["responses"]["values"][0]["content"] = N["Parse NL Search"]["parameters"]["responses"]["values"][0]["content"].replace(
        "json['Natural Language Search']", "json['Brief']")
    R("Prep JD", "json['Job Description']", "json['Job description']")

    sc = N["Search Criteria"]
    sc["name"] = "Draft Criteria"
    sc["id"] = "fix-draft-criteria"
    N["Draft Criteria"] = sc
    R("Draft Criteria", "const nlUsed = String(form['Natural Language Search'] || '').trim().length > 0;",
      "const nlUsed = String(form['Brief'] || '').trim().length > 0;")
    R("Draft Criteria", "pick(form['9) Experience Range (Years)'], p.experience_range, '0-2 years')",
      "pick(form['9) Experience Range (Years)'], p.experience_range, 'Any')")
    R("Draft Criteria", "pick(form['8) Expand Job Title Synonyms'], p.expand_synonyms, 'No')",
      "pick(form['8) Expand Job Title Synonyms'], p.expand_synonyms, 'Yes')")
    # sensible default sources for the role when the brief names none
    R("Draft Criteria", "if (!platforms.length) platforms = ['LinkedIn', 'Github'];",
      "if (!platforms.length) {\n"
      "  const r = String(pick(form['1) Target Role'], p.target_role, '')).toLowerCase();\n"
      "  if (/design|ux|ui\\b/.test(r)) platforms = ['LinkedIn', 'Dribbble', 'Behance'];\n"
      "  else if (/machine learning|\\bml\\b|\\bai\\b|data scien|nlp|computer vision|deep learning/.test(r)) platforms = ['LinkedIn', 'Github', 'Kaggle', 'Hugging Face'];\n"
      "  else if (/engineer|developer|devops|sre|architect|programmer/.test(r)) platforms = ['LinkedIn', 'Github', 'Stack Overflow'];\n"
      "  else platforms = ['LinkedIn', 'Google / Open Web'];\n"
      "}")
    x, y = sc["position"]
    page2 = _node("Check Search", "n8n-nodes-base.form", 2.5, [x + 200, y], {
        "operation": "page", "defineForm": "fields",
        "formFields": {"values": PAGE2_FIELDS},
        "options": {"formTitle": "Here's what we'll search for",
                    "formDescription": "Read from your brief and JD. Change anything, then search.",
                    "buttonLabel": "Search candidates", "customCss": FORM_CSS}})
    page2["webhookId"] = "6f1d2c3b-7a4e-4b5f-9c8d-0e1f2a3b4c51"  # fixed so re-running setup keeps the page
    done = _node("Search Started", "n8n-nodes-base.form", 2.5, [x + 400, y - 200], {
        "operation": "completion", "respondWith": "text", "completionTitle": "Search started",
        "completionMessage": "Results appear in your Google Sheet in about a minute or two. The Search Log tab shows Processing, then Complete with the candidate count. You can close this page.",
        "options": {"customCss": FORM_CSS}})
    done["webhookId"] = "6f1d2c3b-7a4e-4b5f-9c8d-0e1f2a3b4c52"
    final = _node("Search Criteria", "n8n-nodes-base.code", 2, [x + 400, y], {"jsCode": FINAL_CRITERIA})
    nodes.extend([page2, done, final])
    conns["Draft Criteria"] = conns.pop("Search Criteria")
    conns["Draft Criteria"]["main"][0] = [{"node": "Check Search", "type": "main", "index": 0}]
    conns["Check Search"] = {"main": [[{"node": "Search Started", "type": "main", "index": 0}, {"node": "Search Criteria", "type": "main", "index": 0}]]}
    conns["Search Criteria"] = {"main": [[{"node": "Guardrail Check", "type": "main", "index": 0}]]}
    for src, c in conns.items():
        for outs in c.get("main", []):
            for t in outs or []:
                if t["node"] == "Search Criteria" and src not in ("Check Search",):
                    t["node"] = "Draft Criteria"

    # ---- 2. parallel AI scoring
    mam = N["Message a model"]
    vals = mam["parameters"]["responses"]["values"]
    user_t = [v for v in vals if not v.get("role")][0]["content"]
    sys_t = [v for v in vals if v.get("role") == "system"][0]["content"]
    model = mam["parameters"]["modelId"]["value"]
    js = _template_to_js(user_t)
    R("Format Search Results", "const out = [];",
      "const S = function(n){ try { return $(n).first().json || {}; } catch (e) { return {}; } };\n"
      "const SYSTEM = " + json.dumps(sys_t) + ";\n"
      "function scoringPrompt(page_text) { const $json = { page_text: page_text }; return `" + js + "`; }\n"
      "const out = [];")
    R("Format Search Results", "out.push({ json: { page_text: text, lines: lines, batch: out.length + 1, pool_size: all.length } });",
      "out.push({ json: { page_text: text, lines: lines, batch: out.length + 1, pool_size: all.length, prompt: scoringPrompt(text), system: SYSTEM } });")
    pos, cred = mam["position"], mam["credentials"]
    nodes.remove(mam)
    http = _node("Message a model", "n8n-nodes-base.httpRequest", 4.2, pos, {
        "method": "POST", "url": "https://api.openai.com/v1/responses",
        "authentication": "predefinedCredentialType", "nodeCredentialType": "openAiApi",
        "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ model: '" + model + "', input: [{ role: 'system', content: $json.system }, { role: 'user', content: $json.prompt }] }) }}",
        "options": {"batching": {"batch": {"batchSize": 8, "batchInterval": 0}}, "timeout": 120000}},
        credentials=cred, retryOnFail=True, maxTries=3, waitBetweenTries=3000)
    http["id"] = "fix-parallel-scoring"
    nodes.append(http)

    # ---- 3. per-source counts on the requisition row
    R("Add Req Header", "parts.push(result);",
      "const bySrc = {};\n"
      "items.forEach(function(i){ const s = i.json.source_platform || 'Other'; bySrc[s] = (bySrc[s] || 0) + 1; });\n"
      "const srcLine = Object.keys(bySrc).sort(function(a, b){ return bySrc[b] - bySrc[a]; }).map(function(k){ return k + ' ' + bySrc[k]; }).join(' · ');\n"
      "if (srcLine) result += '; by source: ' + srcLine;\n"
      "parts.push(result);")

    # ---- 4. GitHub commit emails
    ge = N["GitHub Enrich"]
    ax, ay = N["Attach Resume"]["position"]
    cs = _node("Commit Email Search", "n8n-nodes-base.httpRequest", 4.2, [ax + 100, ay + 160], {
        "url": COMMIT_URL, "authentication": "predefinedCredentialType", "nodeCredentialType": "githubApi",
        "sendHeaders": True, "headerParameters": {"parameters": [{"name": "Accept", "value": "application/vnd.github+json"},
                                                                  {"name": "User-Agent", "value": "talentrubric-sourcing"}]},
        "options": {}}, credentials=json.loads(json.dumps(ge["credentials"])), onError="continueRegularOutput")
    cs["id"] = "fix-commit-email-search"
    ac = _node("Attach Commit Email", "n8n-nodes-base.code", 2, [ax + 260, ay + 160], {"jsCode": ATTACH_COMMIT, "mode": "runOnceForEachItem"})
    nodes.extend([cs, ac])
    conns["Attach Resume"] = {"main": [[{"node": "Commit Email Search", "type": "main", "index": 0}]]}
    conns["Commit Email Search"] = {"main": [[{"node": "Attach Commit Email", "type": "main", "index": 0}]]}
    conns["Attach Commit Email"] = {"main": [[{"node": "Download Resume", "type": "main", "index": 0}]]}
    R("Parse Contact", "const merged = Object.assign({}, cand);",
      "const merged = Object.assign({}, cand);\n"
      "try { const ce = $('Attach Commit Email').item.json.commit_email; if (ce && !merged.email) { merged.email = ce; merged.email_status = 'public in GitHub commits'; } } catch (e) {}")
    return nodes, conns


# ======================================================================
# Round 20:
# 1. The same person found on two sources becomes ONE row. Merged only when
#    the full name matches AND their city or company agrees (common names
#    stay separate); the row keeps both links and says "Also on ...".
# 2. Resume Link never holds a social page (YouTube, Medium, Linktree, ...).
# 3. A "results ready" email goes to the recruiter when a search finishes
#    (page 2 address, or the connected Gmail account by default).
# ======================================================================
MERGE_PEOPLE = r"""
// ---- The same person found on several sources becomes one row. ----
// Only when the FULL name matches and nothing disagrees: both in the searched
// city, or the same current company. Common names elsewhere stay separate.
const srcOf = function(u){ u = String(u || '').toLowerCase();
  const m = [['linkedin.com','LinkedIn'],['github.com','GitHub'],['stackoverflow.com','Stack Overflow'],['gitlab.com','GitLab'],['kaggle.com','Kaggle'],
    ['huggingface.co','Hugging Face'],['ycombinator.com','Hacker News'],['dev.to','Dev.to'],['reddit.com','Reddit'],['x.com','X (Twitter)'],['twitter.com','X (Twitter)'],
    ['dribbble.com','Dribbble'],['behance.net','Behance']].find(function(p){ return u.indexOf(p[0]) !== -1; });
  return m ? m[1] : 'Web'; };
const fullName = function(n){ const t = String(n || '').toLowerCase().replace(/[^a-z\s]/g, ' ').split(/\s+/).filter(function(w){ return w.length > 1; }); return t.length >= 2 ? t.join(' ') : ''; };
const co = function(c){ return String(c.current_company || '').toLowerCase().replace(/[^a-z0-9]/g, ''); };
const asList = function(v){ return (Array.isArray(v) ? v : String(v || '').split(',')).map(function(s){ return String(s).trim(); }).filter(Boolean); };
const firstOf = {};
const merged = [];
let mergedCount = 0;
kept.forEach(function(k) {
  const key = fullName(k.json.candidate_name);
  const prev = key ? firstOf[key] : null;
  if (prev) {
    const a = prev.json, b = k.json;
    const sameSrc = srcOf(a.profile_url) === srcOf(b.profile_url);
    const bothInCity = a.location_tier === 0 && b.location_tier === 0;
    const sameCo = co(a) && co(a) === co(b);
    if (!sameSrc && (bothInCity || sameCo)) {
      a.fit_score = Math.max(Number(a.fit_score) || 0, Number(b.fit_score) || 0);
      ['email','email_status','current_company','location','resume_link','github_blog','experience_evidence','phone','work_history','twitter'].forEach(function(f){ if (!a[f] && b[f]) a[f] = b[f]; });
      if (!a.github_url && /github\.com/i.test(b.github_url || b.profile_url)) a.github_url = b.github_url || b.profile_url;
      if (!a.verified_linkedin && /linkedin\.com\/in\//i.test(b.profile_url)) a.verified_linkedin = b.profile_url;
      const skills = asList(a.key_skills); asList(b.key_skills).forEach(function(s){ if (skills.map(function(x){ return x.toLowerCase(); }).indexOf(s.toLowerCase()) === -1) skills.push(s); });
      a.key_skills = Array.isArray(a.key_skills) ? skills : skills.join(', ');
      a.also_on = (a.also_on || []).concat([srcOf(b.profile_url)]);
      a.match_summary = String(a.match_summary || '').trim() + ' Also on ' + srcOf(b.profile_url) + ': ' + b.profile_url + '.';
      if (b.location_tier < a.location_tier) { a.location_tier = b.location_tier; a.location_check = b.location_check; a.location_review = b.location_review; }
      mergedCount++;
      return;
    }
  }
  if (key && !prev) firstOf[key] = k;
  merged.push(k);
});
kept.splice(0, kept.length);
merged.forEach(function(k){ k.json.merged_people_total = mergedCount; kept.push(k); });
"""

SOCIAL_FILTER = r"""
// A social page is not a resume: YouTube, Medium, Linktree and the like.
const SOCIAL = /(youtube\.com|youtu\.be|medium\.com|linktr\.ee|linktree|beacons\.ai|bio\.link|instagram\.com|facebook\.com|twitter\.com|\/\/x\.com|\/\/t\.me|tiktok\.com|bit\.ly|lnk\.bio|carrd\.co)/i;
if (merged.resume_link && SOCIAL.test(merged.resume_link) && !merged.drive_cv_link) { merged.resume_link = ''; merged.resume_source = ''; merged.resume_status = ''; }
if (merged.github_blog && SOCIAL.test(merged.github_blog)) merged.github_blog = '';
"""

RESULTS_NOTE = r"""
// What the "results ready" email says. Runs once per search: after the
// sheet write, or from the zero-result path.
let s = {};
try { s = $('Sourcing Summary').first().json; } catch (e) {}
if (!s || s.count === undefined) { try { s = $('Results Gate').first().json; } catch (e) { s = {}; } }
let req = '';
try { req = String($('Add Req Header').first().json.match_summary || ''); } catch (e) {}
let notify = '';
try { notify = String($('Search Criteria').first().json['Notify Email'] || '').trim(); } catch (e) {}
const count = Number(s.count) || 0;
const bySource = (req.match(/by source: ([^|]+)/) || [])[1] || '';
return [{ json: { count: count, role: s.search_role || '', location: s.search_location || '', range_text: s.range_text || '',
  by_source: bySource.trim(), notify: notify } }];
"""


def apply_round20(nodes, conns):
    N = {n["name"]: n for n in nodes}

    def R(name, old, new):
        p = N[name]["parameters"]
        assert old in p["jsCode"], "round20 %s: anchor not found: %s" % (name, old[:70])
        p["jsCode"] = p["jsCode"].replace(old, new, 1)

    # 1. one row per person
    R("Location Check & Rank", "kept.sort(function(a, b) {", MERGE_PEOPLE.lstrip() + "kept.sort(function(a, b) {")
    R("Parse Contact", "merged.source_platform = platformOf(merged.profile_url);",
      "merged.source_platform = platformOf(merged.profile_url) + ((merged.also_on || []).length ? ' + ' + merged.also_on.join(' + ') : '');")

    # 2. no social pages as resumes
    R("Parse Contact", "if (!merged.resume_link && !merged.drive_cv_link && merged.github_blog) {",
      SOCIAL_FILTER.lstrip() + "if (!merged.resume_link && !merged.drive_cv_link && merged.github_blog) {")

    # 3. results-ready email
    page2 = N["Check Search"]["parameters"]["formFields"]["values"]
    page2.append({"fieldType": "html", "elementName": "s_notify", "html": "<div class=\"tr-sec\">When it's ready</div>"})
    page2.append({"fieldName": "notify", "fieldLabel": "Email me the results at (optional – blank uses your connected Gmail)",
                  "fieldType": "email", "placeholder": "you@company.com"})
    R("Search Criteria", "  'nl_used': !!d.nl_used", "  'Notify Email': String(val('notify', 'Email me the results at (optional – blank uses your connected Gmail)') || '').trim(),\n  'nl_used': !!d.nl_used")
    ss = N["Sourcing Summary"]
    x, y = ss["position"]
    note = _node("Results Note", "n8n-nodes-base.code", 2, [x + 200, y + 360], {"jsCode": RESULTS_NOTE})
    note["executeOnce"] = True
    gm = N["Send Outreach Email"]
    addr = _node("Recruiter Address", "n8n-nodes-base.httpRequest", 4.2, [x + 400, y + 360], {
        "url": "https://gmail.googleapis.com/gmail/v1/users/me/profile",
        "authentication": "predefinedCredentialType", "nodeCredentialType": "gmailOAuth2", "options": {}},
        credentials=json.loads(json.dumps(gm["credentials"])), onError="continueRegularOutput")
    mail = _node("Results Email", "n8n-nodes-base.gmail", gm["typeVersion"], [x + 600, y + 360], {
        "sendTo": "={{ $('Results Note').first().json.notify || $json.emailAddress }}",
        "subject": "={{ $('Results Note').first().json.count ? $('Results Note').first().json.count + ' candidates ready' : 'No candidates found' }}: {{ $('Results Note').first().json.role }}{{ $('Results Note').first().json.location ? ' in ' + $('Results Note').first().json.location : '' }}",
        "message": "=<p>Your search for <b>{{ $('Results Note').first().json.role }}</b>{{ $('Results Note').first().json.location ? ' in ' + $('Results Note').first().json.location : '' }} has finished.</p>"
                   "<p><b>{{ $('Results Note').first().json.count }}</b> candidates{{ $('Results Note').first().json.by_source ? ' (' + $('Results Note').first().json.by_source + ')' : '' }}. {{ $('Results Note').first().json.range_text }}</p>"
                   "<p><a href=\"https://docs.google.com/spreadsheets/d/__SHEET_ID__/edit\">Open the results sheet</a> and tick <b>Send Email?</b> on the people you want to contact.</p>",
        "options": {"appendAttribution": False}},
        credentials=json.loads(json.dumps(gm["credentials"])), onError="continueRegularOutput")
    nodes.extend([note, addr, mail])
    conns["Sourcing Summary"]["main"][0].append({"node": "Results Note", "type": "main", "index": 0})
    conns["Results Gate"]["main"][0].append({"node": "Results Note", "type": "main", "index": 0})
    conns["Results Note"] = {"main": [[{"node": "Recruiter Address", "type": "main", "index": 0}]]}
    conns["Recruiter Address"] = {"main": [[{"node": "Results Email", "type": "main", "index": 0}]]}
    return nodes, conns


# ======================================================================
# Round 21:
# 1. "Experience Check" column: what the evidence says (years stated in the
#    profile, Senior/Junior in the title, GitHub account age) against the
#    range the recruiter asked for.
# 2. Wellfound tried and NOT offered: Google no longer indexes wellfound.com/u
#    profiles (0 results live). Naukri and Instahyre are not added: candidate profiles there are
#    private (recruiter database access only), so Google can't find them.
# ======================================================================
EXPERIENCE_CHECK = r"""
// --- experience check: what the evidence says vs the range asked for ---
(function(){
  let want = '';
  try { want = String($('Search Criteria').first().json['Experience Range (Years)'] || ''); } catch (e) {}
  const m = want.match(/(\d+)\s*-\s*(\d+)/), plus = want.match(/(\d+)\s*\+/);
  const lo = m ? Number(m[1]) : (plus ? Number(plus[1]) : null), hi = m ? Number(m[2]) : null;
  const range = lo === null ? '' : (hi === null ? lo + '+ yrs' : lo + '-' + hi + ' yrs');
  const text = [merged.experience_evidence, merged.current_role, merged.verified_title].filter(function(x){ return x && x !== 'not stated'; }).join(' | ');
  let years = null;
  (text.match(/(\d{1,2})\s*\+?\s*(?:years?|yrs?)/gi) || []).forEach(function(t){ const n = parseInt(t, 10); if (n > 0 && n <= 40 && (years === null || n > years)) years = n; });
  const senior = /\b(principal|staff|lead|head|director|architect|senior|sr\.?|manager)\b/i.test(text);
  const junior = /\b(intern|junior|jr\.?|fresher|graduate|trainee)\b/i.test(text);
  let since = parseInt(String(merged.github_since || ''), 10);
  const thisYear = Number($now.toFormat('yyyy'));
  let out;
  if (years !== null) {
    if (lo === null) out = 'ℹ️ ' + years + ' yrs stated';
    else if (years < lo) out = '⚠️ ' + years + ' yrs stated, below ' + range;
    else if (hi !== null && years > hi + 2) out = '🟡 ' + years + ' yrs stated, above ' + range;
    else out = '✅ ' + years + ' yrs stated';
  } else if (senior) {
    out = (hi !== null && hi < 5) ? '🟡 Senior title, asked ' + range : '✅ Senior title';
  } else if (junior) {
    out = (lo !== null && lo >= 3) ? '⚠️ Junior title, asked ' + range : '✅ Junior title';
  } else if (since > 2000 && since <= thisYear) {
    out = '🟡 Not stated · on GitHub since ' + since;
  } else {
    out = '- Not stated';
  }
  merged.experience_check = out;
})();
"""


def apply_round21(nodes, conns):
    N = {n["name"]: n for n in nodes}

    def R(name, old, new):
        p = N[name]["parameters"]
        assert old in p["jsCode"], "round21 %s: anchor not found: %s" % (name, old[:70])
        p["jsCode"] = p["jsCode"].replace(old, new, 1)

    # 1. experience check
    R("Merge GitHub", "const ghName = s(resp.name);", "merged.github_since = s(resp.created_at).slice(0, 4);\n  const ghName = s(resp.name);")
    R("Parse Contact", "// --- data confidence flag", EXPERIENCE_CHECK.lstrip() + "// --- data confidence flag")
    cols = N["Append row in sheet"]["parameters"]["columns"]
    cols["value"]["Experience Check"] = "={{ $json.experience_check || '' }}"
    cols["schema"].append({"id": "Experience Check", "displayName": "Experience Check", "required": False, "defaultMatch": False,
                           "display": True, "type": "string", "canBeUsedToMatch": True, "removed": False})
    # the requisition row has the same columns
    R("Add Req Header", "dedupe_key: 'REQ|' + $execution.id,", "dedupe_key: 'REQ|' + $execution.id, experience_check: '',")

    # 2. Wellfound
    R("Build Search Query", "if (has(/reddit/i)) otherSites.push('site:reddit.com/r/forhire', 'site:reddit.com/user');",
      "if (has(/reddit/i)) otherSites.push('site:reddit.com/r/forhire', 'site:reddit.com/user');\nif (has(/wellfound|angel/i)) otherSites.push('site:wellfound.com/u');")
    R("Merge Pages", "wellfound\\.com\\/jobs", "wellfound\\.com\\/(jobs|role|company|location|startups)")
    R("Location Check & Rank", "['dribbble.com','Dribbble'],['behance.net','Behance']]", "['dribbble.com','Dribbble'],['behance.net','Behance'],['wellfound.com','Wellfound']]")
    # Live test (27 Oct): Google returns nothing for site:wellfound.com/u, even for
    # "site:wellfound.com/u Python bangalore" - Wellfound profiles are no longer
    # indexed. The query code stays (harmless) but Wellfound is NOT offered on
    # the form, so recruiters don't tick a source that can't return anyone.
    return nodes, conns


# ======================================================================
# Round 22: a more colourful form. Same layout and fields; colour only:
# soft gradient page, gradient band on the card, a colour per section,
# brand-tinted source tiles, gradient button. Text keeps 4.5:1 contrast and
# colour never carries meaning on its own (every tile and section is labelled).
# ======================================================================
COLOR_CSS = r"""
/* ---- colour layer ---- */
body { background: linear-gradient(160deg, #EEF0FF 0%, #F7F3FF 45%, #FFF1EE 100%) fixed !important; }
.card { position: relative; overflow: hidden; border: 0; box-shadow: 0 18px 50px rgba(59,75,214,.12), 0 2px 6px rgba(22,26,46,.06); }
.card::before { content: ""; position: absolute; left: 0; right: 0; top: 0; height: 6px;
  background: linear-gradient(90deg, #3B4BD6, #7C3AED 35%, #DB2777 70%, #F97316); }
.form-header h1, .header h1 { color: #1E2A8A !important; }
.form-header h1::after { content: ""; display: block; width: 64px; height: 4px; margin-top: 10px; border-radius: 2px;
  background: linear-gradient(90deg, #E03A3E, #F97316); }
/* tip box */
.form-group.html div:has(b) { background: linear-gradient(135deg, #EEF0FF, #FCE7F3); border: 1px solid #DCDDFE; color: #2A2F6B; }
.form-group.html div:has(b) b { color: #C0264D; }
/* section headings: one colour each */
.form-group.html div:not(:has(b)) { display: inline-block; border-top: 0; margin-top: 18px; padding: 6px 12px; border-radius: 999px;
  font-size: 12px; letter-spacing: .8px; }
.form-group.html:nth-child(1 of .html) div:not(:has(b)) { background: #E0E7FF; color: #3730A3; }
.form-group.html:nth-child(2 of .html) div:not(:has(b)) { background: #CCFBF1; color: #0F766E; }
.form-group.html:nth-child(3 of .html) div:not(:has(b)) { background: #FEF3C7; color: #92400E; }
.form-group.html:nth-child(4 of .html) div:not(:has(b)) { background: #EDE9FE; color: #5B21B6; }
.form-group.html:nth-child(5 of .html) div:not(:has(b)) { background: #FFE4E6; color: #BE123C; }
.form-group.html:nth-child(6 of .html) div:not(:has(b)) { background: #DCFCE7; color: #166534; }
/* inputs */
input[type=text], input[type=email], textarea, select { background: #FCFCFF; border-color: #D9DBEE; }
input:focus, textarea:focus, select:focus { border-color: #7C3AED; box-shadow: 0 0 0 3px rgba(124,58,237,.18); }
/* source tiles: each tinted in its platform's colour */
.multiselect-option { --c: #3B4BD6; --t: #F4F5FF; border-left: 4px solid var(--c); }
.multiselect-option:has(input:checked) { border: 2px solid var(--c); border-left-width: 6px; background: var(--t); }
.multiselect-option input { accent-color: var(--c); }
.multiselect-option:nth-child(1)  { --c: #0A66C2; --t: #EAF3FC; } /* LinkedIn */
.multiselect-option:nth-child(2)  { --c: #24292F; --t: #F2F3F5; } /* GitHub */
.multiselect-option:nth-child(3)  { --c: #E8590C; --t: #FFF1E8; } /* Stack Overflow */
.multiselect-option:nth-child(4)  { --c: #0D9488; --t: #E9FBF8; } /* Open Web */
.multiselect-option:nth-child(5)  { --c: #EA580C; --t: #FFF2EA; } /* Hacker News */
.multiselect-option:nth-child(6)  { --c: #C2410C; --t: #FFF0E8; } /* GitLab */
.multiselect-option:nth-child(7)  { --c: #0891B2; --t: #E8F8FC; } /* Kaggle */
.multiselect-option:nth-child(8)  { --c: #CA8A04; --t: #FFF9E6; } /* Hugging Face */
.multiselect-option:nth-child(9)  { --c: #334155; --t: #F1F5F9; } /* Dev.to */
.multiselect-option:nth-child(10) { --c: #E0480F; --t: #FFF0EA; } /* Reddit */
.multiselect-option:nth-child(11) { --c: #111827; --t: #F3F4F6; } /* X */
.multiselect-option:nth-child(12) { --c: #DB2777; --t: #FDECF4; } /* Dribbble */
.multiselect-option:nth-child(13) { --c: #1D4ED8; --t: #EAF0FD; } /* Behance */
/* button */
button, button[type=submit] { background: linear-gradient(90deg, #3B4BD6, #7C3AED 55%, #DB2777) !important;
  box-shadow: 0 10px 24px rgba(124,58,237,.28); }
button:hover { filter: brightness(.94); }
/* end page */
.header h1::before { content: "✓"; display: inline-flex; align-items: center; justify-content: center; width: 34px; height: 34px;
  margin-right: 12px; border-radius: 50%; background: #16A34A; color: #fff; font-size: 20px; vertical-align: 4px; }
"""


def apply_round22(nodes, conns):
    N = {n["name"]: n for n in nodes}
    for name in ["On form submission", "Check Search", "Search Started"]:
        o = N[name]["parameters"]["options"]
        assert "customCss" in o, name
        o["customCss"] = o["customCss"] + COLOR_CSS
    return nodes, conns


# ======================================================================
# Round 23: Talent Rubric brand colours (charcoal #3A3A3C, red #BF1E2D,
# white) and the logo at the top right of every form page. Fields: Gender
# preference removed (sourcing on gender is a legal and fairness risk, and
# the AI is told to ignore protected attributes); "Current employer" becomes
# "Target companies" (up to three, OR-ed in the search); "Exclude words"
# also takes companies.
# ======================================================================
import base64 as _b64
from pathlib import Path as _Path
_LOGO = _b64.b64encode((_Path(__file__).parent / "src" / "logo-form.png").read_bytes()).decode()

BRAND_CSS = r"""
:root { --ink:#2B2B2E; --muted:#5F5F66; --line:#D6D6DB; --char:#3A3A3C; --char-2:#2E2E30; --red:#BF1E2D; --red-dark:#9C1724; --red-soft:#FBEBED; }
body { background: var(--char) !important; color: var(--ink); font-family: Inter, system-ui, -apple-system, "Segoe UI", sans-serif; }
body::after { content: ""; position: fixed; top: 26px; right: 32px; width: 230px; height: 25px;
  background: url(data:image/png;base64,__LOGO__) no-repeat right center / contain; pointer-events: none; }
.container { max-width: 760px !important; width: 100% !important; box-sizing: border-box; margin: 0 auto; padding: 40px 16px; }
.card { position: relative; overflow: hidden; width: 100%; box-sizing: border-box; border: 0; border-radius: 16px; padding: 36px 40px;
  box-shadow: 0 24px 60px rgba(0,0,0,.35); background: #fff; }
.card::before { content: ""; position: absolute; left: 0; right: 0; top: 0; height: 6px; background: var(--red); }
.form-header, .header { text-align: left; margin-bottom: 8px; }
.form-header h1, .header h1, h1 { font-size: 32px !important; font-weight: 800; letter-spacing: -0.5px; color: var(--char) !important; text-align: left; }
.form-header h1::after { content: ""; display: block; width: 56px; height: 4px; margin-top: 10px; background: var(--red); }
.form-header p, .header p { font-size: 15px !important; line-height: 1.55; color: var(--muted); text-align: left; }
.form-group { margin-bottom: 22px; }
.form-label, label { font-size: 14px; font-weight: 600; color: var(--ink); margin-bottom: 6px; display: block; }
input[type=text], input[type=email], input[type=number], textarea, select {
  width: 100%; box-sizing: border-box; min-height: 44px; border: 1px solid var(--line); border-radius: 10px;
  padding: 10px 14px; font-size: 15px; color: var(--ink); background: #fff; font-family: inherit !important; }
input:focus, textarea:focus, select:focus { border-color: var(--red); box-shadow: 0 0 0 3px rgba(191,30,45,.15); outline: none; }
textarea { min-height: 110px; line-height: 1.5; resize: vertical; }
::placeholder { color: #8E8E96; font-family: inherit; }
/* tip box (html block with bold text) */
.form-group.html { margin-bottom: 10px; }
.form-group.html div:has(b) { padding: 12px 14px; border-radius: 10px; background: #F4F4F5; border-left: 4px solid var(--red);
  color: var(--ink); font-size: 13.5px; line-height: 1.55; }
.form-group.html div:has(b) b { color: var(--red); }
/* section headings: charcoal and red badges, alternating */
.form-group.html div:not(:has(b)) { display: inline-block; margin-top: 18px; padding: 6px 12px; border-radius: 6px;
  font-size: 12px; font-weight: 800; letter-spacing: .9px; text-transform: uppercase; background: var(--char); color: #fff; }
.form-group.html:nth-child(even of .html) div:not(:has(b)) { background: var(--red); }
/* source tiles */
.multiselect { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.multiselect-option { display: flex; align-items: stretch; gap: 10px; min-height: 48px; box-sizing: border-box; padding: 0 0 0 12px; margin: 0;
  border: 1px solid var(--line); border-radius: 10px; background: #fff; cursor: pointer; min-width: 0; }
.multiselect-option:has(input:checked) { border: 2px solid var(--red); background: var(--red-soft); }
.multiselect-option input { accent-color: var(--red); width: 16px; height: 16px; margin: 0; flex: none; align-self: center; }
.multiselect-option label { flex: 1; display: flex; align-items: center; margin: 0; padding: 12px 12px 12px 0; font-size: 14px; font-weight: 500;
  cursor: pointer; color: var(--ink); overflow-wrap: anywhere; }
button, button[type=submit] { width: 100%; min-height: 52px; border: 0; border-radius: 12px; background: var(--red) !important; color: #fff;
  font-size: 16px; font-weight: 700; cursor: pointer; box-shadow: 0 10px 24px rgba(191,30,45,.3); }
button:hover { background: var(--red-dark) !important; }
.header h1::before { content: "✓"; display: inline-flex; align-items: center; justify-content: center; width: 32px; height: 32px;
  margin-right: 12px; border-radius: 50%; background: var(--red); color: #fff; font-size: 18px; vertical-align: 4px; }
.n8n-link { display: none !important; }
/* below desktop width the logo sits above the card instead of beside it */
@media (max-width: 1240px) {
  body::after { position: absolute; top: 18px; right: auto; left: 50%; transform: translateX(-50%); width: 200px; height: 22px; background-position: center; }
  .container { padding-top: 64px; }
}
@media (max-width: 640px) {
  .card { padding: 24px 18px; border-radius: 14px; }
  .form-header h1, .header h1, h1 { font-size: 26px !important; }
  .multiselect { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
""".replace("__LOGO__", _LOGO)


def apply_round23(nodes, conns):
    N = {n["name"]: n for n in nodes}

    def R(name, old, new):
        p = N[name]["parameters"]
        assert old in p["jsCode"], "round23 %s: anchor not found: %s" % (name, old[:70])
        p["jsCode"] = p["jsCode"].replace(old, new, 1)

    for name in ["On form submission", "Check Search", "Search Started"]:
        N[name]["parameters"]["options"]["customCss"] = BRAND_CSS

    page2 = N["Check Search"]["parameters"]["formFields"]["values"]
    page2[:] = [f for f in page2 if f.get("fieldName") != "gender"]
    for f in page2:
        if f.get("fieldName") == "employer":
            f["fieldLabel"] = "Target companies (optional – up to 3, comma-separated)"
            f["placeholder"] = "e.g. people now at Acme Payments, Globex Labs"
        if f.get("fieldName") == "exclude":
            f["fieldLabel"] = "Exclude words or companies"
            f["placeholder"] = "e.g. intern, trainee, a client you can't hire from"
    R("Search Criteria", "val('employer', 'Current employer')", "val('employer', 'Target companies (optional – up to 3, comma-separated)')")
    R("Search Criteria", "val('exclude', 'Exclude words')", "val('exclude', 'Exclude words or companies')")
    R("Search Criteria", "'Gender Preference': val('gender', 'Gender preference') || 'Any',", "'Gender Preference': 'Any',")

    # several target companies: OR-ed in the Google query, first one for Apollo
    R("Build Search Query", "const employerOpts = employer ? [q(employer), ''] : [''];",
      "const employerList = employer.split(',').map(function(s){ return s.trim(); }).filter(Boolean).slice(0, 3).map(q);\n"
      "const employerOpts = employerList.length ? shrink(employerList).concat(['']) : [''];")
    R("Build Apollo Query", "body.organization_names = [employer];",
      "body.organization_names = employer.split(',').map(function(s){ return s.trim(); }).filter(Boolean).slice(0, 3);")

    # the AI never weighs gender or other protected attributes
    R("Format Search Results", "Gender Preference: ${ S('Search Criteria')['Gender Preference'] }\n", "")
    R("Format Search Results",
      "Also factor in the Gender Preference: if a specific gender is requested (Female or Male), prioritize candidates matching it; if 'Any' is selected, ignore gender entirely and rank purely on merit.",
      "Never consider gender, age, religion, caste, ethnicity or any other protected attribute; rank purely on evidence of skills and experience.")
    return nodes, conns
