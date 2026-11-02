# Making apply pages reachable from the internet

The app, the Board and your n8n editor are meant for your team. Two small pages are meant for
outsiders, and only work when n8n has a public **https** address:

- the **apply page** of a role (`/webhook/sourcing-apply?r=…`), and
- the **open-tracking image** in emails (`/webhook/sourcing-open`), if you switch it on.

If n8n runs on your own computer, a free Cloudflare tunnel gives it that address. The steps below
expose **only those two pages**, not your n8n editor or the app.

## Option 1: try it in two minutes (temporary address)

Good for a test. The address changes every time you start it, and it exposes all of n8n (the editor
still asks for your n8n login), so stop it when you're done.

1. Install: `brew install cloudflared` (Mac) or see Cloudflare's downloads page.
2. Run: `cloudflared tunnel --url http://localhost:5678`
3. Copy the `https://….trycloudflare.com` address it prints into **Settings → Sending → public address**
   in the app. Open a role's apply page on the Board and copy the link.

## Option 2: a lasting address that exposes only the two pages (recommended)

You need a free Cloudflare account and a domain managed in Cloudflare (for example `jobs.yourfirm.com`).

1. `cloudflared tunnel login`, then `cloudflared tunnel create sourcing`
2. `cloudflared tunnel route dns sourcing jobs.yourfirm.com`
3. Create `~/.cloudflared/config.yml`:

   ```yaml
   tunnel: sourcing
   credentials-file: /Users/YOU/.cloudflared/<tunnel-id>.json
   ingress:
     # only the public pages; everything else answers 404
     - hostname: jobs.yourfirm.com
       path: ^/webhook/sourcing-(apply|apply-api|open)$
       service: http://localhost:5678
     - service: http_status:404
   ```

4. Run `cloudflared tunnel run sourcing` (or `sudo cloudflared service install` to start it with the computer).
5. In the app: **Settings → Sending → public address** = `https://jobs.yourfirm.com`.

Your computer must be on and n8n running for applicants to reach the page. If that's not practical,
run n8n on a small always-on server instead; the same page and the same settings work there.

## What applicants see and what is kept

- The role title, place, experience and the must-haves and nice-to-haves from the brief. Never your
  internal notes, the company names you target, or other candidates.
- They give name, email, a profile link, and optionally current role, city and a note, and tick a
  consent box. Nothing else is collected.
- The page is closed by default; the team opens it per role on the Board and can close it any time.
- One application per email per role, at most 200 per role; a hidden field catches simple bots.
