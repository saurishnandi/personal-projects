import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen

DATA_FILE = "data/results.json"

START_DATE = datetime(2026, 6, 11, tzinfo=timezone.utc)
END_DATE = datetime(2026, 7, 19, tzinfo=timezone.utc)

SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/"
    "scoreboard?dates={date}&limit=100"
)

ALIASES = {
    "usa": "usa",
    "us": "usa",
    "unitedstates": "usa",
    "unitedstatesofamerica": "usa",
    "unitedstatesmen": "usa",

    "southkorea": "korearepublic",
    "republicofkorea": "korearepublic",
    "korearepublic": "korearepublic",

    "iran": "iriran",
    "iriran": "iriran",
    "islamicrepublicofiran": "iriran",

    "ivorycoast": "cotedivoire",
    "cotedivoire": "cotedivoire",

    "turkey": "turkiye",
    "turkiye": "turkiye",

    "curacao": "curacao",

    "czechrepublic": "czechia",
    "czechia": "czechia",

    "bosniaherzegovina": "bosniaandherzegovina",
    "bosniaandherzegovina": "bosniaandherzegovina",

    "drcongo": "congodr",
    "drc": "congodr",
    "congodr": "congodr",
    "democraticrepublicofcongo": "congodr",

    "capeverde": "caboverde",
    "capeverdeislands": "caboverde",
    "caboverde": "caboverde",

    "newzealand": "newzealand",
    "nz": "newzealand"
}


def norm(value):
    value = unicodedata.normalize("NFD", str(value or ""))
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]", "", value.lower())


def canon(value):
    cleaned = norm(value)
    return ALIASES.get(cleaned, cleaned)


def same_pair(a, b, c, d):
    return sorted([canon(a), canon(b)]) == sorted([canon(c), canon(d)])


def parse_score(value):
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def fetch_json(url):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def date_keys_to_fetch():
    now = datetime.now(timezone.utc)

    if now < START_DATE:
        end = START_DATE
    else:
        end = min(END_DATE, now + timedelta(days=1))

    keys = []
    current = START_DATE

    while current <= end:
        keys.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)

    return keys


def parse_espn_events(payload, date_key):
    events = []

    for event in payload.get("events", []):
        competitions = event.get("competitions") or []
        if not competitions:
            continue

        competition = competitions[0]
        status_type = (
            competition.get("status", {})
            .get("type", {})
        )

        completed = bool(status_type.get("completed")) or status_type.get("name") in {
            "STATUS_FINAL",
            "STATUS_FULL_TIME"
        }

        if not completed:
            continue

        competitors = competition.get("competitors") or []
        if len(competitors) < 2:
            continue

        home = next((team for team in competitors if team.get("homeAway") == "home"), competitors[0])
        away = next((team for team in competitors if team.get("homeAway") == "away"), competitors[1])

        home_name = (
            home.get("team", {}).get("displayName")
            or home.get("team", {}).get("shortDisplayName")
            or home.get("team", {}).get("name")
        )

        away_name = (
            away.get("team", {}).get("displayName")
            or away.get("team", {}).get("shortDisplayName")
            or away.get("team", {}).get("name")
        )

        home_score = parse_score(home.get("score"))
        away_score = parse_score(away.get("score"))

        if home_score is None or away_score is None:
            continue

        events.append({
            "date": date_key,
            "team1": home_name,
            "team2": away_name,
            "score1": home_score,
            "score2": away_score
        })

    return events


def fixture_date_distance(fixture, event):
    try:
        fixture_date = datetime.strptime(fixture["date"], "%Y-%m-%d")
        event_date = datetime.strptime(event["date"], "%Y%m%d")
        return abs((fixture_date - event_date).days)
    except Exception:
        return 999


def scores_for_fixture(fixture, event):
    if canon(fixture["team1"]) == canon(event["team1"]) and canon(fixture["team2"]) == canon(event["team2"]):
        return event["score1"], event["score2"]

    if canon(fixture["team1"]) == canon(event["team2"]) and canon(fixture["team2"]) == canon(event["team1"]):
        return event["score2"], event["score1"]

    return None


def update_fixtures(data, events):
    changed = 0

    for event in events:
        candidates = [
            fixture
            for fixture in data.get("fixtures", [])
            if same_pair(fixture["team1"], fixture["team2"], event["team1"], event["team2"])
        ]

        if not candidates:
            print(f"No fixture match found for {event['team1']} vs {event['team2']} on {event['date']}")
            continue

        best_fixture = min(candidates, key=lambda fixture: fixture_date_distance(fixture, event))
        distance = fixture_date_distance(best_fixture, event)

        if distance > 2:
            print(f"Skipped distant fixture match for {event['team1']} vs {event['team2']} on {event['date']}")
            continue

        scores = scores_for_fixture(best_fixture, event)

        if scores is None:
            continue

        new_score1, new_score2 = scores

        if best_fixture.get("score1") != new_score1 or best_fixture.get("score2") != new_score2:
            print(
                f"Updating {best_fixture['id']}: "
                f"{best_fixture['team1']} {new_score1} to {new_score2} {best_fixture['team2']}"
            )
            best_fixture["score1"] = new_score1
            best_fixture["score2"] = new_score2
            changed += 1

    return changed


def main():
    with open(DATA_FILE, "r", encoding="utf-8") as file:
        data = json.load(file)

    all_events = []

    for date_key in date_keys_to_fetch():
        url = SCOREBOARD_URL.format(date=date_key)

        try:
            payload = fetch_json(url)
            events = parse_espn_events(payload, date_key)
            all_events.extend(events)
            print(f"{date_key}: {len(events)} completed events")
        except Exception as error:
            print(f"{date_key}: fetch failed: {error}")

    changed = update_fixtures(data, all_events)

    if changed:
        with open(DATA_FILE, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")

    print(f"Completed events found: {len(all_events)}")
    print(f"Fixtures updated: {changed}")


if __name__ == "__main__":
    main()
