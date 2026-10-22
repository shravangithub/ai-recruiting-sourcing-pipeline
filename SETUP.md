# One-click setup (about 5 minutes)

Paste your API keys into a friendly setup page and your own n8n configures itself: it creates
every connection, makes your results sheet and Drive folder, installs the pipeline and switches it
on. No workflow editing, no credential wiring.

Everything runs inside **your** n8n. Keys go straight into n8n's own credential store; the setup
workflow doesn't save them, and execution logging is switched off for it, so they never show up in
your execution history. When setup finishes, it switches itself off.

## What you need

| | Needed? | What it does | Where to get it |
|---|---|---|---|
| **n8n** (Cloud paid plan or self-hosted) | Required | Runs everything | [n8n.io](https://n8n.io) · the n8n API isn't available on the Cloud **free trial** |
| **OpenAI** API key | Required | Reads the brief, scores candidates | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| **Serper** API key | Required | Searches the web and professional sites | [serper.dev/api-key](https://serper.dev/api-key) (free credits) |
| **Google** account | Required | Results sheet, CV folder, approved outreach emails | You sign in during setup |
| **GitHub** token | Recommended | Real code activity | [github.com/settings/tokens](https://github.com/settings/tokens) (no scopes needed) |
| **Apollo** API key | Optional | Verified email, title, company | Apollo → Settings → API |
| **GitLab** token | Optional | More code activity | GitLab → Access tokens (`read_api`) |
| **Kaggle** username + key | Optional | Data-science proof of work | Kaggle → Settings → API |
| **Slack** | Optional | Alerts when runs finish | You sign in during setup |

Skip any optional source and the pipeline simply doesn't search it.

## Steps

1. **Create an n8n API key.** In n8n: **Settings → n8n API → Create an API key**. Copy it.
2. **Import the setup workflow.** In n8n: **Workflows → Import from File** →
   [`setup/setup.workflow.json`](setup/setup.workflow.json).
3. **Switch it on.** Click **Publish** / **Active** (top right).
4. **Open the setup page.** Open the **Open setup page** node, copy its **Production URL**
   (it ends in `/webhook/sourcing-setup`) and open it in your browser.
5. **Follow the five screens:**
   - **n8n** – paste your n8n API key → **Check**.
   - **Your keys** – paste each key → **Test**. OpenAI and Serper must show ✓.
   - **Your details** – your company name, outreach signature and (optional) Slack channel.
     These go into the AI scoring prompt, your search form and your emails.
   - **Sign in** – click **Create connections**, then for Google Sheets, Google Drive, Gmail
     (and Slack) click **Open & sign in**, press **Sign in** on the page that opens, and come back.
   - **Launch** – click **Configure my pipeline**.
6. **Start sourcing.** Open your **search form** link. Your ranked shortlist lands in the
   results sheet; tick **Send Email?** on the rows you approve.

Running setup again is safe: it updates the existing connections and pipeline instead of
creating duplicates. To run it again, switch the setup workflow back on.

## Self-hosted n8n

n8n Cloud signs you in to Google and Slack with its own apps. Self-hosted n8n needs your own:

- **Google:** in Google Cloud, create an OAuth client (type *Web application*), enable the
  Google Sheets, Google Drive and Gmail APIs, and add your n8n's OAuth callback URL
  (`https://<your-n8n>/rest/oauth2-credential/callback`) as a redirect URI.
- **Slack** (optional): create a Slack app with the same callback URL.

Paste the client IDs and secrets under **Advanced: your own sign-in apps** on the
**Your details** screen.

## Troubleshooting

- **"Your n8n plan doesn't include the n8n API"** – you're on n8n Cloud's free trial. Upgrade,
  or run n8n yourself.
- **"Google Sheets isn't signed in yet"** – open the Google Sheets sign-in link, press
  **Sign in**, then click **Configure my pipeline** again.
- **A sign-in link doesn't open** – in n8n go to **Credentials** and open the
  `Sourcing · …` item by hand.
- **Want to change a key later?** Edit the `Sourcing · …` credential in n8n, or switch the setup
  workflow on and run it again.

## For contributors

The two workflow files are generated:

```bash
python3 setup/build_pipeline.py   # ai-recruiting-pipeline.template.json -> setup/pipeline.workflow.json
python3 setup/build_setup.py      # setup/src/* -> setup/setup.workflow.json
```

- `setup/src/setup.html` – the setup screens (plain HTML/CSS/JS, no build step)
- `setup/src/handler.js` – the setup API (runs in an n8n Code node; uses n8n's public REST API)
- `setup/pipeline.workflow.json` – the pipeline with `__TOKENS__` that setup fills in

Forks: point setup at your own template with
`python3 setup/build_setup.py --template https://raw.githubusercontent.com/<you>/<repo>/main/setup/pipeline.workflow.json`.
