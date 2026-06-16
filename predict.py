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

# Clamp predictions to the league's allowed range.
MIN_GOALS = 0
MAX_GOALS = 20

# Used when no historical data is available yet (e.g. opening fixtures).
DEFAULT_GOALS = 1


def _clamp(value: int) -> int:
    """Keep a goal count within the allowed 0-20 range."""
    return max(MIN_GOALS, min(MAX_GOALS, value))


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

    Heuristic only — swap this out for your own model. Falls back to a draw when
    there is no data to learn from.
    """
    api_url = match.get("api_url")
    finished = _fetch_finished_matches(api_url) if api_url else []

    if not finished:
        print("No finished matches available yet; predicting a 1-1 draw.")
        return DEFAULT_GOALS, DEFAULT_GOALS

    # League-wide average goals per team per match, used for teams with no history.
    total_goals = sum(m["result_home"] + m["result_away"] for m in finished)
    league_avg = total_goals / (2 * len(finished)) if finished else DEFAULT_GOALS

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

