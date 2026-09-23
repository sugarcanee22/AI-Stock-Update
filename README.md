# Portfolio Watch

Runs daily at **7:00 PM Singapore time** via GitHub Actions. Researches each
ticker for material news from the last 4 days, skips stories already sent in
a prior "Portfolio Watch" DM, and sends the dossier as a Slack DM.

Monitored tickers (SGX): `AWX, S58, C09, D05, G13` — edit the `TICKERS` line
in `.github/workflows/portfolio-watch.yml` to change them.

## 1. Create a Slack app / bot token

1. Go to https://api.slack.com/apps -> **Create New App** -> From scratch.
2. Under **OAuth & Permissions**, add these **Bot Token Scopes**:
   - `users:read.email` — to look up the recipient by email
   - `im:write` — to open a DM channel
   - `im:history` — to check for prior dossiers already sent
   - `chat:write` — to send the DM
3. **Install to Workspace**, then copy the **Bot User OAuth Token**
   (`xoxb-...`).
4. Make sure the recipient (`laijaslyn5@gmail.com`) is a member of the
   workspace and has that email set on their Slack profile.

## 2. Get an Anthropic API key

Create one at https://console.anthropic.com/settings/keys (this is a
pay-as-you-go API key, separate from a claude.ai subscription).

## 3. Add repo secrets

In your GitHub repo: **Settings -> Secrets and variables -> Actions -> New
repository secret**. Add:

| Secret name         | Value                                  |
|----------------------|-----------------------------------------|
| `ANTHROPIC_API_KEY`  | your Anthropic API key                  |
| `SLACK_BOT_TOKEN`    | the `xoxb-...` bot token from step 1    |
| `RECIPIENT_EMAIL`    | `laijaslyn5@gmail.com`                  |

## 4. Push this repo to GitHub

The workflow in `.github/workflows/portfolio-watch.yml` will then run
automatically every day at 7 PM SGT (11:00 UTC cron). You can also trigger
it manually any time from the **Actions** tab -> Portfolio Watch -> **Run
workflow**.

## Notes / limitations

- If `SLACK_BOT_TOKEN` is missing, invalid, or the bot isn't authorized in
  the workspace, the script **skips Slack delivery** (history check and DM)
  but still runs the research and prints the dossier to the Actions log —
  it will not fail the whole run.
- "Already covered" detection works by scanning the last `HISTORY_LOOKBACK`
  (default 50) messages in the DM thread for prior messages containing
  "Portfolio Watch", and passing their text to Claude as context so it
  avoids repeating unchanged stories.
- GitHub Actions' `schedule` cron can run a few minutes late during high
  load; this is a GitHub platform behavior, not something the workflow
  controls.
- Costs: each run makes one Anthropic API call with web search enabled
  (billed per Anthropic's API pricing) — https://www.anthropic.com/pricing.
