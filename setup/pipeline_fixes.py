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
