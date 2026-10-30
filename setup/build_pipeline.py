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
    import sys
    sys.path.insert(0, str(ROOT / "setup"))
    import pipeline_fixes
    src_nodes, src_conns = pipeline_fixes.apply(src["nodes"], src["connections"])
    src_nodes, src_conns = pipeline_fixes.apply_round2(src_nodes, src_conns)
    src_nodes, src_conns = pipeline_fixes.apply_round3(src_nodes, src_conns)
    src_nodes, src_conns = pipeline_fixes.apply_round4(src_nodes, src_conns)
    src_nodes, src_conns = pipeline_fixes.apply_round5(src_nodes, src_conns)
    src_nodes, src_conns = pipeline_fixes.apply_round6(src_nodes, src_conns)
    src_nodes, src_conns = pipeline_fixes.apply_round7(src_nodes, src_conns)
    src_nodes, src_conns = pipeline_fixes.apply_round8(src_nodes, src_conns)
    src_nodes, src_conns = pipeline_fixes.apply_round9(src_nodes, src_conns)
    src_nodes, src_conns = pipeline_fixes.apply_round10(src_nodes, src_conns)
    src_nodes, src_conns = pipeline_fixes.apply_round11(src_nodes, src_conns)
    src_nodes, src_conns = pipeline_fixes.apply_round12(src_nodes, src_conns)
    src_nodes, src_conns = pipeline_fixes.apply_round13(src_nodes, src_conns)
    src_nodes, src_conns = pipeline_fixes.apply_round14(src_nodes, src_conns)
    src_nodes, src_conns = pipeline_fixes.apply_round15(src_nodes, src_conns)
    src_nodes, src_conns = pipeline_fixes.apply_round16(src_nodes, src_conns)
    src_nodes, src_conns = pipeline_fixes.apply_round17(src_nodes, src_conns)
    src_nodes, src_conns = pipeline_fixes.apply_round18(src_nodes, src_conns)
    src_nodes, src_conns = pipeline_fixes.apply_round19(src_nodes, src_conns)
    src_nodes, src_conns = pipeline_fixes.apply_round20(src_nodes, src_conns)
    src_nodes, src_conns = pipeline_fixes.apply_round21(src_nodes, src_conns)
    src_nodes, src_conns = pipeline_fixes.apply_round22(src_nodes, src_conns)
    src_nodes, src_conns = pipeline_fixes.apply_round23(src_nodes, src_conns)
    src_nodes, src_conns = pipeline_fixes.apply_round24(src_nodes, src_conns)
    src_nodes, src_conns = pipeline_fixes.apply_round25(src_nodes, src_conns)
    src_nodes, src_conns = pipeline_fixes.apply_round26(src_nodes, src_conns)
    src_nodes, src_conns = pipeline_fixes.apply_round27(src_nodes, src_conns)
    src_nodes, src_conns = pipeline_fixes.apply_round28(src_nodes, src_conns)
    src_nodes, src_conns = pipeline_fixes.apply_round29(src_nodes, src_conns)
    src_nodes, src_conns, routes30 = pipeline_fixes.apply_round30(src_nodes, src_conns)
    src_nodes, src_conns, routes31 = pipeline_fixes.apply_round31(src_nodes, src_conns, routes30)
    src_nodes, src_conns, routes32 = pipeline_fixes.apply_round32(src_nodes, src_conns, routes31)
    src_nodes, src_conns = pipeline_fixes.apply_round33(src_nodes, src_conns)
    src_nodes, src_conns, _ = pipeline_fixes.apply_round34(src_nodes, src_conns, routes32)
    src_nodes, src_conns = pipeline_fixes.apply_round35(src_nodes, src_conns)
    nodes = []
    for n in src_nodes:
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
        "connections": src_conns,
        "settings": {"executionOrder": "v1"},
    }
    text = json.dumps(out, ensure_ascii=False, indent=1)
    for leftover in ("YOUR_", "REPLACE_WITH", "TalentRubric", "recruitment-alerts", "n8n credits"):
        assert leftover not in text, f"leftover placeholder: {leftover}"
    # every quality fix must have landed (guards against silent no-op replaces)
    for marker in ("Wait For All Sources", "Location Check & Rank", "Rejoin Candidates", "GH User Details",
                   "candidate_location: cand.candidate_location", "location: cand.candidate_location || ''",
                   "merged.candidate_name = ghName", "backend_python", "const cand = $('Location Check & Rank').item.json",
                   "Profile URL", "count = $('Location Check & Rank').all().length", "outside top", "location not confirmed",
                   "skipped - below top", "STRICT HONESTY",
                   "restored_total", "public on GitHub profile", "personal website (from GitHub)",
                   "10. line (number", "Writes ' + lang", "can't filter by location - check profile", "site:reddit.com/r/forhire", "return 'Open Web';", "const LIMIT = 32;", "JOB_AD.test", "followers:>10", "only the platforms the recruiter ticked", "and once by job title", "Here's what we'll search for", "by source: ", "public in GitHub commits", "api.openai.com/v1/responses", "Also on ", "A social page is not a resume", "Open the results sheet", "experience check", "site:wellfound.com/u", "--red:#BF1E2D", "Never consider gender", "outreach_sequence", "const OFFSETS = [2, 4, 5, 7];", "Not interested? Just reply", "sourcing-app-api", "Recruiter rubric", "The intake agent", "site:devfolio.co", "codeforces.com/api/user.info", "keep only people's profile pages", "site:adplist.org/mentors -inurl:expertise", "hr business partner", "save_templates"):
        assert marker in text, f"fix not applied: {marker}"
    assert "$('Merge GitHub').item" not in text and "PDL Skills" not in text
    OUT.write_text(text + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(nodes)} nodes")


if __name__ == "__main__":
    main()
