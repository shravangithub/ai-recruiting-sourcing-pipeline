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
             "Hugging Face", "Dev.to", "Reddit", "X (Twitter)", "Dribbble", "Behance",
             "Devfolio", "Unstop", "Devpost", "HackerEarth", "LeetCode", "CodeChef", "Codeforces",
             "Topmate", "ADPList", "Medium"]
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
.multiselect-checkbox:checked { background-color: var(--red) !important; border-color: var(--red) !important; }
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


# ======================================================================
# Round 24: outreach sequences.
# The first email goes out when "Send Email?" is ticked (unchanged). Each
# person then gets up to 4 follow-ups, sent as replies in the same Gmail
# thread, 2, 4, 5 and 7 days after the first email. A sequence stops the
# moment the person replies (checked in the Gmail thread itself before every
# send, plus the existing reply watcher) or an email bounces.
# Follow-ups go out on weekdays, 9:30-18:30 IST, at most 50 a day.
# State lives in an n8n Data Table ("outreach_sequence"); wording lives in a
# second table ("outreach_templates") that the recruiter can edit in n8n.
# The Google Sheet's Outreach Status shows where each person is.
# Fixed templates: no AI calls, so no token cost.
# ======================================================================
SEQ_TABLE = "outreach_sequence"
TPL_TABLE = "outreach_templates"
SEQ_COLUMNS = [("contact_key", "string"), ("email", "string"), ("first_name", "string"), ("role", "string"),
               ("location", "string"), ("company", "string"), ("message_id", "string"), ("thread_id", "string"),
               ("step", "number"), ("status", "string"), ("status_text", "string"), ("started_at", "date"),
               ("last_sent_at", "date"), ("next_due_at", "date"), ("sent_count", "number"), ("last_error", "string")]
TPL_COLUMNS = [("step", "number"), ("day", "number"), ("body", "string")]

SEQ_SETTINGS = r"""
// ---- sequence settings (edit here) ----
const OFFSETS = [2, 4, 5, 7];        // follow-ups: days after the first email
const DAILY_CAP = 50;                // most follow-ups sent in one day
const START = '09:30', END = '18:30';// sending window, Mon-Fri, India time
const ZONE = 'Asia/Kolkata';
const SIGNATURE = String.raw`Best regards,<br/>TalentRubric Growth Advisors</p>`;
// ----------------------------------------
"""

START_SEQUENCE = SEQ_SETTINGS + r"""
// one sequence row per first email that went out
const sent = $('Send Outreach Email').all();
const rows = $('Filter Outreach Queue').all();
const nice = function(d){ return d.setZone(ZONE).toFormat('d LLL'); };
const out = [];
sent.forEach(function(s, i){
  const r = (rows[i] || {}).json || {};
  const m = s.json || {};
  if (!m.id || m.error || !r['Dedupe Key']) return;
  const now = DateTime.now();
  const name = String(r.Name || '');
  const due = now.plus({ days: OFFSETS[0] });
  out.push({ json: {
    contact_key: String(r['Dedupe Key']),
    email: String(r.Email || '').toLowerCase().trim(),
    first_name: name && !/candidate from/i.test(name) ? name.split(' ')[0] : 'there',
    role: String(r['Search Role'] || ''),
    location: String(r['Search Location'] || ''),
    company: String(r['Current Company'] || ''),
    message_id: String(m.id),
    thread_id: String(m.threadId || ''),
    step: 1,
    status: 'active',
    status_text: 'Contacted · follow-up 1 of ' + OFFSETS.length + ' on ' + nice(due),
    started_at: now.toISO(),
    last_sent_at: now.toISO(),
    next_due_at: due.toISO(),
    sent_count: 1,
    last_error: ''
  }});
});
return out;
"""

DEFAULT_TEMPLATES = r"""
// the wording of each follow-up. Placeholders: {first_name} {role} {location} {company}
// After the first run these live in the "outreach_templates" Data Table - edit them there.
const DEFAULTS = [
  { step: 2, day: 2, body: '<p>Hi {first_name},</p><p>Just bumping this up in case it got buried. Would a quick 15-minute chat about the {role} role{in_location} work for you this week?</p>' },
  { step: 3, day: 4, body: '<p>Hi {first_name},</p><p>A little more context: from your work{at_company}, I think the {role} role could be a strong fit. Happy to send the full job description - just reply and I will share it.</p>' },
  { step: 4, day: 5, body: '<p>Hi {first_name},</p><p>I know inboxes get busy. If it is easier, reply with a time that suits you - or just "maybe later" and I will check back in a few months.</p>' },
  { step: 5, day: 7, body: '<p>Hi {first_name},</p><p>This will be my last note about the {role} role. If the timing is not right, no problem at all - I would still be glad to stay in touch for future roles.</p>' }
];
"""

SEED_TEMPLATES = DEFAULT_TEMPLATES + r"""
// first run only: fill the templates table so the recruiter can edit it
const have = $('Load Templates').all().filter(function(i){ return i.json && i.json.body; });
if (have.length) return [];
return DEFAULTS.map(function(t){ return { json: t }; });
"""

DUE_FOLLOWUPS = SEQ_SETTINGS + DEFAULT_TEMPLATES + r"""
// pick the follow-ups that are due now, inside the sending window and the daily cap
const now = DateTime.now().setZone(ZONE);
const hm = now.toFormat('HH:mm');
if (now.weekday > 5 || hm < START || hm >= END) return [];
const all = $('Load Sequences').all().map(function(i){ return i.json; }).filter(function(r){ return r && r.contact_key; });
const today = now.toFormat('yyyy-LL-dd');
const day = function(v){ if (!v) return ''; const d = DateTime.fromISO(String(v)); return d.isValid ? d.setZone(ZONE).toFormat('yyyy-LL-dd') : String(v).slice(0, 10); };
const sentToday = all.filter(function(r){ return Number(r.step) > 1 && day(r.last_sent_at) === today; }).length;
let room = Math.max(0, DAILY_CAP - sentToday);
const tpl = {};
DEFAULTS.forEach(function(t){ tpl[t.step] = t.body; });
$('Load Templates').all().forEach(function(i){ const t = i.json || {}; if (t.step && t.body) tpl[Number(t.step)] = String(t.body); });
const fill = function(s, r){
  return String(s)
    .replace(/\{first_name\}/g, r.first_name || 'there')
    .replace(/\{role\}/g, r.role || 'open')
    .replace(/\{location\}/g, r.location || '')
    .replace(/\{in_location\}/g, r.location ? ' in ' + r.location : '')
    .replace(/\{company\}/g, r.company || '')
    .replace(/\{at_company\}/g, r.company ? ' at ' + r.company : '');
};
const optOut = '<p style="color:#777;font-size:12px">Not interested? Just reply "no thanks" and I won\'t follow up again.</p>';
const due = all.filter(function(r){
  if (r.status !== 'active' || !r.message_id || !r.next_due_at) return false;
  if (Number(r.step) > OFFSETS.length) return false;
  const d = DateTime.fromISO(String(r.next_due_at));
  return d.isValid && d <= now;
}).sort(function(a, b){ return String(a.next_due_at).localeCompare(String(b.next_due_at)); });
return due.slice(0, room).map(function(r){
  const next = Number(r.step) + 1;
  return { json: Object.assign({}, r, {
    next_step: next,
    html: fill(tpl[next] || DEFAULTS[next - 2].body, r) + optOut + '<p>' + SIGNATURE
  })};
});
"""

THREAD_URL = ("={{ 'https://gmail.googleapis.com/gmail/v1/users/me/threads/' + $json.thread_id + "
              "'?format=metadata&metadataHeaders=From' }}")

DECIDE_FOLLOWUP = r"""
// before each follow-up: did the person reply, or did the first email bounce?
const due = $('Due Follow-ups').all();
return $input.all().map(function(t, i){
  const r = Object.assign({}, (due[i] || {}).json || {});
  const msgs = (t.json && t.json.messages) || [];
  const from = function(m){ const h = ((m.payload || {}).headers || []).find(function(x){ return String(x.name).toLowerCase() === 'from'; }); return h ? String(h.value).toLowerCase() : ''; };
  const bounced = msgs.some(function(m){ return /mailer-daemon|postmaster/.test(from(m)); });
  const replied = msgs.some(function(m){ return (m.labelIds || []).indexOf('SENT') === -1 && !/mailer-daemon|postmaster/.test(from(m)); });
  r.action = bounced ? 'bounced' : (replied ? 'replied' : 'send');
  return { json: r };
});
"""

ADVANCE_SEQUENCE = SEQ_SETTINGS + r"""
// after a follow-up (or a stop): the row's new state, for the table and the sheet
const nice = function(d){ return d.setZone(ZONE).toFormat('d LLL'); };
const r = $('Decide Follow-up').item.json;
const res = $json || {};
const base = { contact_key: r.contact_key };
if (r.action === 'replied') return { json: Object.assign(base, { status: 'replied', status_text: 'Replied' }) };
if (r.action === 'bounced') return { json: Object.assign(base, { status: 'stopped', status_text: 'Bounced · sequence stopped' }) };
if (res.error || !res.id) {
  return { json: Object.assign(base, { last_error: String((res.error && (res.error.message || res.error)) || 'send failed').slice(0, 300),
                                       status_text: 'Follow-up ' + (r.next_step - 1) + ' failed · will retry' }) };
}
const step = Number(r.next_step);
const n = step - 1, total = OFFSETS.length;
const now = DateTime.now();
const started = DateTime.fromISO(String(r.started_at));
const out = Object.assign(base, { step: step, last_sent_at: now.toISO(), sent_count: Number(r.sent_count || 1) + 1, last_error: '' });
if (n >= total) {
  out.status = 'completed';
  out.status_text = 'Sequence complete · ' + total + ' follow-ups, no reply yet';
} else {
  let due = (started.isValid ? started : now).plus({ days: OFFSETS[n] });
  if (due < now) due = now.plus({ hours: 1 });
  out.next_due_at = due.toISO();
  out.status_text = 'Follow-up ' + n + ' of ' + total + ' sent · next ' + nice(due);
}
return { json: out };
"""

STOP_FIELDS = r"""
// a reply came in: stop that person's sequence
const e = String($json.sender_email || '').toLowerCase();
if (!e) return [];
return { json: { email: e, status: 'replied', status_text: 'Replied' } };
"""


def _dt_ref(name):
    return {"__rl": True, "value": name, "mode": "name"}


def _dt_ensure(name, table, cols, pos):
    n = _node(name, "n8n-nodes-base.dataTable", 1.1, pos, {
        "resource": "table", "operation": "create", "tableName": table,
        "columns": {"column": [{"name": c, "type": t} for c, t in cols]},
        "options": {"createIfNotExists": True}})
    n["executeOnce"] = True
    return n


def _dt_cond(key, value):
    return {"conditions": [{"keyName": key, "condition": "eq", "keyValue": value}]}


def _automap():
    return {"mappingMode": "autoMapInputData", "value": {}, "matchingColumns": [], "schema": [],
            "attemptToConvertTypes": False, "convertFieldsToString": False}


def apply_round24(nodes, conns):
    N = {n["name"]: n for n in nodes}
    gm, mc = N["Send Outreach Email"], N["Mark Contacted"]
    gcred = json.loads(json.dumps(gm["credentials"]))
    x0, y0 = mc["position"]

    def link(a, b):
        conns.setdefault(a, {"main": [[]]})["main"][0].append({"node": b, "type": "main", "index": 0})

    def status_node(name, pos):
        n = json.loads(json.dumps(mc))
        n["name"], n["id"], n["position"] = name, "fix-" + name.lower().replace(" ", "-"), pos
        n["parameters"]["columns"]["value"] = {"Dedupe Key": "={{ $json.contact_key }}",
                                               "Outreach Status": "={{ $json.status_text }}"}
        n["onError"] = "continueRegularOutput"
        n.pop("webhookId", None)
        return n

    # --- 1. first email sent -> open a sequence ---
    new = [
        _dt_ensure("Ensure Sequence Table", SEQ_TABLE, SEQ_COLUMNS, [x0 + 220, y0]),
        _node("Start Sequence", "n8n-nodes-base.code", 2, [x0 + 440, y0], {"jsCode": START_SEQUENCE}),
        _node("Save New Sequence", "n8n-nodes-base.dataTable", 1.1, [x0 + 660, y0], {
            "resource": "row", "operation": "upsert", "dataTableId": _dt_ref(SEQ_TABLE),
            "matchType": "allConditions", "filters": _dt_cond("contact_key", "={{ $json.contact_key }}"),
            "columns": _automap(), "options": {}}, onError="continueRegularOutput"),
        status_node("Show Sequence Status", [x0 + 880, y0]),
    ]
    # start from the sent email itself: the sheet update returns nothing when it can't match a row
    link("Send Outreach Email", "Ensure Sequence Table")
    link("Ensure Sequence Table", "Start Sequence")
    link("Start Sequence", "Save New Sequence")
    link("Start Sequence", "Show Sequence Status")

    # --- 2. the follow-up runner ---
    y1 = y0 + 260
    sched = _node("Follow-up Schedule", "n8n-nodes-base.scheduleTrigger", 1.3, [0, y1],
                  {"rule": {"interval": [{"field": "minutes", "minutesInterval": 15}]}})
    ens1 = _dt_ensure("Ensure Sequences", SEQ_TABLE, SEQ_COLUMNS, [220, y1])
    ens2 = _dt_ensure("Ensure Templates", TPL_TABLE, TPL_COLUMNS, [440, y1])
    loadt = _node("Load Templates", "n8n-nodes-base.dataTable", 1.1, [660, y1], {
        "resource": "row", "operation": "get", "dataTableId": _dt_ref(TPL_TABLE), "returnAll": True},
        alwaysOutputData=True, onError="continueRegularOutput")
    seed = _node("Seed Templates", "n8n-nodes-base.code", 2, [880, y1 - 160], {"jsCode": SEED_TEMPLATES})
    seed["executeOnce"] = True
    ins = _node("Save Templates", "n8n-nodes-base.dataTable", 1.1, [1100, y1 - 160], {
        "resource": "row", "operation": "insert", "dataTableId": _dt_ref(TPL_TABLE),
        "columns": _automap(), "options": {}}, onError="continueRegularOutput")
    loads = _node("Load Sequences", "n8n-nodes-base.dataTable", 1.1, [880, y1], {
        "resource": "row", "operation": "get", "dataTableId": _dt_ref(SEQ_TABLE),
        "matchType": "allConditions", "filters": _dt_cond("status", "active"), "returnAll": True},
        alwaysOutputData=True)
    loads["executeOnce"] = True
    due = _node("Due Follow-ups", "n8n-nodes-base.code", 2, [1100, y1], {"jsCode": DUE_FOLLOWUPS})
    thread = _node("Check Thread", "n8n-nodes-base.httpRequest", 4.2, [1320, y1], {
        "url": THREAD_URL, "authentication": "predefinedCredentialType", "nodeCredentialType": "gmailOAuth2",
        "options": {}}, credentials=json.loads(json.dumps(gcred)), onError="continueRegularOutput")
    decide = _node("Decide Follow-up", "n8n-nodes-base.code", 2, [1540, y1], {"jsCode": DECIDE_FOLLOWUP})
    gate = _node("Still No Reply?", "n8n-nodes-base.if", 2.3, [1760, y1], {
        "conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 3},
                       "conditions": [{"id": "seq-send", "leftValue": "={{ $json.action }}", "rightValue": "send",
                                       "operator": {"type": "string", "operation": "equals"}}],
                       "combinator": "and"}, "options": {}})
    send = _node("Send Follow-up", "n8n-nodes-base.gmail", gm["typeVersion"], [1980, y1 - 100], {
        "resource": "message", "operation": "reply", "messageId": "={{ $json.message_id }}",
        "emailType": "html", "message": "={{ $json.html }}",
        "options": {"appendAttribution": False, "replyToSenderOnly": False}},
        credentials=json.loads(json.dumps(gcred)), onError="continueRegularOutput")
    adv = _node("Advance Sequence", "n8n-nodes-base.code", 2, [2200, y1], {"jsCode": ADVANCE_SEQUENCE, "mode": "runOnceForEachItem"})
    save = _node("Save Progress", "n8n-nodes-base.dataTable", 1.1, [2420, y1 - 100], {
        "resource": "row", "operation": "update", "dataTableId": _dt_ref(SEQ_TABLE),
        "matchType": "allConditions", "filters": _dt_cond("contact_key", "={{ $json.contact_key }}"),
        "columns": _automap(), "options": {}}, onError="continueRegularOutput")
    show = status_node("Show Follow-up Status", [2420, y1 + 100])
    new += [sched, ens1, ens2, loadt, seed, ins, loads, due, thread, decide, gate, send, adv, save, show]
    link("Follow-up Schedule", "Ensure Sequences")
    link("Ensure Sequences", "Ensure Templates")
    link("Ensure Templates", "Load Templates")
    link("Load Templates", "Seed Templates")
    link("Seed Templates", "Save Templates")
    link("Load Templates", "Load Sequences")
    link("Load Sequences", "Due Follow-ups")
    link("Due Follow-ups", "Check Thread")
    link("Check Thread", "Decide Follow-up")
    link("Decide Follow-up", "Still No Reply?")
    conns["Still No Reply?"] = {"main": [[{"node": "Send Follow-up", "type": "main", "index": 0}],
                                         [{"node": "Advance Sequence", "type": "main", "index": 0}]]}
    link("Send Follow-up", "Advance Sequence")
    link("Advance Sequence", "Save Progress")
    link("Advance Sequence", "Show Follow-up Status")

    # --- 3. reply watcher also stops the sequence ---
    ex = N["Extract Sender"]
    stop = _node("Stop Fields", "n8n-nodes-base.code", 2, [ex["position"][0] + 220, ex["position"][1] - 160],
                 {"jsCode": STOP_FIELDS, "mode": "runOnceForEachItem"})
    stopu = _node("Stop Sequence", "n8n-nodes-base.dataTable", 1.1, [ex["position"][0] + 440, ex["position"][1] - 160], {
        "resource": "row", "operation": "update", "dataTableId": _dt_ref(SEQ_TABLE),
        "matchType": "allConditions", "filters": _dt_cond("email", "={{ $json.email }}"),
        "columns": _automap(), "options": {}}, onError="continueRegularOutput")
    new += [stop, stopu]
    link("Extract Sender", "Stop Fields")
    link("Stop Fields", "Stop Sequence")

    nodes.extend(new)
    return nodes, conns


# ======================================================================
# Round 25: the sourcing app.
# A web app served by the recruiter's own n8n (GET /webhook/sourcing-app):
#   - one search screen with an AI intake agent: reads the brief or JD as
#     you type, fills the criteria, asks up to 2 clarifying questions and
#     writes a scoring rubric (which the scoring AI then uses);
#   - a live run screen (progress from the pipeline itself);
#   - a shortlist with match %, per-criterion checks, feedback votes and
#     "Approve for outreach" (ticks Send Email? in the sheet, so the
#     existing first email + day 2/4/5/7 follow-ups take over);
#   - an outreach view of every sequence.
# A team access code (chosen on first visit) protects the API.
# State: n8n Data Tables app_settings, app_runs, app_candidates,
# app_feedback. The Google Sheet stays the system of record.
# ======================================================================
import uuid as _uuid

APP_PLATFORMS = list(PLATFORMS)
APP_REGIONS = ["Global (all regions)", "India", "United States", "United Kingdom", "Canada", "Australia", "Germany",
               "France", "Netherlands", "Singapore", "United Arab Emirates", "Ireland"]

RUN_COLUMNS = [("run_id", "string"), ("role", "string"), ("location", "string"), ("must", "string"), ("sources", "string"),
               ("status", "string"), ("stage", "number"), ("count", "number"), ("by_source", "string"), ("note", "string"),
               ("started_at", "date"), ("finished_at", "date")]
CAND_COLUMNS = [("run_id", "string"), ("dedupe_key", "string"), ("name", "string"), ("title", "string"), ("company", "string"),
                ("location", "string"), ("location_review", "string"), ("fit_score", "number"), ("confidence", "string"),
                ("experience_check", "string"), ("summary", "string"), ("key_skills", "string"), ("email", "string"),
                ("contact_source", "string"), ("source", "string"), ("profile_url", "string"), ("linkedin", "string"),
                ("github", "string"), ("resume_link", "string"), ("outreach_status", "string"), ("approved", "boolean"),
                ("feedback", "string")]
SETTINGS_COLUMNS = [("key", "string"), ("value", "string")]
FEEDBACK_COLUMNS = [("run_id", "string"), ("dedupe_key", "string"), ("feedback", "string")]
APP_ROUTES = ["reply", "claim", "intake", "search", "status", "results", "runs", "approve", "feedback", "outreach"]

APP_GATE = r"""
// Every app request: check the team access code, then pick a route.
let body = $('App API').first().json.body;
if (typeof body === 'string') { try { body = JSON.parse(body); } catch (e) { body = {}; } }
body = body || {};
const rows = $('Load App Settings').all().map(function(i){ return i.json || {}; });
const saved = String((rows.find(function(r){ return r.key === 'access_code'; }) || {}).value || '');
const action = String(body.action || '');
const code = String(body.code || '');
const reply = function(r){ return [{ json: { route: 'reply', response: r } }]; };
if (!saved) {
  if (action === 'claim' && code.length >= 6) return [{ json: { route: 'claim', code: code } }];
  return reply({ ok: false, auth: 'need_code', claim: true });
}
if (code !== saved) return reply({ ok: false, auth: 'need_code', claim: false });
if (action === 'ping' || action === 'claim') return reply({ ok: true });
const ROUTES = ['intake', 'search', 'status', 'results', 'runs', 'approve', 'feedback', 'outreach'];
if (ROUTES.indexOf(action) === -1) return reply({ ok: false, error: 'Unknown action.' });
return [{ json: { route: action, body: body } }];
"""

INTAKE_PROMPT = r"""
// The intake agent: what a senior sourcer would take from the brief.
const b = $json.body || {};
const brief = String(b.brief || '').slice(0, 12000);
const answers = b.answers && typeof b.answers === 'object' ? b.answers : {};
const qa = Object.keys(answers).map(function(q){ return 'Q: ' + q + '\nA: ' + answers[q]; }).join('\n');
const PLATFORMS = %s;
const REGIONS = %s;
const system = [
  'You are a senior sourcer at a recruiting firm. A recruiter gives you a hiring brief or a job description.',
  'Return ONLY a JSON object with these keys:',
  '"criteria": { "role": short job title, "similar_titles": up to 3 alternative titles people use for this job,',
  '  "must": 2-5 hard skills or tools that MUST show up in a public profile (never soft skills, never years),',
  '  "nice": up to 4 nice-to-have skills, "experience": exactly one of "0-2 years","3-5 years","5-8 years","8+ years","Any"',
  '  (if years are stated pick the band containing the lower number; junior/graduate => "0-2 years", senior => "5-8 years", lead/principal/head => "8+ years"),',
  '  "city": city or "", "region": exactly one of ' + JSON.stringify(REGIONS) + ',',
  '  "companies": target companies or industry as a short comma list or "", "exclude": words or companies to exclude (array) },',
  '"sources": the best 3-6 of ' + JSON.stringify(PLATFORMS) + ' for this role (engineering: LinkedIn, GitHub, Stack Overflow; data/ML: add Kaggle, Hugging Face; design: Dribbble, Behance; students, graduates and early-career engineers: add Devfolio, Unstop, Devpost or HackerEarth (hackathons) and LeetCode, CodeChef or Codeforces (competitive programming) when problem-solving or building matters; product managers: LinkedIn, Topmate, ADPList; other non-technical roles (sales, marketing, HR, finance, operations): LinkedIn, Topmate, Google / Open Web, plus Medium for people who write),',
  '"questions": 0 to 2 questions, ONLY where the answer would clearly change who we search for (e.g. must-have vs nice-to-have, remote vs on-site, industry). Each {"text": short question, "options": 2-4 short answers}. Never ask about anything already stated or answered. Never ask about gender, age, religion, caste, ethnicity, marital status or any other protected attribute.',
  '"rubric": 3-6 items {"criterion": short name, "weight": 1-3 (3 = deal-breaker), "how": one sentence on what evidence in a public profile shows it}.',
  'RULES: Anything described as "a plus", "nice to have", "preferred", "bonus" or "ideally" goes in "nice", never in "must".',
  '"must" and "nice" contain ONLY skills the brief actually names. Never add generic skills it does not mention (no "Microservices", "REST APIs", "Cloud" unless written).',
  'Anything the brief says to avoid goes in "exclude" as a short word: "no interns" => "intern", "no agencies" => "agency", "not from X" => "X".',
  'Use only what the brief states or clearly implies. Do not invent requirements.'
].join('\n');
const prompt = 'BRIEF OR JOB DESCRIPTION (between the lines):\n-----\n' + brief + '\n-----\n' + (qa ? '\nANSWERS FROM THE RECRUITER:\n' + qa + '\n' : '');
return [{ json: { system: system, prompt: prompt } }];
""" % (json.dumps(APP_PLATFORMS), json.dumps(APP_REGIONS))

INTAKE_PARSE = r"""
// Clean up the intake agent's answer for the app.
const PLATFORMS = %s;
const REGIONS = %s;
const BANDS = ['0-2 years', '3-5 years', '5-8 years', '8+ years', 'Any'];
const j = $json || {};
let raw = '';
try { raw = j.output[0].content[0].text; } catch (e) { raw = ''; }
let p = null;
try { p = JSON.parse(String(raw).replace(/^```(json)?|```$/g, '').trim()); } catch (e) { p = null; }
if (!p || typeof p !== 'object') return [{ json: { response: { ok: false, error: j.error ? 'The AI could not be reached: ' + (j.error.message || j.error) : 'Could not read that brief. Try adding the role and city.' } } }];
const arr = function(v, n){ return (Array.isArray(v) ? v : String(v || '').split(',')).map(function(s){ return String(s).trim(); }).filter(Boolean).slice(0, n); };
const c = p.criteria || {};
const criteria = {
  role: String(c.role || '').slice(0, 80),
  similar_titles: arr(c.similar_titles, 3),
  must: arr(c.must, 5),
  nice: arr(c.nice, 4),
  experience: BANDS.indexOf(c.experience) !== -1 ? c.experience : 'Any',
  city: String(c.city || '').slice(0, 60),
  region: REGIONS.indexOf(c.region) !== -1 ? c.region : '',
  companies: Array.isArray(c.companies) ? c.companies.join(', ') : String(c.companies || '').slice(0, 120),
  exclude: arr(c.exclude, 6)
};
// Hard rule on top of the AI: what the brief calls "a plus" is never a must-have.
let brief = '';
try { brief = String(($('App Gate').first().json.body || {}).brief || ''); } catch (e) {}
const soft = [];
brief.split(/[,.;\n]|\band\b/i).forEach(function(part){
  if (/\b(a plus|a bonus|nice[- ]to[- ]have|preferred|an advantage|good to have)\b/i.test(part)) soft.push(part.toLowerCase());
});
const isSoft = function(skill){ const k = String(skill).toLowerCase(); return soft.some(function(p){ return p.indexOf(k) !== -1; }); };
const moved = criteria.must.filter(isSoft);
criteria.must = criteria.must.filter(function(s){ return !isSoft(s); });
moved.forEach(function(s){ if (criteria.nice.indexOf(s) === -1) criteria.nice.unshift(s); });
const PROTECTED = /gender|female|male|\bage\b|religio|caste|ethnic|marital|married|pregnan|disab/i;
const questions = (Array.isArray(p.questions) ? p.questions : []).filter(function(q){ return q && q.text && !PROTECTED.test(q.text); })
  .slice(0, 2).map(function(q){ return { text: String(q.text).slice(0, 160), options: arr(q.options, 4) }; }).filter(function(q){ return q.options.length >= 2; });
const rubric = (Array.isArray(p.rubric) ? p.rubric : []).slice(0, 6).filter(function(r){ return r && r.criterion && !PROTECTED.test(r.criterion + ' ' + (r.how || '')); })
  .map(function(r){ return { criterion: String(r.criterion).slice(0, 60), weight: Math.max(1, Math.min(3, parseInt(r.weight, 10) || 1)), how: String(r.how || '').slice(0, 200) }; });
const sources = arr(p.sources, 8).map(function(s){ return /github/i.test(s) ? 'GitHub' : s; }).filter(function(s){ return PLATFORMS.indexOf(s) !== -1; });
return [{ json: { response: { ok: true, criteria: criteria, sources: sources.length ? sources : ['LinkedIn', 'GitHub'], questions: questions, rubric: rubric } } }];
""" % (json.dumps(APP_PLATFORMS), json.dumps(APP_REGIONS))

APP_CRITERIA = r"""
// "Find candidates" from the app: the same fields page 2 of the form sends,
// so Search Criteria and everything after it run unchanged.
const PLATFORMS = %s;
const c = ($json.body || {}).criteria || {};
const list = function(v){ return (Array.isArray(v) ? v : String(v || '').split(',')).map(function(s){ return String(s).trim(); }).filter(Boolean); };
const role = String(c.role || '').trim();
const sources = list(c.sources).filter(function(s){ return PLATFORMS.indexOf(s) !== -1; });
if (!role) return [{ json: { bad: true, response: { ok: false, error: 'Add a job title first.' } } }];
if (!sources.length) return [{ json: { bad: true, response: { ok: false, error: 'Pick at least one source.' } } }];
return [{ json: {
  __app: true,
  role: role.slice(0, 120),
  similar: c.similar === 'No' ? 'No' : 'Yes',
  experience: String(c.experience || 'Any'),
  must: list(c.must).join(', '),
  nice: list(c.nice).join(', '),
  city: String(c.city || '').trim(),
  region: String(c.region || 'Global (all regions)'),
  industry: String(c.industry || ''),
  employer: String(c.employer || ''),
  exclude: list(c.exclude).join(', '),
  education: String(c.education || 'Any'),
  sources: sources,
  notify: '',
  rubric: String(c.rubric || '').slice(0, 2000),
  jd_text: String(c.jd_text || '').slice(0, 12000),
  response: { ok: true, run_id: String($execution.id) }
} }];
""" % json.dumps(APP_PLATFORMS)

STATUS_REPLY = r"""
const r = $input.all().map(function(i){ return i.json || {}; }).find(function(x){ return x.run_id; }) || {};
return [{ json: { response: { ok: true, run: r.run_id ? r : { status: 'starting', stage: 0 } } } }];
"""

RUNS_REPLY = r"""
const rows = $input.all().map(function(i){ return i.json || {}; }).filter(function(r){ return r.run_id; });
rows.sort(function(a, b){ return String(b.started_at || '').localeCompare(String(a.started_at || '')); });
return [{ json: { response: { ok: true, runs: rows.slice(0, 40).map(function(r){ return { run_id: r.run_id, role: r.role, location: r.location, status: r.status, count: r.count, started_at: r.started_at }; }) } } }];
"""

RESULTS_REPLY = r"""
// A shortlist: the run, its people, and live outreach status per person.
const run = $('Load Run Info').all().map(function(i){ return i.json || {}; }).find(function(x){ return x.run_id; }) || {};
const seq = {};
try { $('Load Sequence Status').all().forEach(function(i){ const s = i.json || {}; if (s.contact_key) seq[s.contact_key] = s.status_text || s.status; }); } catch (e) {}
const seen = {};
const people = $('Load Run People').all().map(function(i){ return i.json || {}; }).filter(function(c){
  if (!c.dedupe_key || seen[c.dedupe_key]) return false; seen[c.dedupe_key] = 1; return true; })
  .map(function(c){ const o = Object.assign({}, c); if (seq[c.dedupe_key]) o.outreach_status = seq[c.dedupe_key]; delete o.id; delete o.createdAt; delete o.updatedAt; return o; });
return [{ json: { response: { ok: true, run: run, candidates: people } } }];
"""

APPROVE_ROWS = r"""
const b = $json.body || {};
const keys = (Array.isArray(b.keys) ? b.keys : []).map(String).filter(Boolean).slice(0, 50);
return keys.map(function(k){ return { json: { run_id: String(b.run_id || ''), dedupe_key: k, approved: true } }; });
"""

APPROVE_REPLY = r"""
const n = $input.all().filter(function(i){ return i.json && !i.json.error; }).length;
return [{ json: { response: { ok: true, approved: n } } }];
"""

FEEDBACK_ROW = r"""
const b = $json.body || {};
const v = ['up', 'down'].indexOf(b.vote) !== -1 ? b.vote : '';
return [{ json: { run_id: String(b.run_id || ''), dedupe_key: String(b.dedupe_key || ''), feedback: v } }];
"""

OUTREACH_REPLY = r"""
const rows = $input.all().map(function(i){ return i.json || {}; }).filter(function(r){ return r.contact_key; })
  .map(function(r){ return { first_name: r.first_name, email: r.email, role: r.role, location: r.location, step: r.step, status: r.status, status_text: r.status_text, next_due_at: r.next_due_at }; });
return [{ json: { response: { ok: true, rows: rows } } }];
"""

RUN_STARTED = r"""
// The app's live view: this search has started.
const c = $('Search Criteria').first().json || {};
const p = Array.isArray(c['Target Platforms']) ? c['Target Platforms'].join(', ') : String(c['Target Platforms'] || '');
return [{ json: { run_id: String($execution.id), role: c['Target Role'] || '', location: c['Location'] || c['Search Region'] || '',
  must: c['Primary Skills'] || '', sources: p, status: 'running', stage: 1, count: 0, by_source: '', note: '',
  started_at: new Date().toISOString() } }];
"""

RUN_STAGE = r"""
return [{ json: { run_id: String($execution.id), stage: %d%s } }];
"""

RUN_CANDIDATES = r"""
// Every person written to the sheet in this run, for the app's shortlist.
const out = [];
$input.all().forEach(function(i){
  const r = i.json || {};
  const score = parseFloat(r['Fit Score']);
  if (!r['Dedupe Key'] || !r['Name'] || !(score > 0)) return;
  out.push({ json: {
    run_id: String($execution.id), dedupe_key: String(r['Dedupe Key']), name: String(r['Name']),
    title: String(r['Role'] || r['Verified Title'] || ''), company: String(r['Current Company'] || ''),
    location: String(r['Location'] || ''), location_review: String(r['Location Review'] || ''), fit_score: score,
    confidence: String(r['Confidence'] || ''), experience_check: String(r['Experience Check'] || ''),
    summary: String(r['Summary'] || '').slice(0, 1500), key_skills: String(r['Key Skills'] || '').slice(0, 400),
    email: String(r['Email'] || ''), contact_source: String(r['Contact Source'] || ''), source: String(r['Source Platform'] || ''),
    profile_url: String(r['Profile URL'] || ''), linkedin: String(r['LinkedIn'] || ''), github: String(r['GitHub'] || ''),
    resume_link: String(r['Resume Link'] || ''), outreach_status: String(r['Outreach Status'] || ''), approved: false, feedback: ''
  } });
});
return out;
"""

RUN_DONE = r"""
const n = $json || {};
return [{ json: { run_id: String($execution.id), status: 'done', stage: 4, count: Number(n.count) || 0, by_source: String(n.by_source || ''),
  note: String(n.range_text || ''), finished_at: new Date().toISOString() } }];
"""

RUN_STOPPED = r"""
return [{ json: { run_id: String($execution.id), status: 'stopped', note: 'The monthly search budget is used up. It resets on the 1st.', finished_at: new Date().toISOString() } }];
"""

BUILD_APP_PAGE = r"""
// Serve the sourcing app. It calls its API next door (/webhook/sourcing-app-api).
return [{ json: { html: %s } }];
"""


def _app_html():
    p = _Path(__file__).parent / "src" / "app.html"
    html = p.read_text()
    logo = "data:image/png;base64," + _LOGO
    html = html.replace("__LOGO__", logo).replace("__API__", "sourcing-app-api")
    html = html.replace("__SHEET_URL__", "https://docs.google.com/spreadsheets/d/YOUR_GOOGLE_SHEET_ID/edit")
    return html


def apply_round25(nodes, conns):
    N = {n["name"]: n for n in nodes}
    oai = json.loads(json.dumps(N["Message a model"]["credentials"]))
    mc = N["Mark Contacted"]
    new = []

    def link(a, b, idx=0, first=False):
        conns.setdefault(a, {"main": [[]]})
        while len(conns[a]["main"]) <= idx:
            conns[a]["main"].append([])
        c = {"node": b, "type": "main", "index": 0}
        if first:
            conns[a]["main"][idx].insert(0, c)
        else:
            conns[a]["main"][idx].append(c)

    def code(name, js, pos, each=False, once=False, **kw):
        p = {"jsCode": js}
        if each:
            p["mode"] = "runOnceForEachItem"
        n = _node(name, "n8n-nodes-base.code", 2, pos, p, **kw)
        if once:
            n["executeOnce"] = True
        new.append(n)
        return n

    def dt(name, op, table, pos, extra=None, **kw):
        p = {"resource": "row", "operation": op, "dataTableId": _dt_ref(table)}
        p.update(extra or {})
        n = _node(name, "n8n-nodes-base.dataTable", 1.1, pos, p, **kw)
        new.append(n)
        return n

    def ensure(name, table, cols, pos):
        n = _dt_ensure(name, table, cols, pos)
        new.append(n)
        return n

    def cond(*pairs):
        return {"conditions": [{"keyName": k, "condition": "eq", "keyValue": v} for k, v in pairs]}

    # ---------------- the page ----------------
    X, Y = -144, 1400
    page = _node("App Page", "n8n-nodes-base.webhook", 2, [X, Y],
                 {"httpMethod": "GET", "path": "sourcing-app", "responseMode": "responseNode", "options": {}},
                 webhookId="7c1e2a40-8b3d-4f6e-9a12-5d0c3b7e9f01")
    new.append(page)
    code("Build App Page", BUILD_APP_PAGE % json.dumps(_app_html()), [X + 220, Y])
    send = _node("Send App Page", "n8n-nodes-base.respondToWebhook", 1.1, [X + 440, Y], {
        "respondWith": "text", "responseBody": "={{ $json.html }}",
        "options": {"responseHeaders": {"entries": [{"name": "Content-Type", "value": "text/html; charset=utf-8"},
                                                    {"name": "Cache-Control", "value": "no-store"}]}}})
    new.append(send)
    link("App Page", "Build App Page")
    link("Build App Page", "Send App Page")

    # ---------------- the API ----------------
    Y2 = Y + 240
    api = _node("App API", "n8n-nodes-base.webhook", 2, [X, Y2],
                {"httpMethod": "POST", "path": "sourcing-app-api", "responseMode": "responseNode", "options": {}},
                webhookId="7c1e2a40-8b3d-4f6e-9a12-5d0c3b7e9f02")
    new.append(api)
    ensure("Ensure App Settings", "app_settings", SETTINGS_COLUMNS, [X + 220, Y2])
    ensure("Ensure App Runs", "app_runs", RUN_COLUMNS, [X + 440, Y2])
    ensure("Ensure App Candidates", "app_candidates", CAND_COLUMNS, [X + 660, Y2])
    ensure("Ensure App Feedback", "app_feedback", FEEDBACK_COLUMNS, [X + 880, Y2])
    ls = dt("Load App Settings", "get", "app_settings", [X + 1100, Y2], {"returnAll": True}, alwaysOutputData=True)
    ls["executeOnce"] = True
    code("App Gate", APP_GATE, [X + 1320, Y2])
    sw = _node("App Route", "n8n-nodes-base.switch", 3.2, [X + 1540, Y2], {
        "mode": "expression", "numberOutputs": len(APP_ROUTES),
        "output": "={{ %s.indexOf($json.route) }}" % json.dumps(APP_ROUTES)})
    new.append(sw)
    reply = _node("App Reply", "n8n-nodes-base.respondToWebhook", 1.1, [X + 2640, Y2 - 200], {
        "respondWith": "json", "responseBody": "={{ JSON.stringify($json.response) }}", "options": {}})
    new.append(reply)
    for a, b in [("App API", "Ensure App Settings"), ("Ensure App Settings", "Ensure App Runs"), ("Ensure App Runs", "Ensure App Candidates"),
                 ("Ensure App Candidates", "Ensure App Feedback"), ("Ensure App Feedback", "Load App Settings"),
                 ("Load App Settings", "App Gate"), ("App Gate", "App Route")]:
        link(a, b)
    conns["App Route"] = {"main": [[] for _ in APP_ROUTES]}
    R = {r: i for i, r in enumerate(APP_ROUTES)}
    bx = X + 1760

    # reply (0)
    link("App Route", "App Reply", R["reply"])
    # claim (1)
    yy = Y2 - 120
    code("Claim Row", "return [{ json: { key: 'access_code', value: String($json.code) } }];", [bx, yy])
    dt("Save Access Code", "insert", "app_settings", [bx + 220, yy], {"columns": _automap(), "options": {}})
    code("Claim Reply", "return [{ json: { response: { ok: true } } }];", [bx + 440, yy], once=True)
    link("App Route", "Claim Row", R["claim"]); link("Claim Row", "Save Access Code"); link("Save Access Code", "Claim Reply"); link("Claim Reply", "App Reply")
    # intake (2)
    yy = Y2 + 40
    code("Intake Prompt", INTAKE_PROMPT, [bx, yy])
    mm = N["Message a model"]
    intake = _node("Intake Agent", "n8n-nodes-base.httpRequest", 4.2, [bx + 220, yy], {
        "method": "POST", "url": "https://api.openai.com/v1/responses", "authentication": "predefinedCredentialType",
        "nodeCredentialType": "openAiApi", "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ model: 'gpt-4o-mini', temperature: 0.2, text: { format: { type: 'json_object' } }, input: [{ role: 'system', content: $json.system }, { role: 'user', content: $json.prompt }] }) }}",
        "options": {"timeout": 60000}}, credentials=json.loads(json.dumps(oai)), onError="continueRegularOutput")
    new.append(intake)
    code("Intake Result", INTAKE_PARSE, [bx + 440, yy])
    link("App Route", "Intake Prompt", R["intake"]); link("Intake Prompt", "Intake Agent"); link("Intake Agent", "Intake Result"); link("Intake Result", "App Reply")
    # search (3)
    yy = Y2 + 200
    code("App Criteria", APP_CRITERIA, [bx, yy])
    rs = _node("Respond Search", "n8n-nodes-base.respondToWebhook", 1.1, [bx + 220, yy], {
        "respondWith": "json", "responseBody": "={{ JSON.stringify($json.response) }}", "options": {}})
    new.append(rs)
    ok = _node("Start Search?", "n8n-nodes-base.if", 2.3, [bx + 440, yy], {
        "conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 3},
                       "conditions": [{"id": "app-ok", "leftValue": "={{ $json.bad === true }}", "rightValue": "",
                                       "operator": {"type": "boolean", "operation": "false", "singleValue": True}}],
                       "combinator": "and"}, "looseTypeValidation": True, "options": {}})
    new.append(ok)
    link("App Route", "App Criteria", R["search"]); link("App Criteria", "Respond Search"); link("Respond Search", "Start Search?")
    link("Start Search?", "Search Criteria")
    # status (4)
    yy = Y2 + 360
    dt("Load Run Status", "get", "app_runs", [bx, yy], {"matchType": "allConditions", "filters": cond(("run_id", "={{ $json.body.run_id }}")), "returnAll": True}, alwaysOutputData=True)
    code("Status Reply", STATUS_REPLY, [bx + 220, yy])
    link("App Route", "Load Run Status", R["status"]); link("Load Run Status", "Status Reply"); link("Status Reply", "App Reply")
    # results (5)
    yy = Y2 + 520
    dt("Load Run Info", "get", "app_runs", [bx, yy], {"matchType": "allConditions", "filters": cond(("run_id", "={{ $json.body.run_id }}")), "returnAll": True}, alwaysOutputData=True)
    lp = dt("Load Run People", "get", "app_candidates", [bx + 220, yy], {"matchType": "allConditions", "filters": cond(("run_id", "={{ $('App Gate').first().json.body.run_id }}")), "returnAll": True}, alwaysOutputData=True)
    lp["executeOnce"] = True
    lq = dt("Load Sequence Status", "get", SEQ_TABLE, [bx + 440, yy], {"returnAll": True}, alwaysOutputData=True, onError="continueRegularOutput")
    lq["executeOnce"] = True
    code("Results Reply", RESULTS_REPLY, [bx + 660, yy], once=True)
    link("App Route", "Load Run Info", R["results"]); link("Load Run Info", "Load Run People"); link("Load Run People", "Load Sequence Status")
    link("Load Sequence Status", "Results Reply"); link("Results Reply", "App Reply")
    # runs (6)
    yy = Y2 + 680
    lr = dt("Load Runs", "get", "app_runs", [bx, yy], {"returnAll": True}, alwaysOutputData=True)
    code("Runs Reply", RUNS_REPLY, [bx + 220, yy])
    link("App Route", "Load Runs", R["runs"]); link("Load Runs", "Runs Reply"); link("Runs Reply", "App Reply")
    # approve (7)
    yy = Y2 + 840
    code("Approve Rows", APPROVE_ROWS, [bx, yy])
    ap = json.loads(json.dumps(mc))
    ap["name"], ap["id"], ap["position"] = "Approve In Sheet", "fix-approve-in-sheet", [bx + 220, yy]
    ap["parameters"]["columns"]["value"] = {"Dedupe Key": "={{ $json.dedupe_key }}", "Send Email?": "TRUE"}
    ap["parameters"]["columns"]["schema"] = [
        {"id": "Dedupe Key", "displayName": "Dedupe Key", "required": False, "defaultMatch": False, "display": True, "type": "string", "canBeUsedToMatch": True, "removed": False},
        {"id": "Send Email?", "displayName": "Send Email?", "required": False, "defaultMatch": False, "display": True, "type": "string", "canBeUsedToMatch": False, "removed": False}]
    ap["parameters"]["options"] = {"cellFormat": "USER_ENTERED"}
    ap["onError"] = "continueRegularOutput"
    ap.pop("webhookId", None)
    new.append(ap)
    dt("Mark Approved", "update", "app_candidates", [bx + 220, yy + 120], {"matchType": "allConditions",
       "filters": cond(("run_id", "={{ $json.run_id }}"), ("dedupe_key", "={{ $json.dedupe_key }}")), "columns": _automap(), "options": {}}, onError="continueRegularOutput")
    code("Approve Reply", APPROVE_REPLY, [bx + 440, yy], once=True)
    link("App Route", "Approve Rows", R["approve"]); link("Approve Rows", "Mark Approved"); link("Approve Rows", "Approve In Sheet")
    link("Approve In Sheet", "Approve Reply"); link("Approve Reply", "App Reply")
    # feedback (8)
    yy = Y2 + 1040
    code("Feedback Row", FEEDBACK_ROW, [bx, yy])
    dt("Save Feedback", "upsert", "app_feedback", [bx + 220, yy], {"matchType": "allConditions",
       "filters": cond(("run_id", "={{ $json.run_id }}"), ("dedupe_key", "={{ $json.dedupe_key }}")), "columns": _automap(), "options": {}}, onError="continueRegularOutput")
    dt("Feedback On Person", "update", "app_candidates", [bx + 220, yy + 120], {"matchType": "allConditions",
       "filters": cond(("run_id", "={{ $json.run_id }}"), ("dedupe_key", "={{ $json.dedupe_key }}")), "columns": _automap(), "options": {}}, onError="continueRegularOutput")
    code("Feedback Reply", "return [{ json: { response: { ok: true } } }];", [bx + 440, yy], once=True)
    link("App Route", "Feedback Row", R["feedback"]); link("Feedback Row", "Feedback On Person"); link("Feedback Row", "Save Feedback")
    link("Save Feedback", "Feedback Reply"); link("Feedback Reply", "App Reply")
    # outreach (9)
    yy = Y2 + 1240
    dt("Load Outreach Rows", "get", SEQ_TABLE, [bx, yy], {"returnAll": True}, alwaysOutputData=True, onError="continueRegularOutput")
    code("Outreach Reply", OUTREACH_REPLY, [bx + 220, yy])
    link("App Route", "Load Outreach Rows", R["outreach"]); link("Load Outreach Rows", "Outreach Reply"); link("Outreach Reply", "App Reply")

    # ---------------- the pipeline reports its progress ----------------
    def up(name, table, pos, upsert=False):
        return dt(name, "upsert" if upsert else "update", table, pos, {"matchType": "allConditions",
                  "filters": cond(("run_id", "={{ $json.run_id }}")), "columns": _automap(), "options": {}}, onError="continueRegularOutput")

    sc = N["Search Criteria"]; x, y = sc["position"]
    e1 = ensure("Ensure Run Log", "app_runs", RUN_COLUMNS, [x + 220, y - 260])
    e2 = ensure("Ensure Run People", "app_candidates", CAND_COLUMNS, [x + 440, y - 260])
    code("Run Started", RUN_STARTED, [x + 660, y - 260], once=True)
    up("Save Run Started", "app_runs", [x + 880, y - 260], upsert=True)
    link("Search Criteria", "Ensure Run Log", first=True); link("Ensure Run Log", "Ensure Run People")
    link("Ensure Run People", "Run Started"); link("Run Started", "Save Run Started")

    for parent, stage, extra in [("Format Search Results", 2, ""),
                                 ("Location Check & Rank", 3, ", count: $('Location Check & Rank').all().length")]:
        px, py = N[parent]["position"]
        nm = "Run Stage %d" % stage
        code(nm, RUN_STAGE % (stage, extra), [px + 60, py - 260], once=True)
        up("Save Run Stage %d" % stage, "app_runs", [px + 280, py - 260])
        link(parent, nm, first=True); link(nm, "Save Run Stage %d" % stage)

    px, py = N["Append row in sheet"]["position"]
    code("Run People", RUN_CANDIDATES, [px + 220, py - 220])
    dt("Save Run People", "insert", "app_candidates", [px + 440, py - 220], {"columns": _automap(), "options": {}}, onError="continueRegularOutput")
    link("Append row in sheet", "Run People"); link("Run People", "Save Run People")

    px, py = N["Results Note"]["position"]
    code("Run Done", RUN_DONE, [px + 200, py + 200], once=True)
    up("Save Run Done", "app_runs", [px + 420, py + 200])
    link("Results Note", "Run Done"); link("Run Done", "Save Run Done")

    px, py = N["Log Budget Stop"]["position"]
    code("Run Stopped", RUN_STOPPED, [px + 220, py + 160], once=True)
    up("Save Run Stopped", "app_runs", [px + 440, py + 160])
    link("Log Budget Stop", "Run Stopped"); link("Run Stopped", "Save Run Stopped")

    # ---------------- the AI's rubric and pasted JD reach the scoring ----------------
    p = N["Search Criteria"]["parameters"]
    a = "  'nl_used': !!d.nl_used"
    assert a in p["jsCode"], "round25: Search Criteria anchor"
    p["jsCode"] = p["jsCode"].replace(a, "  'Rubric': String(f.rubric || '').slice(0, 2000),\n  'JD Text': String(f.jd_text || '').slice(0, 12000),\n  'From App': !!f.__app,\n" + a, 1)
    fp = N["Format Search Results"]["parameters"]
    a1 = "${ S('Prep JD').jd_text || 'No job description"
    assert a1 in fp["jsCode"], "round25: JD anchor"
    fp["jsCode"] = fp["jsCode"].replace(a1, "${ S('Prep JD').jd_text || S('Search Criteria')['JD Text'] || 'No job description", 1)
    a2 = "Nice-to-have skills: ${ S('Search Criteria')['Secondary Skills'] || 'none' }"
    assert a2 in fp["jsCode"], "round25: rubric anchor"
    fp["jsCode"] = fp["jsCode"].replace(a2, a2 + "${ S('Search Criteria')['Rubric'] ? '\\nRecruiter rubric (weigh each line as stated; weight 3 = deal-breaker):\\n' + S('Search Criteria')['Rubric'] : '' }", 1)

    nodes.extend(new)
    return nodes, conns


# ======================================================================
# Round 26: hackathon and competitive-programming channels.
# Devfolio, Unstop, Devpost, HackerEarth (hackathons) and LeetCode, CodeChef,
# Codeforces (contests), searched through Google like Behance/Dribbble.
# Only real profile pages are kept (no problems, contests or project pages).
# Codeforces profiles are then looked up in Codeforces' free public API for
# the person's name, city, country, organisation and rating, which the
# scoring and location check use as evidence.
# Indeed, Naukri and Instahyre profiles are private (paid recruiter
# databases), so they can't be found this way.
# ======================================================================
COMPETE_SITES = r"""
// Hackathon and competitive-programming profiles: one query per site, aimed at
// profile pages (one shared query lets the biggest site crowd out the rest).
const COMPETE = {
  'Devfolio': { site: 'site:devfolio.co/@', by: 'skills' }, 'Unstop': { site: 'site:unstop.com/u', by: 'skills', pages: 1 },
  'Devpost': { site: 'site:devpost.com -inurl:software -inurl:hackathons -inurl:challenges', by: 'skills' },
  'HackerEarth': { site: 'site:hackerearth.com/@', by: 'skills' },
  'LeetCode': { site: 'site:leetcode.com/u', by: 'place' }, 'CodeChef': { site: 'site:codechef.com/users', by: 'place', pages: 1 },
  'Codeforces': { site: 'site:codeforces.com/profile', by: 'place' }
};
platforms.filter(function(pl){ return COMPETE[pl]; }).forEach(function(pl){
  const c = COMPETE[pl];
  // builders list their stack; contest profiles show a place and languages
  const parts = c.by === 'skills' ? [[c.site], locOpts, skillsDrop, exclOpts] : [[c.site], locOpts, exclOpts];
  // Google indexes few Unstop and CodeChef profiles, so they get one page
  queries.push({ kind: c.by === 'skills' ? 'hackathon-profiles' : 'contest-profiles', pages: c.pages || 2, q: fit(parts) });
});
"""

NOT_PROFILE = r"""
// Hackathon and contest sites: keep only people's profile pages.
const PROFILE_ONLY = [
  [/devfolio\.co/i, /devfolio\.co\/@[\w.-]+\/?$/i],
  [/unstop\.com/i, /unstop\.com\/u\/[\w.-]+\/?$/i],
  [/devpost\.com/i, /devpost\.com\/(?!software|hackathons|c\/|settings|about|help|portfolio\/redirect)[\w.-]+\/?$/i],
  [/hackerearth\.com/i, /hackerearth\.com\/@[\w.-]+\/?$/i],
  [/leetcode\.com/i, /leetcode\.com\/(u\/[\w.-]+|(?!problems|problemset|discuss|contest|explore|company|tag|studyplan|accounts|subscribe|assessment|interview|store|list|playground)[\w.-]+)\/?$/i],
  [/codechef\.com/i, /codechef\.com\/users\/[\w.-]+\/?$/i],
  [/codeforces\.com/i, /codeforces\.com\/profile\/[\w.-]+\/?$/i]
];
const notProfile = function(link){ const l = String(link || ''); const r = PROFILE_ONLY.find(function(p){ return p[0].test(l); }); return r ? !r[1].test(l) : false; };
"""

CF_HANDLES = r"""
// Codeforces profiles in this search: look them up in Codeforces' public API.
const j = $input.first().json || {};
const handles = [];
(j.organic || []).forEach(function(o){
  const m = String(o.link || '').match(/codeforces\.com\/profile\/([\w.-]+)\/?$/i);
  if (m && handles.indexOf(m[1]) === -1) handles.push(m[1]);
});
return [{ json: Object.assign({}, j, { cf_handles: handles.slice(0, 100),
  cf_url: handles.length ? 'https://codeforces.com/api/user.info?handles=' + handles.slice(0, 100).join(';') : '' }) }];
"""

CF_ANNOTATE = r"""
// Add what Codeforces says about each person to their search result, so the
// scoring and the location check can use it.
const base = $('CF Handles').first().json || {};
let users = [];
try { const r = $('CF Users').first().json || {}; if (r.status === 'OK' && Array.isArray(r.result)) users = r.result; } catch (e) {}
const byHandle = {};
users.forEach(function(u){ if (u && u.handle) byHandle[String(u.handle).toLowerCase()] = u; });
const organic = (base.organic || []).map(function(o){
  const m = String(o.link || '').match(/codeforces\.com\/profile\/([\w.-]+)\/?$/i);
  const u = m && byHandle[m[1].toLowerCase()];
  if (!u) return o;
  const name = [u.firstName, u.lastName].filter(Boolean).join(' ');
  const where = [u.city, u.country].filter(Boolean).join(', ');
  const bits = [];
  if (u.rating) bits.push('Codeforces rating ' + u.rating + (u.rank ? ' (' + u.rank + ')' : '') + (u.maxRating ? ', best ' + u.maxRating : ''));
  if (where) bits.push('location: ' + where);
  if (u.organization) bits.push('organisation: ' + u.organization);
  return Object.assign({}, o, { title: (name ? name + ' (' + u.handle + ')' : o.title) + ' - Codeforces', snippet: [o.snippet, bits.join('; ')].filter(Boolean).join(' | ') });
});
const out = Object.assign({}, base, { organic: organic });
delete out.cf_url; delete out.cf_handles;
return [{ json: out }];
"""


def apply_round26(nodes, conns):
    N = {n["name"]: n for n in nodes}

    def R(name, old, new):
        p = N[name]["parameters"]
        assert old in p["jsCode"], "round26 %s: anchor not found: %s" % (name, old[:70])
        p["jsCode"] = p["jsCode"].replace(old, new, 1)

    # 1. the searches
    R("Build Search Query", "const out = [];\nfor (const qq of queries) {", COMPETE_SITES.lstrip() + "const out = [];\nfor (const qq of queries) {")

    # 2. keep only profile pages
    R("Merge Pages", "const items = $input.all();", NOT_PROFILE.lstrip() + "const items = $input.all();")
    R("Merge Pages", "    if (o && o.link && JOB_AD.test(String(o.link))) { seen[key] = true; continue; }",
      "    if (o && o.link && (JOB_AD.test(String(o.link)) || notProfile(o.link))) { seen[key] = true; continue; }")

    # 3. they are always real people
    R("Format Search Results", "linkedin\\.com\\/in\\/)/i;",
      "linkedin\\.com\\/in\\/|devfolio\\.co\\/@|unstop\\.com\\/u\\/|hackerearth\\.com\\/@|codechef\\.com\\/users\\/|codeforces\\.com\\/profile\\/|leetcode\\.com\\/u\\/)/i;")

    # 4. source names
    R("Parse Contact", "  if (u.indexOf('apollo.io') !== -1) return 'Apollo';",
      "  if (u.indexOf('apollo.io') !== -1) return 'Apollo';\n"
      "  if (u.indexOf('devfolio.co') !== -1) return 'Devfolio';\n  if (u.indexOf('unstop.com') !== -1) return 'Unstop';\n"
      "  if (u.indexOf('devpost.com') !== -1) return 'Devpost';\n  if (u.indexOf('hackerearth.com') !== -1) return 'HackerEarth';\n"
      "  if (u.indexOf('leetcode.com') !== -1) return 'LeetCode';\n  if (u.indexOf('codechef.com') !== -1) return 'CodeChef';\n"
      "  if (u.indexOf('codeforces.com') !== -1) return 'Codeforces';")
    R("Location Check & Rank", "['behance.net','Behance'],['wellfound.com','Wellfound']]",
      "['behance.net','Behance'],['wellfound.com','Wellfound'],['devfolio.co','Devfolio'],['unstop.com','Unstop'],['devpost.com','Devpost'],"
      "['hackerearth.com','HackerEarth'],['leetcode.com','LeetCode'],['codechef.com','CodeChef'],['codeforces.com','Codeforces']]")

    # 5. Codeforces public API, between Merge Pages and the scoring
    mp = N["Merge Pages"]; x, y = mp["position"]
    h = _node("CF Handles", "n8n-nodes-base.code", 2, [x + 60, y + 200], {"jsCode": CF_HANDLES})
    gate = _node("Any Codeforces?", "n8n-nodes-base.if", 2.3, [x + 260, y + 200], {
        "conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 3},
                       "conditions": [{"id": "cf-any", "leftValue": "={{ $json.cf_url }}", "rightValue": "",
                                       "operator": {"type": "string", "operation": "notEmpty", "singleValue": True}}],
                       "combinator": "and"}, "looseTypeValidation": True, "options": {}})
    users = _node("CF Users", "n8n-nodes-base.httpRequest", 4.2, [x + 460, y + 120], {
        "url": "={{ $json.cf_url }}", "options": {"timeout": 20000}}, onError="continueRegularOutput")
    ann = _node("CF Annotate", "n8n-nodes-base.code", 2, [x + 660, y + 200], {"jsCode": CF_ANNOTATE})
    nodes.extend([h, gate, users, ann])
    nxt = conns["Merge Pages"]["main"][0]
    assert [c["node"] for c in nxt] == ["Format Search Results"], nxt
    conns["Merge Pages"]["main"][0] = [{"node": "CF Handles", "type": "main", "index": 0}]
    conns["CF Handles"] = {"main": [[{"node": "Any Codeforces?", "type": "main", "index": 0}]]}
    conns["Any Codeforces?"] = {"main": [[{"node": "CF Users", "type": "main", "index": 0}],
                                         [{"node": "CF Annotate", "type": "main", "index": 0}]]}
    conns["CF Users"] = {"main": [[{"node": "CF Annotate", "type": "main", "index": 0}]]}
    conns["CF Annotate"] = {"main": [[{"node": "Format Search Results", "type": "main", "index": 0}]]}
    return nodes, conns


# ======================================================================
# Round 27: product and non-tech channels.
# Topmate and ADPList (mentor pages with title and company) and Medium
# writers. Product Hunt and Substack were tried and dropped: Google returns
# their posts and launches, almost never people's profile pages. Searched by job title and place,
# keeping only people's profile pages (no posts, lists or category pages).
# ======================================================================
PEOPLE_SITES = r"""
// Product and non-tech profiles: one query per site, by job title and place.
const PEOPLE = {
  'Topmate': 'site:topmate.io', 'ADPList': 'site:adplist.org/mentors -inurl:expertise -inurl:location -inurl:company',
  'Medium': 'site:medium.com/@'
};
platforms.filter(function(pl){ return PEOPLE[pl]; }).forEach(function(pl){
  queries.push({ kind: 'people-profiles', pages: 2, q: fit([[PEOPLE[pl]], roleOpts.length ? roleOpts : [roleShort], locOpts, exclOpts]) });
});
"""

NOT_PROFILE_27 = r"""
// Product and non-tech sites: keep only people's profile pages.
PROFILE_ONLY.push(
  [/topmate\.io/i, /topmate\.io\/(?!blog|explore|about|pricing|login|signup|features|terms|privacy|careers|search|categor)[\w.-]+\/?$/i],
  [/adplist\.org/i, /adplist\.org\/mentors\/(?!expertise|location|company)[\w.-]+\/?$/i],
  [/medium\.com/i, /medium\.com\/@[\w.-]+(\/about)?\/?$/i]
);
"""


def apply_round27(nodes, conns):
    N = {n["name"]: n for n in nodes}

    def R(name, old, new):
        p = N[name]["parameters"]
        assert old in p["jsCode"], "round27 %s: anchor not found: %s" % (name, old[:70])
        p["jsCode"] = p["jsCode"].replace(old, new, 1)

    R("Build Search Query", "const out = [];\nfor (const qq of queries) {", PEOPLE_SITES.lstrip() + "const out = [];\nfor (const qq of queries) {")
    R("Merge Pages", "const notProfile = function(link)", NOT_PROFILE_27.lstrip() + "const notProfile = function(link)")
    R("Format Search Results", "leetcode\\.com\\/u\\/)/i;",
      "leetcode\\.com\\/u\\/|topmate\\.io\\/|adplist\\.org\\/mentors\\/|medium\\.com\\/@)/i;")
    R("Parse Contact", "  if (u.indexOf('codeforces.com') !== -1) return 'Codeforces';",
      "  if (u.indexOf('codeforces.com') !== -1) return 'Codeforces';\n"
      "  if (u.indexOf('topmate.io') !== -1) return 'Topmate';\n  if (u.indexOf('adplist.org') !== -1) return 'ADPList';\n"
      "  if (u.indexOf('medium.com') !== -1) return 'Medium';")
    R("Location Check & Rank", "['codeforces.com','Codeforces']]",
      "['codeforces.com','Codeforces'],['topmate.io','Topmate'],['adplist.org','ADPList'],['medium.com','Medium']]")
    # nothing found: still send the model a valid (tiny) request, so the run
    # finishes as "0 found" instead of failing on an empty prompt
    R("Format Search Results", "if (!out.length) out.push({ json: { page_text: '', lines: [], batch: 1, pool_size: 0 } });",
      "if (!out.length) out.push({ json: { page_text: '', lines: [], batch: 1, pool_size: 0, system: 'You return JSON only.', prompt: 'There are no profiles to score. Return [].' } });")
    return nodes, conns


# ======================================================================
# Round 28: non-tech roles, pedigree filters, and the follow-up editor.
# - Title variants for product, program, sales, marketing, HR, finance,
#   operations and customer-success roles, so "HR Business Partner" also
#   finds "HRBP" and "People Partner".
# - Colleges: an optional pedigree filter next to target companies
#   (e.g. "IIM Ahmedabad, ISB"), used in the LinkedIn search.
# - Non-tech scoring: judge scope, outcomes, team size and domain, not code.
# - The app can read and edit the follow-up emails (outreach_templates).
# ======================================================================
NONTECH_SYNONYMS = r"""  'program manager': ['program manager','technical program manager','project manager'],
  'project manager': ['project manager','program manager','delivery manager'],
  'sales manager': ['sales manager','area sales manager','regional sales manager'],
  'area sales manager': ['area sales manager','sales manager','territory sales manager'],
  'account executive': ['account executive','enterprise account executive','account manager'],
  'account manager': ['account manager','key account manager','client relationship manager'],
  'key account manager': ['key account manager','account manager','client relationship manager'],
  'business development manager': ['business development manager','bdm','sales manager'],
  'inside sales': ['inside sales','sales development representative','business development representative'],
  'marketing manager': ['marketing manager','digital marketing manager','growth marketing manager'],
  'digital marketing manager': ['digital marketing manager','performance marketing manager','growth marketing manager'],
  'performance marketing manager': ['performance marketing manager','digital marketing manager','growth marketing manager'],
  'product marketing manager': ['product marketing manager','pmm','marketing manager'],
  'brand manager': ['brand manager','brand marketing manager','category manager'],
  'content writer': ['content writer','content strategist','copywriter'],
  'hr manager': ['hr manager','hr business partner','people partner'],
  'hr business partner': ['hr business partner','hrbp','people partner'],
  'talent acquisition': ['talent acquisition','recruiter','talent partner'],
  'financial analyst': ['financial analyst','fp&a analyst','finance analyst'],
  'finance manager': ['finance manager','fp&a manager','financial controller'],
  'chartered accountant': ['chartered accountant','accountant','finance manager'],
  'operations manager': ['operations manager','business operations manager','ops manager'],
  'customer success manager': ['customer success manager','client success manager','account manager'],
  'supply chain manager': ['supply chain manager','procurement manager','logistics manager'],
  'business analyst': ['business analyst','product analyst','data analyst'],
"""

COLLEGES_JS = r"""
// Colleges (optional pedigree filter, e.g. "IIM Ahmedabad, ISB"): LinkedIn search only.
const colleges = String(form['Colleges'] || '').split(',').map(function(s){ return s.trim(); }).filter(Boolean).slice(0, 3).map(q);
const collegeOpts = colleges.length ? shrink(colleges).concat(['']) : [''];
"""

NONTECH_SCORING = ("${ /(product|program|project|sales|account|business development|marketing|brand|content|hr\\b|human resources|people|talent|recruit|finance|financial|accountant|fp&a|operations|customer success|supply chain|procurement|business analyst|consultant)/i.test(S('Search Criteria')['Target Role'] || '') "
                   "&& !/(engineer|developer|scientist|architect|devops|sre)/i.test(S('Search Criteria')['Target Role'] || '') ? "
                   "'NON-TECHNICAL ROLE: do not look for code, repositories or programming languages. Judge on scope (team size, budget, revenue, region or number of accounts owned), "
                   "measurable outcomes (targets hit, growth, savings, launches), seniority of title and reporting line, domain or industry match, and companies worked at. "
                   "A profile that states numbers (\"grew revenue 3x\", \"managed a team of 12\") is stronger evidence than one that lists duties.\\n' : '' }")

TEMPLATES_REPLY = r"""
// The follow-up emails as plain text for the app's editor.
""" + DEFAULT_TEMPLATES + r"""
const OFFSETS = [2, 4, 5, 7];
const rows = $('App Load Templates').all().map(function(i){ return i.json || {}; }).filter(function(r){ return r.step && r.body; });
const by = {};
DEFAULTS.forEach(function(t){ by[t.step] = t.body; });
rows.forEach(function(r){ by[Number(r.step)] = String(r.body); });
const text = function(h){
  return String(h).replace(/<\/p>\s*<p[^>]*>/gi, '\n\n').replace(/<br\s*\/?>/gi, '\n').replace(/<[^>]+>/g, '')
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, '&').trim();
};
return [{ json: { response: { ok: true, steps: [2, 3, 4, 5].map(function(s, i){
  return { step: s, day: OFFSETS[i], body: text(by[s]), custom: rows.some(function(r){ return Number(r.step) === s; }) };
}) } } }];
"""

TEMPLATE_ROWS = r"""
// Save the edited follow-ups: plain text from the app becomes simple HTML
// paragraphs (anything that looks like HTML is shown as text, not run).
const OFFSETS = [2, 4, 5, 7];
const b = $('App Gate').first().json.body || {};
const esc = function(s){ return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); };
return (b.steps || []).map(function(s){
  const n = Number(s.step);
  const body = String(s.body || '').replace(/\r/g, '').trim().slice(0, 4000);
  const html = body.split(/\n\s*\n/).map(function(p){ return '<p>' + esc(p.trim()).replace(/\n/g, '<br/>') + '</p>'; }).join('');
  return { json: { step: n, day: OFFSETS[n - 2], body: html } };
});
"""

APP_GATE_28 = r"""const ROUTES = ['intake', 'search', 'status', 'results', 'runs', 'approve', 'feedback', 'outreach', 'templates', 'save_templates'];
if (ROUTES.indexOf(action) === -1) return reply({ ok: false, error: 'Unknown action.' });
if (action === 'save_templates') {
  const st = Array.isArray(body.steps) ? body.steps : [];
  const okSteps = st.length && st.every(function(s){ return [2, 3, 4, 5].indexOf(Number(s && s.step)) !== -1 && String(s.body || '').trim().length >= 10 && String(s.body || '').length <= 4000; });
  if (!okSteps) return reply({ ok: false, error: 'Each follow-up needs some text (10 to 4000 characters).' });
}"""

COLLEGE_FIELD = {"fieldName": "colleges", "fieldLabel": "Colleges (optional – up to 3, comma-separated)", "fieldType": "text",
                 "defaultValue": "={{ $json['Colleges'] || '' }}", "placeholder": "e.g. IIM Ahmedabad, ISB, IIT Bombay"}

APP_ROUTES_28 = APP_ROUTES + ["templates", "save_templates"]


def apply_round28(nodes, conns):
    N = {n["name"]: n for n in nodes}

    def R(name, old, new):
        p = N[name]["parameters"]
        assert old in p["jsCode"], "round28 %s: anchor not found: %s" % (name, old[:70])
        p["jsCode"] = p["jsCode"].replace(old, new, 1)

    # 1. non-tech title variants
    R("Build Search Query", "  'hr': ['hr','human resources','talent acquisition','hrbp','people operations']\n};",
      "  'hr': ['hr','human resources','talent acquisition','hrbp','people operations'],\n" + NONTECH_SYNONYMS.rstrip().rstrip(",") + "\n};")
    R("Build Search Query", "|computer vision)$/;",
      "|computer vision|digital marketing|performance marketing|growth marketing|hr business|key account|area sales|supply chain|inside sales|financial planning)$/;")
    # 2. colleges (pedigree) in the LinkedIn search
    R("Build Search Query", "const employerOpts = ", COLLEGES_JS.lstrip() + "const employerOpts = ")
    R("Build Search Query", "    employerOpts, eduOpts, exclOpts, ['-intitle:jobs -inurl:jobs', '']]) });",
      "    employerOpts, collegeOpts, eduOpts, exclOpts, ['-intitle:jobs -inurl:jobs', '']]) });")
    R("Search Criteria", "  'Keywords to Exclude':", "  'Colleges': String(val('colleges', 'Colleges') || '').trim(),\n  'Keywords to Exclude':")
    # the form's page 2 gets the same field, after target companies
    fields = None
    def find(x):
        nonlocal fields
        if isinstance(x, list):
            if any(isinstance(f, dict) and f.get("fieldName") == "employer" for f in x):
                fields = x
            for y in x:
                find(y)
        elif isinstance(x, dict):
            for y in x.values():
                find(y)
    find(N["Check Search"]["parameters"])
    assert fields is not None, "round28: Check Search employer field not found"
    i = [f.get("fieldName") for f in fields].index("employer")
    fields.insert(i + 1, COLLEGE_FIELD)
    R("App Criteria", "  employer: String(c.employer || ''),", "  employer: String(c.employer || ''),\n  colleges: String(c.colleges || '').slice(0, 200),")
    # 3. intake: colleges + non-tech must-haves and rubric
    R("Intake Prompt", """  '  "companies": target companies or industry as a short comma list or "", "exclude": words or companies to exclude (array) },',""",
      """  '  "companies": target companies or industry as a short comma list or "", "colleges": colleges or institutes the brief requires (e.g. "IIM, ISB"), or "" if it only prefers them, "exclude": words or companies to exclude (array) },',""")
    R("Intake Prompt", "  'Use only what the brief states or clearly implies. Do not invent requirements.'",
      "  'NON-TECHNICAL ROLES (product, sales, marketing, HR, finance, operations): \"must\" is the domain, function or tools a profile would name (e.g. \"B2B SaaS\", \"Salesforce\", \"FP&A\", \"performance marketing\"), and the rubric judges scope, measurable outcomes, team size and domain, never code.',\n"
      "  'Use only what the brief states or clearly implies. Do not invent requirements.'")
    R("Intake Result", "  exclude: arr(c.exclude, 6)\n};",
      "  colleges: Array.isArray(c.colleges) ? c.colleges.join(', ') : String(c.colleges || '').slice(0, 120),\n  exclude: arr(c.exclude, 6)\n};")
    # "ideally IIM" is a preference: rank on it, don't filter the search by it
    R("Intake Result", "  if (/\\b(a plus|a bonus|nice[- ]to[- ]have|preferred|an advantage|good to have)\\b/i.test(part)) soft.push(part.toLowerCase());",
      "  if (/\\b(a plus|a bonus|nice[- ]to[- ]have|preferred|an advantage|good to have|ideally)\\b/i.test(part)) soft.push(part.toLowerCase());")
    R("Intake Result", "moved.forEach(function(s){ if (criteria.nice.indexOf(s) === -1) criteria.nice.unshift(s); });",
      "moved.forEach(function(s){ if (criteria.nice.indexOf(s) === -1) criteria.nice.unshift(s); });\n"
      "const colls = criteria.colleges.split(',').map(function(s){ return s.trim(); }).filter(Boolean);\n"
      "if (colls.length && colls.every(isSoft)) { criteria.nice.push(colls.join(' or ')); criteria.colleges = ''; }\n"
      "// non-tech: duties are not search words\n"
      "const DUTY = /^(team (management|leadership|handling)|people management|stakeholder management|communication|leadership|(quarterly |sales |revenue )?targets?|target achievement|quota|negotiation|problem solving|ownership)$/i;\n"
      "criteria.must.filter(function(s){ return DUTY.test(s); }).forEach(function(s){ if (criteria.nice.indexOf(s) === -1) criteria.nice.push(s); });\n"
      "criteria.must = criteria.must.filter(function(s){ return !DUTY.test(s); });\n"
      "criteria.nice = criteria.nice.slice(0, 6);")
    # 4. non-tech scoring
    R("Format Search Results", "--- INSTRUCTIONS ---\n", "--- INSTRUCTIONS ---\n" + NONTECH_SCORING + "\n")
    # 5. app: read and save the follow-up emails
    R("App Gate", "const ROUTES = ['intake', 'search', 'status', 'results', 'runs', 'approve', 'feedback', 'outreach'];\nif (ROUTES.indexOf(action) === -1) return reply({ ok: false, error: 'Unknown action.' });", APP_GATE_28)
    sw = N["App Route"]
    sw["parameters"]["numberOutputs"] = len(APP_ROUTES_28)
    sw["parameters"]["output"] = "={{ %s.indexOf($json.route) }}" % json.dumps(APP_ROUTES_28)
    while len(conns["App Route"]["main"]) < len(APP_ROUTES_28):
        conns["App Route"]["main"].append([])
    ri = {r: i for i, r in enumerate(APP_ROUTES_28)}
    lo = N["Load Outreach Rows"]; bx, yy = lo["position"]
    y1, y2 = yy + 180, yy + 340
    e = _dt_ensure("App Ensure Templates", TPL_TABLE, TPL_COLUMNS, [bx, y1])
    lt = _node("App Load Templates", "n8n-nodes-base.dataTable", 1.1, [bx + 220, y1], {
        "resource": "row", "operation": "get", "dataTableId": _dt_ref(TPL_TABLE), "returnAll": True},
        alwaysOutputData=True, onError="continueRegularOutput")
    lt["executeOnce"] = True
    tr = _node("Templates Reply", "n8n-nodes-base.code", 2, [bx + 440, y1], {"jsCode": TEMPLATES_REPLY})
    tr["executeOnce"] = True
    e2 = _dt_ensure("App Ensure Templates 2", TPL_TABLE, TPL_COLUMNS, [bx, y2])
    rows = _node("Template Rows", "n8n-nodes-base.code", 2, [bx + 220, y2], {"jsCode": TEMPLATE_ROWS})
    rows["executeOnce"] = True
    save = _node("Save Template", "n8n-nodes-base.dataTable", 1.1, [bx + 440, y2], {
        "resource": "row", "operation": "upsert", "dataTableId": _dt_ref(TPL_TABLE), "matchType": "allConditions",
        "filters": {"conditions": [{"keyName": "step", "condition": "eq", "keyValue": "={{ $json.step }}"}]},
        "columns": _automap(), "options": {}}, onError="continueRegularOutput")
    done = _node("Save Templates Reply", "n8n-nodes-base.code", 2, [bx + 660, y2],
                 {"jsCode": "return [{ json: { response: { ok: true, saved: $('Template Rows').all().length } } }];"})
    done["executeOnce"] = True
    nodes.extend([e, lt, tr, e2, rows, save, done])

    def link(a, b, idx=0):
        conns.setdefault(a, {"main": [[]]})
        while len(conns[a]["main"]) <= idx:
            conns[a]["main"].append([])
        conns[a]["main"][idx].append({"node": b, "type": "main", "index": 0})
    link("App Route", "App Ensure Templates", ri["templates"])
    link("App Ensure Templates", "App Load Templates"); link("App Load Templates", "Templates Reply"); link("Templates Reply", "App Reply")
    link("App Route", "App Ensure Templates 2", ri["save_templates"])
    link("App Ensure Templates 2", "Template Rows"); link("Template Rows", "Save Template"); link("Save Template", "Save Templates Reply"); link("Save Templates Reply", "App Reply")
    return nodes, conns


# ======================================================================
# Round 29: the app becomes a team product.
# - Team logins: one account per person (admin or recruiter), passwords
#   stored as salted PBKDF2-SHA256, 7-day sessions, and an activity log of
#   who searched, approved, edited or invited whom. The old shared access
#   code is only used once, to prove you own the workspace when you create
#   the first admin.
# - Settings: signature, sending window, daily cap and weekdays-only for the
#   follow-ups, kept in app_settings and read by the follow-up runner.
# - Home dashboard: this week's numbers, what needs attention, best sources,
#   recent activity and a getting-started checklist.
# ======================================================================
USER_COLUMNS = [("email", "string"), ("name", "string"), ("role", "string"), ("pass_hash", "string"), ("salt", "string"),
                ("status", "string"), ("must_change", "boolean"), ("created_at", "date"), ("last_login", "date")]
SESSION_COLUMNS = [("token", "string"), ("email", "string"), ("expires_at", "date"), ("created_at", "date")]
ACTIVITY_COLUMNS = [("at", "date"), ("email", "string"), ("name", "string"), ("action", "string"), ("detail", "string")]

SHA256_JS = r"""
// ---- SHA-256 / HMAC / PBKDF2 in plain JavaScript (no modules needed) ----
function utf8(s) { return Array.from(unescape(encodeURIComponent(String(s)))).map(function(c){ return c.charCodeAt(0); }); }
const K256 = [0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
  0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
  0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
  0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
function sha256(bytes) {
  const H = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19];
  const l = bytes.length, m = bytes.slice(); m.push(0x80);
  while ((m.length % 64) !== 56) m.push(0);
  const bits = l * 8; for (let i = 7; i >= 0; i--) m.push(i >= 4 ? 0 : (bits >>> (i * 8)) & 0xff);
  const w = new Array(64);
  for (let o = 0; o < m.length; o += 64) {
    for (let i = 0; i < 16; i++) w[i] = (m[o+i*4] << 24) | (m[o+i*4+1] << 16) | (m[o+i*4+2] << 8) | m[o+i*4+3];
    for (let i = 16; i < 64; i++) {
      const a = w[i-15], b = w[i-2];
      const s0 = ((a >>> 7) | (a << 25)) ^ ((a >>> 18) | (a << 14)) ^ (a >>> 3);
      const s1 = ((b >>> 17) | (b << 15)) ^ ((b >>> 19) | (b << 13)) ^ (b >>> 10);
      w[i] = (w[i-16] + s0 + w[i-7] + s1) | 0;
    }
    let [a, b, c, d, e, f, g, h] = H;
    for (let i = 0; i < 64; i++) {
      const S1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7));
      const t1 = (h + S1 + ((e & f) ^ (~e & g)) + K256[i] + w[i]) | 0;
      const S0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10));
      const t2 = (S0 + ((a & b) ^ (a & c) ^ (b & c))) | 0;
      h = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
    }
    H[0] = (H[0] + a) | 0; H[1] = (H[1] + b) | 0; H[2] = (H[2] + c) | 0; H[3] = (H[3] + d) | 0;
    H[4] = (H[4] + e) | 0; H[5] = (H[5] + f) | 0; H[6] = (H[6] + g) | 0; H[7] = (H[7] + h) | 0;
  }
  const out = []; H.forEach(function(x){ out.push((x >>> 24) & 0xff, (x >>> 16) & 0xff, (x >>> 8) & 0xff, x & 0xff); });
  return out;
}
function hmac256(key, msg) {
  if (key.length > 64) key = sha256(key);
  const k = key.concat(new Array(64 - key.length).fill(0));
  return sha256(k.map(function(b){ return b ^ 0x5c; }).concat(sha256(k.map(function(b){ return b ^ 0x36; }).concat(msg))));
}
function pbkdf2hex(pass, salt, iter) {
  const P = utf8(pass);
  let u = hmac256(P, utf8(salt).concat([0, 0, 0, 1])), t = u.slice();
  for (let i = 1; i < iter; i++) { u = hmac256(P, u); for (let j = 0; j < 32; j++) t[j] ^= u[j]; }
  return t.map(function(b){ return ('0' + b.toString(16)).slice(-2); }).join('');
}
"""

APP_GATE_29 = SHA256_JS + r"""
// ---- Every app request: who is this, are they allowed, then pick a route ----
let body = $('App API').first().json.body;
if (typeof body === 'string') { try { body = JSON.parse(body); } catch (e) { body = {}; } }
body = body || {};
const rand = String((($('Fresh Random').first() || {}).json || {}).rand || '');
const settings = {};
$('Load App Settings').all().forEach(function(i){ const r = i.json || {}; if (r.key) settings[r.key] = String(r.value == null ? '' : r.value); });
const users = $('Load App Users').all().map(function(i){ return i.json || {}; }).filter(function(u){ return u.email; });
const sessions = $('Load App Sessions').all().map(function(i){ return i.json || {}; }).filter(function(s){ return s.token; });
const action = String(body.action || '');
const now = new Date();
const W = { users: [], sessions: [], activity: [], settings: [] };
const out = function(route, extra){ return [{ json: Object.assign({ route: route, writes: W }, extra || {}) }]; };
const reply = function(r){ return out('reply', { response: r }); };
const ITER = 4000;
const hashPw = function(pw, salt){ return pbkdf2hex(String(pw), String(salt), ITER); };
const USER_KEYS = ['email', 'name', 'role', 'pass_hash', 'salt', 'status', 'must_change', 'created_at', 'last_login'];
const saveUser = function(u){ const r = {}; USER_KEYS.forEach(function(k){ if (u[k] !== undefined && u[k] !== null) r[k] = u[k]; }); r.must_change = r.must_change === true || r.must_change === 'true'; W.users.push(r); };
const pub = function(u){ return { email: u.email, name: u.name, role: u.role, status: u.status, last_login: u.last_login || '', must_change: u.must_change === true || u.must_change === 'true' }; };
const log = function(u, what, detail){ W.activity.push({ at: now.toISOString(), email: u.email, name: u.name, action: what, detail: String(detail || '').slice(0, 300) }); };
const newSession = function(u){ const token = rand.slice(32, 64); W.sessions.push({ token: token, email: u.email, expires_at: new Date(now.getTime() + 7 * 864e5).toISOString(), created_at: now.toISOString() }); return token; };
const tempPassword = function(){ return 'tr-' + rand.slice(40, 50); };
const em = function(e){ return String(e || '').trim().toLowerCase(); };
const okEmail = function(e){ return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(e); };
const pwErr = function(p){ return String(p || '').length < 8 ? 'Use at least 8 characters for the password.' : ''; };
const activeAdmins = function(){ return users.filter(function(u){ return u.role === 'admin' && u.status === 'active'; }); };
if (rand.length < 64) return reply({ ok: false, error: 'Could not start a secure session. Try again.' });

// -- signed out --
if (action === 'auth_state') return reply({ ok: true, has_users: users.length > 0, need_code: !users.length && !!settings.access_code });
if (action === 'setup_admin') {
  if (users.length) return reply({ ok: false, error: 'This workspace is already set up. Sign in instead.' });
  if (settings.access_code && String(body.code || '') !== settings.access_code) return reply({ ok: false, error: 'That access code is not right.' });
  const email = em(body.email), name = String(body.name || '').trim().slice(0, 80);
  if (!name || !okEmail(email)) return reply({ ok: false, error: 'Add your name and a valid email.' });
  if (pwErr(body.password)) return reply({ ok: false, error: pwErr(body.password) });
  const salt = rand.slice(0, 32);
  const u = { email: email, name: name, role: 'admin', salt: salt, pass_hash: hashPw(body.password, salt), status: 'active', must_change: false, created_at: now.toISOString(), last_login: now.toISOString() };
  saveUser(u); log(u, 'created the workspace', '');
  return reply({ ok: true, token: newSession(u), user: pub(u) });
}
if (action === 'login') {
  const u = users.find(function(x){ return x.email === em(body.email); });
  if (!u || u.status !== 'active' || hashPw(body.password || '', u.salt) !== u.pass_hash) return reply({ ok: false, error: 'Email or password is not right.' });
  saveUser(Object.assign({}, u, { last_login: now.toISOString() })); log(u, 'signed in', '');
  return reply({ ok: true, token: newSession(u), user: pub(u) });
}

// -- everything else needs a live session --
const tok = String(body.token || '');
const sess = tok.length >= 32 ? sessions.find(function(s){ return s.token === tok; }) : null;
const me = sess && new Date(sess.expires_at) > now ? users.find(function(u){ return u.email === sess.email && u.status === 'active'; }) : null;
if (!me) return reply({ ok: false, auth: 'need_login', has_users: users.length > 0 });
const isAdmin = me.role === 'admin';
if (action === 'ping' || action === 'me') return reply({ ok: true, user: pub(me) });
if (action === 'logout') { W.sessions.push({ token: tok, email: me.email, expires_at: now.toISOString(), created_at: sess.created_at || now.toISOString() }); return reply({ ok: true }); }
if (action === 'change_password') {
  if (hashPw(body.old || '', me.salt) !== me.pass_hash) return reply({ ok: false, error: 'Your current password is not right.' });
  if (pwErr(body.password)) return reply({ ok: false, error: pwErr(body.password) });
  const salt = rand.slice(0, 32);
  saveUser(Object.assign({}, me, { salt: salt, pass_hash: hashPw(body.password, salt), must_change: false })); log(me, 'changed their password', '');
  return reply({ ok: true });
}
const ADMIN_ONLY = ['users', 'invite', 'update_user', 'reset_password', 'save_settings', 'save_templates'];
if (ADMIN_ONLY.indexOf(action) !== -1 && !isAdmin) return reply({ ok: false, error: 'Only an admin can do that.' });
if (action === 'users') return reply({ ok: true, users: users.map(pub) });
if (action === 'invite') {
  const email = em(body.email), name = String(body.name || '').trim().slice(0, 80), role = body.role === 'admin' ? 'admin' : 'recruiter';
  if (!name || !okEmail(email)) return reply({ ok: false, error: 'Add their name and a valid email.' });
  if (users.some(function(u){ return u.email === email; })) return reply({ ok: false, error: 'Someone with that email is already on the team.' });
  const temp = tempPassword(), salt = rand.slice(0, 32);
  const u = { email: email, name: name, role: role, salt: salt, pass_hash: hashPw(temp, salt), status: 'active', must_change: true, created_at: now.toISOString() };
  saveUser(u); log(me, 'added a teammate', name + ' (' + role + ')');
  return reply({ ok: true, user: pub(u), temp_password: temp });
}
if (action === 'update_user' || action === 'reset_password') {
  const u = users.find(function(x){ return x.email === em(body.email); });
  if (!u) return reply({ ok: false, error: 'No one with that email.' });
  if (action === 'reset_password') {
    const temp = tempPassword(), salt = rand.slice(0, 32);
    saveUser(Object.assign({}, u, { salt: salt, pass_hash: hashPw(temp, salt), must_change: true })); log(me, 'reset a password', u.name);
    return reply({ ok: true, temp_password: temp });
  }
  const role = body.role === 'admin' ? 'admin' : (body.role === 'recruiter' ? 'recruiter' : u.role);
  const status = body.status === 'disabled' ? 'disabled' : (body.status === 'active' ? 'active' : u.status);
  const losesAdmin = u.role === 'admin' && u.status === 'active' && (role !== 'admin' || status !== 'active');
  if (losesAdmin && activeAdmins().length <= 1) return reply({ ok: false, error: 'The team needs at least one active admin.' });
  if (u.email === me.email && status !== 'active') return reply({ ok: false, error: 'You cannot turn off your own account.' });
  saveUser(Object.assign({}, u, { role: role, status: status }));
  log(me, status !== u.status ? (status === 'active' ? 'turned on' : 'turned off') + ' an account' : 'changed a role', u.name + (role !== u.role ? ' → ' + role : ''));
  return reply({ ok: true, user: pub(Object.assign({}, u, { role: role, status: status })) });
}
const SETTING_KEYS = ['signature', 'window_start', 'window_end', 'daily_cap', 'weekdays_only'];
const sendSettings = function(){ return { signature: settings.signature || '', window_start: settings.window_start || '09:30', window_end: settings.window_end || '18:30',
  daily_cap: Number(settings.daily_cap) || 50, weekdays_only: settings.weekdays_only !== 'false' }; };
if (action === 'settings') return reply({ ok: true, settings: sendSettings(), is_admin: isAdmin });
if (action === 'save_settings') {
  const s = body.settings || {};
  const hm = /^([01]\d|2[0-3]):[0-5]\d$/;
  if (!hm.test(String(s.window_start)) || !hm.test(String(s.window_end)) || String(s.window_start) >= String(s.window_end)) return reply({ ok: false, error: 'Pick a sending window like 09:30 to 18:30.' });
  const cap = parseInt(s.daily_cap, 10);
  if (!(cap >= 1 && cap <= 200)) return reply({ ok: false, error: 'Daily limit must be between 1 and 200.' });
  const vals = { signature: String(s.signature || '').slice(0, 500), window_start: String(s.window_start), window_end: String(s.window_end), daily_cap: String(cap), weekdays_only: s.weekdays_only === false ? 'false' : 'true' };
  SETTING_KEYS.forEach(function(k){ W.settings.push({ key: k, value: vals[k] }); });
  log(me, 'updated the sending settings', vals.window_start + '–' + vals.window_end + ', ' + vals.daily_cap + '/day');
  return reply({ ok: true, settings: Object.assign(sendSettings(), vals, { daily_cap: cap, weekdays_only: vals.weekdays_only !== 'false' }) });
}

// -- routes with their own steps --
const ROUTES = ['intake', 'search', 'status', 'results', 'runs', 'approve', 'feedback', 'outreach', 'templates', 'save_templates', 'dashboard'];
if (ROUTES.indexOf(action) === -1) return reply({ ok: false, error: 'Unknown action.' });
if (action === 'save_templates') {
  const st = Array.isArray(body.steps) ? body.steps : [];
  const okSteps = st.length && st.every(function(s){ return [2, 3, 4, 5].indexOf(Number(s && s.step)) !== -1 && String(s.body || '').trim().length >= 10 && String(s.body || '').length <= 4000; });
  if (!okSteps) return reply({ ok: false, error: 'Each follow-up needs some text (10 to 4000 characters).' });
  log(me, 'edited the follow-up emails', '');
}
if (action === 'search') { const c = body.criteria || {}; log(me, 'started a search', [c.role, c.city].filter(Boolean).join(' · ')); }
if (action === 'approve') log(me, 'approved people for outreach', (Array.isArray(body.keys) ? body.keys.length : 0) + ' people');
body.user = pub(me);
return out(action, { body: body, user: pub(me), users_count: users.length });
"""

WRITES_JS = r"""
// Save what the gate decided (%s). Nothing to save: the branch stops here.
const w = ($('App Gate').first().json.writes || {})['%s'] || [];
return w.map(function(r){ return { json: r }; });
"""

LOAD_SEQ_SETTINGS_JS = r"""
// Sending settings from the app (Settings → Sending); the constants above are the defaults.
const _S = {};
try { $('Load Seq Settings').all().forEach(function(i){ const r = i.json || {}; if (r.key) _S[r.key] = String(r.value == null ? '' : r.value); }); } catch (e) {}
const _HM = /^([01]\d|2[0-3]):[0-5]\d$/;
const CAP = parseInt(_S.daily_cap, 10) > 0 ? parseInt(_S.daily_cap, 10) : DAILY_CAP;
const WIN_START = _HM.test(_S.window_start || '') ? _S.window_start : START;
const WIN_END = _HM.test(_S.window_end || '') ? _S.window_end : END;
const WEEKDAYS_ONLY = _S.weekdays_only !== 'false';
const SIG = _S.signature ? 'Best regards,<br/>' + _S.signature.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\n/g, '<br/>') + '</p>' : SIGNATURE;
"""

DASHBOARD_REPLY = r"""
// Home: this week, what needs attention, best sources, team activity, getting started.
""" + DEFAULT_TEMPLATES + r"""
const plain = function(h){ return String(h || '').replace(/<[^>]+>/g, ' ').replace(/&[a-z#0-9]+;/gi, ' ').replace(/\s+/g, ' ').trim(); };
const g = $('App Gate').first().json;
const all = function(n){ try { return $(n).all().map(function(i){ return i.json || {}; }); } catch (e) { return []; } };
const runs = all('Load Dash Runs').filter(function(r){ return r.run_id; });
const people = all('Load Dash People').filter(function(r){ return r.dedupe_key; });
const seq = all('Load Dash Sequences').filter(function(r){ return r.contact_key; });
const acts = all('Load Dash Activity').filter(function(r){ return r.at; });
const tpl = all('Load Dash Templates').filter(function(r){ return r.step && r.body; });
const sets = {}; all('Load Dash Settings').forEach(function(r){ if (r.key) sets[r.key] = r.value; });
const now = Date.now(), WEEK = 7 * 864e5;
const t = function(v){ const d = new Date(v); return isNaN(d) ? 0 : d.getTime(); };
const thisWeek = function(v){ return now - t(v) < WEEK; };
const pct = function(c){ return (Number(c.fit_score) || 0) * 10; };
const runsWeek = runs.filter(function(r){ return thisWeek(r.started_at); });
const peopleWeek = people.filter(function(p){ return runsWeek.some(function(r){ return String(r.run_id) === String(p.run_id); }); });
const replied = seq.filter(function(s){ return s.status === 'replied'; });
const contacted = seq.length;
// by source: found, strong, contacted, replied
const src = {};
const bySeq = {}; seq.forEach(function(s){ bySeq[s.contact_key] = s; });
people.forEach(function(p){ const k = p.source || 'Other'; const s = src[k] = src[k] || { source: k, found: 0, strong: 0, contacted: 0, replied: 0 };
  s.found++; if (pct(p) >= 80) s.strong++; const q = bySeq[p.dedupe_key]; if (q) { s.contacted++; if (q.status === 'replied') s.replied++; } });
const sources = Object.keys(src).map(function(k){ return src[k]; }).sort(function(a, b){ return (b.replied - a.replied) || (b.strong - a.strong) || (b.found - a.found); }).slice(0, 8);
// needs attention
const attention = [];
replied.slice(0, 5).forEach(function(s){ attention.push({ kind: 'reply', text: (s.first_name || 'Someone') + ' replied about ' + (s.role || 'a role'), when: s.last_sent_at || '' }); });
seq.filter(function(s){ return s.status === 'stopped' && /bounc/i.test(s.status_text || ''); }).slice(0, 3).forEach(function(s){ attention.push({ kind: 'bounce', text: 'Email to ' + (s.first_name || 'a candidate') + ' bounced. Find another address.', when: s.last_sent_at || '' }); });
runs.filter(function(r){ return r.status === 'done'; }).sort(function(a, b){ return t(b.started_at) - t(a.started_at); }).slice(0, 6).forEach(function(r){
  const ps = people.filter(function(p){ return String(p.run_id) === String(r.run_id); });
  const fresh = ps.filter(function(p){ return pct(p) >= 80 && !p.approved && !p.feedback && !bySeq[p.dedupe_key]; }).length;
  if (fresh) attention.push({ kind: 'review', run_id: String(r.run_id), text: fresh + ' strong match' + (fresh > 1 ? 'es' : '') + ' to review in ' + (r.role || 'a search') + (r.location ? ' · ' + r.location : ''), when: r.finished_at || r.started_at || '' });
});
const running = runs.filter(function(r){ return r.status === 'running' && now - t(r.started_at) < 15 * 60000; })
  .map(function(r){ return { run_id: String(r.run_id), role: r.role, location: r.location, stage: r.stage }; });
// connected services: last time each one produced people
const services = [];
const lastBy = {};
runs.filter(function(r){ return r.status === 'done' && r.by_source; }).forEach(function(r){
  String(r.by_source).split('·').forEach(function(p){ const m = p.trim().match(/^(.+?)\s+(\d+)$/); if (m && (!lastBy[m[1]] || t(r.started_at) > t(lastBy[m[1]]))) lastBy[m[1]] = r.started_at; });
});
const svc = function(name, uses, ok){ services.push({ name: name, uses: uses, last_ok: ok || '' }); };
const anyOf = function(list){ let best = ''; list.forEach(function(s){ if (lastBy[s] && t(lastBy[s]) > t(best)) best = lastBy[s]; }); return best; };
svc('Google search (Serper)', 'LinkedIn, Open Web and 15 profile sites', anyOf(['LinkedIn', 'Open Web', 'Topmate', 'ADPList', 'Codeforces', 'HackerEarth', 'Devfolio', 'LeetCode', 'Stack Overflow', 'Kaggle', 'Behance', 'Dribbble']));
svc('OpenAI', 'Reads briefs and scores every profile', runs.filter(function(r){ return r.status === 'done' && Number(r.count) > 0; }).map(function(r){ return r.started_at; }).sort().pop() || '');
svc('GitHub', 'Developer profiles and commit emails', lastBy.GitHub || '');
svc('Apollo', 'Work emails and titles', lastBy.Apollo || '');
svc('Gmail', 'Sends outreach and follow-ups', seq.map(function(s){ return s.last_sent_at; }).sort().pop() || '');
// getting started
const steps = [
  { key: 'search', text: 'Run your first search', done: runs.length > 0 },
  { key: 'vote', text: 'Give a 👍 or 👎 on a shortlist so the ranking learns', done: people.some(function(p){ return p.feedback; }) },
  { key: 'approve', text: 'Approve someone for outreach', done: contacted > 0 || people.some(function(p){ return p.approved === true || p.approved === 'true'; }) },
  { key: 'templates', text: 'Make the follow-up emails yours', done: tpl.some(function(r){ const d = DEFAULTS.find(function(x){ return x.step === Number(r.step); }); return d && plain(r.body) !== plain(d.body); }) },
  { key: 'signature', text: 'Set your email signature', done: !!sets.signature },
  { key: 'team', text: 'Invite a teammate', done: (g.users_count || 1) > 1 }
];
return [{ json: { response: { ok: true, user: g.user,
  week: { searches: runsWeek.length, people: peopleWeek.length, strong: peopleWeek.filter(function(p){ return pct(p) >= 80; }).length },
  totals: { searches: runs.length, people: people.length, contacted: contacted, replied: replied.length, active: seq.filter(function(s){ return s.status === 'active'; }).length,
    reply_rate: contacted ? Math.round(100 * replied.length / contacted) : 0 },
  attention: attention.slice(0, 8), running: running, sources: sources, services: services,
  activity: acts.sort(function(a, b){ return t(b.at) - t(a.at); }).slice(0, 12).map(function(a){ return { at: a.at, name: a.name, action: a.action, detail: a.detail }; }),
  checklist: steps } } }];
"""

APP_ROUTES_29 = APP_ROUTES_28 + ["dashboard"]


def apply_round29(nodes, conns):
    N = {n["name"]: n for n in nodes}
    new = []

    def link(a, b, idx=0, first=False):
        conns.setdefault(a, {"main": [[]]})
        while len(conns[a]["main"]) <= idx:
            conns[a]["main"].append([])
        c = {"node": b, "type": "main", "index": 0}
        if first:
            conns[a]["main"][idx].insert(0, c)
        else:
            conns[a]["main"][idx].append(c)

    def dt(name, op, table, pos, extra=None, **kw):
        p = {"resource": "row", "operation": op, "dataTableId": _dt_ref(table)}
        p.update(extra or {})
        n = _node(name, "n8n-nodes-base.dataTable", 1.1, pos, p, **kw)
        new.append(n)
        return n

    def code(name, js, pos, once=False):
        n = _node(name, "n8n-nodes-base.code", 2, pos, {"jsCode": js})
        if once:
            n["executeOnce"] = True
        new.append(n)
        return n

    def cond(k, v):
        return {"conditions": [{"keyName": k, "condition": "eq", "keyValue": v}]}

    # 1. the gate knows who you are
    api, gate = N["App API"], N["App Gate"]
    ax, ay = api["position"]
    gx, gy = gate["position"]
    rnd = _node("Fresh Random", "n8n-nodes-base.crypto", 1, [ax, ay + 180], {
        "action": "generate", "dataPropertyName": "rand", "encodingType": "hex", "stringLength": 64})
    new.append(rnd)
    nxt = conns["App API"]["main"][0]
    assert [c["node"] for c in nxt] == ["Ensure App Settings"], nxt
    conns["App API"]["main"][0] = [{"node": "Fresh Random", "type": "main", "index": 0}]
    link("Fresh Random", "Ensure App Settings")
    prev = conns["Load App Settings"]["main"][0]
    assert [c["node"] for c in prev] == ["App Gate"], prev
    e1 = _dt_ensure("Ensure App Users", "app_users", USER_COLUMNS, [gx - 220, gy + 180]); new.append(e1)
    e2 = _dt_ensure("Ensure App Sessions", "app_sessions", SESSION_COLUMNS, [gx - 220, gy + 340]); new.append(e2)
    e3 = _dt_ensure("Ensure App Activity", "app_activity", ACTIVITY_COLUMNS, [gx - 220, gy + 500]); new.append(e3)
    lu = dt("Load App Users", "get", "app_users", [gx, gy + 180], {"returnAll": True}, alwaysOutputData=True); lu["executeOnce"] = True
    ls = dt("Load App Sessions", "get", "app_sessions", [gx, gy + 340], {"returnAll": True}, alwaysOutputData=True); ls["executeOnce"] = True
    conns["Load App Settings"]["main"][0] = [{"node": "Ensure App Users", "type": "main", "index": 0}]
    link("Ensure App Users", "Ensure App Sessions"); link("Ensure App Sessions", "Ensure App Activity")
    link("Ensure App Activity", "Load App Users"); link("Load App Users", "Load App Sessions"); link("Load App Sessions", "App Gate")
    gate["parameters"]["jsCode"] = APP_GATE_29
    gate["executeOnce"] = True

    # 2. the gate's writes, saved before the request is answered (placed above App Route)
    wy = gy - 640
    for i, (kind, table, op, key) in enumerate([("users", "app_users", "upsert", "email"), ("sessions", "app_sessions", "upsert", "token"),
                                                ("activity", "app_activity", "insert", None), ("settings", "app_settings", "upsert", "key")]):
        nm = kind.capitalize() + " Writes"
        code(nm, WRITES_JS % (kind, kind), [gx + 220, wy + i * 120])
        extra = {"columns": _automap(), "options": {}}
        if key:
            extra.update({"matchType": "allConditions", "filters": cond(key, "={{ $json.%s }}" % key)})
        dt("Save " + kind.capitalize(), op, table, [gx + 440, wy + i * 120], extra, onError="continueRegularOutput")
        link(nm, "Save " + kind.capitalize())
    route_link = conns["App Gate"]["main"][0]
    conns["App Gate"]["main"][0] = [{"node": k + " Writes", "type": "main", "index": 0} for k in ["Users", "Sessions", "Activity", "Settings"]] + route_link

    # 3. dashboard route
    sw = N["App Route"]
    sw["parameters"]["numberOutputs"] = len(APP_ROUTES_29)
    sw["parameters"]["output"] = "={{ %s.indexOf($json.route) }}" % json.dumps(APP_ROUTES_29)
    while len(conns["App Route"]["main"]) < len(APP_ROUTES_29):
        conns["App Route"]["main"].append([])
    bx, by = N["App Ensure Templates 2"]["position"]
    by += 200
    loads = [("Load Dash Runs", "app_runs"), ("Load Dash People", "app_candidates"), ("Load Dash Sequences", SEQ_TABLE),
             ("Load Dash Activity", "app_activity"), ("Load Dash Templates", TPL_TABLE), ("Load Dash Settings", "app_settings")]
    for i, (nm, table) in enumerate(loads):
        n = dt(nm, "get", table, [bx + i * 220, by], {"returnAll": True}, alwaysOutputData=True, onError="continueRegularOutput")
        n["executeOnce"] = True
        if i:
            link(loads[i - 1][0], nm)
    code("Dashboard Reply", DASHBOARD_REPLY, [bx + len(loads) * 220, by], once=True)
    link(loads[-1][0], "Dashboard Reply"); link("Dashboard Reply", "App Reply")
    link("App Route", loads[0][0], APP_ROUTES_29.index("dashboard"))

    # 4. follow-ups use the app's sending settings
    lt = N["Load Templates"]; x, y = lt["position"]
    lss = dt("Load Seq Settings", "get", "app_settings", [x + 110, y + 160], {"returnAll": True}, alwaysOutputData=True, onError="continueRegularOutput")
    lss["executeOnce"] = True
    i = [c["node"] for c in conns["Load Templates"]["main"][0]].index("Load Sequences")
    conns["Load Templates"]["main"][0][i] = {"node": "Load Seq Settings", "type": "main", "index": 0}
    link("Load Seq Settings", "Load Sequences")
    p = N["Due Follow-ups"]["parameters"]
    marker = "// pick the follow-ups that are due now"
    assert marker in p["jsCode"]
    p["jsCode"] = p["jsCode"].replace(marker, LOAD_SEQ_SETTINGS_JS.lstrip() + marker, 1)
    for old, newv in [("if (now.weekday > 5 || hm < START || hm >= END) return [];", "if ((WEEKDAYS_ONLY && now.weekday > 5) || hm < WIN_START || hm >= WIN_END) return [];"),
                      ("let room = Math.max(0, DAILY_CAP - sentToday);", "let room = Math.max(0, CAP - sentToday);"),
                      ("optOut + '<p>' + SIGNATURE", "optOut + '<p>' + SIG")]:
        assert old in p["jsCode"], "round29 Due Follow-ups: " + old
        p["jsCode"] = p["jsCode"].replace(old, newv, 1)

    nodes.extend(new)
    return nodes, conns


# ======================================================================
# Round 30: the Board. Every person on a shortlist has one stage
# (found, shortlisted, approved, follow-up, replied, interview, hired,
# not a fit), an owner, a note and an optional "not now until" date. Each
# change is kept in a history. Stages that the pipeline already knows
# (approved, in follow-up, replied) are filled in automatically.
# The same person across roles is recognised (LinkedIn link, email, or name
# plus company), and the first-email sender never emails someone who was
# contacted for any role in the last 90 days (Settings can change the days).
# ======================================================================
STAGE_COLUMNS = [("run_id", "string"), ("dedupe_key", "string"), ("stage", "string"), ("owner", "string"), ("note", "string"),
                 ("snooze_until", "string"), ("updated_at", "date"), ("updated_by", "string")]
EVENT_COLUMNS = [("at", "date"), ("run_id", "string"), ("dedupe_key", "string"), ("person", "string"), ("kind", "string"),
                 ("from_stage", "string"), ("to_stage", "string"), ("detail", "string"), ("by", "string")]
STAGES = ["found", "shortlisted", "approved", "followup", "replied", "interview", "hired", "not_fit"]

PERSON_KEY_JS = r"""
// The same person across roles: any shared LinkedIn link, email, profile link,
// or full name + company counts as a match.
const personKeys = function(c){
  const ks = [];
  const norm = function(u){ return String(u || '').toLowerCase().replace(/^https?:\/\//, '').replace(/^[a-z]{2,3}\.linkedin\.com/, 'linkedin.com').replace(/^www\./, '').replace(/[?#].*$/, '').replace(/\/+$/, ''); };
  const li = norm(c.linkedin || (/linkedin\.com\/in\//i.test(c.profile_url || '') ? c.profile_url : ''));
  if (li.indexOf('linkedin.com/in/') === 0) ks.push('li:' + li.slice(16));
  const em = String(c.email || '').toLowerCase().trim();
  if (em.indexOf('@') > 0) ks.push('em:' + em);
  const pu = norm(c.profile_url);
  if (pu && pu.indexOf('linkedin.com/in/') !== 0 && /\/[^/]{3,}/.test(pu)) ks.push('url:' + pu);
  const nm = String(c.name || '').toLowerCase().replace(/[^a-z\s]/g, ' ').split(/\s+/).filter(Boolean);
  const co = String(c.company || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  if (nm.length >= 2 && co) ks.push('nc:' + nm.join(' ') + '@' + co);
  return ks;
};
"""

GATE_CARD_JS = r"""
// -- a card on the board: stage, owner, note, "not now until" --
const STAGES = %s;
if (action === 'update_card') {
  const c = body.card || {};
  const runId = String(c.run_id || ''), key = String(c.dedupe_key || '');
  if (!runId || !key) return reply({ ok: false, error: 'Which person?' });
  const stage = STAGES.indexOf(c.stage) !== -1 ? c.stage : 'found';
  const snooze = /^\d{4}-\d{2}-\d{2}$/.test(String(c.snooze_until || '')) ? String(c.snooze_until) : '';
  const row = { run_id: runId, dedupe_key: key, stage: stage, owner: String(c.owner || '').slice(0, 120), note: String(c.note || '').slice(0, 2000),
    snooze_until: snooze, updated_at: now.toISOString(), updated_by: me.name };
  W.stages.push(row);
  const ev = function(kind, from, to, detail){ W.events.push({ at: now.toISOString(), run_id: runId, dedupe_key: key, person: String(c.name || '').slice(0, 120),
    kind: kind, from_stage: from || '', to_stage: to || '', detail: String(detail || '').slice(0, 500), by: me.name }); };
  const prev = body.prev || {};
  if (prev.stage !== stage) ev('stage', prev.stage, stage, '');
  if ((prev.owner || '') !== row.owner) ev('owner', '', '', row.owner ? 'owner: ' + row.owner : 'owner removed');
  if ((prev.note || '') !== row.note && row.note) ev('note', '', '', row.note);
  if ((prev.snooze_until || '') !== snooze && snooze) ev('snooze', '', '', 'not now until ' + snooze);
  return reply({ ok: true, card: row });
}
"""

BOARD_REPLY = PERSON_KEY_JS + r"""
// One role as a board: every person with their stage, owner, note, history,
// and whether they appear in other roles or were contacted recently.
const g = $('App Gate').first().json;
const b = g.body || {};
const all = function(n){ try { return $(n).all().map(function(i){ return i.json || {}; }); } catch (e) { return []; } };
const runs = all('Load Board Runs').filter(function(r){ return r.run_id; }).sort(function(a, x){ return String(x.started_at).localeCompare(String(a.started_at)); });
const people = all('Load Board People').filter(function(c){ return c.dedupe_key && c.run_id; });
const stageRows = all('Load Board Stages').filter(function(s){ return s.dedupe_key; });
const events = all('Load Board Events').filter(function(e){ return e.at; });
const seqs = all('Load Board Sequences').filter(function(s){ return s.contact_key; });
const sets = {}; all('Load Board Settings').forEach(function(r){ if (r.key) sets[r.key] = r.value; });
const days = parseInt(sets.recontact_days, 10) > 0 ? parseInt(sets.recontact_days, 10) : 90;
const runId = String(b.run_id || (runs.find(function(r){ return r.status === 'done'; }) || runs[0] || {}).run_id || '');
const run = runs.find(function(r){ return String(r.run_id) === runId; }) || {};
const RANK = { found: 0, shortlisted: 1, approved: 2, followup: 3, replied: 4, interview: 5, hired: 6, not_fit: -1 };
const t = function(v){ const d = new Date(v); return isNaN(d) ? 0 : d.getTime(); };
const now = Date.now();
const seqByKey = {}, seqByEmail = {};
seqs.forEach(function(s){ seqByKey[s.contact_key] = s; const e = String(s.email || '').toLowerCase(); if (e && (!seqByEmail[e] || t(s.started_at) > t(seqByEmail[e].started_at))) seqByEmail[e] = s; });
const stageOf = {}; stageRows.forEach(function(s){ stageOf[s.run_id + '|' + s.dedupe_key] = s; });
const runName = {}; runs.forEach(function(r){ runName[r.run_id] = [r.role, r.location].filter(Boolean).join(' · '); });
const byPerson = {};
people.forEach(function(c){ personKeys(c).forEach(function(k){ (byPerson[k] = byPerson[k] || []).push(c); }); });
const seen = {};
const cards = people.filter(function(c){ if (String(c.run_id) !== runId || seen[c.dedupe_key]) return false; seen[c.dedupe_key] = 1; return true; }).map(function(c){
  const s = seqByKey[c.dedupe_key];
  let auto = 'found', flag = '';
  if (s) { auto = s.status === 'replied' ? 'replied' : 'followup'; if (s.status === 'stopped') flag = /bounc/i.test(s.status_text || '') ? 'bounced' : 'stopped'; }
  else if (c.approved === true || c.approved === 'true') auto = 'approved';
  else if (c.feedback === 'up') auto = 'shortlisted';
  else if (c.feedback === 'down') auto = 'not_fit';
  const m = stageOf[runId + '|' + c.dedupe_key] || {};
  let stage = auto;
  if (m.stage === 'not_fit' || m.stage === 'hired' || m.stage === 'interview') stage = m.stage;
  else if (m.stage && RANK[m.stage] > RANK[auto]) stage = m.stage;
  const oseen = {};
  const others = [];
  personKeys(c).forEach(function(k){ (byPerson[k] || []).forEach(function(o){ if (String(o.run_id) !== runId && !oseen[o.run_id]) { oseen[o.run_id] = 1; others.push(o); } }); });
  const em = String(c.email || '').toLowerCase();
  const last = em && seqByEmail[em] && seqByEmail[em].contact_key !== c.dedupe_key ? seqByEmail[em] : null;
  const recent = last && now - t(last.started_at) < days * 864e5;
  const o = { dedupe_key: c.dedupe_key, name: c.name, title: c.title, company: c.company, location: c.location, location_review: c.location_review,
    fit_score: c.fit_score, confidence: c.confidence, experience_check: c.experience_check, summary: c.summary, key_skills: c.key_skills,
    email: c.email, contact_source: c.contact_source, source: c.source, profile_url: c.profile_url, linkedin: c.linkedin, github: c.github,
    resume_link: c.resume_link, feedback: c.feedback || '', approved: c.approved === true || c.approved === 'true',
    stage: stage, auto_stage: auto, flag: flag, owner: m.owner || '', note: m.note || '', snooze_until: m.snooze_until || '',
    stage_since: m.updated_at || (s && s.last_sent_at) || '', sequence: s ? { step: s.step, status: s.status, status_text: s.status_text, next_due_at: s.next_due_at, last_sent_at: s.last_sent_at } : null,
    other_roles: others.map(function(x){ return { run_id: x.run_id, role: runName[x.run_id] || 'another role' }; }).slice(0, 3),
    contacted: last ? { role: last.role, at: last.started_at, status: last.status, recent: !!recent } : null };
  o.overdue = stage === 'shortlisted' && !o.approved && o.stage_since && now - t(o.stage_since) > 2 * 864e5;
  o.snoozed = o.snooze_until && t(o.snooze_until) > now;
  return o;
});
const hist = events.filter(function(e){ return String(e.run_id) === runId; }).sort(function(a, x){ return t(x.at) - t(a.at); }).slice(0, 60)
  .map(function(e){ return { at: e.at, dedupe_key: e.dedupe_key, person: e.person, kind: e.kind, from_stage: e.from_stage, to_stage: e.to_stage, detail: e.detail, by: e.by }; });
const roles = runs.filter(function(r){ return r.status === 'done' || r.status === 'running'; }).slice(0, 30).map(function(r){
  return { run_id: String(r.run_id), role: r.role, location: r.location, status: r.status, count: r.count, started_at: r.started_at }; });
let saved = {}; try { saved = JSON.parse(sets['run:' + runId] || '{}'); } catch (e) { saved = {}; }
return [{ json: { response: { ok: true, run: run, roles: roles, cards: cards, history: hist, recontact_days: days, team: g.team || [],
  criteria: saved.criteria || null, brief: saved.brief || '' } } }];
"""

FILTER_RECENT_JS = r"""
// Never email the same person twice: skip anyone already emailed for any role in
// the last N days (Settings → Sending, default 90), and send one email per address per run.
const sets = {};
try { $('Load Contact Settings').all().forEach(function(i){ const r = i.json || {}; if (r.key) sets[r.key] = r.value; }); } catch (e) {}
const days = parseInt(sets.recontact_days, 10) > 0 ? parseInt(sets.recontact_days, 10) : 90;
const recent = {};
try { $('Load Recent Contacts').all().forEach(function(i){ const s = i.json || {}; const e = String(s.email || '').toLowerCase().trim();
  if (e && Date.now() - new Date(s.started_at || 0).getTime() < days * 864e5) recent[e] = true; }); } catch (e) {}
const batch = {};
const ok = function(r){ const e = String(r['Email'] || '').toLowerCase().trim(); if (!e || recent[e] || batch[e]) return false; batch[e] = true; return true; };
"""


def apply_round30(nodes, conns):
    N = {n["name"]: n for n in nodes}
    new = []

    def R(name, old, newv):
        p = N[name]["parameters"]
        assert old in p["jsCode"], "round30 %s: anchor not found: %s" % (name, old[:70])
        p["jsCode"] = p["jsCode"].replace(old, newv, 1)

    def link(a, b, idx=0):
        conns.setdefault(a, {"main": [[]]})
        while len(conns[a]["main"]) <= idx:
            conns[a]["main"].append([])
        conns[a]["main"][idx].append({"node": b, "type": "main", "index": 0})

    def dt(name, op, table, pos, extra=None, **kw):
        p = {"resource": "row", "operation": op, "dataTableId": _dt_ref(table)}
        p.update(extra or {})
        n = _node(name, "n8n-nodes-base.dataTable", 1.1, pos, p, **kw)
        new.append(n)
        return n

    def code(name, js, pos, once=False):
        n = _node(name, "n8n-nodes-base.code", 2, pos, {"jsCode": js})
        if once:
            n["executeOnce"] = True
        new.append(n)
        return n

    # 1. two more tables, ensured with the others
    gx, gy = N["App Gate"]["position"]
    e1 = _dt_ensure("Ensure App Stages", "app_stages", STAGE_COLUMNS, [gx - 220, gy + 660]); new.append(e1)
    e2 = _dt_ensure("Ensure App Events", "app_events", EVENT_COLUMNS, [gx - 220, gy + 820]); new.append(e2)
    nxt = conns["Ensure App Activity"]["main"][0]
    assert [c["node"] for c in nxt] == ["Load App Users"], nxt
    conns["Ensure App Activity"]["main"][0] = [{"node": "Ensure App Stages", "type": "main", "index": 0}]
    link("Ensure App Stages", "Ensure App Events"); link("Ensure App Events", "Load App Users")

    # 2. the gate saves card changes and their history
    R("App Gate", "const W = { users: [], sessions: [], activity: [], settings: [] };",
      "const W = { users: [], sessions: [], activity: [], settings: [], stages: [], events: [] };")
    R("App Gate", "// -- routes with their own steps --", (GATE_CARD_JS % json.dumps(STAGES)).lstrip() + "\n// -- routes with their own steps --")
    R("App Gate", "'templates', 'save_templates', 'dashboard'];", "'templates', 'save_templates', 'dashboard', 'board'];")
    # each search keeps its full brief, so the board can show and re-run it
    R("App Gate", "if (action === 'search') { const c = body.criteria || {}; log(me, 'started a search', [c.role, c.city].filter(Boolean).join(' · ')); }",
      "if (action === 'search') { const c = body.criteria || {}; log(me, 'started a search', [c.role, c.city].filter(Boolean).join(' · '));\n"
      "  W.settings.push({ key: 'run:' + $execution.id, value: JSON.stringify({ criteria: c, brief: String(body.brief || '').slice(0, 4000), by: me.name }).slice(0, 12000) }); }")
    R("App Gate", "return out(action, { body: body, user: pub(me), users_count: users.length });",
      "return out(action, { body: body, user: pub(me), users_count: users.length, team: users.filter(function(u){ return u.status === 'active'; }).map(function(u){ return u.name; }) });")
    R("App Gate", "const SETTING_KEYS = ['signature', 'window_start', 'window_end', 'daily_cap', 'weekdays_only'];",
      "const SETTING_KEYS = ['signature', 'window_start', 'window_end', 'daily_cap', 'weekdays_only', 'recontact_days'];")
    R("App Gate", "daily_cap: Number(settings.daily_cap) || 50, weekdays_only: settings.weekdays_only !== 'false' }; };",
      "daily_cap: Number(settings.daily_cap) || 50, weekdays_only: settings.weekdays_only !== 'false', recontact_days: Number(settings.recontact_days) || 90 }; };")
    R("App Gate", "weekdays_only: s.weekdays_only === false ? 'false' : 'true' };",
      "weekdays_only: s.weekdays_only === false ? 'false' : 'true', recontact_days: String(Math.max(0, Math.min(365, parseInt(s.recontact_days, 10) || 90))) };")
    R("App Gate", "{ daily_cap: cap, weekdays_only: vals.weekdays_only !== 'false' }", "{ daily_cap: cap, weekdays_only: vals.weekdays_only !== 'false', recontact_days: Number(vals.recontact_days) }")
    wy = gy - 240
    for i, (kind, table, op, keys) in enumerate([("stages", "app_stages", "upsert", ["run_id", "dedupe_key"]), ("events", "app_events", "insert", [])]):
        nm = kind.capitalize() + " Writes"
        code(nm, WRITES_JS % (kind, kind), [gx - 200, wy + i * 120])
        extra = {"columns": _automap(), "options": {}}
        if keys:
            extra.update({"matchType": "allConditions", "filters": {"conditions": [{"keyName": k, "condition": "eq", "keyValue": "={{ $json.%s }}" % k} for k in keys]}})
        dt("Save " + kind.capitalize(), op, table, [gx + 20, wy + i * 120], extra, onError="continueRegularOutput")
        link(nm, "Save " + kind.capitalize())
    gl = conns["App Gate"]["main"][0]
    i = [c["node"] for c in gl].index("App Route")
    gl[i:i] = [{"node": "Stages Writes", "type": "main", "index": 0}, {"node": "Events Writes", "type": "main", "index": 0}]

    # 3. the board route
    routes = APP_ROUTES_29 + ["board"]
    sw = N["App Route"]
    sw["parameters"]["numberOutputs"] = len(routes)
    sw["parameters"]["output"] = "={{ %s.indexOf($json.route) }}" % json.dumps(routes)
    while len(conns["App Route"]["main"]) < len(routes):
        conns["App Route"]["main"].append([])
    bx, by = N["Load Dash Runs"]["position"]
    by += 200
    loads = [("Load Board Runs", "app_runs"), ("Load Board People", "app_candidates"), ("Load Board Stages", "app_stages"),
             ("Load Board Events", "app_events"), ("Load Board Sequences", SEQ_TABLE), ("Load Board Settings", "app_settings")]
    for i, (nm, table) in enumerate(loads):
        n = dt(nm, "get", table, [bx + i * 220, by], {"returnAll": True}, alwaysOutputData=True, onError="continueRegularOutput")
        n["executeOnce"] = True
        if i:
            link(loads[i - 1][0], nm)
    code("Board Reply", BOARD_REPLY, [bx + len(loads) * 220, by], once=True)
    link(loads[-1][0], "Board Reply"); link("Board Reply", "App Reply")
    link("App Route", loads[0][0], routes.index("board"))

    # 4. never email the same person twice across roles
    os_ = N["Outreach Schedule"]; ox, oy = os_["position"]
    rc = dt("Load Recent Contacts", "get", SEQ_TABLE, [ox - 400, oy - 40], {"returnAll": True}, alwaysOutputData=True, onError="continueRegularOutput")
    rc["executeOnce"] = True
    cs = dt("Load Contact Settings", "get", "app_settings", [ox - 180, oy - 40], {"returnAll": True}, alwaysOutputData=True, onError="continueRegularOutput")
    cs["executeOnce"] = True
    conns["Outreach Schedule"]["main"][0].insert(0, {"node": "Load Recent Contacts", "type": "main", "index": 0})
    link("Load Recent Contacts", "Load Contact Settings")
    R("Filter Outreach Queue", "return $input.all().filter(function(i){", FILTER_RECENT_JS.lstrip() + "return $input.all().filter(function(i){")
    R("Filter Outreach Queue", "  return ready && notContacted && hasEmail && notLow;\n});", "  return ready && notContacted && hasEmail && notLow && ok(r);\n});")

    nodes.extend(new)
    return nodes, conns, routes


# ======================================================================
# Round 31: the Board's smart layer.
# - Replies: read the candidate's reply, draft an answer with 3 free slots
#   from Google Calendar (when connected), and send it from Gmail in the same
#   thread. Nothing is sent until the recruiter presses Send.
# - Interview kit: 5 questions and a scorecard from the brief and the person.
# - Teammates' past companies (warm connections), who else is on this board,
#   team reply rate, and when replies usually arrive.
# - Optional open tracking (Settings → Sending): a 1x1 image in each email.
#   Off by default; it only works when n8n is reachable from the internet.
# ======================================================================
OPEN_COLUMNS = [("contact_key", "string"), ("step", "number"), ("at", "date")]

GATE_R31_JS = r"""
// -- your profile (past companies, for warm connections) --
if (action === 'save_profile') {
  const cos = String(body.companies || '').split(',').map(function(s){ return s.trim(); }).filter(Boolean).slice(0, 12);
  W.settings.push({ key: 'past:' + me.email, value: JSON.stringify({ name: me.name, companies: cos }) });
  return reply({ ok: true, companies: cos });
}
if (action === 'send_reply') {
  const txt = String(body.text || '').trim();
  if (!body.message_id || txt.length < 2 || txt.length > 5000) return reply({ ok: false, error: 'Write a reply first.' });
  W.events.push({ at: now.toISOString(), run_id: String(body.run_id || ''), dedupe_key: String(body.dedupe_key || ''), person: String(body.name || '').slice(0, 120),
    kind: 'email', from_stage: '', to_stage: '', detail: 'replied: ' + txt.slice(0, 160), by: me.name });
}
if (action === 'board') W.settings.push({ key: 'seen:' + me.email, value: JSON.stringify({ name: me.name, run: String(body.run_id || ''), at: now.toISOString() }) });
"""

THREAD_URL_FULL = "={{ 'https://gmail.googleapis.com/gmail/v1/users/me/threads/' + $('App Gate').first().json.body.thread_id + '?format=full' }}"

DRAFT_PROMPT = r"""
// The candidate's latest reply, three free slots, and a prompt for a short answer.
const g = $('App Gate').first().json, b = g.body || {};
const th = $('App Thread').first().json || {};
const msgs = Array.isArray(th.messages) ? th.messages : [];
const hdr = function(m, n){ const h = ((m.payload || {}).headers || []).find(function(x){ return String(x.name).toLowerCase() === n; }); return h ? String(h.value) : ''; };
const dec = function(s){ try { return Buffer.from(String(s).replace(/-/g, '+').replace(/_/g, '/'), 'base64').toString('utf8'); } catch (e) { return ''; } };
const textOf = function(p){
  if (!p) return '';
  if (p.mimeType === 'text/plain' && p.body && p.body.data) return dec(p.body.data);
  let out = ''; (p.parts || []).forEach(function(x){ if (!out) out = textOf(x); });
  if (!out && p.mimeType === 'text/html' && p.body && p.body.data) out = dec(p.body.data).replace(/<[^>]+>/g, ' ');
  return out;
};
const mine = (msgs[0] ? hdr(msgs[0], 'from') : '').toLowerCase().replace(/.*</, '').replace(/>.*/, '');
const theirs = msgs.filter(function(m){ return hdr(m, 'from').toLowerCase().indexOf(mine) === -1; });
const last = theirs[theirs.length - 1] || null;
// strip the quoted earlier emails
const clean = function(t){ return String(t || '').split(/\r?\n(On .{5,120} wrote:|-----Original Message-----|From: )/)[0].replace(/\n{3,}/g, '\n\n').trim().slice(0, 3000); };
const lastText = last ? clean(textOf(last.payload) || last.snippet) : '';
// free slots: next 5 weekdays, 10:00-17:30 IST, 45 minutes, not busy
const fb = $('App FreeBusy').first().json || {};
const busy = (((fb.calendars || {}).primary || {}).busy || []).map(function(x){ return [new Date(x.start).getTime(), new Date(x.end).getTime()]; });
const calendar = !!(fb.calendars && fb.calendars.primary && !fb.calendars.primary.errors);
const slots = [];
if (calendar) {
  let d = DateTime.now().setZone('Asia/Kolkata').plus({ days: 1 }).startOf('day');
  for (let i = 0; i < 10 && slots.length < 3; i++, d = d.plus({ days: 1 })) {
    if (d.weekday > 5) continue;
    for (let h = 10 * 60; h <= 17 * 60; h += 90) {
      const s = d.plus({ minutes: h }), e = s.plus({ minutes: 45 });
      const free = !busy.some(function(x){ return x[0] < e.toMillis() && x[1] > s.toMillis(); });
      if (free) { slots.push({ start: s.toISO(), label: s.toFormat('ccc d LLL, HH:mm') }); break; }
    }
  }
}
const ctx = b.context || {};
const system = 'You help a recruiter answer a candidate who replied to an outreach email. Return ONLY JSON: {"sentiment": "keen" | "question" | "not_now" | "no", "summary": one short line, "reply": the email body}. ' +
  'The reply: warm, brief (under 90 words), plain text, no subject, no signature. If they are keen or asked a question, answer what you can from the role details and offer the time slots given (or ask for times if none). ' +
  'If they said not now, thank them and say you will check back in a few months. If they said no, thank them politely. Never invent salary, team size or facts not given.';
const prompt = 'ROLE: ' + [ctx.role, ctx.location].filter(Boolean).join(', ') + (ctx.must ? '\nMUST-HAVES: ' + ctx.must : '') + (ctx.brief ? '\nBRIEF: ' + String(ctx.brief).slice(0, 1200) : '') +
  '\nCANDIDATE: ' + (b.name || '') + '\nFREE SLOTS: ' + (slots.length ? slots.map(function(s){ return s.label + ' IST'; }).join('; ') : 'none - ask for their availability') +
  '\n\nTHEIR LATEST REPLY:\n-----\n' + (lastText || '(could not read the reply)') + '\n-----';
return [{ json: { system: system, prompt: prompt, last: last ? { id: last.id, from: hdr(last, 'from'), date: hdr(last, 'date'), text: lastText } : null,
  thread_ok: msgs.length > 0, slots: slots, calendar: calendar } }];
"""

DRAFT_REPLY = r"""
const p = $('Draft Prompt').first().json;
const j = $json || {};
let raw = ''; try { raw = j.output[0].content[0].text; } catch (e) {}
let d = {}; try { d = JSON.parse(String(raw).replace(/^```(json)?|```$/g, '').trim()); } catch (e) {}
if (!p.thread_ok) return [{ json: { response: { ok: false, error: 'Could not open this Gmail thread. Is Gmail still connected in n8n?' } } }];
return [{ json: { response: { ok: true, message: p.last, slots: p.slots, calendar: p.calendar,
  sentiment: ['keen', 'question', 'not_now', 'no'].indexOf(d.sentiment) !== -1 ? d.sentiment : '', summary: String(d.summary || '').slice(0, 200),
  draft: String(d.reply || '').slice(0, 2000), reply_to: p.last ? p.last.id : '' } } }];
"""

REPLY_BODY = r"""
const b = $('App Gate').first().json.body || {};
const esc = function(s){ return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); };
const html = String(b.text || '').trim().split(/\n\s*\n/).map(function(p){ return '<p>' + esc(p).replace(/\n/g, '<br/>') + '</p>'; }).join('');
return [{ json: { message_id: String(b.message_id), html: html } }];
"""

REPLY_SENT = r"""
const j = $json || {};
if (j.error || !j.id) return [{ json: { response: { ok: false, error: 'Gmail did not send it: ' + String((j.error && (j.error.message || j.error)) || 'unknown error').slice(0, 200) } } }];
return [{ json: { response: { ok: true, id: j.id } } }];
"""

KIT_PROMPT = r"""
// An interview kit for one person: questions that test the brief's must-haves.
const b = $('App Gate').first().json.body || {};
const c = b.context || {};
const system = 'You are an expert in structured, evidence-based interviewing. Return ONLY JSON: {"questions": [{"q": question, "listen_for": what a strong answer shows, "competency": short name}], "scorecard": [{"competency": name, "weight": 1-3, "strong": one line, "weak": one line}]}. ' +
  'Write exactly 5 behavioural or work-sample questions that test the must-haves and gaps for THIS person. Never ask about age, family, religion, caste, health or any protected attribute.';
const prompt = 'ROLE: ' + [c.role, c.location].filter(Boolean).join(', ') + '\nMUST-HAVES: ' + (c.must || '') + '\nNICE: ' + (c.nice || '') +
  '\nCANDIDATE: ' + (b.name || '') + ' - ' + (c.title || '') + '\nWHAT WE KNOW: ' + String(c.summary || '').slice(0, 1200) + '\nNOT YET SEEN ON PROFILE: ' + (c.gaps || 'none');
return [{ json: { system: system, prompt: prompt } }];
"""

KIT_REPLY = r"""
const j = $json || {};
let raw = ''; try { raw = j.output[0].content[0].text; } catch (e) {}
let d = {}; try { d = JSON.parse(String(raw).replace(/^```(json)?|```$/g, '').trim()); } catch (e) {}
const qs = (Array.isArray(d.questions) ? d.questions : []).slice(0, 6).map(function(q){ return { q: String(q.q || '').slice(0, 300), listen_for: String(q.listen_for || '').slice(0, 300), competency: String(q.competency || '').slice(0, 60) }; }).filter(function(q){ return q.q; });
const sc = (Array.isArray(d.scorecard) ? d.scorecard : []).slice(0, 6).map(function(s){ return { competency: String(s.competency || '').slice(0, 60), weight: Math.max(1, Math.min(3, parseInt(s.weight, 10) || 1)), strong: String(s.strong || '').slice(0, 200), weak: String(s.weak || '').slice(0, 200) }; });
if (!qs.length) return [{ json: { response: { ok: false, error: 'Could not write the questions. Try again.' } } }];
return [{ json: { response: { ok: true, questions: qs, scorecard: sc } } }];
"""

OPEN_ROW = r"""
const q = ($('Open Pixel').first().json || {}).query || {};
const k = String(q.k || '').slice(0, 200);
if (!k) return [];
return [{ json: { contact_key: k, step: parseInt(q.s, 10) || 1, at: new Date().toISOString() } }];
"""

BOARD_R31_JS = r"""
// opens, reply timing, team reply rate, who else is here, warm connections
const opens = {};
all('Load Board Opens').forEach(function(o){ if (!o.contact_key) return; const x = opens[o.contact_key] = opens[o.contact_key] || { count: 0, last: '' }; x.count++; if (String(o.at) > x.last) x.last = String(o.at); });
const repliedSeqs = seqs.filter(function(s){ return s.status === 'replied'; });
const teamRate = seqs.length ? Math.round(100 * repliedSeqs.length / seqs.length) : 0;
const hours = {};
repliedSeqs.forEach(function(s){ const d = DateTime.fromISO(String(s.updatedAt || '')); if (!d.isValid) return; const z = d.setZone('Asia/Kolkata'); const k = z.toFormat('ccc') + '|' + Math.floor(z.hour / 2) * 2; hours[k] = (hours[k] || 0) + 1; });
const topH = Object.keys(hours).sort(function(a, x){ return hours[x] - hours[a]; })[0];
const bestTime = topH && hours[topH] >= 2 ? topH.split('|')[0] + ' ' + ('0' + topH.split('|')[1]).slice(-2) + ':00–' + ('0' + (+topH.split('|')[1] + 2)).slice(-2) + ':00' : '';
const meEmail = ((g.user || {}).email) || '';
const viewers = []; const teamCos = [];
Object.keys(sets).forEach(function(k){
  let v = {}; try { v = JSON.parse(sets[k] || '{}'); } catch (e) { v = {}; }
  if (k.indexOf('seen:') === 0 && k !== 'seen:' + meEmail && v.run === runId && Date.now() - t(v.at) < 90000) viewers.push(v.name);
  if (k.indexOf('past:') === 0) (v.companies || []).forEach(function(co){ teamCos.push({ name: v.name, company: co, key: String(co).toLowerCase().replace(/[^a-z0-9]/g, '') }); });
});
cards.forEach(function(o){
  const s = seqByKey[o.dedupe_key];
  if (s) { o.sequence.thread_id = s.thread_id; o.sequence.message_id = s.message_id; if (s.status === 'replied') o.replied_at = s.updatedAt || ''; }
  if (opens[o.dedupe_key]) o.opens = opens[o.dedupe_key];
  const ck = String(o.company || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  const w = ck && teamCos.find(function(x){ return x.key && (x.key === ck || (ck.length > 4 && x.key.indexOf(ck) === 0) || (x.key.length > 4 && ck.indexOf(x.key) === 0)); });
  if (w) o.warm = w.name + ' worked at ' + w.company;
  if (/open to (work|new|opportunit)|#opentowork|looking for (a )?new|actively (looking|exploring)/i.test(String(o.summary || '') + ' ' + String(o.title || ''))) o.open_to_work = true;
});
"""


def apply_round31(nodes, conns, routes_in):
    N = {n["name"]: n for n in nodes}
    oai = json.loads(json.dumps(N["Message a model"]["credentials"]))
    gcred = json.loads(json.dumps(N["Send Outreach Email"]["credentials"]))
    new = []

    def R(name, old, newv):
        p = N[name]["parameters"]
        assert old in p["jsCode"], "round31 %s: anchor not found: %s" % (name, old[:70])
        p["jsCode"] = p["jsCode"].replace(old, newv, 1)

    def link(a, b, idx=0):
        conns.setdefault(a, {"main": [[]]})
        while len(conns[a]["main"]) <= idx:
            conns[a]["main"].append([])
        conns[a]["main"][idx].append({"node": b, "type": "main", "index": 0})

    def code(name, js, pos, once=True):
        n = _node(name, "n8n-nodes-base.code", 2, pos, {"jsCode": js})
        if once:
            n["executeOnce"] = True
        new.append(n)
        return n

    def ai(name, pos):
        n = _node(name, "n8n-nodes-base.httpRequest", 4.2, pos, {
            "method": "POST", "url": "https://api.openai.com/v1/responses", "authentication": "predefinedCredentialType",
            "nodeCredentialType": "openAiApi", "sendBody": True, "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify({ model: 'gpt-4o-mini', temperature: 0.3, text: { format: { type: 'json_object' } }, input: [{ role: 'system', content: $json.system }, { role: 'user', content: $json.prompt }] }) }}",
            "options": {"timeout": 60000}}, credentials=json.loads(json.dumps(oai)), onError="continueRegularOutput")
        new.append(n)
        return n

    # 1. gate: profile, presence, reply logging, new routes, tracking settings
    R("App Gate", "// -- routes with their own steps --", GATE_R31_JS.lstrip() + "\n// -- routes with their own steps --")
    R("App Gate", "'dashboard', 'board'];", "'dashboard', 'board', 'thread', 'send_reply', 'kit'];")
    R("App Gate", "if (action === 'ping' || action === 'me') return reply({ ok: true, user: pub(me) });",
      "if (action === 'ping' || action === 'me') { let past = {}; try { past = JSON.parse(settings['past:' + me.email] || '{}'); } catch (e) {} return reply({ ok: true, user: Object.assign(pub(me), { companies: past.companies || [] }) }); }")
    R("App Gate", "'weekdays_only', 'recontact_days'];", "'weekdays_only', 'recontact_days', 'open_tracking', 'public_url'];")
    R("App Gate", "recontact_days: Number(settings.recontact_days) || 90 }; };",
      "recontact_days: Number(settings.recontact_days) || 90, open_tracking: settings.open_tracking === 'true', public_url: settings.public_url || '' }; };")
    R("App Gate", "recontact_days: String(Math.max(0, Math.min(365, parseInt(s.recontact_days, 10) || 90))) };",
      "recontact_days: String(Math.max(0, Math.min(365, parseInt(s.recontact_days, 10) || 90))), open_tracking: s.open_tracking === true ? 'true' : 'false',\n"
      "    public_url: /^https:\\/\\/[^\\s]+$/.test(String(s.public_url || '').trim()) ? String(s.public_url).trim().replace(/\\/+$/, '') : '' };\n"
      "  if (vals.open_tracking === 'true' && !vals.public_url) return reply({ ok: false, error: 'Open tracking needs the public https address of your n8n.' });")
    R("App Gate", "recontact_days: Number(vals.recontact_days) }", "recontact_days: Number(vals.recontact_days), open_tracking: vals.open_tracking === 'true', public_url: vals.public_url }")

    routes = routes_in + ["thread", "send_reply", "kit"]
    sw = N["App Route"]
    sw["parameters"]["numberOutputs"] = len(routes)
    sw["parameters"]["output"] = "={{ %s.indexOf($json.route) }}" % json.dumps(routes)
    while len(conns["App Route"]["main"]) < len(routes):
        conns["App Route"]["main"].append([])
    bx, by = N["Load Board Runs"]["position"]

    # 2. thread → free slots → AI draft
    y = by + 200
    th = _node("App Thread", "n8n-nodes-base.httpRequest", 4.2, [bx, y], {"url": THREAD_URL_FULL, "authentication": "predefinedCredentialType",
        "nodeCredentialType": "gmailOAuth2", "options": {}}, credentials=json.loads(json.dumps(gcred)), onError="continueRegularOutput")
    new.append(th)
    fb = _node("App FreeBusy", "n8n-nodes-base.httpRequest", 4.2, [bx + 220, y], {"method": "POST", "url": "https://www.googleapis.com/calendar/v3/freeBusy",
        "authentication": "predefinedCredentialType", "nodeCredentialType": "googleCalendarOAuth2Api", "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ timeMin: $now.toISO(), timeMax: $now.plus({ days: 10 }).toISO(), timeZone: 'Asia/Kolkata', items: [{ id: 'primary' }] }) }}",
        "options": {"timeout": 20000}}, onError="continueRegularOutput")
    new.append(fb)
    code("Draft Prompt", DRAFT_PROMPT, [bx + 440, y])
    ai("Draft Agent", [bx + 660, y])
    code("Draft Reply", DRAFT_REPLY, [bx + 880, y])
    link("App Route", "App Thread", routes.index("thread")); link("App Thread", "App FreeBusy"); link("App FreeBusy", "Draft Prompt")
    link("Draft Prompt", "Draft Agent"); link("Draft Agent", "Draft Reply"); link("Draft Reply", "App Reply")

    # 3. send the reply in the same Gmail thread
    y = by + 400
    code("Reply Body", REPLY_BODY, [bx, y])
    gs = _node("Send Candidate Reply", "n8n-nodes-base.gmail", N["Send Follow-up"]["typeVersion"], [bx + 220, y], {
        "resource": "message", "operation": "reply", "messageId": "={{ $json.message_id }}", "emailType": "html", "message": "={{ $json.html }}",
        "options": {"appendAttribution": False, "replyToSenderOnly": True}}, credentials=json.loads(json.dumps(gcred)), onError="continueRegularOutput")
    new.append(gs)
    code("Reply Sent", REPLY_SENT, [bx + 440, y])
    link("App Route", "Reply Body", routes.index("send_reply")); link("Reply Body", "Send Candidate Reply"); link("Send Candidate Reply", "Reply Sent"); link("Reply Sent", "App Reply")

    # 4. interview kit
    y = by + 600
    code("Kit Prompt", KIT_PROMPT, [bx, y]); ai("Kit Agent", [bx + 220, y]); code("Kit Reply", KIT_REPLY, [bx + 440, y])
    link("App Route", "Kit Prompt", routes.index("kit")); link("Kit Prompt", "Kit Agent"); link("Kit Agent", "Kit Reply"); link("Kit Reply", "App Reply")

    # 5. open tracking pixel (only used when switched on)
    px, py = N["App API"]["position"]
    hook = _node("Open Pixel", "n8n-nodes-base.webhook", 2, [px - 900, py + 600],
                 {"httpMethod": "GET", "path": "sourcing-open", "responseMode": "onReceived", "options": {}},
                 webhookId="7c1e2a40-8b3d-4f6e-9a12-5d0c3b7e9f03")
    new.append(hook)
    eo = _dt_ensure("Ensure App Opens", "app_opens", OPEN_COLUMNS, [px - 680, py + 600]); new.append(eo)
    code("Open Row", OPEN_ROW, [px - 460, py + 600])
    so = _node("Save Open", "n8n-nodes-base.dataTable", 1.1, [px - 240, py + 600], {"resource": "row", "operation": "insert",
        "dataTableId": _dt_ref("app_opens"), "columns": _automap(), "options": {}}, onError="continueRegularOutput")
    new.append(so)
    link("Open Pixel", "Ensure App Opens"); link("Ensure App Opens", "Open Row"); link("Open Row", "Save Open")

    # 6. pixels in emails, when switched on
    R("Due Follow-ups", "optOut + '<p>' + SIG",
      "optOut + '<p>' + SIG + (_S.open_tracking === 'true' && _S.public_url ? '<img src=\"' + _S.public_url + '/webhook/sourcing-open?k=' + encodeURIComponent(r.contact_key) + '&s=' + next + '\" width=\"1\" height=\"1\" alt=\"\">' : '')")
    R("Filter Outreach Queue", "return $input.all().filter(function(i){",
      "const pixel = function(r){ return sets.open_tracking === 'true' && sets.public_url ? '<img src=\"' + String(sets.public_url).replace(/\\/+$/, '') + '/webhook/sourcing-open?k=' + encodeURIComponent(String(r['Dedupe Key'] || '')) + '&s=1\" width=\"1\" height=\"1\" alt=\"\">' : ''; };\n"
      "return $input.all().filter(function(i){")
    R("Filter Outreach Queue", "  return ready && notContacted && hasEmail && notLow && ok(r);\n});",
      "  return ready && notContacted && hasEmail && notLow && ok(r);\n}).map(function(i){ return { json: Object.assign({}, i.json, { _pixel: pixel(i.json) }), pairedItem: i.pairedItem }; });")
    m = N["Send Outreach Email"]["parameters"]
    assert m["message"].endswith("</p>"), m["message"][-40:]
    m["message"] = m["message"] + "{{ $json._pixel || '' }}"

    # 7. board: opens, timing, presence, warm connections
    lo = _node("Load Board Opens", "n8n-nodes-base.dataTable", 1.1, [N["Load Board Settings"]["position"][0], N["Load Board Settings"]["position"][1] + 160],
               {"resource": "row", "operation": "get", "dataTableId": _dt_ref("app_opens"), "returnAll": True}, alwaysOutputData=True, onError="continueRegularOutput")
    lo["executeOnce"] = True
    new.append(lo)
    nxt = conns["Load Board Settings"]["main"][0]
    assert [c["node"] for c in nxt] == ["Board Reply"], nxt
    conns["Load Board Settings"]["main"][0] = [{"node": "Load Board Opens", "type": "main", "index": 0}]
    link("Load Board Opens", "Board Reply")
    R("Board Reply", "const hist = events.filter(", BOARD_R31_JS.lstrip() + "const hist = events.filter(")
    R("Board Reply", "criteria: saved.criteria || null, brief: saved.brief || '' } } }];",
      "criteria: saved.criteria || null, brief: saved.brief || '', team_reply_rate: teamRate, best_time: bestTime, viewers: viewers } } }];")
    nodes.extend(new)
    return nodes, conns, routes
