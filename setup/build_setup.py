#!/usr/bin/env python3
"""Assemble setup/setup.workflow.json from setup/src/*.

  python3 setup/build_setup.py                 # template from GitHub main
  python3 setup/build_setup.py --template URL  # another template location (testing / forks)
"""
import argparse, json, pathlib

ROOT = pathlib.Path(__file__).resolve().parent
SRC = ROOT / "src"
DEFAULT_TEMPLATE = "https://raw.githubusercontent.com/shravangithub/ai-recruiting-sourcing-pipeline/main/setup/pipeline.workflow.json"

BUILD_PAGE = """// Serve the setup page with this n8n's own API address filled in.
const h = $input.first().json.headers || {};
const host = h['x-forwarded-host'] || h.host || '';
let proto = String(h['x-forwarded-proto'] || '').split(',')[0].trim();
if (!proto) proto = /^(localhost|127\\.0\\.0\\.1|\\[::1\\])(:\\d+)?$/.test(host) ? 'http' : 'https';
const api = proto + '://' + host + '/webhook/sourcing-setup-api';
const html = %s;
return [{ json: { html: html.split('__API_URL__').join(api) } }];
"""

SWITCH_OFF = """// After a successful install, this setup workflow switches itself off.
const it = $input.first().json;
if (it.switchOff && it.k && it.apiBase) {
  try {
    await this.helpers.httpRequest({ method: 'POST', url: it.apiBase + '/api/v1/workflows/' + $workflow.id + '/deactivate',
      headers: { 'X-N8N-API-KEY': it.k }, json: true });
  } catch (e) { /* not fatal: the recruiter can switch it off by hand */ }
}
return [{ json: { done: true } }];
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", default=DEFAULT_TEMPLATE)
    ap.add_argument("--out", default=str(ROOT / "setup.workflow.json"))
    a = ap.parse_args()

    html = (SRC / "setup.html").read_text()
    handler = (SRC / "handler.js").read_text().replace("__TEMPLATE_URL__", a.template)

    nodes = [
        {"id": "s1", "name": "Open setup page", "type": "n8n-nodes-base.webhook", "typeVersion": 2, "position": [0, 0],
         "webhookId": "4b1f3c7e-0c8e-4d7a-9a51-3e6d2f8b1a01",
         "parameters": {"httpMethod": "GET", "path": "sourcing-setup", "responseMode": "responseNode", "options": {}}},
        {"id": "s2", "name": "Build page", "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [220, 0],
         "parameters": {"jsCode": BUILD_PAGE % json.dumps(html)}},
        {"id": "s3", "name": "Send page", "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.1, "position": [440, 0],
         "parameters": {"respondWith": "text", "responseBody": "={{ $json.html }}",
                        "options": {"responseHeaders": {"entries": [{"name": "Content-Type", "value": "text/html; charset=utf-8"}]}}}},
        {"id": "s4", "name": "Setup API", "type": "n8n-nodes-base.webhook", "typeVersion": 2, "position": [0, 260],
         "webhookId": "9d2e6a10-5f3b-4c21-8e77-1c4b9e0d2f02",
         "parameters": {"httpMethod": "POST", "path": "sourcing-setup-api", "responseMode": "responseNode",
                        "options": {"allowedOrigins": "*"}}},
        {"id": "s5", "name": "Handle request", "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [220, 260],
         "parameters": {"jsCode": handler}},
        {"id": "s6", "name": "Reply", "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.1, "position": [440, 260],
         "parameters": {"respondWith": "json", "responseBody": "={{ JSON.stringify($json.response) }}", "options": {}}},
        {"id": "s7", "name": "Switch setup off", "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [660, 260],
         "parameters": {"jsCode": SWITCH_OFF}},
        {"id": "s8", "name": "README", "type": "n8n-nodes-base.stickyNote", "typeVersion": 1, "position": [-40, -260],
         "parameters": {"width": 720, "height": 220, "content":
            "## One-click setup\n1. Click **Publish / Activate** (top right).\n2. Open the **Open setup page** node and copy its **Production URL**.\n"
            "3. Open that URL in your browser and follow the steps.\n\nKeys you paste are used once to create n8n credentials and are never stored by this workflow. "
            "Execution saving is off for this workflow, so they never appear in your execution logs. When setup finishes, this workflow switches itself off."}},
    ]
    connections = {
        "Open setup page": {"main": [[{"node": "Build page", "type": "main", "index": 0}]]},
        "Build page": {"main": [[{"node": "Send page", "type": "main", "index": 0}]]},
        "Setup API": {"main": [[{"node": "Handle request", "type": "main", "index": 0}]]},
        "Handle request": {"main": [[{"node": "Reply", "type": "main", "index": 0}]]},
        "Reply": {"main": [[{"node": "Switch setup off", "type": "main", "index": 0}]]},
    }
    wf = {
        "name": "Sourcing Pipeline · One-click Setup",
        "nodes": nodes,
        "connections": connections,
        "settings": {"executionOrder": "v1", "saveDataSuccessExecution": "none", "saveDataErrorExecution": "none",
                     "saveManualExecutions": False, "saveExecutionProgress": False},
    }
    pathlib.Path(a.out).write_text(json.dumps(wf, ensure_ascii=False, indent=1) + "\n")
    print("wrote", a.out)


if __name__ == "__main__":
    main()
