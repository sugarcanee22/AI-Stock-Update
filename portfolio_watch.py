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

DEFAULT_TICKERS = [
    ("D05", "DBS Group Holdings"),
    ("S58", "SATS Ltd"),
    ("C09", "City Developments Limited (CDL)"),
    ("G13", "Genting Singapore"),
    ("AWX", "AEM Holdings"),
]

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")
HISTORY_LOOKBACK = int(os.environ.get("HISTORY_LOOKBACK", "50"))

SOURCES_HINT = (
    "investing.com, theedgesingapore.com, fool.com (Motley Fool), nbcnews.com, "
    "marketwatch.com, tradingkey.com, tipranks.com, and reuters.com"
)


def get_tickers():
    """Returns a list of (ticker, company_name) tuples.

    TICKERS env var format: "D05:DBS Group Holdings,S58:SATS Ltd,..."
    A bare ticker with no ":name" is also accepted (name left blank).
    """
    raw = os.environ.get("TICKERS")
    if not raw:
        return DEFAULT_TICKERS

    pairs = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            ticker, name = item.split(":", 1)
            pairs.append((ticker.strip().upper(), name.strip()))
        else:
            pairs.append((item.upper(), ""))
    return pairs


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


def build_ticker_prompt(ticker, company_name, today, prior_dossiers):
    """Build a prompt scoped to exactly ONE ticker, so each ticker gets its
    own guaranteed API call + search pass rather than sharing a budget with
    the rest of the list."""

    prior_block = "\n\n---\n\n".join(prior_dossiers) if prior_dossiers else "(none found)"
    identity = f"{ticker} ({company_name})" if company_name else ticker

    return f"""You are monitoring a single stock for material, price-moving news.

Today's date: {today}
STOCK: {identity} — trades on the Singapore Exchange (SGX) under ticker
code {ticker}.

TASK:
Run at least one web search for this stock using its company name
("{company_name}") AND at least one search using its SGX ticker code
("{ticker}"), since the code alone is often ambiguous outside Singapore.
Look for material news, catalysts, or developing events from today and the
last 4 days — earnings, guidance changes, M&A, regulatory/legal action,
major product/contract announcements, executive changes, analyst rating
changes with notable price-target moves, supply chain or geopolitical
developments, and relevant macro events.

Prioritize these sources when available: {SOURCES_HINT}.

PRIOR DOSSIERS ALREADY SENT TO THE USER FOR THIS STOCK (do not re-report
these unless there is a genuinely NEW, material update since they were last
reported):
{prior_block}

OUTPUT:
If there is a genuinely new, material development not already covered
above, respond with ONLY this block (Slack-friendly plain text, *bold*
with single asterisks, no markdown headers, no tables, no preamble):

*{ticker} ({company_name})* — one-line event/catalyst summary
Near-term (days–weeks): <impact forecast>
Longer-term (months+): <impact forecast>

If there is NO material new development for this stock, respond with
exactly the single word: NONE
Do not include any other text, explanation, or preamble in either case.
"""


def research_one_ticker(client, ticker, company_name, today, prior_dossiers):
    prompt = build_ticker_prompt(ticker, company_name, today, prior_dossiers)

    message = client.messages.create(
        model=MODEL,
        max_tokens=1200,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}],
    )

    text_parts = [block.text for block in message.content if block.type == "text"]
    result = "\n".join(text_parts).strip()

    if result.upper() == "NONE" or not result:
        print(f"[info] {ticker}: no material development found.", file=sys.stderr)
        return None

    print(f"[info] {ticker}: material development found.", file=sys.stderr)
    return result


def run_research(tickers, prior_dossiers):
    """Runs one dedicated, independent API call per ticker so every ticker
    is guaranteed its own search pass — coverage of one ticker never eats
    into another's budget. `tickers` is a list of (ticker, company_name)."""
    client = Anthropic()  # picks up ANTHROPIC_API_KEY from env
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    ticker_blocks = []
    for ticker, company_name in tickers:
        try:
            block = research_one_ticker(client, ticker, company_name, today, prior_dossiers)
        except Exception as e:
            print(f"[warn] {ticker}: research call failed ({e}) — skipping this "
                  f"ticker, continuing with the rest.", file=sys.stderr)
            block = None
        if block:
            ticker_blocks.append(block)

    header = f"*Portfolio Watch — {today}*"

    if not ticker_blocks:
        return f"{header}\nNo material new developments across the monitored tickers today."

    return header + "\n\n" + "\n\n".join(ticker_blocks)


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
