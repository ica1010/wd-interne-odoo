"""
Contrôle de cohérence des jours fériés du Togo (site Lomé).

Compare la feuille Jours_Feries du classeur source aux
références Nager.Date API v4 (source secondaire) et produit
un journal d'audit append-only, un fichier de comparaison
et un fichier de propositions.

Aucune correction n'est appliquée silencieusement et le
classeur source n'est jamais modifié.
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE_DIR / "scripts"))

import holidays  # noqa: E402 - package local scripts/holidays
from holidays import fetch_sources, normalize  # noqa: E402
from holidays import audit, lunar  # noqa: E402


SOURCE = (
    BASE_DIR
    / "data"
    / "raw"
    / "jeu_donnees_westdigital_odoo_interne.xlsx"
)
SHEET_NAME = "Jours_Feries"

CONFIG_DIR = BASE_DIR / "config" / "holidays"
PROCESSED_DIR = BASE_DIR / "data" / "processed"
REPORT_DIR = BASE_DIR / "data" / "reports"

# Périmètre strict : Togo, site Lomé.
COUNTRY_CODE = "TG"
SITE = "Lomé"

PROXIMITY_DAYS = 3
LABEL_THRESHOLD = 0.85


def load_config():
    sources = json.loads(
        (CONFIG_DIR / "sources.json").read_text(encoding="utf-8")
    )
    countries = json.loads(
        (CONFIG_DIR / "countries.json").read_text(encoding="utf-8")
    )
    return sources, countries


def load_source_rows():
    """
    Lit la feuille Jours_Feries du classeur source.
    Retourne (DataFrame, empreinte SHA-256 du classeur).
    """

    fingerprint = hashlib.sha256(SOURCE.read_bytes()).hexdigest()

    df = pd.read_excel(
        SOURCE,
        sheet_name=SHEET_NAME,
        engine="openpyxl",
    )
    df["Date"] = df["Date"].map(normalize.parse_date)

    return df, fingerprint


def export_source_copy(df):
    """Exporte une copie conforme de la feuille source."""

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    output = PROCESSED_DIR / "jours_feries_source.csv"
    df_out = df.copy()
    df_out["Date"] = df_out["Date"].map(str)
    df_out.to_csv(output, index=False, encoding="utf-8-sig")
    return output


def covered_years(df):
    return sorted({row.year for row in df["Date"]})


def find_proximity(file_date, reference_dates):
    """
    Recherche une date de référence à ±PROXIMITY_DAYS.
    Retourne la plus proche ou None. Ne fusionne rien :
    sert uniquement à émettre une suggestion A_EXAMINER.
    """

    best = None
    for ref_date in reference_dates:
        delta = abs((ref_date - file_date).days)
        if delta <= PROXIMITY_DAYS and (
            best is None or delta < abs((best - file_date).days)
        ):
            best = ref_date
    return best


def make_proposition_id(date_value, label, taken_ids):
    """Identifiant stable et unique pour une proposition."""

    base = f"PROP-{date_value.strftime('%Y%m%d')}-{normalize.slugify_label(label)}"
    candidate = base
    suffix = 2
    while candidate in taken_ids:
        candidate = f"{base}-{suffix}"
        suffix += 1
    taken_ids.add(candidate)
    return candidate


def confirmation_status(source_type, fetch_status):
    """Statut de confirmation selon la nature de la source."""

    if fetch_status != "OK":
        return "SOURCE_INACCESSIBLE"
    return {
        "official_legal": "CONFIRME_OFFICIELLEMENT",
        "official_calendar": "CONFIRME_OFFICIELLEMENT",
        "secondary_api": "REFERENCE_SECONDAIRE",
        "estimated_calendar": "DATE_ESTIMEE",
    }.get(source_type, "A_CONFIRMER")


def build_comparisons(file_df, reference_records, fetch_status,
                      source, pays, site):
    """
    Construit les lignes de comparaison et de journal.
    Appariement par date exacte uniquement ; la proximité
    ne produit que des suggestions A_EXAMINER séparées.
    """

    matched_ref_dates = set()
    comparisons = []
    taken_ids = set()

    # Source inaccessible : les lignes du fichier sont journalisées
    # comme non vérifiées, sans comparaison ni proposition.
    if fetch_status != "OK":
        for index, row in file_df.iterrows():
            comparisons.append({
                "proposition_id": make_proposition_id(
                    row["Date"], row["Libelle"], taken_ids
                ),
                "pays": pays,
                "site": site,
                "date_fichier": row["Date"],
                "libelle_fichier": row["Libelle"],
                "date_reference": "",
                "libelle_reference": "",
                "statut": "SOURCE_INACCESSIBLE",
                "decision_proposee": "SOURCE_NON_VERIFIEE",
                "justification": (
                    "Source de référence inaccessible ; "
                    "contrôle non effectué pour cette ligne."
                ),
                "source": source,
                "fetch_status": fetch_status,
            })
        return comparisons

    ref_by_date = {}
    for record in reference_records:
        ref_date = normalize.parse_date(record["date"])
        ref_by_date.setdefault(ref_date, record)

    for index, row in file_df.iterrows():
        file_date = row["Date"]
        file_label = row["Libelle"]
        proposition_id = make_proposition_id(
            file_date, file_label, taken_ids
        )

        reference = ref_by_date.get(file_date)

        if reference is not None:
            matched_ref_dates.add(file_date)
            if normalize.labels_match(
                file_label, reference["name"], LABEL_THRESHOLD
            ):
                statut = "CONCORDANT"
                decision = "CONSERVER"
                justification = "Date et libellé concordants avec la référence."
            else:
                statut = "LIBELLE_DIFFERENT"
                decision = "A_EXAMINER"
                justification = (
                    f"Libellé fichier « {file_label} » différent de la "
                    f"référence « {reference['name']} ». Aucune "
                    "correction automatique."
                )

            comparisons.append({
                "proposition_id": proposition_id,
                "pays": pays,
                "site": site,
                "date_fichier": file_date,
                "libelle_fichier": file_label,
                "date_reference": file_date,
                "libelle_reference": reference["name"],
                "statut": statut,
                "decision_proposee": decision,
                "justification": justification,
                "source": reference.get("source_info", source),
                "fetch_status": reference.get("fetch_status", fetch_status),
            })
        else:
            # Date absente des références : conservée, à confirmer.
            comparisons.append({
                "proposition_id": proposition_id,
                "pays": pays,
                "site": site,
                "date_fichier": file_date,
                "libelle_fichier": file_label,
                "date_reference": "",
                "libelle_reference": "",
                "statut": "ABSENT_SOURCE",
                "decision_proposee": "A_CONFIRMER",
                "justification": (
                    "Date absente de la source de référence ; "
                    "absence dans la source ne signifie pas absence "
                    "légale. Aucun retrait automatique."
                ),
                "source": source,
                "fetch_status": fetch_status,
            })

            # Suggestion de rapprochement par proximité :
            # enregistrement séparé, aucune fusion.
            nearby = find_proximity(file_date, ref_by_date.keys())
            if nearby is not None and nearby not in matched_ref_dates:
                comparisons.append({
                    "proposition_id": "",
                    "pays": pays,
                    "site": site,
                    "date_fichier": file_date,
                    "libelle_fichier": file_label,
                    "date_reference": nearby,
                    "libelle_reference": ref_by_date[nearby]["name"],
                    "statut": "A_EXAMINER",
                    "decision_proposee": "A_EXAMINER",
                    "justification": (
                        f"Date de référence proche ({nearby}) : "
                        "rapprochement suggéré, non fusionné."
                    ),
                    "source": ref_by_date[nearby].get(
                        "source_info", source
                    ),
                    "fetch_status": ref_by_date[nearby].get(
                        "fetch_status", fetch_status
                    ),
                })

    # Dates de référence absentes du fichier : propositions
    # d'ajout, limitées à la période couverte par le fichier.
    file_dates = [row["Date"] for _, row in file_df.iterrows()]
    period_start = min(file_dates)
    period_end = max(file_dates)

    for ref_date, record in sorted(ref_by_date.items()):
        if ref_date in matched_ref_dates:
            continue
        if ref_date < period_start or ref_date > period_end:
            continue

        proposition_id = make_proposition_id(
            ref_date, record["name"], taken_ids
        )

        comparisons.append({
            "proposition_id": proposition_id,
            "pays": pays,
            "site": site,
            "date_fichier": "",
            "libelle_fichier": "",
            "date_reference": ref_date,
            "libelle_reference": record["name"],
            "statut": "ABSENT_FICHIER",
            "decision_proposee": "PROPOSITION_AJOUT",
            "justification": (
                "Jour férié présent dans la référence mais absent "
                "du fichier ; proposition d'ajout avec source."
            ),
            "source": record.get("source_info", source),
            "fetch_status": record.get("fetch_status", fetch_status),
            "lunar": record.get("lunar"),
        })

    return comparisons


def run_control(offline=False, country_code=COUNTRY_CODE):
    """
    Exécute le contrôle complet et retourne le contexte
    nécessaire à l'écriture des rapports.
    """

    sources_config, countries_config = load_config()
    country = countries_config[country_code]
    pays = country["code"]
    site = country["sites"][0]

    file_df, fingerprint = load_source_rows()
    export_source_copy(file_df)
    years = covered_years(file_df)

    machine_sources = [
        s for s in sources_config[country_code]["sources"]
        if s.get("machine_readable")
    ]
    manual_sources = [
        s for s in sources_config[country_code]["sources"]
        if not s.get("machine_readable")
    ]

    execution = {
        "execution_id": datetime.now().strftime("CTRL-%Y%m%d-%H%M%S"),
        "version": holidays.__version__,
        "date_controle": datetime.now().isoformat(timespec="seconds"),
        "empreinte_source": fingerprint,
        "pays": pays,
        "site": site,
        "annees": years,
        "comparisons": [],
        "fetch_results": [],
        "manual_sources": manual_sources,
    }

    primary = machine_sources[0] if machine_sources else None

    if primary is None:
        execution["error"] = "Aucune source machine-readable configurée"
    else:
        # Vérification de la couverture du pays avant comparaison.
        supported, support_error = fetch_sources.is_country_supported(
            primary["available_url"], country_code
        ) if not offline else (True, "")

        source_info = {
            "id": primary["id"],
            "name": primary["name"],
            "type": primary["type"],
            "reliability": primary["reliability"],
            "url": primary.get("available_url", ""),
        }

        if not supported:
            execution["fetch_results"].append({
                "source": primary,
                "status": "SOURCE_INACCESSIBLE",
                "error": support_error,
            })
            execution["comparisons"] = build_comparisons(
                file_df, [], "SOURCE_INACCESSIBLE",
                source_info, pays, site,
            )
        else:
            result = fetch_sources.fetch_holidays(
                primary, country_code, years, offline=offline
            )
            execution["fetch_results"].append({
                "source": primary,
                "status": result["status"],
                "error": result["error"],
                "cache_files": result["cache_files"],
            })

            # Seconde source : fêtes musulmanes estimées (AlAdhan).
            islamic_source = next(
                (s for s in machine_sources
                 if s["type"] == "estimated_calendar"),
                None,
            )
            islamic_records = []

            if islamic_source and country.get("islamic_events"):
                islamic = fetch_sources.fetch_islamic_events(
                    islamic_source, country, offline=offline
                )
                execution["fetch_results"].append({
                    "source": islamic_source,
                    "status": islamic["status"],
                    "error": islamic["error"],
                    "cache_files": islamic["cache_files"],
                })
                islamic_records = islamic["records"]

            all_records = list(result["records"]) + islamic_records

            for record in all_records:
                record_source = (
                    islamic_source
                    if record.get("is_estimated")
                    else primary
                )
                record["source_info"] = {
                    "id": record_source["id"],
                    "name": record_source["name"],
                    "type": record_source["type"],
                    "reliability": record_source["reliability"],
                    "url": record.get("source_url", ""),
                }
                record["fetch_status"] = (
                    "OK" if record.get("is_estimated")
                    else result["status"]
                )

            # Couche lunaire : visibilité astronomique à Lomé
            # et recherche d'observations/annonces saisies.
            for record in islamic_records:
                record["lunar"] = lunar.enrich_record(
                    country_code, record
                )

            execution["comparisons"] = build_comparisons(
                file_df,
                all_records,
                result["status"],
                source_info,
                pays,
                site,
            )

    return execution


def main():
    parser = argparse.ArgumentParser(
        description="Contrôle des jours fériés du Togo (Lomé)."
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Utilise le cache local au lieu d'interroger l'API.",
    )
    args = parser.parse_args()

    print("Démarrage du contrôle des jours fériés (TG / Lomé)...\n")

    execution = run_control(offline=args.offline)

    journal_rows, comparison_rows, proposal_rows = audit.build_outputs(
        execution
    )

    audit.append_journal(journal_rows)
    audit.append_comparisons(comparison_rows)
    audit.write_proposals(proposal_rows)
    audit.write_report(journal_rows, comparison_rows, proposal_rows, execution)

    print(f"Exécution : {execution['execution_id']}")
    print(f"Journal : {len(journal_rows)} ligne(s) ajoutée(s)")
    print(f"Comparaison : {len(comparison_rows)} ligne(s) ajoutée(s)")
    print(f"Propositions : {len(proposal_rows)} ligne(s)")
    print("\nContrôle terminé. Aucune importation Odoo "
          "ne doit être lancée avant validation humaine.")


if __name__ == "__main__":
    main()
