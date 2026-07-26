# AI Recruiting Sourcing Pipeline

**Built by Shravan Kumar Shesham · 2026**
[LinkedIn](https://www.linkedin.com/in/shravanskumar/) · Licensed under [MIT](./LICENSE)

An evidence-based, AI-powered candidate sourcing and outreach system built on [n8n](https://n8n.io).
It turns a plain-English hiring brief (or an uploaded job description) into a ranked, enriched,
confidence-scored shortlist of real candidates — and manages recruiter-approved outreach end to end.

> **Why this exists.** Modern talent acquisition increasingly rewards recruiters who can *build*,
> not just operate an ATS. This project is my working proof of that: a production sourcing pipeline
> I designed, debugged, and hardened myself — with an emphasis on **data quality, judgment, and
> respecting the limits of automation**, not "AI will replace recruiters" hype.

---

## What it does (in one pass)

1. **Intake** — a recruiter submits a form: role, skills, location, seniority, exclusions, target
   platforms, geography, and (optionally) a full job description pasted or uploaded as PDF/Word.
2. **Natural-language understanding** — an LLM parses the free-text brief/JD into structured search
   criteria; explicit form fields always override the AI.
3. **Query construction** — a Boolean "X-ray" search string is built per target platform
   (LinkedIn, GitHub, Stack Overflow, etc.), with synonym expansion, exclusions, education filters,
   and a job-post noise filter so the search returns *people*, not job ads.
4. **Multi-source search** — paginated search (via [Serper](https://serper.dev)) across the selected
   platforms and the open web, results merged and de-duplicated.
5. **AI ranking** — every result is scored 0–10 against the criteria/JD, with a hard experience-minimum
   filter and explicit rules to reject job postings, directories, and non-person pages.
6. **Enrichment** — top candidates are enriched with verified work email/title/company (Apollo) and
   public GitHub profile data; LinkedIn-less candidates skip paid enrichment to save credits.
7. **Resume discovery** — a targeted secondary search hunts for a downloadable CV, scored for
   relevance and **verified against the candidate's name** before it's trusted.
8. **Confidence scoring** — every candidate gets a High/Medium/Low confidence label with reasons,
   so a recruiter knows which rows to trust.
9. **CRM write** — deduped candidates are upserted into a tracker (Google Sheets) with 25+ fields,
   plus per-candidate cards posted to Slack.
10. **Approved outreach** — the recruiter ticks "Send Email?" on chosen rows; a scheduler sends a
    personalized outreach email, marks the row "Contacted", and a mail trigger auto-flags "Replied".
11. **Governance** — monthly API-credit budget tracking with auto-reset and an 80% near-cap alert.

---

## Architecture

```
Form (brief + JD upload)
   │
   ├─ JD normalize / extract (PDF · Word · TXT · RTF · HTML)
   ├─ LLM: parse natural-language brief → structured criteria
   │
   ▼
Budget gate (monthly credit guardrail)
   │
   ▼
Build Boolean query ──► Serper search (paginated) ──► merge + dedupe
   │
   ▼
LLM ranking (0–10, hard experience filter, reject non-persons)
   │
   ├─ has LinkedIn? ──► Apollo enrich (verified email/title/company)
   │                └─► GitHub enrich (bio/repos/company)
   │
   ▼
Resume discovery + name-verification  ─►  Save CV to Drive
   │
   ▼
Confidence scoring + composite dedupe key
   │
   ├─► Google Sheets upsert (CRM tracker)
   ├─► Slack candidate cards + run summary
   └─► Usage tracker write-back + near-cap alert

Outreach scheduler (every N min)
   └─ rows where Send Email? = TRUE & Not contacted & has email
        └─► personalized Gmail send ──► mark "Contacted"

Reply trigger (Gmail)
   └─► match sender to candidate ──► mark "Replied"
```

---

## Engineering decisions & tradeoffs (the interesting part)

This is where the real work lives. A demo that only shows the happy path hides the hard problems.
Here are the decisions I'm most proud of:

- **People vs. job-posts is the core sourcing problem.** Naive X-ray search returns job ads, ATS
  pages, and directories. I encoded a sourcing playbook into the ranking prompt (reject "we are
  looking", careers/ATS URLs, aggregator listings → score 0) *and* a Boolean noise filter, so the
  pipeline surfaces individuals.
- **Experience as a hard filter, not a nudge.** Recruiters kept getting under-qualified matches, so
  the minimum experience band is enforced as a hard cutoff (below-minimum → score 0 → filtered).
- **Data you can't trust is worse than no data.** Resume scraping produced false positives (a court
  cause-list PDF's email attached to the wrong candidate). I added name-in-document verification,
  institutional/government domain rejection, and first-name-match rules before trusting any scraped
  contact — and a **Confidence label** so recruiters see *why* a row is or isn't trustworthy.
- **Cost governance is a feature.** Search/enrichment APIs cost per call. A monthly budget gate
  pauses sourcing at a cap, auto-resets each month, and warns at 80% — so the system can't quietly
  blow a budget.
- **Human in the loop for outreach.** The system never cold-emails automatically. A recruiter must
  explicitly approve each contact; only then does the scheduler send. Reply detection then removes
  contacted people from further outreach.
- **Idempotency via composite dedupe keys.** Re-running a search doesn't create duplicates — rows are
  upserted on a key that prefers email → profile URL → normalized name/role/location.
- **Graceful degradation everywhere.** GitHub rate limits, missing CVs, 404s, and non-PDF files all
  continue-on-error rather than crashing a run.

---

## Bugs I found and fixed (a sampling)

Real systems are defined by how they fail. A few representative fixes:

- **LLM inventing names.** Hacker News "who's hiring" threads made the model fabricate names like
  "Candidate from July 2019". Fixed by instructing the model to emit an empty name + score 0 for any
  non-person source.
- **Trailing-space column** in the tracker silently broke the budget check (`Serper_Used ` vs
  `Serper_Used`) and routed every run to "budget reached". Caught by inspecting resolved node data.
- **Wrong-credential enrichment.** An enrichment node was silently bound to the wrong stored
  credential, returning 401s. Fixed the binding and hardened header-name handling.
- **Zero-results dead end.** When a search returned nobody, the run silently ended with no signal.
  Added an always-fires "0 results found" alert with the exact query used, so recruiters get feedback.

---

## Tech stack

| Layer | Tool |
|---|---|
| Orchestration | n8n |
| Search | Serper (Google/X-ray) |
| LLM (parsing + ranking) | OpenAI |
| Contact enrichment | Apollo |
| Profile enrichment | GitHub API |
| CRM / tracker | Google Sheets |
| CV storage | Google Drive |
| Notifications | Slack |
| Outreach + replies | Gmail |

---

## Using this template

This repository contains a **sanitized** export (`ai-recruiting-pipeline.template.json`).
All private IDs, URLs, channels, and credentials have been replaced with `YOUR_*` placeholders —
**no secrets are included**.

1. Import `ai-recruiting-pipeline.template.json` into your own n8n instance.
2. Create your own credentials (OpenAI, Serper, Apollo, Google Sheets, Google Drive, Slack, Gmail,
   GitHub) and attach them to the nodes marked `REPLACE_WITH_YOUR_CREDENTIAL`.
3. Replace every `YOUR_*` placeholder (Sheet ID, Drive folder, Slack channel/user, form host, tracker gid).
4. Create the tracker sheet tabs and header rows described in the workflow's Sheets nodes.
5. Publish and test with a single sample search before going live.

---

## Responsible-use notes

- **Respect platform terms.** X-ray search and enrichment providers each have their own usage terms
  and rate limits; some sites (e.g. LinkedIn) restrict scraping. Run this only within the terms of the
  data sources and API providers you're entitled to use.
- **Outreach compliance.** Cold outreach is regulated (GDPR / CAN-SPAM / India DPDP). Keep a human
  approval step, honor opt-outs, and identify yourself. This template keeps outreach human-approved by
  design.
- **AI ranking is decision-support, not a decision.** Scores and confidence labels are there to help a
  recruiter prioritize — a human reviews every shortlist.

---

## About

Built by **Shravan Kumar Shesham**, a technical recruiter exploring the emerging
"recruiting engineer" space — where sourcing, data quality, and automation meet.
Feedback and questions welcome — reach me on [LinkedIn](https://www.linkedin.com/in/shravanskumar/).

## License & attribution

Released under the [MIT License](./LICENSE) — you're welcome to learn from, adapt, and reuse this,
provided the copyright and attribution notice is retained. If it helps you, a credit or a link back
is appreciated.
