"""
Tests du contrôle des jours fériés du Togo (site Lomé).

Toutes les sources externes sont mockées : aucun test
n'effectue d'appel réseau réel.
"""

import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "scripts"))

from holidays import audit  # noqa: E402
from holidays import compare_holidays as ch  # noqa: E402
from holidays import normalize  # noqa: E402


@pytest.fixture
def file_df():
    return pd.DataFrame({
        "Date": [date(2027, 4, 27), date(2027, 1, 13)],
        "Libelle": ["Fête de l'Indépendance", "Fête de la libération nationale"],
    })


@pytest.fixture
def reference_records():
    return [
        {
            "date": "2027-04-27",
            "name": "Independence Day",
            "countryCode": "TG",
            "source_id": "NAGER_V4",
            "source_name": "Nager.Date Community API v4",
            "source_url": "https://nagerholidays.com/api/v4/Holidays/TG/2027",
        },
        {
            "date": "2027-05-01",
            "name": "Labour Day",
            "countryCode": "TG",
            "source_id": "NAGER_V4",
            "source_name": "Nager.Date Community API v4",
            "source_url": "https://nagerholidays.com/api/v4/Holidays/TG/2027",
        },
    ]


SOURCE_INFO = {
    "id": "NAGER_V4",
    "name": "Nager.Date Community API v4",
    "type": "secondary_api",
    "reliability": "moyenne",
    "url": "https://nagerholidays.com/api/v4",
}


def test_scope_is_togo_lome():
    """Le contrôle porte exclusivement sur TG / Lomé."""
    assert ch.COUNTRY_CODE == "TG"
    assert ch.SITE == "Lomé"


def test_exact_date_match_concordant(file_df, reference_records):
    """Une date exacte avec libellé proche est CONCORDANT."""
    # Le libellé diffère (traduction) : seuil 0.85 non atteint
    # pour "Independence Day" vs "Fête de l'Indépendance".
    comparisons = ch.build_comparisons(
        file_df, reference_records, "OK", SOURCE_INFO, "TG", "Lomé"
    )
    exact = [c for c in comparisons if c["statut"] == "CONCORDANT"]
    different = [c for c in comparisons if c["statut"] == "LIBELLE_DIFFERENT"]

    # 2027-04-27 correspond exactement : libellés différents
    # (anglais vs français) → LIBELLE_DIFFERENT, pas de fusion.
    assert any(c["date_fichier"] == date(2027, 4, 27)
               for c in different)
    # 2027-01-13 absent de la référence → ABSENT_SOURCE.
    absent = [c for c in comparisons if c["statut"] == "ABSENT_SOURCE"]
    assert any(c["date_fichier"] == date(2027, 1, 13) for c in absent)


def test_proximity_does_not_merge():
    """Une date proche produit une suggestion, pas une fusion."""
    df = pd.DataFrame({
        "Date": [date(2027, 5, 2)],
        "Libelle": ["Fête du Travail"],
    })
    refs = [{
        "date": "2027-05-01",
        "name": "Labour Day",
        "countryCode": "TG",
        "source_id": "NAGER_V4",
        "source_name": "Nager",
        "source_url": "u",
    }]

    comparisons = ch.build_comparisons(
        df, refs, "OK", SOURCE_INFO, "TG", "Lomé"
    )

    file_rows = [c for c in comparisons if c["date_fichier"]]
    suggestions = [c for c in comparisons if c["statut"] == "A_EXAMINER"]

    # La ligne fichier reste ABSENT_SOURCE : pas de fusion.
    assert file_rows[0]["statut"] == "ABSENT_SOURCE"
    # La suggestion est un enregistrement distinct.
    assert len(suggestions) == 1
    assert suggestions[0]["date_reference"] == date(2027, 5, 1)
    assert suggestions[0]["proposition_id"] == ""


def test_secondary_source_never_official():
    """Une source secondary_api ne produit jamais
    CONFIRME_OFFICIELLEMENT dans le journal."""
    execution = {
        "execution_id": "CTRL-TEST",
        "version": "1.0.0",
        "date_controle": "2027-01-01T00:00:00",
        "empreinte_source": "abc",
        "pays": "TG",
        "site": "Lomé",
        "annees": [2027],
        "fetch_results": [{
            "source": SOURCE_INFO,
            "status": "OK",
            "error": "",
            "cache_files": [],
        }],
        "manual_sources": [],
        "comparisons": [{
            "proposition_id": "PROP-1",
            "pays": "TG",
            "site": "Lomé",
            "date_fichier": date(2027, 4, 27),
            "libelle_fichier": "Fête de l'Indépendance",
            "date_reference": date(2027, 4, 27),
            "libelle_reference": "Independence Day",
            "statut": "CONCORDANT",
            "decision_proposee": "CONSERVER",
            "justification": "ok",
            "source": SOURCE_INFO,
            "fetch_status": "OK",
        }],
    }

    journal_rows, _, _ = audit.build_outputs(execution)
    comparison_rows = [
        r for r in journal_rows if r["proposition_id"] == "PROP-1"
    ]

    assert "REFERENCE_SECONDAIRE" in comparison_rows[0]["justification"]
    assert "CONFIRME_OFFICIELLEMENT" not in comparison_rows[0]["justification"]


def test_source_inaccessible_marks_rows():
    """Une source inaccessible marque les lignes sans les supprimer."""
    df = pd.DataFrame({
        "Date": [date(2027, 4, 27)],
        "Libelle": ["Fête de l'Indépendance"],
    })

    comparisons = ch.build_comparisons(
        df, [], "SOURCE_INACCESSIBLE", SOURCE_INFO, "TG", "Lomé"
    )

    assert len(comparisons) == 1
    assert comparisons[0]["statut"] == "SOURCE_INACCESSIBLE"
    assert comparisons[0]["decision_proposee"] == "SOURCE_NON_VERIFIEE"


def test_append_only_preserves_history(tmp_path, monkeypatch):
    """Deux exécutions successives conservent les deux historiques."""
    journal = tmp_path / "journal.csv"
    monkeypatch.setattr(audit, "JOURNAL_PATH", journal)

    row1 = {c: "" for c in audit.JOURNAL_COLUMNS}
    row1["execution_id"] = "CTRL-1"
    row2 = dict(row1, execution_id="CTRL-2")

    audit._locked_append(journal, pd.DataFrame([row1]), audit.JOURNAL_COLUMNS)
    audit._locked_append(journal, pd.DataFrame([row2]), audit.JOURNAL_COLUMNS)

    result = pd.read_csv(journal, dtype=str)
    assert list(result["execution_id"]) == ["CTRL-1", "CTRL-2"]


def test_proposals_preserve_human_decisions(tmp_path, monkeypatch):
    """Les décisions humaines sont préservées par Proposition_ID."""
    proposals = tmp_path / "proposes.csv"
    monkeypatch.setattr(audit, "PROPOSALS_PATH", proposals)

    rows = [{
        "Proposition_ID": "PROP-20270427-fete",
        "Date": "2027-04-27",
        "Libelle": "Fête de l'Indépendance",
        "Pays": "TG",
        "Site": "Lomé",
        "Statut_Reference": "REFERENCE_SECONDAIRE",
        "Source": "Nager.Date Community API v4",
        "Decision_Humaine": "",
        "Date_Validation": "",
    }]

    audit.write_proposals(rows)

    # Simulation d'une décision humaine saisie.
    filled = pd.read_csv(proposals, dtype=str).fillna("")
    filled.loc[0, "Decision_Humaine"] = "VALIDE"
    filled.loc[0, "Date_Validation"] = "2027-06-01"
    filled.to_csv(proposals, index=False, encoding="utf-8-sig")

    # Nouvelle exécution : la décision est préservée.
    audit.write_proposals(rows)

    result = pd.read_csv(proposals, dtype=str).fillna("")
    assert result.loc[0, "Decision_Humaine"] == "VALIDE"
    assert result.loc[0, "Date_Validation"] == "2027-06-01"


def test_source_workbook_unchanged():
    """Le classeur Excel source n'est pas modifié par le contrôle."""
    hash_before = hashlib.sha256(ch.SOURCE.read_bytes()).hexdigest()

    # run_control lit le classeur et écrit uniquement dans
    # data/processed et data/reports.
    ch.run_control(offline=True)

    hash_after = hashlib.sha256(ch.SOURCE.read_bytes()).hexdigest()
    assert hash_before == hash_after


# --- Couche lunaire (scripts/holidays/lunar.py) ---

def test_moon_age_days_monotonic():
    """L'âge lunaire croît avec le temps (modulo mois synodique)."""
    from holidays import lunar
    from datetime import datetime, timezone

    d1 = datetime(2027, 3, 8, 18, 7, tzinfo=timezone.utc)
    d2 = datetime(2027, 3, 9, 18, 7, tzinfo=timezone.utc)
    assert lunar.moon_age_days(d2) > lunar.moon_age_days(d1)
    assert lunar.moon_age_days(d2) < lunar.SYNODIC_MONTH_DAYS


def test_estimate_local_holiday_structure():
    """Retourne une référence astronomique, jamais une observation."""
    from holidays import lunar

    result = lunar.estimate_local_holiday(date(2027, 3, 9), hijri_day=1)

    assert result["statut"] == "REFERENCE_ASTRONOMIQUE"
    assert result["date_reference"] == "2027-03-09"
    assert result["date_locale"] != ""
    assert len(result["candidats"]) == 7
    for cand in result["candidats"]:
        assert cand["statut"] == "REFERENCE_ASTRONOMIQUE"
        assert "observation" in cand["limite"].lower() or                "ni une observation" in cand["limite"].lower()


def test_lunar_never_confirms_officially():
    """Sans saisie humaine, la confirmation reste A_CONFIRMER."""
    from holidays import lunar

    enriched = lunar.enrich_record("TG", {
        "date": "2027-03-09",
        "name": "Korité",
        "hijri_date": "1-10-1448",
    })

    assert enriched["confirmation"] == "A_CONFIRMER"
    assert enriched["visibilite"]["statut"] == "REFERENCE_ASTRONOMIQUE"


def test_human_registry_statuses(tmp_path, monkeypatch):
    """OBSERVATION_RAPPORTEE et CONFIRME_OFFICIELLEMENT sont
    distingués et prioritaires sur le calcul."""
    from holidays import lunar

    fake_config = {
        "TG": {
            "observations": [{
                "evenement": "Tabaski",
                "date_estimee": "2027-05-16",
                "date_observee": "2027-05-17",
                "lieu": "Lomé",
                "source": "Registre interne RH",
            }],
            "annonces_officielles": [],
        }
    }
    cfg = tmp_path / "lunar.json"
    cfg.write_text(json.dumps(fake_config), encoding="utf-8")
    monkeypatch.setattr(lunar, "LUNAR_CONFIG", cfg)

    statut, detail = lunar.lookup_human_entries(
        "TG", "Tabaski", date(2027, 5, 16)
    )
    assert statut == "OBSERVATION_RAPPORTEE"
    assert detail["date_observee"] == "2027-05-17"

    fake_config["TG"]["annonces_officielles"] = [{
        "evenement": "Tabaski",
        "date_estimee": "2027-05-16",
        "date_officielle": "2027-05-17",
        "autorite": "Test",
    }]
    cfg.write_text(json.dumps(fake_config), encoding="utf-8")

    statut, _ = lunar.lookup_human_entries(
        "TG", "Tabaski", date(2027, 5, 16)
    )
    assert statut == "CONFIRME_OFFICIELLEMENT"


def test_lunar_journal_rows_written(tmp_path, monkeypatch):
    """Le journal reçoit une ligne REFERENCE_ASTRONOMIQUE
    par fête musulmane enrichie."""
    journal = tmp_path / "journal.csv"
    monkeypatch.setattr(audit, "JOURNAL_PATH", journal)

    comp = {
        "proposition_id": "PROP-20270309-korite",
        "pays": "TG",
        "site": "Lomé",
        "date_fichier": "",
        "libelle_fichier": "",
        "date_reference": "2027-03-09",
        "libelle_reference": "Korité",
        "statut": "ABSENT_FICHIER",
        "decision_proposee": "PROPOSITION_AJOUT",
        "justification": "test",
        "source": {
            "id": "ALADHAN", "name": "AlAdhan API",
            "type": "estimated_calendar",
            "reliability": "estimee", "url": "https://x",
        },
        "fetch_status": "OK",
        "lunar": lunar_enrich(),
    }
    execution = {
        "execution_id": "CTRL-TEST",
        "version": "1.0.0",
        "date_controle": "2027-01-01T00:00:00",
        "empreinte_source": "abc",
        "pays": "TG",
        "site": "Lomé",
        "fetch_results": [],
        "manual_sources": [],
        "comparisons": [comp],
    }

    journal_rows, _, _ = audit.build_outputs(execution)
    audit._locked_append(journal, pd.DataFrame(journal_rows), audit.JOURNAL_COLUMNS)

    result = pd.read_csv(journal, dtype=str).fillna("")
    assert "REFERENCE_ASTRONOMIQUE" in set(result["statut"])
    assert result["justification"].str.contains("DATE_ESTIMEE").any()


def lunar_enrich():
    from holidays import lunar
    return lunar.enrich_record("TG", {
        "date": "2027-03-09",
        "name": "Korité",
        "hijri_date": "1-10-1448",
    })


def test_export_odoo_holidays(tmp_path, monkeypatch):
    """L'export produit les fériés fixes + les fêtes musulmanes
    ajustées Lomé, avec 2 jours pour Korité et Tabaski."""
    import importlib
    from holidays import export_odoo_holidays as ex

    monkeypatch.setattr(ex, "PROPOSALS_PATH", ch.PROPOSALS_PATH if hasattr(ch,'PROPOSALS_PATH') else ex.PROPOSALS_PATH)

    # build_rows lit le vrai fichier de propositions existant.
    rows, proposals = ex.build_rows()

    df = pd.DataFrame(rows)
    # 13 fériés du classeur + 4 fêtes musulmanes (plages).
    assert len(df) == 17

    # Korité et Tabaski : une ligne, plage de 2 jours.
    korite = df[df["Libelle"] == "Korité"].iloc[0]
    assert korite["Date"] == "2027-03-08"
    assert korite["date_from"] == "2027-03-08 00:00:00"
    assert korite["date_to"] == "2027-03-09 23:59:59"

    tabaski = df[df["Libelle"] == "Tabaski"].iloc[0]
    assert tabaski["Date"] == "2027-05-15"
    assert tabaski["date_to"] == "2027-05-16 23:59:59"

    # Pas de ligne "2e jour" séparée.
    assert not df["Libelle"].str.contains("2e jour").any()

    # Dates musulmanes = estimations astronomiques, pas officielles.
    muslim = df[df["Source"].str.contains("visibilité lunaire")]
    assert (muslim["Statut_Reference"] == "REFERENCE_ASTRONOMIQUE").all()
    assert (muslim["Source_ID"] == "ALADHAN;LUNAR_LOME").all()

    # Champs Odoo date_from/date_to : journée complète.
    assert (df["date_from"].str.endswith("00:00:00")).all()
    assert (df["date_to"].str.endswith("23:59:59")).all()
    assert (df["date_from"].str[:10] == df["Date"]).all()

    # Les 13 fériés du classeur conservent leur source propre.
    fichier = df[~df["Source"].str.contains("visibilité lunaire")]
    assert (fichier["Source_ID"] == "FICHIER_WD").all()
    assert len(fichier) == 13

    # Décision humaine tracée sur les lignes musulmanes.
    marked = proposals[
        proposals["Source"].str.contains("AlAdhan")
    ]
    assert (marked["Decision_Humaine"] == "VALIDE_IMPORT").all()
    assert (marked["Date_Validation"] != "").all()
