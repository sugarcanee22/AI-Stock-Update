#!/usr/bin/env python3
"""
Portfolio Watch — daily ticker monitor.

For each ticker in TICKERS, searches the web (via the Claude API's web_search
tool) for material news/catalysts from the last 4 days, skips anything
already covered in a prior "Portfolio Watch" DM (checked via Slack history),
and sends a concise dossier as a Slack DM to RECIPIENT_EMAIL.

Required environment variables (set as GitHub Actions secrets):
  ANTHROPIC_API_KEY   - Anthropic API key
  SLACK_BOT_TOKEN     - Slack bot token (xoxb-...) with scopes:
                           users:read.email, im:write, im:history, chat:write
  RECIPIENT_EMAIL     - email of the Slack user to DM

Optional:
  TICKERS             - comma-separated list, overrides the default below
  CLAUDE_MODEL         - defaults to "claude-sonnet-5"
  HISTORY_LOOKBACK     - number of past DM messages to scan for prior
                          dossiers (default 50)
"""

import os
import sys
import json
from datetime import datetime, timezone

from anthropic import Anthropic
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

DEFAULT_TICKERS = ["AWX", "S58", "C09", "D05", "G13"]

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")
HISTORY_LOOKBACK = int(os.environ.get("HISTORY_LOOKBACK", "50"))

SOURCES_HINT = (
    "investing.com, theedgesingapore.com, fool.com (Motley Fool), nbcnews.com, "
    "marketwatch.com, tradingkey.com, tipranks.com, and reuters.com"
)


def get_tickers():
    raw = os.environ.get("TICKERS")
    if raw:
        return [t.strip().upper() for t in raw.split(",") if t.strip()]
    return DEFAULT_TICKERS


def slack_client():
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        return None
    return WebClient(token=token)


def find_dm_channel(client, email):
    """Resolve a Slack user ID by email, then open/get their DM channel."""
    resp = client.users_lookupByEmail(email=email)
    user_id = resp["user"]["id"]
    dm = client.conversations_open(users=[user_id])
    return user_id, dm["channel"]["id"]


def fetch_prior_dossiers(client, channel_id, limit):
    """Pull recent DM history and return raw text of past Portfolio Watch
    messages, so the research prompt can avoid repeating stale stories."""
    try:
        history = client.conversations_history(channel=channel_id, limit=limit)
    except SlackApiError as e:
        print(f"[warn] could not read DM history: {e.response['error']}", file=sys.stderr)
        return []

    prior_texts = []
    for msg in history.get("messages", []):
        text = msg.get("text", "")
        if "Portfolio Watch" in text:
            prior_texts.append(text)
    return prior_texts


def build_prompt(tickers, prior_dossiers):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    prior_block = "\n\n---\n\n".join(prior_dossiers) if prior_dossiers else "(none found)"

    checklist = "\n".join(f"  {i+1}. {t}" for i, t in enumerate(tickers))

    return f"""You are monitoring a stock portfolio for material, price-moving news.

Today's date: {today}

TICKERS TO MONITOR ({len(tickers)} total — every single one below MUST be
individually researched, with no exceptions):
{checklist}

MANDATORY SEARCH PROCESS — follow this exactly, do not skip or shortcut it:
For EACH ticker in the list above, run at least one dedicated web search
using that ticker's symbol AND its company name (e.g. search both "{tickers[0]}"
and the company name it refers to) before deciding whether it has material
news. Do not rely on general knowledge or skip a ticker because an earlier
search for a different ticker seemed to cover the market broadly — each
ticker gets its own explicit search pass. Work through the list in order,
one ticker at a time, and only move to the write-up step once all
{len(tickers)} tickers have been individually searched.

For each ticker, look for material news, catalysts, or developing events
from today and the last 4 days — earnings, guidance changes, M&A,
regulatory/legal action, major product/contract announcements, executive
changes, analyst rating changes with notable price-target moves, supply
chain or geopolitical developments, and relevant macro events.

Prioritize these sources when available: {SOURCES_HINT}.

PRIOR DOSSIERS ALREADY SENT TO THE USER (do not re-report these unless there
is a genuinely NEW, material update since they were last reported):
{prior_block}

OUTPUT FORMAT (Slack message, plain text, Slack-friendly formatting only —
use *bold* with single asterisks, line breaks, no markdown headers, no
tables):

*Portfolio Watch — {today}*

For each ticker with a genuinely new, material development not already
covered above, include:
*TICKER* — one-line event/catalyst summary
Near-term (days–weeks): <impact forecast>
Longer-term (months+): <impact forecast>

Leave a blank line between tickers.

If a ticker has no material new development, omit it entirely — do not pad
the report with routine/no-news items.

If NOTHING material happened across the whole list, output only:
*Portfolio Watch — {today}*
No material new developments across the monitored tickers today.

Do not include a preamble, sign-off, or any text outside the format above.
"""


def run_research(tickers, prior_dossiers):
    client = Anthropic()  # picks up ANTHROPIC_API_KEY from env
    prompt = build_prompt(tickers, prior_dossiers)

    message = client.messages.create(
        model=MODEL,
        max_tokens=4000,  # raised to give room for a dedicated search per ticker
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}],
    )

    text_parts = [block.text for block in message.content if block.type == "text"]
    return "\n".join(text_parts).strip()


def main():
    tickers = get_tickers()
    recipient_email = os.environ.get("RECIPIENT_EMAIL")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[error] ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    if not recipient_email:
        print("[error] RECIPIENT_EMAIL not set", file=sys.stderr)
        sys.exit(1)

    client = slack_client()
    prior_dossiers = []
    channel_id = None

    if client is None:
        print("[warn] SLACK_BOT_TOKEN not set — skipping Slack history check "
              "and delivery. Will still run research and print the result.",
              file=sys.stderr)
    else:
        try:
            _, channel_id = find_dm_channel(client, recipient_email)
            prior_dossiers = fetch_prior_dossiers(client, channel_id, HISTORY_LOOKBACK)
        except SlackApiError as e:
            print(f"[warn] Slack auth/lookup failed ({e.response['error']}) — "
                  f"skipping delivery, will still run research.", file=sys.stderr)
            client = None

    dossier = run_research(tickers, prior_dossiers)
    print(dossier)

    if client is not None and channel_id is not None:
        try:
            client.chat_postMessage(channel=channel_id, text=dossier)
            print("[ok] Sent Portfolio Watch DM.", file=sys.stderr)
        except SlackApiError as e:
            print(f"[warn] Failed to send Slack DM ({e.response['error']}). "
                  f"Dossier was generated but NOT delivered.", file=sys.stderr)
    else:
        print("[info] Slack delivery skipped (not authorized/connected).",
              file=sys.stderr)


if __name__ == "__main__":
    main()
