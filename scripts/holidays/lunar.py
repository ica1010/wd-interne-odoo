"""
Couche lunaire pour les fêtes musulmanes du Togo.

Trois niveaux distincts, jamais confondus :
- REFERENCE_ASTRONOMIQUE : calcul local de l'âge de la lune
  au coucher du soleil à Lomé (prévision, pas observation) ;
- OBSERVATION_RAPPORTEE : saisie manuelle dans
  config/holidays/lunar.json (observation réelle) ;
- CONFIRME_OFFICIELLEMENT : annonce officielle compétente,
  également saisie manuellement.

Aucune observation n'est inventée à partir des calculs.
"""

import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
LUNAR_CONFIG = BASE_DIR / "config" / "holidays" / "lunar.json"

# Paramètres astronomiques (formules de basse précision, Meeus).
SYNODIC_MONTH_DAYS = 29.530588853
# Nouvelle lune de référence : 2000-01-06 18:14 UTC (JD 2451550.1).
NEW_MOON_J2000_JD = 2451550.1
J2000_UNIX = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

# Critère indicatif de visibilité du croissant au coucher du
# soleil en zone équatoriale : âge lunaire entre ~15 h et
# ~72 h — jeune lune croissante uniquement. Une lune plus
# vieille (croissant décroissant ou gibbeuse) n'est pas le
# signal du nouveau mois. Approximation documentée,
# non contractuelle.
MIN_MOON_AGE_HOURS = 15.0
MAX_MOON_AGE_HOURS = 72.0


def _julian_day(dt):
    return dt.timestamp() / 86400.0 + 2440587.5


def moon_age_days(dt_utc):
    """Âge de la lune en jours (formule de basse précision)."""

    jd = _julian_day(dt_utc)
    age = (jd - NEW_MOON_J2000_JD) % SYNODIC_MONTH_DAYS
    return age


def load_lunar_config(country_code="TG"):
    config = json.loads(LUNAR_CONFIG.read_text(encoding="utf-8"))
    return config.get(country_code, {})


def assess_visibility(candidate_date, sunset_local="18:07"):
    """
    Évalue la visibilité probable du croissant à Lomé pour une
    date candidate. Le coucher du soleil est fourni par la
    couche de timings (ou valeur par défaut documentée).

    Retourne un dict avec l'âge lunaire au coucher du soleil
    et un verdict indicatif REFERENCE_ASTRONOMIQUE.
    """

    hour, minute = map(int, sunset_local.split(":"))
    # Lomé = UTC+0 : l'heure locale est l'heure UTC.
    sunset_utc = datetime.combine(
        candidate_date, time(hour, minute), tzinfo=timezone.utc
    )

    age_days = moon_age_days(sunset_utc)
    age_hours = age_days * 24.0

    visible = (
        MIN_MOON_AGE_HOURS <= age_hours <= MAX_MOON_AGE_HOURS
    )

    return {
        "date_candidate": candidate_date.isoformat(),
        "coucher_soleil": sunset_local,
        "age_lune_heures": round(age_hours, 1),
        "visibilite_premiere_fois": visible,
        "statut": "REFERENCE_ASTRONOMIQUE",
        "methode": "Calcul astronomique local (âge lunaire)",
        "limite": (
            "Prévision astronomique indicatives ; ne constitue "
            "ni une observation réelle ni une décision religieuse."
        ),
    }


def estimate_local_holiday(estimated_date, hijri_day,
                           window_days=3):
    """
    Estime la date locale de la fête à partir de la visibilité
    du croissant à Lomé.

    Le jour férié tombe le `hijri_day`-ième jour du mois
    hégirien : on cherche donc la date de nouveau mois —
    premier coucher de soleil où le croissant est visible —
    autour du 1er du mois estimé ( Umm al-Qura ), puis on
    ajoute (hijri_day - 1) jours.

    Retourne un dict REFERENCE_ASTRONOMIQUE avec l'écart
    éventuel par rapport à la date Umm al-Qura.
    """

    month_start_estimate = estimated_date - timedelta(
        days=hijri_day - 1
    )

    candidates = []
    for offset in range(-window_days, window_days + 1):
        candidate = month_start_estimate + timedelta(days=offset)
        assessment = assess_visibility(candidate)
        candidates.append(assessment)

    visible = [
        c for c in candidates if c["visibilite_premiere_fois"]
    ]

    if not visible:
        return {
            "date_reference": estimated_date.isoformat(),
            "date_nouveau_mois": "",
            "date_locale": "",
            "ecart_jours": None,
            "candidats": candidates,
            "statut": "REFERENCE_ASTRONOMIQUE",
            "conclusion": (
                "Aucune date candidate avec visibilité "
                "probable du croissant à Lomé."
            ),
        }

    # Premier coucher de soleil avec visibilité : c'est le
    # 1er du mois local estimé.
    sighting = min(
        visible,
        key=lambda c: date.fromisoformat(c["date_candidate"]),
    )
    sighting_date = date.fromisoformat(sighting["date_candidate"])
    local_holiday = sighting_date + timedelta(days=hijri_day - 1)

    return {
        "date_reference": estimated_date.isoformat(),
        "date_nouveau_mois": sighting["date_candidate"],
        "date_locale": local_holiday.isoformat(),
        "ecart_jours": (local_holiday - estimated_date).days,
        "age_lune_coucher_soleil": sighting["age_lune_heures"],
        "candidats": candidates,
        "statut": "REFERENCE_ASTRONOMIQUE",
        "conclusion": (
            f"Croissant probablement visible le "
            f"{sighting['date_candidate']} (âge lunaire "
            f"{sighting['age_lune_heures']} h au coucher du "
            f"soleil) → 1er du mois estimé à Lomé ; fête "
            f"estimée le {local_holiday.isoformat()}. "
            "À confirmer par observation ou annonce "
            "officielle."
        ),
    }


def enrich_record(country_code, record):
    """
    Enrichit un record de fête musulmane estimée avec la
    couche lunaire : date de visibilité astronomique à Lomé
    et statut de confirmation humaine éventuel.
    """

    estimated_date = date.fromisoformat(record["date"])
    hijri_day = int(record.get("hijri_day", 1))
    visibility = estimate_local_holiday(estimated_date, hijri_day)

    confirmation, detail = lookup_human_entries(
        country_code, record.get("name", ""), estimated_date
    )

    return {
        "evenement": record.get("name", ""),
        "date_estimee": record["date"],
        "hijri_date": record.get("hijri_date", ""),
        "visibilite": visibility,
        "confirmation": confirmation or "A_CONFIRMER",
        "detail_confirmation": detail,
    }


def lookup_human_entries(country_code, event_key, estimated_date):
    """
    Recherche une observation rapportée ou une annonce
    officielle saisie manuellement pour un événement.

    Retourne (statut, detail) ou (None, "") si rien.
    """

    config = load_lunar_config(country_code)

    for entry in config.get("annonces_officielles", []):
        if entry.get("evenement") == event_key or (
            entry.get("date_estimee") == estimated_date.isoformat()
        ):
            return "CONFIRME_OFFICIELLEMENT", entry

    for entry in config.get("observations", []):
        if entry.get("evenement") == event_key or (
            entry.get("date_estimee") == estimated_date.isoformat()
        ):
            return "OBSERVATION_RAPPORTEE", entry

    return None, ""
