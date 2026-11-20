"""
Récupération des jours fériés de référence via l'API
Nager.Date v4, avec conservation horodatée des réponses
brutes et gestion explicite des échecs.
"""

import json
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]
CACHE_DIR = BASE_DIR / "data" / "raw" / "holidays_cache"

TIMEOUT_SECONDS = 30


def _get_json(url):
    """Appel HTTP GET JSON. Lève une exception explicite en cas d'échec."""

    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "WD-Interne-HolidayCheck/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def _cache_path(country_code, year, fetched_at):
    stamp = fetched_at.strftime("%Y%m%d_%H%M%S")
    return CACHE_DIR / f"{country_code}_{year}_{stamp}.json"


def _save_cache(country_code, year, payload, fetched_at):
    """Conserve une copie horodatée de la réponse brute."""

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    output = _cache_path(country_code, year, fetched_at)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output


def _load_latest_cache(country_code, year):
    """Retourne le cache le plus récent pour un pays/année."""

    pattern = f"{country_code}_{year}_*.json"
    files = sorted(CACHE_DIR.glob(pattern))
    if not files:
        return None
    return json.loads(files[-1].read_text(encoding="utf-8"))


def is_country_supported(available_url, country_code):
    """
    Vérifie que le code pays est couvert par l'API.
    Retourne (bool, error_message).
    """

    try:
        countries = _get_json(available_url)
    except Exception as error:  # noqa: BLE001 - erreur réseau/API
        return False, f"{type(error).__name__}: {error}"

    codes = {
        str(country.get("countryCode", "")).upper()
        for country in countries
    }

    if country_code.upper() not in codes:
        return False, f"Code pays {country_code} absent de la liste des pays disponibles"

    return True, ""


def fetch_holidays(source, country_code, years, offline=False):
    """
    Récupère les jours fériés d'une année/pays via Nager.Date v4.

    Retourne un dict :
    {
        "status": "OK" | "SOURCE_INACCESSIBLE",
        "records": [...],
        "error": "...",
        "cache_files": [...],
        "source": {...},
    }
    """

    result = {
        "status": "SOURCE_INACCESSIBLE",
        "records": [],
        "error": "",
        "cache_files": [],
        "source": source,
    }

    template = source["holidays_url_template"]

    for year in years:
        fetched_at = datetime.now()
        if offline:
            payload = _load_latest_cache(country_code, year)
            if payload is None:
                result["error"] = (
                    f"Aucun cache disponible pour {country_code}/{year}"
                )
                continue
        else:
            url = template.format(
                country_code=country_code, year=year
            )
            try:
                payload = _get_json(url)
            except Exception as error:  # noqa: BLE001
                result["error"] = (
                    f"{year} : {type(error).__name__}: {error}"
                )
                continue

            cache_file = _save_cache(
                country_code, year, payload, fetched_at
            )
            result["cache_files"].append(str(cache_file))

        if not isinstance(payload, list):
            result["error"] = f"{year} : réponse inattendue (non liste)"
            continue

        for item in payload:
            if item.get("nationalHoliday") is False:
                continue
            holiday_types = item.get("holidayTypes") or []
            if holiday_types and "Public" not in holiday_types:
                continue

            result["records"].append({
                "date": item.get("date"),
                "name": item.get("name"),
                "countryCode": item.get("countryCode"),
                "nationalHoliday": item.get("nationalHoliday"),
                "holidayTypes": holiday_types,
                "source_id": source["id"],
                "source_name": source["name"],
                "source_url": template.format(
                    country_code=country_code, year=year
                ),
                "fetched_at": fetched_at.isoformat()
                if not offline else "cache",
            })

    if result["records"] or not result["error"]:
        result["status"] = "OK"

    return result


def fetch_islamic_events(source, country_config, offline=False):
    """
    Convertit les dates des fêtes musulmanes configurées
    (hégiriennes) en dates grégoriennes via AlAdhan hToG.
    Ces dates sont des estimations de calcul : lasource
    est de type estimated_calendar.

    Retourne le même format que fetch_holidays.
    """

    result = {
        "status": "SOURCE_INACCESSIBLE",
        "records": [],
        "error": "",
        "cache_files": [],
        "source": source,
    }

    template = source["date_url_template"]
    events = country_config.get("islamic_events", [])
    hijri_years = country_config.get("hijri_years", [])

    for hijri_year in hijri_years:
        for event in events:
            hijri_date = (
                f"{event['jour']}-{event['mois']}-{hijri_year}"
            )
            cache_key = f"ALADHAN_{hijri_date}"
            fetched_at = datetime.now()

            if offline:
                payload = _load_latest_cache(cache_key, "")
                if payload is None:
                    result["error"] = (
                        f"{hijri_date} : aucun cache disponible"
                    )
                    continue
            else:
                url = template.format(hijri_date=hijri_date)
                try:
                    payload = _get_json(url)
                except Exception as error:  # noqa: BLE001
                    result["error"] = (
                        f"{hijri_date} : "
                        f"{type(error).__name__}: {error}"
                    )
                    continue

                cache_file = _save_cache(
                    cache_key, "", payload, fetched_at
                )
                result["cache_files"].append(str(cache_file))

            data = payload.get("data", {}) if isinstance(payload, dict) else {}
            gregorian = data.get("gregorian", {})
            hijri = data.get("hijri", {})

            greg_date = gregorian.get("date", "")
            if not greg_date:
                result["error"] = f"{hijri_date} : date grégorienne absente"
                continue

            day, month, year = greg_date.split("-")
            iso_date = f"{year}-{month}-{day}"

            hijri_label = (
                f"{hijri.get('day', '?')} "
                f"{hijri.get('month', {}).get('en', '?')} "
                f"{hijri.get('year', '?')}"
            )

            result["records"].append({
                "date": iso_date,
                "name": event["nom"],
                "hijri_date": hijri_label,
                "hijri_day": event["jour"],
                "hijri_month": event["mois"],
                "hijri_year": hijri_year,
                "countryCode": "TG",
                "is_estimated": True,
                "source_id": source["id"],
                "source_name": source["name"],
                "source_url": template.format(hijri_date=hijri_date),
                "fetched_at": fetched_at.isoformat()
                if not offline else "cache",
            })

    if result["records"] or not result["error"]:
        result["status"] = "OK"

    return result
