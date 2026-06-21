"""My World Cup Prediction League bot.

Entrypoint contract (see PARTICIPANT_GUIDE.md):
  - The framework writes a single JSON object to a file and passes its path as
    sys.argv[1] (mounted at /match/input.json).
  - The bot may print anything to stdout; only the LAST non-empty line is read
    as the prediction and must be JSON: {"home_goals": int, "away_goals": int}
    with both values integers in the range 0-20.

This starter uses a simple, transparent heuristic: it fetches every finished
fixture from the public league API (GET {api_url}/api/matches), derives a crude
"attack vs defence" estimate per team, and rounds to a scoreline. If anything
goes wrong (API unreachable, missing data, unexpected errors) it falls back to a
safe 1-1 prediction so the bot never forfeits on an exception.

Replace the logic in `predict_score` with your own model.
"""
import json
import sys
import urllib.request
import json
import functools
import logging
import os
import random
import re
import sys
import time
from urllib.parse import urljoin

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s [zoli-bot] %(levelname)s %(message)s",
)
log = logging.getLogger("zoli-bot")

# gpt model served by the Azure-style company LLM proxy.
MODEL = "gpt-5.4-mini"
API_VERSION = "2025-04-01-preview"


# Clamp predictions to the league's allowed range.
MIN_GOALS = 0
MAX_GOALS = 5

# Used when no historical data is available yet (e.g. opening fixtures).
DEFAULT_GOALS_WINNER = 2
DEFAULT_GOALS_LOSER = 1

# FIFA-style world ranking shipped alongside the bot; injected into the LLM
# prompt. Resolved relative to this file so it works regardless of the CWD
# (the bot runs in a container where the working directory may differ).
RANKING_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "world-ranking.txt")


def _clamp(value: int) -> int:
    """Keep a goal count within the allowed 0-5 range."""
    return max(MIN_GOALS, min(MAX_GOALS, value))


def _parse_score(text: str) -> dict | None:
    """Pull a {home_goals, away_goals} object out of the model's reply.

    Tries a strict JSON parse first, then falls back to finding the first
    ``{...}`` block in the text (models often wrap JSON in prose or code fences).
    """
    candidates = [text]
    match = re.search(r"\{.*}", text, re.DOTALL)
    if match:
        candidates.append(match.group(0))

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and "home_goals" in data and "away_goals" in data:
            parsed = {
                "home_goals": _clamp(data["home_goals"]),
                "away_goals": _clamp(data["away_goals"]),
            }
            log.info(f"Parsed score from LLM reply: {parsed}")
            return parsed
    log.warning("Could not parse a score from LLM reply")
    return None


@functools.lru_cache(maxsize=1)
def _load_world_ranking() -> str:
    """Load and normalise the world ranking for embedding in the LLM prompt.

    The file is ``rank<TAB>team`` per line (with some leading whitespace). We
    reformat each entry as ``<rank>. <team>`` and join them, one per line.
    Cached so repeated prompt builds don't re-read the file. Returns an empty
    string if the file is missing or unreadable, so the prompt still works.
    """
    try:
        with open(RANKING_FILE, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError as exc:  # noqa: BLE001 - missing ranking must never crash the bot
        log.warning(f"Could not read world ranking ({exc}); omitting from prompt")
        return ""

    entries = []
    for line in lines:
        parts = line.split("\t")
        if len(parts) >= 2:
            rank, team = parts[0].strip(), parts[1].strip()
        else:
            # Fall back to whitespace splitting if the line isn't tab-separated.
            bits = line.split(None, 1)
            if len(bits) < 2:
                continue
            rank, team = bits[0].strip(), bits[1].strip()
        if not rank or not team:
            continue
        entries.append(f"{rank}. {team}")

    log.info(f"Loaded {len(entries)} world-ranking entries for the prompt")
    return "\n".join(entries)


def _build_prompt(match: dict) -> str:
    """Build the shared scoreline prompt sent to every model."""
    home, away = match["home_team"], match["away_team"]
    stage = match.get("stage", "group")
    ranking = _load_world_ranking()
    ranking_block = f"{ranking}\n" if ranking else ""

    # Results already played within this fixture's group, so the model can weigh
    # current group standings/form, not just the static world ranking.
    api_url = match.get("api_url")
    group_results = ""
    if api_url:
        group_matches = _get_group_matches(api_url, home, away)
        group_results = _format_group_results(group_matches)
    group_block = (
        "Here are the results already played in this group so far:\n"
        f"{group_results}\n"
        if group_results
        else ""
    )

    return (
        "You are a football pundit predicting a World Cup match.\n"
        f"Fixture ({stage} stage): {home} (home) vs {away} (away).\n"
        "Predict the final score.\n"
        "Consider, that we are in the group stage,\n"
        "so the main target for the teams might not be to risk a lot and win.\n"
        "Sometimes they just want to secure their spot in the knockout stage.\n"
        "There are groups of 4. It is safe to say, that teams aim for the top 2 spots in their group.\n"
        "Win: 3 points, draw: 1 point.\n"
        "If two or more teams have the same amount of points, their head-to-head record counts.\n"
        "For more information, here is the current world-ranking (rank and team):\n"
        f"{ranking_block}"
        f"{group_block}"
        'Reply with ONLY a JSON object of this exact form: '
        '{"home_goals": <int>, "away_goals": <int>}'
    )


def _fetch_finished_matches(api_url: str) -> list:
    """Return all finished fixtures from the public league API.

    Returns an empty list if the API is unreachable or returns bad data, so the
    caller can fall back to a default prediction instead of crashing.
    """
    try:
        url = api_url.rstrip("/") + "/api/matches"
        with urllib.request.urlopen(url, timeout=10) as resp:
            matches = json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001 - never let a fetch error forfeit
        print(f"Could not fetch matches ({exc}); using defaults.")
        return []

    finished = []
    for m in matches:
        if m.get("status") != "finished":
            continue
        if m.get("result_home") is None or m.get("result_away") is None:
            continue
        finished.append(m)
    return finished


def _get_group_matches(api_url: str, home: str, away: str) -> list:
    """Get the games that the home team or the away team has played in or will play in.
    Also get the match where the other 2 teams played from the same 4-team group."""
    try:
        url = api_url.rstrip("/") + "/api/matches"
        with urllib.request.urlopen(url, timeout=10) as resp:
            matches = json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001 - never let a fetch error forfeit
        print(f"Could not fetch matches ({exc}); using defaults.")
        return []

    group_matches = []
    group_teams = [home, away]
    # Discover the other group members: any team that shares a fixture with a
    # known group member belongs to the same group. A World Cup group has 4
    # teams, so stop once we have found them all.
    for m in matches:
        ht, at = m.get("home_team"), m.get("away_team")
        if ht in group_teams and at not in group_teams:
            group_teams.append(at)
        elif at in group_teams and ht not in group_teams:
            group_teams.append(ht)
        if len(group_teams) >= 4:
            break
    # Collect every match played strictly between two members of the group
    # (this includes the fixture between the other two teams).
    for m in matches:
        if m.get("home_team") in group_teams and m.get("away_team") in group_teams:
            group_matches.append(m)
    return group_matches


def _format_group_results(group_matches: list) -> str:
    """Render the group matches as readable scorelines for the prompt.

    Produces one line per played match, e.g. ``Spain 1-0 Mexico``.
    Result-less fixtures are skipped. Returns an empty string if none are
    finished yet, so the caller can omit the section entirely.
    """
    lines = []
    for m in group_matches:
        if m.get("status") != "finished":
            lines.append(
                f"{m.get('home_team')} {m.get('away_team')} - Yet to be played."
            )
        if m.get("result_home") is None or m.get("result_away") is None:
            continue
        lines.append(
            f"{m.get('home_team')} {m['result_home']}-{m['result_away']} {m.get('away_team')}"
        )
    return "\n".join(lines)


def _team_form(finished: list) -> dict:
    """Build a per-team {scored, conceded, played} tally from finished matches."""
    form: dict = {}

    def bucket(team: str) -> dict:
        return form.setdefault(team, {"scored": 0, "conceded": 0, "played": 0})

    for m in finished:
        home, away = m["home_team"], m["away_team"]
        hg, ag = m["result_home"], m["result_away"]

        h = bucket(home)
        h["scored"] += hg
        h["conceded"] += ag
        h["played"] += 1

        a = bucket(away)
        a["scored"] += ag
        a["conceded"] += hg
        a["played"] += 1

    return form


def _expected_goals(attacker: dict, defender: dict, league_avg: float) -> float:
    """Crude expected-goals estimate: blend attacker's scoring and defender's leakiness."""
    atk = attacker["scored"] / attacker["played"] if attacker["played"] else league_avg
    dfn = defender["conceded"] / defender["played"] if defender["played"] else league_avg
    return (atk + dfn) / 2


def predict_score(match: dict) -> tuple:
    """Return a (home_goals, away_goals) prediction for the given match.

    Strategy: ask the LLM first (it has the world ranking, stage context, and
    can reason about group dynamics). If the LLM is unconfigured, errors out, or
    returns an unparseable reply, fall back to the transparent statistical
    heuristic. The heuristic itself falls back to a safe default when there is no
    historical data, so the bot never forfeits on an exception.
    """
    home = match["home_team"]
    away = match["away_team"]

    llm_prediction = _ask_llm(match)
    if llm_prediction is not None:
        hg = _clamp(int(llm_prediction["home_goals"]))
        ag = _clamp(int(llm_prediction["away_goals"]))
        print(f"LLM prediction for {home} vs {away}: {hg}-{ag}")
        return hg, ag

    print("LLM unavailable or unparseable; falling back to statistical heuristic.")
    return _heuristic_score(match)


def _heuristic_score(match: dict) -> tuple:
    """Transparent statistical fallback: crude attack-vs-defence expected goals.

    Falls back to a safe default scoreline when there is no historical data.
    """
    api_url = match.get("api_url")
    finished = _fetch_finished_matches(api_url) if api_url else []

    if not finished:
        print("No finished matches available yet; predicting a 1-1 draw.")
        return DEFAULT_GOALS_WINNER, DEFAULT_GOALS_LOSER

    # League-wide average goals per team per match, used for teams with no history.
    total_goals = sum(m["result_home"] + m["result_away"] for m in finished)
    league_avg = total_goals / (2 * len(finished)) if finished else DEFAULT_GOALS_WINNER

    form = _team_form(finished)
    home = match["home_team"]
    away = match["away_team"]

    home_form = form.get(home, {"scored": 0, "conceded": 0, "played": 0})
    away_form = form.get(away, {"scored": 0, "conceded": 0, "played": 0})

    home_xg = _expected_goals(home_form, away_form, league_avg)
    away_xg = _expected_goals(away_form, home_form, league_avg)

    # Small home-field nudge.
    home_xg += 0.2

    print(f"xG estimate: {home}={home_xg:.2f}, {away}={away_xg:.2f} (league avg {league_avg:.2f})")
    return _clamp(round(home_xg)), _clamp(round(away_xg))


def _invoke(llm, prompt: str, label: str) -> dict | None:
    """Invoke a LangChain chat model and parse its reply into a score.

    Returns a parsed prediction, or None if the call fails or the reply can't
    be parsed.
    """
    log.info(f"[{label}] Prompt sent: {prompt.replace('\n', ' | ')}")
    started = time.monotonic()
    try:
        reply = llm.invoke(prompt).content
    except Exception as exc:  # noqa: BLE001 — any provider/network error → None
        log.error(f"[{label}] call failed after {time.monotonic() - started:.2f}s: {exc!r}")
        return None

    log.info(f"[{label}] responded in {time.monotonic() - started:.2f}s")
    log.info(f"[{label}] raw reply: {reply!r}")
    return _parse_score(reply if isinstance(reply, str) else str(reply))


def _ask_llm(match: dict) -> dict | None:
    """Ask GPT-4.2-nano for a predicted scoreline via the company LLM proxy.

    Returns a parsed prediction, or None if the proxy is unconfigured, the call
    fails, or the reply can't be parsed.
    """
    proxy_url = match.get("llm_proxy_url")
    if not proxy_url:
        log.info(f"No LLM proxy configured (url={bool(proxy_url)}) — skipping LLM")
        return None

    # The proxy authenticates by network/identity, so a token is optional;
    # fall back to a dummy key when none is supplied (as the proxy expects).
    api_key = match.get("llm_api_key") or "DUMMY"
    endpoint = proxy_url

    log.info(f"Connecting to Azure LLM proxy at {endpoint} (deployment {MODEL})")
    # Imported lazily so an import error surfaces only on this provider's path.
    from langchain_openai import AzureChatOpenAI

    llm = AzureChatOpenAI(
        azure_endpoint=endpoint,
        azure_deployment=MODEL,
        api_version=API_VERSION,
        api_key=api_key,
        temperature=0,
    )

    return _invoke(llm, _build_prompt(match), "azure")


def main() -> None:
    match = json.loads(open(sys.argv[1]).read())

    home = match["home_team"]
    away = match["away_team"]

    home_goals, away_goals = predict_score(match)

    # Debug lines are fine — the framework ignores everything except the last line.
    print(f"Predicting {home} vs {away}: {home_goals}-{away_goals}")

    # This MUST be the last non-empty line printed.
    print(json.dumps({"home_goals": home_goals, "away_goals": away_goals}))


if __name__ == "__main__":
    main()

