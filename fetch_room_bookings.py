"""
Hakee toimiston työhuoneiden varaukset Microsoft Graph API:sta ja kirjoittaa
niistä siivotun varaukset.json-tiedoston infonäyttöä varten.

Skripti ei kirjaa lokiin tai tulosta varausten sisältöjä (aihe, osallistujat
tms.), ja tallentaa JSON-tiedostoon vain minimitiedot:
- alkamisaika, päättymisaika
- varaajan nimikirjaimet (esim. "M.V.")

Ympäristömuuttujat (asetetaan GitHub Secretseinä):
- AZURE_TENANT_ID
- AZURE_CLIENT_ID
- AZURE_CLIENT_SECRET
"""

import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

# --- KONFIGURAATIO ---
# Vaihda nämä oman organisaationne huoneiden postilaatikko-osoitteisiin
# ja näytettäviin nimiin.
ROOMS = [
    {"email": "neukkari1@esimerkki.fi", "name": "Neukkari 1"},
    {"email": "neukkari2@esimerkki.fi", "name": "Neukkari 2"},
    {"email": "neukkari3@esimerkki.fi", "name": "Neukkari 3"},
    {"email": "neukkari4@esimerkki.fi", "name": "Neukkari 4"},
]

LOCAL_TZ = ZoneInfo("Europe/Helsinki")
OUTPUT_FILE = "varaukset.json"


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
    """Palauttaa nimestä nimikirjaimet, esim. 'Matti Virtanen' -> 'M.V.'"""
    if not name:
        return ""
    # Poista mahdolliset sulut ja niiden sisältö (esim. osastotieto nimen perässä)
    clean = name.split("(")[0].split(",")[0].strip()
    parts = [p for p in clean.split() if p and p[0].isalpha()]
    if not parts:
        return ""
    if len(parts) == 1:
        return f"{parts[0][0].upper()}."
    return f"{parts[0][0].upper()}.{parts[-1][0].upper()}."


def parse_graph_datetime(dt_str: str, tz_name: str) -> datetime:
    """Parsii Graph API:n dateTime-kentän aikavyöhykeaidoksi datetimeksi."""
    # Graph API palauttaa muodon "2026-04-20T10:00:00.0000000" (tarkkuus vaihtelee)
    cleaned = dt_str.split(".")[0].rstrip("Z")
    naive = datetime.fromisoformat(cleaned)
    # tz_name on esim. "UTC" — liitetään aikavyöhyke
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
        # Haetaan vain välttämättömät kentät — ei kokouksen aihetta, kuvausta tms.
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
        # Ohita perutut ja "free"-statuksella merkityt varaukset
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

    # Haetaan tältä päivältä Helsingin ajassa
    now_local = datetime.now(LOCAL_TZ)
    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    # Graph API hyväksyy ISO-8601 aikavyöhykeoffsetilla
    start_iso = day_start.isoformat()
    end_iso = day_end.isoformat()

    result = {
        "updatedAt": now_local.isoformat(timespec="seconds"),
        "rooms": [],
    }

    for room in ROOMS:
        room_entry = {
            "name": room["name"],
            "email": room["email"],
            "bookings": [],
            "error": None,
        }
        try:
            events = fetch_room_bookings(token, room["email"], start_iso, end_iso)
            room_entry["bookings"] = process_events(events)
            print(f"OK  {room['name']} ({room['email']}): {len(room_entry['bookings'])} varausta")
        except requests.HTTPError as ex:
            msg = f"HTTP {ex.response.status_code}"
            room_entry["error"] = msg
            print(f"ERR {room['name']} ({room['email']}): {msg}", file=sys.stderr)
        except Exception as ex:
            room_entry["error"] = type(ex).__name__
            print(f"ERR {room['name']} ({room['email']}): {ex}", file=sys.stderr)
        result["rooms"].append(room_entry)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"Kirjoitettu {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
