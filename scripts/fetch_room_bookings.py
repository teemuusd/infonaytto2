"""
Hakee toimiston työhuoneiden varaukset Microsoft Graph API:sta ja kirjoittaa
niistä siivotun varaukset.json-tiedoston infonäyttöä varten.
Hakee myös Tampereen säätiedot wttr.in-palvelusta ja kirjoittaa saatiedot.json-tiedoston.

Ympäristömuuttujat (asetetaan GitHub Secretseinä):
- AZURE_TENANT_ID
- AZURE_CLIENT_ID
- AZURE_CLIENT_SECRET
- ROOM_EMAILS  (JSON-lista, esim. [{"email":"aava@proviko.fi","name":"Aava"}, ...])
"""

import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

# --- KONFIGURAATIO ---
# Huoneiden sähköpostit ja nimet luetaan ROOM_EMAILS-ympäristömuuttujasta (GitHub Secret).
# Muoto: [{"email": "huone@esimerkki.fi", "name": "Huone"}, ...]
_room_emails_raw = os.environ.get("ROOM_EMAILS")
if not _room_emails_raw:
    print("VIRHE: Puuttuva ympäristömuuttuja: ROOM_EMAILS", file=sys.stderr)
    sys.exit(1)
try:
    ROOMS = json.loads(_room_emails_raw)
except json.JSONDecodeError as e:
    print(f"VIRHE: ROOM_EMAILS ei ole kelvollinen JSON: {e}", file=sys.stderr)
    sys.exit(1)

LOCAL_TZ = ZoneInfo("Europe/Helsinki")
OUTPUT_FILE = "varaukset.json"
WEATHER_FILE = "saatiedot.json"

# met.no symbol_code → WMO-koodi (index.html käyttää WMO-koodeja sää-ikoneissa)
METNO_TO_WMO = {
    "clearsky": 0, "fair": 1, "partlycloudy": 2, "cloudy": 3,
    "fog": 45, "lightdrizzle": 51, "drizzle": 53, "heavydrizzle": 55,
    "lightrain": 61, "rain": 63, "heavyrain": 65,
    "lightsleet": 66, "sleet": 67, "heavysleet": 67,
    "lightsnow": 71, "snow": 73, "heavysnow": 75,
    "lightrainshowers": 80, "rainshowers": 80, "heavyrainshowers": 82,
    "lightsleetshowers": 67, "sleetshowers": 67,
    "lightsnowshowers": 85, "snowshowers": 85, "heavysnowshowers": 86,
    "thunder": 95, "lightrainandthunder": 95, "rainandthunder": 99,
    "heavyrainandthunder": 99, "lightsleetandthunder": 95,
    "lightsnowandthunder": 95, "snowandthunder": 99,
}


def metno_symbol_to_wmo(symbol: str) -> int:
    """Muuntaa met.no symbol_code WMO-koodiksi."""
    # Poistetaan _day/_night-pääte
    base = symbol.replace("_day", "").replace("_night", "").replace("_polartwilight", "")
    return METNO_TO_WMO.get(base, 3)


def fetch_weather() -> dict:
    """Hakee säätiedot met.no-palvelusta (Tampere) ja palauttaa siivotun rakenteen."""
    url = "https://api.met.no/weatherapi/locationforecast/2.0/compact?lat=61.498&lon=23.761"
    headers = {"User-Agent": "infonaytto/1.0 github.com/teemuusd/infonaytto2"}
    r = requests.get(url, headers=headers, timeout=15)
    r.raise_for_status()
    data = r.json()

    timeseries = data["properties"]["timeseries"]
    now = datetime.now(LOCAL_TZ)

    # Etsi nykyinen tunti
    current_entry = None
    for entry in timeseries:
        t = datetime.fromisoformat(entry["time"].replace("Z", "+00:00")).astimezone(LOCAL_TZ)
        if t <= now:
            current_entry = entry
        else:
            break

    if not current_entry:
        current_entry = timeseries[0]

    inst = current_entry["data"]["instant"]["details"]
    symbol = (current_entry["data"].get("next_1_hours") or
              current_entry["data"].get("next_6_hours", {})).get("summary", {}).get("symbol_code", "cloudy")

    current = {
        "temperature_2m": inst["air_temperature"],
        "apparent_temperature": inst["air_temperature"],  # met.no ei anna feels-like, käytetään lämpötilaa
        "weather_code": metno_symbol_to_wmo(symbol),
        "wind_speed_10m": round(inst["wind_speed"], 1),
    }

    # Poimi 4 tulevan tunnin ennustetta 2h välein
    future = [
        entry for entry in timeseries
        if datetime.fromisoformat(entry["time"].replace("Z", "+00:00")).astimezone(LOCAL_TZ) > now
    ]
    hourly = []
    for i in range(4):
        idx = i * 2
        if idx < len(future):
            entry = future[idx]
            t = datetime.fromisoformat(entry["time"].replace("Z", "+00:00")).astimezone(LOCAL_TZ)
            sym = (entry["data"].get("next_1_hours") or
                   entry["data"].get("next_6_hours", {})).get("summary", {}).get("symbol_code", "cloudy")
            hourly.append({
                "time": t.isoformat(timespec="seconds"),
                "temp": entry["data"]["instant"]["details"]["air_temperature"],
                "code": metno_symbol_to_wmo(sym),
            })

    return {
        "updatedAt": now.isoformat(timespec="seconds"),
        "current": current,
        "hourly": hourly,
    }


def get_access_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    """Hakee access tokenin client credentials -flow'lla."""
    url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }
    r = requests.post(url, data=data, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def get_initials(name: str) -> str:
    """Palauttaa nimestä nimikirjaimet, esim. 'Esko Esimerkki' -> 'EE'"""
    if not name:
        return ""
    clean = name.split("(")[0].split(",")[0].strip()
    parts = [p for p in clean.split() if p and p[0].isalpha()]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0][0].upper()
    return f"{parts[0][0].upper()}{parts[-1][0].upper()}"


def parse_graph_datetime(dt_str: str, tz_name: str) -> datetime:
    """Parsii Graph API:n dateTime-kentän aikavyöhykeaidoksi datetimeksi."""
    cleaned = dt_str.split(".")[0].rstrip("Z")
    naive = datetime.fromisoformat(cleaned)
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    return naive.replace(tzinfo=tz)


def fetch_room_bookings(access_token: str, room_email: str, start_iso: str, end_iso: str):
    """Hakee huoneen varaukset annetulta aikaväliltä."""
    url = f"https://graph.microsoft.com/v1.0/users/{room_email}/calendar/calendarView"
    params = {
        "startDateTime": start_iso,
        "endDateTime": end_iso,
        "$select": "start,end,organizer,isCancelled,showAs",
        "$orderby": "start/dateTime",
        "$top": 100,
    }
    headers = {"Authorization": f"Bearer {access_token}"}
    r = requests.get(url, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json().get("value", [])


def process_events(events):
    """Muuntaa Graphin tapahtumat minimaaliseksi JSON-rakenteeksi."""
    bookings = []
    for e in events:
        if e.get("isCancelled"):
            continue
        if e.get("showAs") in ("free", "workingElsewhere"):
            continue

        start_dt = parse_graph_datetime(
            e["start"]["dateTime"], e["start"].get("timeZone", "UTC")
        ).astimezone(LOCAL_TZ)
        end_dt = parse_graph_datetime(
            e["end"]["dateTime"], e["end"].get("timeZone", "UTC")
        ).astimezone(LOCAL_TZ)

        organizer_name = ""
        organizer = e.get("organizer")
        if organizer and isinstance(organizer.get("emailAddress"), dict):
            organizer_name = organizer["emailAddress"].get("name", "") or ""

        bookings.append({
            "start": start_dt.isoformat(timespec="seconds"),
            "end": end_dt.isoformat(timespec="seconds"),
            "organizerInitials": get_initials(organizer_name),
        })
    return bookings


def main():
    tenant_id = os.environ.get("AZURE_TENANT_ID")
    client_id = os.environ.get("AZURE_CLIENT_ID")
    client_secret = os.environ.get("AZURE_CLIENT_SECRET")

    missing = [k for k, v in {
        "AZURE_TENANT_ID": tenant_id,
        "AZURE_CLIENT_ID": client_id,
        "AZURE_CLIENT_SECRET": client_secret,
    }.items() if not v]
    if missing:
        print(f"VIRHE: Puuttuvat ympäristömuuttujat: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    try:
        token = get_access_token(tenant_id, client_id, client_secret)
    except requests.HTTPError as ex:
        print(f"VIRHE: Tokenin haku epäonnistui: {ex}", file=sys.stderr)
        sys.exit(1)

    now_local = datetime.now(LOCAL_TZ)
    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    start_iso = day_start.isoformat()
    end_iso = day_end.isoformat()

    result = {
        "updatedAt": now_local.isoformat(timespec="seconds"),
        "rooms": [],
    }

    for room in ROOMS:
        room_entry = {
            "name": room["name"],
            # Sähköpostia ei kirjoiteta JSON-tiedostoon
            "bookings": [],
            "error": None,
        }
        try:
            events = fetch_room_bookings(token, room["email"], start_iso, end_iso)
            room_entry["bookings"] = process_events(events)
            print(f"OK  {room['name']}: {len(room_entry['bookings'])} varausta")
        except requests.HTTPError as ex:
            msg = f"HTTP {ex.response.status_code}"
            room_entry["error"] = msg
            print(f"ERR {room['name']}: {msg}", file=sys.stderr)
        except Exception as ex:
            room_entry["error"] = type(ex).__name__
            print(f"ERR {room['name']}: {ex}", file=sys.stderr)
        result["rooms"].append(room_entry)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"Kirjoitettu {OUTPUT_FILE}")

    # Säätiedot
    try:
        weather = fetch_weather()
        with open(WEATHER_FILE, "w", encoding="utf-8") as f:
            json.dump(weather, f, ensure_ascii=False, indent=2)
        print(f"Kirjoitettu {WEATHER_FILE}")
    except Exception as ex:
        print(f"VAROITUS: Säätietojen haku epäonnistui: {ex}", file=sys.stderr)


if __name__ == "__main__":
    main()
