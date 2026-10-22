#!/usr/bin/env python3
"""Build setup/pipeline.workflow.json from ai-recruiting-pipeline.template.json.

The output is the same 11-stage pipeline, with every account-specific value
replaced by a __TOKEN__ that the one-click setup fills in:

  __CRED_<NAME>__     credential ids (created by the setup)
  __SHEET_ID__        results spreadsheet (created by the setup)
  __DRIVE_FOLDER_ID__ Drive folder for CVs and JDs (created by the setup)
  __COMPANY__         the recruiting firm's name
  __SIGNATURE__       outreach email signature (HTML)
  __SLACK_CHANNEL__   alerts channel, e.g. #sourcing-alerts
  __SLACK_USER__      Slack member ID for the "run complete" DM (optional)
  __FORM_ID__         id of the search form URL

Run from the repo root:  python3 setup/build_pipeline.py
"""
import json, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "ai-recruiting-pipeline.template.json"
OUT = ROOT / "setup" / "pipeline.workflow.json"

CRED_NAMES = {
    "OPENAI": "Sourcing · OpenAI",
    "SERPER": "Sourcing · Serper",
    "APOLLO": "Sourcing · Apollo",
    "GITHUB": "Sourcing · GitHub",
    "GITLAB": "Sourcing · GitLab",
    "KAGGLE": "Sourcing · Kaggle",
    "SHEETS": "Sourcing · Google Sheets",
    "DRIVE": "Sourcing · Google Drive",
    "GMAIL": "Sourcing · Gmail",
    "SLACK": "Sourcing · Slack",
}
TYPE_TO_KEY = {
    "openAiApi": "OPENAI",
    "githubApi": "GITHUB",
    "gitlabApi": "GITLAB",
    "httpBasicAuth": "KAGGLE",
    "googleSheetsOAuth2Api": "SHEETS",
    "googleDriveOAuth2Api": "DRIVE",
    "gmailOAuth2": "GMAIL",
    "slackOAuth2Api": "SLACK",
}

STRING_SWAPS = [
    ("YOUR_GOOGLE_SHEET_ID", "__SHEET_ID__"),
    ("YOUR_DRIVE_FOLDER_ID", "__DRIVE_FOLDER_ID__"),
    ("Best regards,<br/>TalentRubric Growth Advisors</p>", "Best regards,<br/>__SIGNATURE__</p>"),
    ("TalentRubric — Candidate Sourcing", "__COMPANY__ — Candidate Sourcing"),
    ("TalentRubric Growth Advisors", "__COMPANY__"),
]


def swap(obj):
    if isinstance(obj, str):
        for a, b in STRING_SWAPS:
            obj = obj.replace(a, b)
        return obj
    if isinstance(obj, list):
        return [swap(x) for x in obj]
    if isinstance(obj, dict):
        return {k: swap(v) for k, v in obj.items()}
    return obj


def main():
    src = json.loads(SRC.read_text())
    nodes = []
    for n in src["nodes"]:
        n = swap(n)
        t = n["type"]
        # credentials -> tokens
        if n.get("credentials"):
            new = {}
            for ctype in n["credentials"]:
                if ctype == "httpHeaderAuth":
                    key = "APOLLO" if "apollo" in n["name"].lower() else "SERPER"
                else:
                    key = TYPE_TO_KEY[ctype]
                new[ctype] = {"id": f"__CRED_{key}__", "name": CRED_NAMES[key]}
            n["credentials"] = new
        # Slack: post to a channel chosen at setup; never let Slack break a run
        if t.endswith(".slack"):
            p = n["parameters"]
            if p.get("select") == "channel":
                p["channelId"] = {"__rl": True, "value": "__SLACK_CHANNEL__", "mode": "name"}
            elif p.get("select") == "user":
                p["user"] = {"__rl": True, "value": "__SLACK_USER__", "mode": "id"}
            n["onError"] = "continueRegularOutput"
        # Search form gets a stable, setup-assigned URL
        if t.endswith(".formTrigger"):
            n["webhookId"] = "__FORM_ID__"
        nodes.append(n)

    out = {
        "name": "AI Recruiting Sourcing Pipeline",
        "nodes": nodes,
        "connections": src["connections"],
        "settings": {"executionOrder": "v1"},
    }
    text = json.dumps(out, ensure_ascii=False, indent=1)
    for leftover in ("YOUR_", "REPLACE_WITH", "TalentRubric", "recruitment-alerts", "n8n credits"):
        assert leftover not in text, f"leftover placeholder: {leftover}"
    OUT.write_text(text + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(nodes)} nodes")


if __name__ == "__main__":
    main()
