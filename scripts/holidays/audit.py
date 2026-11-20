"""
Écriture des rapports du contrôle des jours fériés :
journal d'audit append-only sécurisé, comparaison,
propositions et rapport Excel.
"""

import os
from datetime import datetime
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[2]
REPORT_DIR = BASE_DIR / "data" / "reports"
PROCESSED_DIR = BASE_DIR / "data" / "processed"

JOURNAL_PATH = REPORT_DIR / "journal_controle_jours_feries.csv"
COMPARISON_PATH = REPORT_DIR / "comparaison_jours_feries.csv"
PROPOSALS_PATH = PROCESSED_DIR / "jours_feries_proposes.csv"
REPORT_XLSX = REPORT_DIR / "rapport_controle_jours_feries.xlsx"

JOURNAL_COLUMNS = [
    "execution_id",
    "version_programme",
    "date_controle",
    "empreinte_source",
    "pays",
    "site",
    "identifiant_source",
    "source_nom",
    "source_url",
    "type_source",
    "fiabilite_source",
    "statut_recuperation",
    "erreur_technique",
    "proposition_id",
    "date_fichier",
    "libelle_fichier",
    "date_reference",
    "libelle_reference",
    "statut",
    "decision_proposee",
    "decision_humaine",
    "date_validation",
    "justification",
    "utilisateur_ou_script",
]

COMPARISON_COLUMNS = [
    "execution_id",
    "proposition_id",
    "pays",
    "site",
    "date_fichier",
    "libelle_fichier",
    "date_reference",
    "libelle_reference",
    "statut",
    "decision_proposee",
    "justification",
]

PROPOSAL_COLUMNS = [
    "Proposition_ID",
    "Date",
    "Libelle",
    "Pays",
    "Site",
    "Statut_Reference",
    "Source",
    "Decision_Humaine",
    "Date_Validation",
]


def _locked_append(path, new_df, columns):
    """
    Ajout append-only sécurisé : verrou exclusif pendant
    l'écriture, fichier temporaire puis remplacement atomique.
    """

    path.parent.mkdir(parents=True, exist_ok=True)

    lock_path = path.with_name(path.name + ".lock")
    try:
        fd = os.open(
            str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY
        )
    except FileExistsError:
        raise RuntimeError(
            f"Journal verrouillé par une autre exécution : {lock_path}"
        )

    try:
        if path.exists():
            existing = pd.read_csv(path, dtype=str).fillna("")
        else:
            existing = pd.DataFrame(columns=columns)

        combined = pd.concat(
            [existing, new_df[columns].astype(str)],
            ignore_index=True,
        )

        tmp_path = path.with_suffix(path.suffix + ".tmp")
        combined.to_csv(tmp_path, index=False, encoding="utf-8-sig")
        os.replace(tmp_path, path)
    finally:
        os.close(fd)
        lock_path.unlink(missing_ok=True)


def build_outputs(execution):
    """
    Transforme le contexte d'exécution en lignes de journal,
    de comparaison et de propositions.
    """

    journal_rows = []
    comparison_rows = []
    proposal_rows = []

    base = {
        "execution_id": execution["execution_id"],
        "version_programme": execution["version"],
        "date_controle": execution["date_controle"],
        "empreinte_source": execution["empreinte_source"],
        "pays": execution["pays"],
        "site": execution["site"],
        "decision_humaine": "",
        "date_validation": "",
        "utilisateur_ou_script": "scripts/holidays/compare_holidays.py",
    }

    # 1. Une ligne de statut par source machine-readable.
    for fetch_result in execution["fetch_results"]:
        source = fetch_result["source"]
        journal_rows.append({
            **base,
            "identifiant_source": source["id"],
            "source_nom": source["name"],
            "source_url": source.get("available_url", ""),
            "type_source": source["type"],
            "fiabilite_source": source["reliability"],
            "statut_recuperation": fetch_result["status"],
            "erreur_technique": fetch_result.get("error", ""),
            "proposition_id": "",
            "date_fichier": "",
            "libelle_fichier": "",
            "date_reference": "",
            "libelle_reference": "",
            "statut": fetch_result["status"],
            "decision_proposee": "SOURCE_NON_VERIFIEE"
            if fetch_result["status"] != "OK" else "DONNEES_RECUPEREES",
            "justification": source.get("note", ""),
        })

    # 1b. Sources institutionnelles : références à consulter,
    # jamais présentées comme vérifiées automatiquement.
    for source in execution.get("manual_sources", []):
        journal_rows.append({
            **base,
            "identifiant_source": source["id"],
            "source_nom": source["name"],
            "source_url": source.get("url", ""),
            "type_source": source["type"],
            "fiabilite_source": source["reliability"],
            "statut_recuperation": "NON_MACHINE_READABLE",
            "erreur_technique": "",
            "proposition_id": "",
            "date_fichier": "",
            "libelle_fichier": "",
            "date_reference": "",
            "libelle_reference": "",
            "statut": "SOURCE_INACCESSIBLE",
            "decision_proposee": "CONSULTER_MANUELLEMENT",
            "justification": source.get("note", ""),
        })

    # 2. Une ligne de journal par comparaison.
    for comp in execution["comparisons"]:
        source = comp["source"]
        lunar_info = comp.get("lunar")

        if lunar_info and lunar_info["confirmation"] != "A_CONFIRMER":
            statut_confirmation = lunar_info["confirmation"]
        else:
            statut_confirmation = (
                "SOURCE_INACCESSIBLE"
                if comp["fetch_status"] != "OK"
                else {
                    "official_legal": "CONFIRME_OFFICIELLEMENT",
                    "official_calendar": "CONFIRME_OFFICIELLEMENT",
                    "secondary_api": "REFERENCE_SECONDAIRE",
                    "estimated_calendar": "DATE_ESTIMEE",
                }.get(source["type"], "A_CONFIRMER")
            )

        journal_rows.append({
            **base,
            "identifiant_source": source["id"],
            "source_nom": source["name"],
            "source_url": source["url"],
            "type_source": source["type"],
            "fiabilite_source": source["reliability"],
            "statut_recuperation": comp["fetch_status"],
            "erreur_technique": "",
            "proposition_id": comp["proposition_id"],
            "date_fichier": comp["date_fichier"],
            "libelle_fichier": comp["libelle_fichier"],
            "date_reference": comp["date_reference"],
            "libelle_reference": comp["libelle_reference"],
            "statut": comp["statut"],
            "decision_proposee": comp["decision_proposee"],
            "justification": (
                f"[{statut_confirmation}] {comp['justification']}"
            ),
        })

        # 2b. Couche lunaire : prévision astronomique à Lomé,
        # puis observation/annonce saisie manuellement.
        if lunar_info:
            vis = lunar_info["visibilite"]
            candidates = "; ".join(
                f"{c['date_candidate']}: "
                f"{c['age_lune_heures']}h"
                + (" visible" if c["visibilite_premiere_fois"]
                   else " non visible")
                for c in vis["candidats"]
            )
            journal_rows.append({
                **base,
                "identifiant_source": "LUNAR_LOCAL",
                "source_nom": "Calcul astronomique local (Lomé)",
                "source_url": "",
                "type_source": "astronomical_calculation",
                "fiabilite_source": "indicative",
                "statut_recuperation": "OK",
                "erreur_technique": "",
                "proposition_id": comp["proposition_id"],
                "date_fichier": "",
                "libelle_fichier": "",
                "date_reference": lunar_info["date_estimee"],
                "libelle_reference": lunar_info["evenement"],
                "statut": "REFERENCE_ASTRONOMIQUE",
                "decision_proposee": "A_CONFIRMER",
                "justification": (
                    f"{vis['conclusion']} Candidats : {candidates}. "
                    "Prévision astronomique, ni observation réelle "
                    "ni décision religieuse."
                ),
            })

            if lunar_info["confirmation"] != "A_CONFIRMER":
                journal_rows.append({
                    **base,
                    "identifiant_source": "LUNAR_HUMAIN",
                    "source_nom": "Registre manuel observations/annonces",
                    "source_url": "config/holidays/lunar.json",
                    "type_source": "human_registry",
                    "fiabilite_source": "a_valider",
                    "statut_recuperation": "OK",
                    "erreur_technique": "",
                    "proposition_id": comp["proposition_id"],
                    "date_fichier": "",
                    "libelle_fichier": "",
                    "date_reference": lunar_info["date_estimee"],
                    "libelle_reference": lunar_info["evenement"],
                    "statut": lunar_info["confirmation"],
                    "decision_proposee": "A_CONFIRMER",
                    "justification": str(
                        lunar_info["detail_confirmation"]
                    ),
                })

        comparison_rows.append({
            "execution_id": execution["execution_id"],
            "proposition_id": comp["proposition_id"],
            "pays": comp["pays"],
            "site": comp["site"],
            "date_fichier": comp["date_fichier"],
            "libelle_fichier": comp["libelle_fichier"],
            "date_reference": comp["date_reference"],
            "libelle_reference": comp["libelle_reference"],
            "statut": comp["statut"],
            "decision_proposee": comp["decision_proposee"],
            "justification": comp["justification"],
        })

        # 3. Propositions : lignes fichier + ajouts de référence.
        # Les suggestions A_EXAMINER (sans proposition_id) sont exclues.
        if not comp["proposition_id"]:
            continue

        is_addition = comp["statut"] == "ABSENT_FICHIER"
        is_file_row = bool(comp["date_fichier"])

        if not (is_addition or is_file_row):
            continue

        lunar_extra = {}
        if is_addition:
            date_value, libelle = (
                comp["date_reference"],
                comp["libelle_reference"],
            )
            if comp.get("lunar"):
                vis = comp["lunar"]["visibilite"]
                lunar_extra = {
                    "Date_Visibilite": vis.get("date_locale", ""),
                    "Ecart_Jours": vis["ecart_jours"],
                    "Statut_Lunaire": vis["statut"],
                }
                if comp["lunar"]["confirmation"] != "A_CONFIRMER":
                    statut_ref = comp["lunar"]["confirmation"]
                else:
                    statut_ref = "DATE_ESTIMEE"
            else:
                statut_ref = {
                    "official_legal": "CONFIRME_OFFICIELLEMENT",
                    "official_calendar": "CONFIRME_OFFICIELLEMENT",
                    "secondary_api": "REFERENCE_SECONDAIRE",
                    "estimated_calendar": "DATE_ESTIMEE",
                }.get(source["type"], "A_CONFIRMER")
        else:
            date_value, libelle = comp["date_fichier"], comp["libelle_fichier"]
            statut_ref = (
                "REFERENCE_SECONDAIRE"
                if comp["statut"] == "CONCORDANT"
                else "A_CONFIRMER"
            )

        proposal_rows.append({
            "Proposition_ID": comp["proposition_id"],
            "Date": str(date_value),
            "Libelle": libelle,
            "Pays": comp["pays"],
            "Site": comp["site"],
            "Statut_Reference": statut_ref,
            "Source": source["name"],
            "Decision_Humaine": "",
            "Date_Validation": "",
            **lunar_extra,
        })

    return journal_rows, comparison_rows, proposal_rows


def append_journal(journal_rows):
    """Ajoute les lignes au journal append-only."""

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(journal_rows, columns=JOURNAL_COLUMNS)
    _locked_append(JOURNAL_PATH, df, JOURNAL_COLUMNS)


def append_comparisons(comparison_rows):
    """Ajoute les lignes au fichier de comparaison append-only."""

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(comparison_rows, columns=COMPARISON_COLUMNS)
    _locked_append(COMPARISON_PATH, df, COMPARISON_COLUMNS)


def write_proposals(proposal_rows):
    """
    Réécrit le fichier de propositions en préservant les
    Decision_Humaine / Date_Validation déjà saisies,
    appariées par Proposition_ID.
    """

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    new_df = pd.DataFrame(proposal_rows, columns=PROPOSAL_COLUMNS)

    preserved = {}
    if PROPOSALS_PATH.exists():
        old = pd.read_csv(PROPOSALS_PATH, dtype=str).fillna("")
        for _, row in old.iterrows():
            preserved[row["Proposition_ID"]] = (
                row.get("Decision_Humaine", ""),
                row.get("Date_Validation", ""),
            )

    for index, row in new_df.iterrows():
        decision, validation = preserved.get(
            row["Proposition_ID"], ("", "")
        )
        new_df.at[index, "Decision_Humaine"] = decision
        new_df.at[index, "Date_Validation"] = validation

    new_df.to_csv(PROPOSALS_PATH, index=False, encoding="utf-8-sig")


def write_report(journal_rows, comparison_rows, proposal_rows, execution):
    """Génère le rapport Excel de l'exécution courante."""

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    journal_df = pd.DataFrame(journal_rows)
    comparison_df = pd.DataFrame(comparison_rows)

    synthese = pd.DataFrame({
        "Execution_ID": [execution["execution_id"]] * 3,
        "Metrique": [
            "Lignes de journal",
            "Lignes de comparaison",
            "Statuts distincts",
        ],
        "Valeur": [
            len(journal_df),
            len(comparison_df),
            comparison_df["statut"].nunique()
            if not comparison_df.empty else 0,
        ],
    })

    # Fêtes musulmanes estimées : mises en avant dans le rapport.
    estimated = [
        row for row in proposal_rows
        if row["Statut_Reference"] in (
            "DATE_ESTIMEE", "REFERENCE_ASTRONOMIQUE",
            "OBSERVATION_RAPPORTEE",
        )
    ]
    fetes_df = pd.DataFrame(
        estimated if estimated else [{"information": "aucune"}]
    )

    with pd.ExcelWriter(REPORT_XLSX, engine="openpyxl") as writer:
        journal_df.to_excel(writer, sheet_name="Journal", index=False)
        comparison_df.to_excel(
            writer, sheet_name="Comparaison", index=False
        )
        fetes_df.to_excel(
            writer, sheet_name="Fetes_Musulmanes", index=False
        )
        synthese.to_excel(writer, sheet_name="Synthese", index=False)
