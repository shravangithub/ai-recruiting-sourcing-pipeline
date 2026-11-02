# Sourcing from anywhere

Five ways to start a search or add a person to the Board, all in plain words. The same brief
parser reads every one, so a brief gives the same search wherever you say it. Each channel also
explains itself: Telegram on `/start` and `help`, email when a mail asks for nothing, the browser
button in its popup, the app under the brief box.

## How to write a brief

Say it the way you'd tell a colleague. One or two sentences is enough. The parser picks up:

- **Role**: "senior product manager", "backend engineer". Close titles are searched too.
- **Place**: a city ("in Bangalore"). Say "remote" or "anywhere in India" if it matters.
- **Experience**: "5 to 8 years", "around 3 years", "8+", "senior".
- **Must-haves**: "must have", "has done", "hands on with".
- **Nice-to-haves**: "nice if", "ideally", "bonus".
- **Companies or colleges**: "from a payments company", "IIT or NIT".
- **Exclusions**: "not agencies", "no freshers".
- **Channels**: "on LinkedIn and GitHub", "include research papers", "everywhere", "LinkedIn only".
  Say nothing and the defaults apply.
- **Veterans**: "ex-servicemen" or "veterans" switches the veteran search on.

## In the app, and on your phone

Board → **New role** → type the brief → **Find people**. The app shows what it understood before it
starts. On a phone, sign in with **Stay signed in on this device** ticked and add the page to the
home screen; it opens signed in.

- "I need a senior product manager in Bangalore, around 5 to 8 years, who has done pricing for B2B
  SaaS. Must be hands on with SQL. Nice if they have worked at a payments company. Look on LinkedIn,
  GitHub and research papers."
- "Backend engineer in Pune, 3 to 5 years, Go or Java, has run services on Kubernetes. No agencies.
  LinkedIn and GitHub only."
- "Ex-servicemen for a security operations lead in Hyderabad, 10+ years, has managed a team of 20."

## On Telegram, typed or spoken

Set-up once: an admin makes a bot (BotFather → `/newbot`) and pastes the token in Settings →
Telegram; each teammate presses **Connect Telegram** and sends `/start CODE` to the bot.

Send the brief as text or a voice note. The bot repeats what it understood and which channels it
will search, and sends the top 5 when done. A profile link on its own adds that person to the role
you're on. `roles`, `role 2`, `top`, `more`, `shortlist 1 3`, `not fit 2`, `approve 1`, `send R1`,
`later R1`, `status`, `help`.

## By email

From your own work address, to the mailbox n8n uses for outreach. Replies take up to 15 minutes.

- Start a search: subject `Search: Senior Product Manager, Bangalore`, brief in the body. You get a
  reply naming the channels, then the "N candidates ready" email when it finishes.
- Add a person: subject `Fwd: Backend Engineer, Pune`, a link or a CV (PDF read) in the mail. You
  get the score back.
- Anything else from a teammate (subject "help", or an empty mail) gets these instructions back.

## From the browser

Settings → Phone & browser → drag **Add to Board** to the bookmarks bar. On any profile page, click
it: pick the role, see the score, and the person is in Found. It adds one person; it doesn't search.

## What each channel can do

| | App / phone | Telegram | Voice note | Email | Browser button |
|---|---|---|---|---|---|
| Start a search from a brief | Yes | Yes | Yes | Yes (`Search:`) | No |
| Pick channels | Tick boxes, or name them | Name them | Name them | Name them | n/a |
| Full form (rubric, JD, exclusions) | Yes | No | No | No | No |
| Add one person | Paste in the app | Send a link | No | Forward a CV or link | Yes |
| See results | Board | Top 5 in chat | Top 5 in chat | Results email + Board | Popup + Board |
| Shortlist, approve, answer replies | Yes | By number | No | No | No |
