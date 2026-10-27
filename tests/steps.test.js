// Behaviour of each pipeline step, with inputs a recruiter would actually produce.
const test = require('node:test');
const assert = require('node:assert/strict');
const { runNode, criteria, aiOutput } = require('./harness');

const formBase = { 'Natural Language Search': '', 'Job Description': '', '1) Target Role': '', '2) Primary Skills (must-have)': [],
  '2b) Add other primary skills': '', '2c) Secondary Skills (nice-to-have)': [], '2d) Add other secondary skills': '', '2e) Use Secondary Skills?': '',
  '3) Location': '', '4) Preferred Industry': '', '5) Current Employer': '', '6) Keywords to Exclude': '', '7) Education': '',
  '8) Expand Job Title Synonyms': '', '9) Experience Range (Years)': 'Auto (from description)', '10) Gender Preference': 'Auto (from description)',
  '11) Target Platforms': [], '12) Search Region (Geography)': '' };
const nl = (o) => aiOutput(o);

// ------------------------------------------------------------ Search Criteria
test('A: plain-English brief only -> role, location, experience, skills from the brief', async () => {
  const [c] = await runNode('Draft Criteria', [{}], {
    'On form submission': [Object.assign({}, formBase, { 'Natural Language Search': 'Senior data engineer in Hyderabad, Spark and Airflow, 5+ years' })],
    'Parse NL Search': [nl({ target_role: 'Data Engineer', key_skills: 'Spark, Airflow', location: 'Hyderabad', experience_range: '5-8 years' })] });
  assert.equal(c['Target Role'], 'Data Engineer');
  assert.equal(c['Location'], 'Hyderabad');
  assert.equal(c['Experience Range (Years)'], '5-8 years');
  assert.equal(c['Primary Skills'], 'Spark, Airflow');
  assert.ok(c['Target Platforms'].includes('LinkedIn') && c['Target Platforms'].includes('Github'));
});

test('B: filled fields override the brief; dropdown + typed skills merge without duplicates', async () => {
  const [c] = await runNode('Draft Criteria', [{}], {
    'On form submission': [Object.assign({}, formBase, { 'Natural Language Search': 'python dev in pune', '1) Target Role': 'Backend Engineer',
      '2) Primary Skills (must-have)': ['Java', 'Kafka'], '2b) Add other primary skills': 'gRPC, java', '3) Location': 'Chennai',
      '9) Experience Range (Years)': '3-5 years', '11) Target Platforms': ['Stack Overflow'] })],
    'Parse NL Search': [nl({ target_role: 'Python Developer', location: 'Pune', key_skills: 'Python' })] });
  assert.equal(c['Target Role'], 'Backend Engineer');
  assert.equal(c['Location'], 'Chennai');
  assert.equal(c['Primary Skills'], 'Java, Kafka, gRPC');
  assert.equal(c['Experience Range (Years)'], '3-5 years');
  assert.deepEqual(c['Target Platforms'], ['Stack Overflow'], 'only what was ticked');
});

test('AI parser failure does not break the search (falls back to the form)', async () => {
  const [c] = await runNode('Draft Criteria', [{}], {
    'On form submission': [Object.assign({}, formBase, { '1) Target Role': 'QA Engineer', '3) Location': 'Pune' })],
    'Parse NL Search': [{ error: { message: '429 no credits' } }] });
  assert.equal(c['Target Role'], 'QA Engineer');
  assert.equal(c['Location'], 'Pune');
  assert.equal(c['Gender Preference'], 'Any');
});

test('no skills anywhere -> tech words taken from the role', async () => {
  const [c] = await runNode('Draft Criteria', [{}], {
    'On form submission': [Object.assign({}, formBase, { 'Natural Language Search': 'golang developer bangalore' })],
    'Parse NL Search': [nl({ target_role: 'golang developer', location: 'Bangalore', key_skills: '' })] });
  assert.equal(c['Primary Skills'], 'Golang');
});

// ------------------------------------------------------------ Search queries
test('LinkedIn query: core title, skills, city aliases, India region, no job pages', async () => {
  const q = await runNode('Build Search Query', [{}], { 'Search Criteria': [criteria()] });
  const li = q.find((x) => x.kind === 'profiles');
  assert.ok(li, 'no LinkedIn query');
  assert.match(li.q, /site:linkedin\.com\/in/);
  assert.match(li.q, /"backend developer"/);
  assert.match(li.q, /\(bangalore OR bengaluru\)/);
  assert.match(li.q, /-intitle:jobs/);
  assert.equal(li.gl, 'in');
  assert.equal(q.filter((x) => x.kind === 'profiles').length, 3, '3 pages');
});

test('multi-word city is quoted; excludes and employer applied', async () => {
  const q = await runNode('Build Search Query', [{}], { 'Search Criteria': [criteria({ Location: 'New Delhi', 'Keywords to Exclude': 'intern, fresher', 'Current Employer': 'Infosys' })] });
  assert.match(q[0].q, /"new delhi"/);
  assert.match(q[0].q, /-intern -fresher/);
  assert.match(q[0].q, /Infosys/);
});

test('non-tech role (HR Business Partner, Mumbai) builds a sensible query', async () => {
  const q = await runNode('Build Search Query', [{}], { 'Search Criteria': [criteria({ 'Target Role': 'HR Business Partner', 'Primary Skills': 'Employee Relations, HRBP', 'Key Skills & Criteria': 'Employee Relations, HRBP', Location: 'Mumbai' })] });
  assert.match(q[0].q, /"business partner" hr/);
  assert.match(q[0].q, /\(mumbai OR bombay/);
});

test('recruiter-written Boolean role is used as-is', async () => {
  const q = await runNode('Build Search Query', [{}], { 'Search Criteria': [criteria({ 'Target Role': '("SDE 2" OR "Software Engineer II")' })] });
  assert.match(q[0].q, /\(\("SDE 2" OR "Software Engineer II"\)\)/);
});

test('Open Web adds a second query and never removes the LinkedIn one', async () => {
  const q = await runNode('Build Search Query', [{}], { 'Search Criteria': [criteria({ 'Target Platforms': ['LinkedIn', 'Google / Open Web'] })] });
  assert.ok(q.some((x) => x.kind === 'profiles') && q.some((x) => x.kind === 'open-web'));
});

test('GitHub: language + location qualifiers only (free text matches user names)', async () => {
  const q = await runNode('Build GH Query', [{}], { 'Search Criteria': [criteria()] });
  assert.deepEqual(q.map((x) => x.gh_query), ['language:java repos:>3 location:bangalore', 'language:java repos:>3 location:bengaluru']);
});

test('flagship-repo contributors (global) are skipped when a city is given', async () => {
  assert.equal((await runNode('Build Repo Query', [{}], { 'Search Criteria': [criteria()] })).length, 0);
  const r = await runNode('Build Repo Query', [{}], { 'Search Criteria': [criteria({ Location: '' })] });
  assert.ok(r[0].repo.startsWith('spring-projects/'));
});

test('Apollo search uses role, city and seniority', async () => {
  const [a] = await runNode('Build Apollo Query', [{}], { 'Search Criteria': [criteria()] });
  assert.deepEqual(a.apollo_body.person_locations, ['Bangalore']);
  assert.deepEqual(a.apollo_body.person_titles, ['Java backend developer']);
});

// ------------------------------------------------------------ AI scoring
test('pool is split into numbered batches of 12', async () => {
  const organic = Array.from({ length: 30 }, (_, i) => ({ title: 'P' + i, snippet: 's', link: 'https://github.com/u' + i }));
  const b = await runNode('Format Search Results', [{ organic }]);
  assert.deepEqual(b.map((x) => x.lines.length), [12, 12, 6]);
  assert.match(b[0].page_text, /^\[1\] P0 \|/);
});

test('AI output parsing survives code fences, wrapper objects and junk', async () => {
  const batches = [{ lines: [{ n: 1, title: 'A', link: 'https://x/a', person: false }] }];
  const r1 = await runNode('Split Candidates', [aiOutput('```json\n{"candidates":[{"line":1,"candidate_name":"Asha N","is_person":true,"fit_score":"8","profile_url":"https://x/a"}]}\n```')], { 'Format Search Results': batches });
  assert.equal(r1[0].candidate_name, 'Asha N'); assert.equal(r1[0].fit_score, 8);
  const r2 = await runNode('Split Candidates', [aiOutput('sorry, I cannot help')], { 'Format Search Results': batches });
  assert.equal(r2.length, 0);
});

test('a real profile the AI skipped is put back for the recruiter', async () => {
  const fmt = await runNode('Format Search Results', [{ organic: [
    { title: 'Neha Rao (@neha) - GitHub developer', snippet: 'Writes java', link: 'https://github.com/neha' },
    { title: 'Hiring thread', snippet: 'We are hiring', link: 'https://news.ycombinator.com/item?id=1' }] }]);
  const out = await runNode('Split Candidates', [aiOutput([])], { 'Format Search Results': fmt });
  assert.equal(out.length, 1);
  assert.equal(out[0].candidate_name, 'Neha Rao');
  assert.equal(out[0].restored, true);
});

test('ranking keeps everyone, highest score first', async () => {
  const r = await runNode('Rank Top 10', Array.from({ length: 30 }, (_, i) => ({ candidate_name: 'c' + i, fit_score: i % 10 })));
  assert.equal(r.length, 30);
  assert.equal(r[0].fit_score, 9); assert.equal(r[0].rank, 1);
});

// ------------------------------------------------------------ location
test('location: in-city first, same country next, other countries removed', async () => {
  const rows = [['UK dev', 9, 'London, UK', 'https://github.com/a'], ['BLR dev', 7, 'Bengaluru, Karnataka, India', 'https://github.com/b'],
    ['Mumbai dev', 8, 'Mumbai, India', 'https://in.linkedin.com/in/c'], ['US dev', 9, 'Austin, TX', 'https://github.com/d'],
    ['Unknown LI', 6, '', 'https://in.linkedin.com/in/e'], ['SO global', 9, '', 'https://stackoverflow.com/users/1/f']]
    .map(([n, f, l, u]) => ({ candidate_name: n, fit_score: f, location: l, profile_url: u }));
  const out = await runNode('Location Check & Rank', rows, { 'Search Criteria': [criteria()] });
  // only the people clearly in another country are removed; unknown-city people stay, labelled
  assert.deepEqual(out.map((x) => x.candidate_name), ['BLR dev', 'Unknown LI', 'Mumbai dev', 'SO global']);
  assert.equal(out[0].location_dropped_total, 2);
  assert.equal(out[0].location_review, '✅ In Bangalore');
  assert.match(out[2].location_review, /^🟡 India - other city/);
  assert.match(out[3].location_review, /Review: Stack Overflow can't filter by location/);
});

test('no city requested -> nobody removed', async () => {
  const rows = [{ candidate_name: 'x', fit_score: 5, location: 'Berlin', profile_url: 'https://github.com/x' }];
  const out = await runNode('Location Check & Rank', rows, { 'Search Criteria': [criteria({ Location: '' })] });
  assert.equal(out.length, 1);
});

// ------------------------------------------------------------ enrichment + sheet row
const baseCand = { rank: 1, candidate_name: 'Ravi Kumar', current_role: 'Backend Engineer', fit_score: 8, match_summary: 'ok', key_skills: 'Java',
  profile_url: 'https://github.com/ravik', github_url: 'https://github.com/ravik', email: '', email_status: '', location: 'Bengaluru',
  enrichment_status: 'skipped - not a LinkedIn/Apollo profile', github_status: 'enriched', resume_link: '', resume_status: 'none found', location_check: 'in Bangalore' };

test('GitHub public email lands in Email with Contact Source "GitHub profile"', async () => {
  const merged = await runNode('Merge GitHub', [{ login: 'ravik', id: 5, name: 'Ravi Kumar', email: 'ravi@example.com', blog: 'ravi.dev', html_url: 'https://github.com/ravik' }],
    { 'Enriched Candidates': [Object.assign({}, baseCand, { email: '' })] });
  assert.equal(merged[0].email, 'ravi@example.com');
  const [row] = await runNode('Parse Contact', [{}], { 'Attach Resume': [merged[0]], 'Save CV to Drive': [{}], 'Search Criteria': [criteria()] });
  assert.equal(row.contact_source, 'GitHub profile');
  assert.equal(row.resume_link, 'https://ravi.dev');
  assert.equal(row.resume_source, 'personal website (from GitHub)');
  assert.equal(row.source_platform, 'GitHub');
});

test('GitHub login is replaced by the real name', async () => {
  const [m] = await runNode('Merge GitHub', [{ login: 'ravik', id: 5, name: 'Ravi Kumar', html_url: 'https://github.com/ravik' }],
    { 'Enriched Candidates': [Object.assign({}, baseCand, { candidate_name: 'ravik' })] });
  assert.equal(m.candidate_name, 'Ravi Kumar');
});

test('resume scorer accepts a name-matched PDF and rejects directories', async () => {
  const cand = Object.assign({}, baseCand);
  const ok = await runNode('Attach Resume', [{ organic: [{ link: 'https://x.org/ravi-kumar-resume.pdf', title: 'Ravi Kumar Resume', snippet: 'Ravi Kumar backend' }] }], { 'Location Check & Rank': [cand] });
  assert.equal(ok[0].resume_link, 'https://x.org/ravi-kumar-resume.pdf');
  const bad = await runNode('Attach Resume', [{ organic: [{ link: 'https://x.gov.in/list.pdf', title: 'Ravi Kumar list', snippet: 'a@b.com c@d.com' }] }], { 'Location Check & Rank': [cand] });
  assert.equal(bad[0].resume_link, '');
});

test('candidate skipped by resume search keeps its data', async () => {
  const [r] = await runNode('Attach Resume', [Object.assign({}, baseCand)], { 'Location Check & Rank': [Object.assign({}, baseCand)] });
  assert.equal(r.candidate_name, 'Ravi Kumar');
  assert.equal(r.resume_status, 'none found');
});

test('open-web profile shows "Open Web" as the source platform', async () => {
  const [row] = await runNode('Parse Contact', [{}], { 'Attach Resume': [Object.assign({}, baseCand, { profile_url: 'https://someone.netlify.app/', github_url: '' })], 'Save CV to Drive': [{}], 'Search Criteria': [criteria()] });
  assert.equal(row.source_platform, 'Open Web');
});

// ------------------------------------------------------------ outreach + reporting
test('outreach only sends to rows ticked TRUE, with an email, not yet contacted, not Low confidence', async () => {
  const rows = [
    { 'Send Email?': 'TRUE', Email: 'a@x.com', 'Outreach Status': 'Not contacted', Confidence: 'High' },
    { 'Send Email?': 'FALSE', Email: 'b@x.com', 'Outreach Status': 'Not contacted', Confidence: 'High' },
    { 'Send Email?': 'TRUE', Email: '', 'Outreach Status': 'Not contacted', Confidence: 'High' },
    { 'Send Email?': 'TRUE', Email: 'c@x.com', 'Outreach Status': 'Contacted', Confidence: 'High' },
    { 'Send Email?': 'TRUE', Email: 'd@x.com', 'Outreach Status': 'Not contacted', Confidence: 'Low — anonymous' }];
  const out = await runNode('Filter Outreach Queue', rows);
  assert.deepEqual(out.map((r) => r.Email), ['a@x.com']);
});

test('Search Log shows the real candidate count', async () => {
  const [s] = await runNode('Sourcing Summary', Array.from({ length: 5 }, () => ({})), {
    'Location Check & Rank': Array.from({ length: 42 }, () => ({})), 'Search Criteria': [criteria()],
    'Serper Search': [{}, {}, {}], 'Resume Search': [{}], 'Apollo Enrich': [{}], 'Apollo People Search': [{}],
    'Budget Gate': [{ Serper_Used: 10, PDL_Used: 2, Month: 'October 2026' }] });
  assert.equal(s.count, 42);
  assert.match(s.range_text, /42 candidates/);
});

// ------------------------------------------------------------ UAT-derived cases
test('UAT: non-tech search (HR Business Partner) does not pull random GitHub developers', async () => {
  const q = await runNode('Build GH Query', [{}], { 'Search Criteria': [criteria({ 'Target Role': 'HR Business Partner', 'Primary Skills': 'Employee Relations', 'Key Skills & Criteria': 'Employee Relations', Location: 'Mumbai' })] });
  assert.equal(q.length, 0);
});

test('UAT: zero candidates after filtering still closes the Search Log row', async () => {
  const out = await runNode('Results Gate', [aiOutput([{ candidate_name: 'Far Away', is_person: true, fit_score: 7 }])], {
    'Search Criteria': [criteria()], 'Build Search Query': [{ q: 'x', platforms_used: 'profiles' }] });
  assert.equal(out.length, 1, 'no zero-result notice when every candidate was filtered out');
  assert.equal(out[0].count, 0);
  assert.ok(out[0].search_role && out[0].range_text);
});

test('UAT: when candidates were written, the zero-result notice stays silent', async () => {
  const out = await runNode('Results Gate', [aiOutput([])], { 'Search Criteria': [criteria()], 'Build Search Query': [{ q: 'x' }], 'Location Check & Rank': [{ candidate_name: 'a' }] });
  assert.equal(out.length, 0);
});

test('UAT: re-running a search keeps the recruiter\'s ticks and statuses', async () => {
  const sheetRow = { 'Dedupe Key': 'url:https://github.com/ravik', 'Send Email?': true, 'Pipeline Status': 'Shortlisted', 'Outreach Status': 'Contacted', 'Date Added': '2026-10-20 09:00' };
  const [m] = await runNode('Merge CRM State', [{ dedupe_key: 'url:https://github.com/ravik', profile_url: 'https://github.com/ravik' }], { 'Load CRM Rows': [sheetRow] });
  assert.equal(m.pipeline_status, 'Shortlisted');
  assert.equal(m.outreach_status, 'Contacted');
  assert.equal(m.date_added, '2026-10-20 09:00');
  assert.equal(String(m.send_email).toUpperCase(), 'TRUE');
  const [n] = await runNode('Merge CRM State', [{ dedupe_key: 'url:https://github.com/new', profile_url: 'https://github.com/new' }], { 'Load CRM Rows': [sheetRow] });
  assert.equal(String(n.send_email).toUpperCase(), 'FALSE');
});

test('UAT: Apollo people search is not over-narrowed by skill keywords when a title is given', async () => {
  const [a] = await runNode('Build Apollo Query', [{}], { 'Search Criteria': [criteria({ 'Target Role': 'HR Business Partner', 'Key Skills & Criteria': 'Employee Relations, Talent Management', 'Experience Range (Years)': '8+ years', Location: 'Mumbai' })] });
  assert.equal(a.apollo_body.q_keywords, undefined);
  assert.deepEqual(a.apollo_body.person_titles, ['HR Business Partner']);
});

// ------------------------------------------------------------ Round 9: recruiter UAT (Senior ML Engineer, Bangalore)
test('UAT: Kaggle / Stack Overflow / Hugging Face are searched by city through Google', async () => {
  const q = await runNode('Build Search Query', [{}], { 'Search Criteria': [criteria({ 'Target Role': 'Senior Machine Learning Engineer', 'Primary Skills': 'Python, PyTorch',
    'Target Platforms': ['LinkedIn', 'Kaggle', 'Stack Overflow', 'Hugging Face'] })] });
  const city = q.filter((x) => x.kind === 'city-profiles');
  assert.equal(city.length, 3, 'skills query x2 pages + title query x1');
  assert.match(city[0].q, /site:kaggle\.com/);
  assert.match(city[0].q, /site:stackoverflow\.com\/users/);
  assert.match(city[0].q, /bengaluru/i);
  assert.match(city[0].q, /-inurl:datasets/);
  assert.ok(q.some((x) => x.kind === 'profiles'), 'LinkedIn query still there');
});

test('UAT: no city-profile query without a city, or when those sources are not ticked', async () => {
  const noCity = await runNode('Build Search Query', [{}], { 'Search Criteria': [criteria({ Location: '', 'Target Platforms': ['Kaggle'] })] });
  assert.ok(!noCity.some((x) => x.kind === 'city-profiles'));
  const notTicked = await runNode('Build Search Query', [{}], { 'Search Criteria': [criteria({ 'Target Platforms': ['LinkedIn'] })] });
  assert.ok(!notTicked.some((x) => x.kind === 'city-profiles'));
});

test('UAT: Kaggle / Stack Overflow people are kept: in-city first, unknown location flagged for review', async () => {
  const rows = [['Kaggle BLR', 8, 'Bengaluru, Karnataka, India', 'https://www.kaggle.com/someone'],
    ['SO BLR', 7, 'Bangalore', 'https://stackoverflow.com/users/42/x'], ['Kaggle unknown', 9, '', 'https://www.kaggle.com/other']]
    .map(([n, f, l, u]) => ({ candidate_name: n, fit_score: f, location: l, profile_url: u }));
  const out = await runNode('Location Check & Rank', rows, { 'Search Criteria': [criteria()] });
  assert.deepEqual(out.map((x) => x.candidate_name), ['Kaggle BLR', 'SO BLR', 'Kaggle unknown']);
  assert.match(out[2].location_review, /^⚠️ Review: Kaggle/);
});

test('recruiter rule: city unknown but country known -> kept, sheet shows the country', async () => {
  const rows = [{ candidate_name: 'Country only', fit_score: 8, location: 'Karnataka, India', profile_url: 'https://in.linkedin.com/in/x' },
    { candidate_name: 'Elsewhere', fit_score: 9, location: 'Toronto, Canada', profile_url: 'https://in.linkedin.com/in/y' }];
  const out = await runNode('Location Check & Rank', rows, { 'Search Criteria': [criteria()] });
  assert.deepEqual(out.map((x) => x.candidate_name), ['Country only']);
  assert.equal(out[0].location_review, '🟡 India - city not listed');
});

test('sheet: each search starts with one requisition row carrying the full req', async () => {
  const items = [{ candidate_name: 'A', is_new: true, search_geography: 'Hyderabad, India', location_review: '✅ In Hyderabad' },
    { candidate_name: 'B', is_new: true, search_geography: 'Hyderabad, India', location_review: "⚠️ Review: Kaggle can't filter by location - check profile" },
    { candidate_name: 'C', is_new: false, search_geography: 'Hyderabad, India', location_review: '🟡 India - city not listed' }];
  const out = await runNode('Add Req Header', items, {
    'Search Criteria': [criteria({ 'Target Role': 'NLP Engineer', Location: 'Hyderabad', 'Primary Skills': 'Python, PyTorch', 'Experience Range (Years)': '5-8 years',
      'Keywords to Exclude': 'intern', 'Education': 'Masters', 'Target Platforms': ['LinkedIn', 'Kaggle'] })],
    'Prep JD': [{ jd_present: true, jd_source: 'pasted' }] });
  assert.equal(out.length, 4);
  const h = out[0];
  assert.equal(h.rank, 'REQ');
  assert.equal(h.candidate_name, 'NLP Engineer');
  assert.equal(h.current_role, '📍 Hyderabad, India');
  for (const want of ['Must-have: Python, PyTorch', 'Experience: 5-8 years', 'Education: Masters', 'Excluding: intern', 'Platforms: LinkedIn, Kaggle',
    'JD pasted', '3 candidates (1 in Hyderabad, 1 country/unknown, 1 to review)', '1 already listed under an earlier run', 'Searched ']) assert.ok(h.match_summary.includes(want), want);
  assert.equal(h.key_skills, '', 'cells right of Summary stay empty so the req text reads across the row');
  assert.match(h.dedupe_key, /^REQ\|/);
  assert.deepEqual(out.slice(1).map((x) => x.candidate_name), ['A', 'B', 'C']);
  assert.deepEqual(await runNode('Add Req Header', [], { 'Search Criteria': [criteria()] }), []);
});

test('sheet: Search Geography shows what the recruiter searched (city + region, no repeats)', async () => {
  const [row] = await runNode('Parse Contact', [{}], { 'Attach Resume': [Object.assign({}, baseCand)], 'Save CV to Drive': [{}], 'Search Criteria': [criteria({ 'Search Region': 'India' })] });
  assert.equal(row.search_geography, 'Bangalore, India');
  const [g] = await runNode('Parse Contact', [{}], { 'Attach Resume': [Object.assign({}, baseCand)], 'Save CV to Drive': [{}], 'Search Criteria': [criteria({ Location: '', 'Search Region': 'Global (all regions)' })] });
  assert.equal(g.search_geography, 'Global');
});

test('sheet: same person, same req -> their row is updated; different req -> a new row in that req block', async () => {
  const cand = { dedupe_key: 'url:https://github.com/ravik', search_role: 'Backend Engineer', search_location: 'Bangalore', profile_url: 'https://github.com/ravik' };
  const sheet = [{ 'Dedupe Key': 'url:https://github.com/ravik', 'Search Role': 'Backend Engineer', 'Search Location': 'Bangalore', 'Send Email?': 'TRUE', 'Pipeline Status': 'Shortlisted' }];
  const [same] = await runNode('Merge CRM State', [cand], { 'Load CRM Rows': sheet });
  assert.equal(same.is_new, false);
  assert.equal(same.send_email, 'TRUE');
  assert.equal(same.dedupe_key, 'url:https://github.com/ravik', 'keeps the existing row key so the row is updated in place');
  const [other] = await runNode('Merge CRM State', [Object.assign({}, cand, { search_role: 'Data Engineer' })], { 'Load CRM Rows': sheet });
  assert.equal(other.is_new, true);
  assert.match(other.dedupe_key, /\|req:data engineer@bangalore$/);
});

test('UAT: a long PDF that only mentions the name in its text is not their resume', async () => {
  const cand = Object.assign({}, baseCand);
  const [r1] = await runNode('Attach Resume', [{ organic: [{ link: 'https://www.college.edu/Documents/SSR2024/Criteria5/DVV-5_1_2_1.pdf', title: 'Criteria 5 student support', snippet: 'Ravi Kumar placed, resume workshop 2023' }] }], { 'Location Check & Rank': [cand] });
  assert.equal(r1.resume_link, '');
  const [r2] = await runNode('Attach Resume', [{ organic: [{ link: 'https://bank.example.in/assets/Z-E1_DEAF_Jan.pdf', title: 'Unclaimed deposits', snippet: 'Ravi Kumar curriculum vitae' }] }], { 'Location Check & Rank': [cand] });
  assert.equal(r2.resume_link, '');
  const own = Object.assign({}, baseCand, { github_blog: 'https://rk-dev.github.io' });
  const [r3] = await runNode('Attach Resume', [{ organic: [{ link: 'https://rk-dev.github.io/curriculum/', title: 'Curriculum', snippet: 'Ravi Kumar resume' }] }], { 'Location Check & Rank': [own] });
  assert.equal(r3.resume_link, 'https://rk-dev.github.io/curriculum/', 'their own site from GitHub is still accepted');
});

test('UAT: Apollo is skipped once the monthly Apollo allowance is used up', async () => {
  const c = criteria({ 'Target Platforms': ['LinkedIn'] });
  const over = await runNode('Build Apollo Query', [{}], { 'Search Criteria': [c], 'Budget Gate': [{ PDL_Used: 127, Serper_Used: 153 }] });
  assert.equal(over.length, 0);
  const under = await runNode('Build Apollo Query', [{}], { 'Search Criteria': [c], 'Budget Gate': [{ PDL_Used: 20, Serper_Used: 153 }] });
  assert.equal(under.length, 1);
});

test('UAT: zero-result check counts every AI batch, not just the first item it receives', async () => {
  const batches = [aiOutput([{ candidate_name: 'A', is_person: true, fit_score: 6 }]), aiOutput([{ candidate_name: 'B', is_person: true, fit_score: 5 }, { candidate_name: 'C', is_person: true, fit_score: 4 }])];
  const out = await runNode('Results Gate', [batches[0]], { 'Search Criteria': [criteria()], 'Build Search Query': [{ q: 'x', platforms_used: 'profiles' }], 'Message a model': batches });
  assert.equal(out.length, 1);
  assert.match(out[0].range_text, /3 people/);
});

test('UAT: "Senior Machine Learning Engineer" keeps the full title, with ML synonyms', async () => {
  const q = await runNode('Build Search Query', [{}], { 'Search Criteria': [criteria({ 'Target Role': 'Senior Machine Learning Engineer', 'Primary Skills': 'Python, PyTorch', 'Expand Job Title Synonyms': 'Yes' })] });
  const li = q.find((x) => x.kind === 'profiles').q;
  assert.match(li, /"machine learning engineer"/);
  assert.match(li, /"ml engineer"/);
  assert.ok(!/"learning engineer"/.test(li));
  assert.match(li, /\) senior /);
});

test('two-word titles are unchanged ("Senior Backend Engineer")', async () => {
  const q = await runNode('Build Search Query', [{}], { 'Search Criteria': [criteria({ 'Target Role': 'Senior Backend Engineer' })] });
  assert.match(q.find((x) => x.kind === 'profiles').q, /"backend engineer"/);
});

// ------------------------------------------------------------ Round 15: Apollo note, GitLab, Hacker News
test('Apollo off for the month -> the row says so (not "outside top 25")', async () => {
  const d = { candidate_name: 'A', profile_url: 'https://in.linkedin.com/in/a', rank: 3 };
  const [over] = await runNode('Combine No Apollo', [d], { 'Budget Gate': [{ PDL_Used: 127 }] });
  assert.match(over.enrichment_status, /monthly limit/);
  const [under] = await runNode('Combine No Apollo', [d], { 'Budget Gate': [{ PDL_Used: 10 }] });
  assert.match(under.enrichment_status, /below top 25/);
});

test('GitLab is searched through Google (its API only matches usernames)', async () => {
  assert.deepEqual(await runNode('Build GitLab Query', [{}], { 'Search Criteria': [criteria({ 'Target Platforms': ['GitLab'] })] }), []);
  const q = await runNode('Build Search Query', [{}], { 'Search Criteria': [criteria({ 'Target Platforms': ['LinkedIn', 'GitLab'] })] });
  const g = q.filter((x) => x.kind === 'city-profiles');
  assert.equal(g.length, 3);
  assert.match(g[0].q, /site:gitlab\.com/);
  assert.match(g[0].q, /-inurl:issues/);
  const noCity = await runNode('Build Search Query', [{}], { 'Search Criteria': [criteria({ Location: '', 'Target Platforms': ['GitLab'] })] });
  assert.ok(noCity.some((x) => x.kind === 'city-profiles' && /site:gitlab\.com/.test(x.q)), 'GitLab works without a city too');
});

test('Hacker News: city required, skills optional; candidate links to their HN profile', async () => {
  const [b] = await runNode('Build HN Query', [{}], { 'Search Criteria': [criteria({ 'Target Platforms': ['Hacker News'] })] });
  assert.match(b.hn_url, /query=Java%20Spring%20Boot%20Microservices%20Bangalore/);
  assert.match(b.hn_url, /optionalWords=Java%20Spring%20Boot%20Microservices/);
  const [o] = await runNode('HN To Organic', [{ id: 'sagarw', about: 'DevOps', karma: 10 }], { 'HN Split Authors': [{ author: 'sagarw', objectID: '1', comment_text: 'Location: Pune, India. Kubernetes, AWS' }] });
  assert.equal(o.organic[0].link, 'https://news.ycombinator.com/user?id=sagarw');
});

test('an empty Google page is retried once; pages with results are not', async () => {
  const pages = [{ searchParameters: { q: 'site:linkedin.com/in x', gl: 'in', num: 10, page: 1 }, organic: [] },
    { searchParameters: { q: 'site:linkedin.com/in x', gl: 'in', num: 10, page: 2 }, organic: [{ link: 'a' }] }];
  const out = await runNode('Empty Pages', pages, {});
  assert.equal(out.length, 1);
  assert.deepEqual(out[0].serper_body, { q: 'site:linkedin.com/in x', num: 10, page: 1, gl: 'in' });
});

// ------------------------------------------------------------ Round 17: 32-word Google limit, job-ad filter, GitHub fallback
const LONG = { 'Target Role': 'Senior Machine Learning Engineer', 'Primary Skills': 'Python, PyTorch, TensorFlow, Kubernetes, Spark',
  'Key Skills & Criteria': 'Python, PyTorch, TensorFlow, Kubernetes, Spark', Location: 'Bangalore', 'Keywords to Exclude': 'intern, trainee, fresher, student',
  'Education': 'Masters', 'Current Employer': 'Acme Analytics', 'Expand Job Title Synonyms': 'Yes',
  'Target Platforms': ['LinkedIn', 'X (Twitter)', 'Dribbble', 'Behance', 'Reddit', 'Google / Open Web', 'Kaggle', 'Stack Overflow', 'Hugging Face', 'GitLab'] };
const words = (s) => s.trim().split(/\s+/).length;

test('R17: every Google query fits in 32 words, even for a long brief', async () => {
  const q = await runNode('Build Search Query', [{}], { 'Search Criteria': [criteria(LONG)] });
  const kinds = new Set(q.map((x) => x.kind));
  assert.ok(kinds.has('profiles') && kinds.has('open-web') && kinds.has('city-profiles'));
  for (const x of q) assert.ok(words(x.q) <= 32, x.kind + ' is ' + words(x.q) + ' words: ' + x.q);
  const li = q.find((x) => /site:linkedin\.com\/in/.test(x.q)).q;
  assert.match(li, /"machine learning engineer"/);
  assert.match(li, /bangalore/);
  assert.match(li, /Python/);
  assert.ok(!/site:dribbble/.test(li), 'LinkedIn has its own query');
  const others = q.find((x) => /site:dribbble/.test(x.q));
  assert.ok(others && /site:behance\.net/.test(others.q) && /site:x\.com/.test(others.q));
  assert.equal(q.filter((x) => x.kind === 'open-web').length, 4, 'two open-web variants x 2 pages');
  const city = q.find((x) => x.kind === 'city-profiles').q;
  assert.match(city, /site:kaggle\.com/);
  assert.match(city, /site:gitlab\.com/);
});

test('R17: the trimming drops exclusions before role, city or site', async () => {
  const q = await runNode('Build Search Query', [{}], { 'Search Criteria': [criteria(Object.assign({}, LONG, { 'Target Platforms': ['Google / Open Web'] }))] });
  for (const x of q) {
    assert.ok(words(x.q) <= 32);
    assert.match(x.q, /"machine learning engineer"/);
    assert.match(x.q, /bangalore/);
  }
  assert.ok(q.some((x) => /resume/.test(x.q)) && q.some((x) => /portfolio/.test(x.q)));
});

test('R17: job ads and job boards are removed from Google results', async () => {
  const organic = [
    { link: 'https://acme.wd102.myworkdayjobs.com/en-US/Acme/job/Senior-ML-Engineer', title: 'Senior ML Engineer' },
    { link: 'https://careers.example.com/job/ml-engineer-bengaluru', title: 'ML Engineer' },
    { link: 'https://www.naukri.com/machine-learning-python-jobs', title: 'jobs' },
    { link: 'https://www.scribd.com/document/1/ML-engineer', title: 'cv' },
    { link: 'https://janedoe.github.io/cv', title: 'Jane Doe - CV' },
    { link: 'https://in.linkedin.com/in/jane-doe', title: 'Jane Doe - ML Engineer' },
  ];
  const [m] = await runNode('Merge Pages', [{ organic }]);
  assert.deepEqual(m.organic.map((o) => o.link), ['https://janedoe.github.io/cv', 'https://in.linkedin.com/in/jane-doe']);
});

test('R17: GitHub runs for DevOps skills and falls back when there is no language', async () => {
  const devops = await runNode('Build GH Query', [{}], { 'Search Criteria': [criteria({ 'Target Role': 'DevOps Engineer', 'Key Skills & Criteria': 'Kubernetes, Terraform, AWS', Location: 'Pune' })] });
  assert.deepEqual(devops.map((x) => x.gh_query), ['language:go repos:>3 location:pune', 'language:go repos:>3 location:pimpri']);
  const pe = await runNode('Build GH Query', [{}], { 'Search Criteria': [criteria({ 'Target Role': 'Platform Engineer', 'Key Skills & Criteria': 'Observability, Incident management', Location: 'Pune' })] });
  assert.equal(pe[0].gh_query, 'repos:>5 followers:>10 location:pune');
  const pm = await runNode('Build GH Query', [{}], { 'Search Criteria': [criteria({ 'Target Role': 'Product Manager', 'Key Skills & Criteria': 'Roadmaps, Stakeholders', Location: 'Pune' })] });
  assert.deepEqual(pm, [], 'non-technical roles still skip GitHub');
});

// ------------------------------------------------------------ Round 18
test('R18: ticking one platform searches only that platform; none ticked -> LinkedIn + GitHub', async () => {
  const [one] = await runNode('Draft Criteria', [{}], {
    'On form submission': [Object.assign({}, formBase, { '1) Target Role': 'Backend Engineer', '3) Location': 'Pune', '11) Target Platforms': ['Kaggle'] })],
    'Parse NL Search': [nl({})] });
  assert.deepEqual(one['Target Platforms'], ['Kaggle']);
  const [none] = await runNode('Draft Criteria', [{}], {
    'On form submission': [Object.assign({}, formBase, { '1) Target Role': 'Backend Engineer', '3) Location': 'Pune' })],
    'Parse NL Search': [nl({})] });
  assert.deepEqual(none['Target Platforms'], ['LinkedIn', 'Github']);
});

test('R18: X/Reddit and Kaggle-type sites get title and skill variants, all within 32 words', async () => {
  const q = await runNode('Build Search Query', [{}], { 'Search Criteria': [criteria(Object.assign({}, LONG, { 'Target Platforms': ['X (Twitter)', 'Hugging Face'] }))] });
  const other = [...new Set(q.filter((x) => /site:x\.com/.test(x.q)).map((x) => x.q))];
  assert.equal(other.length, 3);
  const city = [...new Set(q.filter((x) => x.kind === 'city-profiles').map((x) => x.q))];
  assert.equal(city.length, 2);
  for (const x of q) assert.ok(x.q.trim().split(/\s+/).length <= 32);
});

test('R18: every Google Sheets step retries on rate limits', () => {
  const { WF } = require('./harness');
  for (const n of WF.nodes.filter((n) => n.type.endsWith('googleSheets'))) assert.equal(n.retryOnFail, true, n.name);
});

// ------------------------------------------------------------ Round 19: two-page form, parallel scoring, per-source counts, commit emails
test('R19: page 2 answers become the search criteria (same shape as before)', async () => {
  const [c] = await runNode('Search Criteria', [{ role: 'Backend Engineer', similar: 'Yes', experience: 'Any', must: 'Java, Kafka, java',
    nice: 'AWS', city: 'Pune', region: 'India', industry: '', employer: '', exclude: 'intern', education: 'Any', gender: 'Any', sources: ['GitHub', 'Kaggle'] }],
    { 'Draft Criteria': [{ nl_used: true, 'Primary Skills': 'Python' }] });
  assert.equal(c['Target Role'], 'Backend Engineer');
  assert.equal(c['Primary Skills'], 'Java, Kafka');
  assert.equal(c['Secondary Skills'], 'AWS');
  assert.equal(c['Key Skills & Criteria'], 'Java, Kafka, AWS');
  assert.equal(c['Location'], 'Pune');
  assert.deepEqual(c['Target Platforms'], ['GitHub', 'Kaggle']);
  assert.equal(c['Experience Range (Years)'], 'Any');
  assert.equal(c['nl_used'], true);
  const [g] = await runNode('Search Criteria', [{ role: 'QA', region: 'Global (all regions)' }], { 'Draft Criteria': [{ 'Primary Skills': 'Selenium' }] });
  assert.equal(g['Search Region'], '');
  assert.equal(g['Primary Skills'], 'Selenium', 'skills fall back to the brief');
  assert.deepEqual(g['Target Platforms'], ['LinkedIn', 'Github']);
});

test('R19: the scoring prompt is built per batch with the criteria and the JD', async () => {
  const b = await runNode('Format Search Results', [{ organic: [{ title: 'Asha N - Backend Engineer', snippet: 'Java', link: 'https://in.linkedin.com/in/asha' }] }],
    { 'Search Criteria': [criteria()], 'Prep JD': [{ jd_text: 'Build payment APIs' }] });
  assert.match(b[0].prompt, /Target Role: Java backend developer/);
  assert.match(b[0].prompt, /Build payment APIs/);
  assert.match(b[0].prompt, /\[1\] Asha N/);
  assert.match(b[0].system, /JSON/);
  const none = await runNode('Format Search Results', [{ organic: [{ title: 'x', link: 'https://a.b' }] }]);
  assert.match(none[0].prompt, /No job description was provided/);
});

test('R19: requisition row lists candidates per source', async () => {
  const items = [{ source_platform: 'LinkedIn', search_geography: 'Bangalore' }, { source_platform: 'GitHub' }, { source_platform: 'LinkedIn' }];
  const out = await runNode('Add Req Header', items, { 'Search Criteria': [criteria()], 'Prep JD': [{ jd_present: false }] });
  assert.match(out[0].match_summary, /by source: LinkedIn 2 · GitHub 1/);
});

test('R19: a GitHub commit email is used only when it is clearly theirs', async () => {
  const cand = { github_url: 'https://github.com/asha-dev', rank: 3, email: '' };
  const resp = { items: [
    { author: { login: 'someone-else' }, commit: { author: { email: 'x@corp.com' } } },
    { author: { login: 'asha-dev' }, commit: { author: { email: '123+asha@users.noreply.github.com' } } },
    { author: { login: 'asha-dev' }, commit: { author: { email: 'asha@example-mail.in' } } }] };
  const [a] = await runNode('Attach Commit Email', [resp], { 'Attach Resume': [cand] });
  assert.equal(a.commit_email, 'asha@example-mail.in');
  const [b] = await runNode('Attach Commit Email', [{ rate: {} }], { 'Attach Resume': [cand] });
  assert.equal(b.commit_email, '');
});
