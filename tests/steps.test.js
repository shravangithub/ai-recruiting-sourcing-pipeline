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
  const [c] = await runNode('Search Criteria', [{}], {
    'On form submission': [Object.assign({}, formBase, { 'Natural Language Search': 'Senior data engineer in Hyderabad, Spark and Airflow, 5+ years' })],
    'Parse NL Search': [nl({ target_role: 'Data Engineer', key_skills: 'Spark, Airflow', location: 'Hyderabad', experience_range: '5-8 years' })] });
  assert.equal(c['Target Role'], 'Data Engineer');
  assert.equal(c['Location'], 'Hyderabad');
  assert.equal(c['Experience Range (Years)'], '5-8 years');
  assert.equal(c['Primary Skills'], 'Spark, Airflow');
  assert.ok(c['Target Platforms'].includes('LinkedIn') && c['Target Platforms'].includes('Github'));
});

test('B: filled fields override the brief; dropdown + typed skills merge without duplicates', async () => {
  const [c] = await runNode('Search Criteria', [{}], {
    'On form submission': [Object.assign({}, formBase, { 'Natural Language Search': 'python dev in pune', '1) Target Role': 'Backend Engineer',
      '2) Primary Skills (must-have)': ['Java', 'Kafka'], '2b) Add other primary skills': 'gRPC, java', '3) Location': 'Chennai',
      '9) Experience Range (Years)': '3-5 years', '11) Target Platforms': ['Stack Overflow'] })],
    'Parse NL Search': [nl({ target_role: 'Python Developer', location: 'Pune', key_skills: 'Python' })] });
  assert.equal(c['Target Role'], 'Backend Engineer');
  assert.equal(c['Location'], 'Chennai');
  assert.equal(c['Primary Skills'], 'Java, Kafka, gRPC');
  assert.equal(c['Experience Range (Years)'], '3-5 years');
  assert.deepEqual(c['Target Platforms'], ['Stack Overflow', 'LinkedIn', 'Github']);
});

test('AI parser failure does not break the search (falls back to the form)', async () => {
  const [c] = await runNode('Search Criteria', [{}], {
    'On form submission': [Object.assign({}, formBase, { '1) Target Role': 'QA Engineer', '3) Location': 'Pune' })],
    'Parse NL Search': [{ error: { message: '429 no credits' } }] });
  assert.equal(c['Target Role'], 'QA Engineer');
  assert.equal(c['Location'], 'Pune');
  assert.equal(c['Gender Preference'], 'Any');
});

test('no skills anywhere -> tech words taken from the role', async () => {
  const [c] = await runNode('Search Criteria', [{}], {
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
  assert.deepEqual(out.map((x) => x.candidate_name), ['BLR dev', 'Unknown LI', 'Mumbai dev']);
  assert.equal(out[0].location_dropped_total, 3);
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
  assert.equal(city.length, 2, 'two pages');
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

test('UAT: a Kaggle / Stack Overflow person whose profile shows the city is kept; unknown location still dropped', async () => {
  const rows = [['Kaggle BLR', 8, 'Bengaluru, Karnataka, India', 'https://www.kaggle.com/someone'],
    ['SO BLR', 7, 'Bangalore', 'https://stackoverflow.com/users/42/x'], ['Kaggle unknown', 9, '', 'https://www.kaggle.com/other']]
    .map(([n, f, l, u]) => ({ candidate_name: n, fit_score: f, location: l, profile_url: u }));
  const out = await runNode('Location Check & Rank', rows, { 'Search Criteria': [criteria()] });
  assert.deepEqual(out.map((x) => x.candidate_name), ['Kaggle BLR', 'SO BLR']);
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
