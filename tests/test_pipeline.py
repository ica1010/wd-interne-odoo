"""
Tests du pipeline de préparation des données West Digital.

Couvre les règles d'exclusion du matériel IT, la détection
des anomalies et la non-modification du classeur source.
"""

import hashlib
import sys
from pathlib import Path

import pandas as pd
import pytest

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "scripts" / "data"))

import clean
import validate


EQUIPMENT_COLUMNS = [
    "AssetID", "Categorie", "ProfilPortable", "Marque", "Modele",
    "NumSerie", "DateAchat", "PrixAchat_FCFA", "Statut",
    "AffecteA", "FinGarantie", "Site",
]


def make_employes(*employee_ids):
    return pd.DataFrame({"EmployeeID": list(employee_ids)})


def make_equipment(asset_id, num_serie, statut, affecte_a=None,
                   categorie="Ordinateur portable"):
    return {
        "AssetID": asset_id,
        "Categorie": categorie,
        "ProfilPortable": "Standard",
        "Marque": "HP",
        "Modele": "ProBook 450",
        "NumSerie": num_serie,
        "DateAchat": "2025-01-01",
        "PrixAchat_FCFA": 650000,
        "Statut": statut,
        "AffecteA": affecte_a,
        "FinGarantie": "2028-01-01",
        "Site": "Lomé",
    }


def run_prepare(rows, employes):
    df = pd.DataFrame(rows, columns=EQUIPMENT_COLUMNS)
    return clean.prepare_equipment(df, make_employes(*employes))


def test_orphan_assignment_excluded():
    """Un équipement affecté à un employé inexistant est exclu."""
    cleaned, exclusions, anomalies = run_prepare(
        [make_equipment("WD-1", "SN1", "Affecté", "E999")],
        ["E001"],
    )

    assert cleaned.empty
    assert len(exclusions) == 1
    assert exclusions[0]["AssetID"] == "WD-1"
    assert "inexistant" in exclusions[0]["Motif_Exclusion"]
    assert anomalies[0]["Gravite"] == "ERREUR"


def test_serial_duplicate_mixed_keeps_assigned():
    """Doublon série : l'équipement affecté à un employé existant
    est conservé, l'autre exemplaire est exclu."""
    cleaned, exclusions, _ = run_prepare(
        [
            make_equipment("WD-1", "SN-DUP", "Affecté", "E001"),
            make_equipment("WD-2", "SN-DUP", "En stock"),
        ],
        ["E001"],
    )

    assert list(cleaned["AssetID"]) == ["WD-1"]
    assert [e["AssetID"] for e in exclusions] == ["WD-2"]
    assert "doublon" in exclusions[0]["Motif_Exclusion"]


def test_serial_duplicate_two_valid_excludes_both():
    """Doublon série entre deux affectations valides :
    les deux lignes sont exclues, aucun choix arbitraire."""
    cleaned, exclusions, _ = run_prepare(
        [
            make_equipment("WD-1", "SN-DUP", "Affecté", "E001"),
            make_equipment("WD-2", "SN-DUP", "Affecté", "E002"),
        ],
        ["E001", "E002"],
    )

    assert cleaned.empty
    assert len(exclusions) == 2
    assert all("ambigu" in e["Motif_Exclusion"] or "plusieurs" in e["Motif_Exclusion"]
               for e in exclusions)


def test_stock_with_unique_serial_kept():
    """Un équipement en stock sans employé et série unique
    reste importable."""
    cleaned, exclusions, _ = run_prepare(
        [make_equipment("WD-1", "SN-UNIQUE", "En stock")],
        ["E001"],
    )

    assert list(cleaned["AssetID"]) == ["WD-1"]
    assert exclusions == []


def test_equipment_name_column():
    """La colonne Nom = Marque + modèle complet est ajoutée."""
    cleaned, _, _ = run_prepare(
        [make_equipment("WD-1", "SN1", "En stock")],
        ["E001"],
    )

    assert cleaned.iloc[0]["Nom"] == "HP ProBook 450"


def test_affecte_sans_employe_signale():
    """Un équipement 'Affecté' sans détenteur est conservé
    mais signalé par validate.py."""
    row = make_equipment("WD-1", "SN1", "Affecté")
    df = pd.DataFrame([row], columns=EQUIPMENT_COLUMNS)
    workbook = {
        "Materiel_IT": df,
        "Employes": make_employes("E001"),
    }
    results = []

    validate.check_equipment_rules(workbook, results)

    statuts = [r for r in results if r["Type"] == "STATUT_INCOHERENT"]
    assert len(statuts) == 1
    assert statuts[0]["Gravite"] == "AVERTISSEMENT"


def test_validate_flags_orphan_and_serial():
    """validate.py signale l'affectation orpheline en ERREUR
    et le numéro de série dupliqué en ERREUR."""
    rows = [
        make_equipment("WD-1", "SN-DUP", "Affecté", "E999"),
        make_equipment("WD-2", "SN-DUP", "En stock"),
    ]
    df = pd.DataFrame(rows, columns=EQUIPMENT_COLUMNS)
    workbook = {
        "Materiel_IT": df,
        "Employes": make_employes("E001"),
    }
    results = []

    validate.check_equipment_rules(workbook, results)

    erreurs = [r for r in results if r["Gravite"] == "ERREUR"]
    types = {r["Type"] for r in erreurs}
    assert "AFFECTATION_SANS_CORRESPONDANCE" in types
    assert "NUMERO_SERIE_DUPLIQUE" in types


@pytest.fixture(scope="module")
def pipeline_output(tmp_path_factory):
    """Exécute une fois le pipeline complet sur les données réelles."""
    source = clean.SOURCE
    hash_before = hashlib.sha256(source.read_bytes()).hexdigest()

    clean.main()

    hash_after = hashlib.sha256(source.read_bytes()).hexdigest()

    return {
        "processed": clean.PROCESSED_DIR,
        "reports": clean.REPORT_DIR,
        "source": source,
        "hash_before": hash_before,
        "hash_after": hash_after,
    }


def test_source_workbook_unchanged(pipeline_output):
    """Le classeur Excel source n'est jamais modifié."""
    assert pipeline_output["hash_before"] == pipeline_output["hash_after"]


def test_processed_csvs_coherent(pipeline_output):
    """Les CSV générés sont cohérents avec les règles de nettoyage."""
    equip = pd.read_csv(
        pipeline_output["processed"] / "Materiel_IT.csv"
    )
    employes = pd.read_csv(
        pipeline_output["processed"] / "Employes.csv"
    )
    employe_ids = set(employes["EmployeeID"])

    # Aucun AffecteA orphelin dans le CSV d'import.
    affectes = equip["AffecteA"].dropna()
    assert affectes.isin(employe_ids).all()

    # WD-0008 (E099) et WD-0076 (doublon de série) sont exclus.
    assert "WD-0008" not in set(equip["AssetID"])
    assert "WD-0076" not in set(equip["AssetID"])
    assert "WD-0013" in set(equip["AssetID"])

    # Le rapport d'exclusion existe et justifie chaque retrait.
    exclus = pd.read_csv(
        pipeline_output["reports"] / "materiel_exclu.csv"
    )
    assert set(exclus["AssetID"]) == {"WD-0008", "WD-0076"}
    assert exclus["Motif_Exclusion"].notna().all()


def test_journal_has_severity_and_value(pipeline_output):
    """Le journal distingue gravité et valeur détectée."""
    journal = pd.read_csv(
        pipeline_output["reports"] / "journal_anomalies.csv"
    )

    for column in ["Valeur", "Gravite", "Decision", "Justification"]:
        assert column in journal.columns

    assert set(journal["Gravite"]).issubset(
        {"ERREUR", "AVERTISSEMENT", "INFORMATION"}
    )
