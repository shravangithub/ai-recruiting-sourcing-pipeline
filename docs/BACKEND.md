# How the backend fits together

One n8n workflow, 339 steps, eleven ways in. Each "way in" is a trigger; everything it reaches is its
part of the canvas. When something looks wrong, open **Health** in the app first: it names the part.

| Way in (trigger) | What it does | Steps | Runs |
|---|---|---|---|
| **App API** (`/webhook/sourcing-app-api`) | Every button in the app: sign-in, Board, search, approve, replies, interview kit, Health, and `add_person` for the browser button, email and Telegram | 226* | On each click |
| **App Page** (`/webhook/sourcing-app`) | Serves the app itself | 3 | When the page opens |
| **On form submission** | The original n8n form: brief → check → search | 126* | When the form is sent |
| **Outreach Schedule** | Sends the first email to people you approved | 13 | Every 15 min |
| **Follow-up Schedule** | Sends the day 2, 4, 5 and 7 follow-ups inside your sending hours | 18 | Every 15 min |
| **On Candidate Reply** | Spots replies in Gmail and stops follow-ups for that person; a teammate's forwarded CV or link is scored and added, and "Search: <role>" starts a search (Email In → Email Add → Email Answer) | 19 | Gmail poll, 15 min |
| **Weekly Report Schedule** | Emails admins last week's numbers | 11 | Monday 9:17 |
| **Open Pixel** (`/webhook/sourcing-open`) | Counts opens, only if open tracking is on | 4 | When an email is opened |
| **Apply Page Hook** (`/webhook/sourcing-apply`) | The public apply page of a role (only when opened on the Board) | 5 | When an applicant opens it |
| **Apply API Hook** (`/webhook/sourcing-apply-api`) | Checks an application, scores it, adds it to Found | 11 | When someone applies |
| **Telegram Poll** | Asks Telegram for new messages, runs them as the teammate who sent them through the app's own API, pushes finished searches and candidate replies; voice notes are transcribed first | 24 | Every 30 s |
| **App Icon** (`/webhook/sourcing-app-icon`) | The icon for a phone home screen | 3 | When the page opens |

\* The search engine is shared: a search from the app and from the form run the same steps
(Search Criteria → Build Search Query and the source branches → Wait For All Sources → Tag Google
Links → Merge Pages → scoring → enrichment → results sheet and app tables).

## The search engine, in order

1. **Search Criteria**: the brief as fields (role, must-haves, city, sources, target companies, veterans).
2. **Build Search Query** plus one branch per API source (GitHub, Stack Overflow, Kaggle, Hugging Face,
   GitLab, Hacker News, Reddit, Dev.to, Apollo, repo contributors, Research papers). Google searches go
   through Serper, one query per site.
3. **Wait For All Sources** → **Tag Google Links** (repairs Google's `goto` links) → **Merge Pages**
   (drops job ads and non-profile pages, removes duplicates).
4. **Format Search Results** → the AI scores each line → **Split Candidates**.
5. Enrichment: Apollo for work emails and titles, GitHub for commit emails, CV checks.
6. **Parse Contact** → **Location Check & Rank** → the results sheet, and **Run People** saves them for the app.

## Where the data lives (n8n Data Tables)

| Table | Holds |
|---|---|
| `app_users`, `app_sessions`, `app_activity` | Team logins, sessions, who did what |
| `app_settings` | Sending settings, saved briefs (`run:<id>`), heartbeats (`hb:*`), weekly report on/off, profiles |
| `app_runs`, `app_candidates` | Each search and the people it found |
| `app_stages`, `app_events` | Board stage, owner, notes, history |
| `outreach_sequence`, `outreach_templates` | Who is being emailed, and the follow-up texts |
| `app_opens`, `app_feedback` | Opens (if tracking is on), votes that re-rank |

## When something breaks

1. **Health** in the app says which part, since when, and the usual fix.
2. In n8n, **Executions**, filter by "Error": the red step is where it stopped. Most failures are a
   service out of credits (Serper, OpenAI, Apollo) or an expired Google connection.
3. Fix the connection in n8n **Credentials**, then search again or wait for the next 15-minute run.

## Splitting into smaller workflows (planned)

The natural cut is along the table above: **Search engine**, **App API**, **Outreach + Follow-ups +
Replies**, **Weekly report**. Each would become its own workflow, called with *Execute Workflow*. That
makes each canvas small, but every part must then be imported, connected and published separately, and
linked by ID, which makes the one-file install harder. We'll do it once v1.4 is live and stable.
