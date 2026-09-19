#!/usr/bin/env python3
"""
Fantasy Football Weekly Report
Pulls roster/matchup data from Sleeper (BNA league) and Yahoo (Amsterdam league)
and prints a combined summary you can act on.

FIRST-TIME SETUP (one-time, Yahoo only — Sleeper needs nothing):
  1. Run:  python3 fantasy_report.py --auth
     This prints a URL. Open it in a browser, log into Yahoo, approve access.
  2. You'll land on a page that looks broken (localhost). That's expected.
     Look at the browser's address bar — copy everything after "code=" and
     before any "&" that follows it.
  3. Run:  python3 fantasy_report.py --code PASTE_CODE_HERE
     This exchanges it for tokens and saves them to yahoo_tokens.json.
     You only need to do this once — after that, tokens auto-refresh.

NORMAL WEEKLY USE:
  python3 fantasy_report.py

REQUIREMENTS:
  pip install requests --break-system-packages

CREDENTIALS:
  Yahoo app credentials are read from the environment, not hardcoded.
  Copy .env.example to .env and fill in YAHOO_CLIENT_ID / YAHOO_CLIENT_SECRET,
  or export them directly in your shell.
"""

import os
import sys
import json
import argparse
import requests
from datetime import datetime, timezone


def _load_dotenv(path=".env"):
    """Minimal .env loader (no external dependency)."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

# ── CONFIG — fill these in ───────────────────────────────────────────────
SLEEPER_LEAGUE_ID = "1389372431033991168"   # BNA league (already set)
YAHOO_CLIENT_ID = os.environ.get("YAHOO_CLIENT_ID", "")
YAHOO_CLIENT_SECRET = os.environ.get("YAHOO_CLIENT_SECRET", "")
YAHOO_REDIRECT_URI = os.environ.get("YAHOO_REDIRECT_URI", "https://localhost:8080")
YAHOO_LEAGUE_KEY = "nfl.l.71249"   # Amsterdam league

TOKEN_FILE = "yahoo_tokens.json"
SLEEPER_BASE = "https://api.sleeper.app/v1"
YAHOO_AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
YAHOO_TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
YAHOO_FANTASY_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"


# ── YAHOO OAUTH ───────────────────────────────────────────────────────────
def _require_yahoo_credentials():
    if not YAHOO_CLIENT_ID or not YAHOO_CLIENT_SECRET:
        print("Missing YAHOO_CLIENT_ID / YAHOO_CLIENT_SECRET.")
        print("Copy .env.example to .env and fill them in, or export them in your shell.")
        sys.exit(1)


def yahoo_print_auth_url():
    _require_yahoo_credentials()
    url = (
        f"{YAHOO_AUTH_URL}?client_id={YAHOO_CLIENT_ID}"
        f"&redirect_uri={YAHOO_REDIRECT_URI}"
        f"&response_type=code&language=en-us"
    )
    print("\nOpen this URL, log in, and approve access:\n")
    print(url)
    print("\nThen copy the 'code' value from the resulting URL and run:")
    print("  python3 fantasy_report.py --code YOUR_CODE\n")


def yahoo_exchange_code(code):
    _require_yahoo_credentials()
    resp = requests.post(
        YAHOO_TOKEN_URL,
        data={
            "client_id": YAHOO_CLIENT_ID,
            "client_secret": YAHOO_CLIENT_SECRET,
            "redirect_uri": YAHOO_REDIRECT_URI,
            "code": code,
            "grant_type": "authorization_code",
        },
    )
    resp.raise_for_status()
    tokens = resp.json()
    tokens["saved_at"] = datetime.now(timezone.utc).isoformat()
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)
    print(f"Success. Tokens saved to {TOKEN_FILE}. You're set for future runs.")


def yahoo_refresh_token(tokens):
    _require_yahoo_credentials()
    resp = requests.post(
        YAHOO_TOKEN_URL,
        data={
            "client_id": YAHOO_CLIENT_ID,
            "client_secret": YAHOO_CLIENT_SECRET,
            "redirect_uri": YAHOO_REDIRECT_URI,
            "refresh_token": tokens["refresh_token"],
            "grant_type": "refresh_token",
        },
    )
    resp.raise_for_status()
    new_tokens = resp.json()
    new_tokens["saved_at"] = datetime.now(timezone.utc).isoformat()
    with open(TOKEN_FILE, "w") as f:
        json.dump(new_tokens, f, indent=2)
    return new_tokens


def yahoo_get_tokens():
    if not os.path.exists(TOKEN_FILE):
        print("No Yahoo tokens found yet. Run --auth first, then --code.")
        sys.exit(1)
    with open(TOKEN_FILE) as f:
        return json.load(f)


def yahoo_api_get(endpoint):
    """GET a Yahoo Fantasy API endpoint, refreshing the token if needed."""
    tokens = yahoo_get_tokens()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    url = f"{YAHOO_FANTASY_BASE}/{endpoint}?format=json"
    resp = requests.get(url, headers=headers)
    if resp.status_code == 401:
        tokens = yahoo_refresh_token(tokens)
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json()


# ── SLEEPER (no auth needed) ─────────────────────────────────────────────
PLAYERS_CACHE_FILE = "sleeper_players.json"
PLAYERS_CACHE_MAX_AGE_HOURS = 24


def sleeper_get(endpoint):
    resp = requests.get(f"{SLEEPER_BASE}/{endpoint}")
    resp.raise_for_status()
    return resp.json()


def sleeper_current_week():
    state = requests.get("https://api.sleeper.app/v1/state/nfl").json()
    return state["week"]


def sleeper_load_player_map():
    """Return {player_id: player_info}, using a local cache (the full
    player list is ~5MB, so we only refresh it once a day)."""
    if os.path.exists(PLAYERS_CACHE_FILE):
        with open(PLAYERS_CACHE_FILE) as f:
            cache = json.load(f)
        fetched_at = datetime.fromisoformat(cache["fetched_at"])
        age_hours = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600
        if age_hours < PLAYERS_CACHE_MAX_AGE_HOURS:
            return cache["players"]

    print("(Refreshing Sleeper player database — happens at most once a day...)")
    players = sleeper_get("players/nfl")
    with open(PLAYERS_CACHE_FILE, "w") as f:
        json.dump(
            {"fetched_at": datetime.now(timezone.utc).isoformat(), "players": players},
            f,
        )
    return players


def sleeper_player_label(pid, player_map):
    info = player_map.get(pid)
    if not info:
        if pid and pid.isalpha():
            return pid  # team defense, e.g. "BAL"
        return f"Unknown ({pid})"
    name = info.get("full_name") or f"{info.get('first_name', '')} {info.get('last_name', '')}".strip()
    pos = info.get("position", "?")
    team = info.get("team") or "FA"
    tag = ""
    if info.get("injury_status"):
        tag = f" [{info['injury_status']}]"
    return f"{name} ({pos}-{team}){tag}"


def sleeper_report():
    print("\n" + "=" * 50)
    print("BNA (Sleeper) — Week", sleeper_current_week())
    print("=" * 50)

    player_map = sleeper_load_player_map()
    rosters = sleeper_get(f"league/{SLEEPER_LEAGUE_ID}/rosters")
    users = sleeper_get(f"league/{SLEEPER_LEAGUE_ID}/users")
    week = sleeper_current_week()
    matchups = sleeper_get(f"league/{SLEEPER_LEAGUE_ID}/matchups/{week}")

    user_map = {u["user_id"]: u.get("display_name", "Unknown") for u in users}
    roster_owner = {r["roster_id"]: user_map.get(r["owner_id"], "Unknown") for r in rosters}
    roster_players = {r["roster_id"]: r.get("players", []) for r in rosters}

    for m in matchups:
        owner = roster_owner.get(m["roster_id"], "Unknown")
        starters = m.get("starters", [])
        all_players = roster_players.get(m["roster_id"], [])
        bench = [pid for pid in all_players if pid not in starters]
        points = m.get("players_points", {})

        print(f"\n{owner} — total so far: {m.get('points', 0)}")
        print("  Starters:")
        for pid in starters:
            pts = points.get(pid, "-")
            print(f"    {sleeper_player_label(pid, player_map)}: {pts} pts")
        if bench:
            print("  Bench:")
            for pid in bench:
                pts = points.get(pid, "-")
                print(f"    {sleeper_player_label(pid, player_map)}: {pts} pts")

    # Trending adds league-wide, filtered to players not already rostered here —
    # a real, data-driven pickup signal (no fabricated projections).
    rostered_ids = {pid for players in roster_players.values() for pid in players}
    try:
        trending = sleeper_get("players/nfl/trending/add?lookback_hours=48&limit=50")
    except requests.RequestException:
        trending = []
    available_trending = [t for t in trending if t["player_id"] not in rostered_ids][:10]
    if available_trending:
        print("\n" + "-" * 50)
        print("Trending waiver adds NOT currently on a roster in this league:")
        for t in available_trending:
            label = sleeper_player_label(t["player_id"], player_map)
            print(f"  {label} — added by {t['count']} teams league-wide (last 48h)")


# ── YAHOO ─────────────────────────────────────────────────────────────────
def yahoo_report():
    if not YAHOO_LEAGUE_KEY:
        print("\nAmsterdam (Yahoo) — skipped: YAHOO_LEAGUE_KEY not set yet.")
        print("Send over your Yahoo league ID and I'll fill this in.")
        return

    print("\n" + "=" * 50)
    print("Amsterdam (Yahoo)")
    print("=" * 50)

    data = yahoo_api_get(f"league/{YAHOO_LEAGUE_KEY}/scoreboard")
    print(json.dumps(data, indent=2)[:2000])
    print("\n(Raw data shown for now — once confirmed working, I'll format")
    print("this into the same clean start/sit style as the Sleeper report.)")


# ── MAIN ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--auth", action="store_true", help="Print Yahoo auth URL")
    parser.add_argument("--code", type=str, help="Exchange Yahoo auth code for tokens")
    args = parser.parse_args()

    if args.auth:
        yahoo_print_auth_url()
    elif args.code:
        yahoo_exchange_code(args.code)
    else:
        sleeper_report()
        yahoo_report()
