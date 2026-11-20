"""
Export du calendrier des jours fériés pour import Odoo.

Produit deux fichiers dans `data/processed/` :

- `Jours_Feries.csv` : dataset final des jours fériés TG/Lomé.
  - Les 13 fériés du classeber source sont repris sans
    modification de date ni de libellé.
  - Les fêtes musulmanes utilisent la date ajustée selon la
    couche lunaire (premier croissant visible à Lomé) ;
    Korité et Tabaski couvrent deux jours.
  - Colonnes : Date, Libelle, date_from, date_to (format
    Odoo), Pays, Site, Source, Source_ID, Statut_Reference.
- `Jours_Feries_Sources.csv` : référentiel des sources
  utilisées (identifiant, nom, type, URL, fiabilité, rôle).

La décision humaine est tracée dans
`jours_feries_proposes.csv` (Decision_Humaine /
Date_Validation) pour les lignes concernées.
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE_DIR / "scripts"))

from holidays import lunar  # noqa: E402

CONFIG_DIR = BASE_DIR / "config" / "holidays"
PROCESSED_DIR = BASE_DIR / "data" / "processed"

SOURCE_WORKBOOK = (
    BASE_DIR
    / "data"
    / "raw"
    / "jeu_donnees_westdigital_odoo_interne.xlsx"
)
SHEET_NAME = "Jours_Feries"
PROPOSALS_PATH = PROCESSED_DIR / "jours_feries_proposes.csv"
OUTPUT_PATH = PROCESSED_DIR / "Jours_Feries.csv"
SOURCES_PATH = PROCESSED_DIR / "Jours_Feries_Sources.csv"

TWO_DAY_EVENTS = {"Korité", "Tabaski"}

FILE_SOURCE_ID = "FICHIER_WD"
FILE_SOURCE_NAME = "Classeur West Digital (Jours_Feries)"
LUNAR_SOURCE_ID = "LUNAR_LOME"
LUNAR_SOURCE_NAME = "Calcul astronomique local (Lomé)"

OUTPUT_COLUMNS = [
    "Date",
    "Libelle",
    "date_from",
    "date_to",
    "Pays",
    "Site",
    "Source",
    "Source_ID",
    "Statut_Reference",
]


def load_islamic_events():
    """Mapping nom d'événement -> jour hijri (config, pas de codage en dur)."""

    countries = json.loads(
        (CONFIG_DIR / "countries.json").read_text(encoding="utf-8")
    )
    return {
        event["nom"]: event["jour"]
        for event in countries["TG"]["islamic_events"]
    }


def adjusted_date(libelle, uaq_date):
    """
    Date ajustée Lomé pour une fête musulmane.
    Retourne (date, ecart_jours) — jamais de confirmation.
    """

    hijri_day = load_islamic_events().get(libelle)
    if hijri_day is None:
        return uaq_date, None

    result = lunar.estimate_local_holiday(uaq_date, hijri_day)
    if not result["date_locale"]:
        return uaq_date, None

    return (
        date.fromisoformat(result["date_locale"]),
        result["ecart_jours"],
    )


def _odoo_range(day):
    """Plage datetime d'une journée complète, format Odoo."""

    return (
        f"{day.isoformat()} 00:00:00",
        f"{day.isoformat()} 23:59:59",
    )


def _row(day, libelle, pays, site, source, source_id, statut,
         end_day=None):
    """
    Ligne du dataset. `end_day` permet une plage multi-jours
    (ex. Korité sur 2 jours) : date_to = end_day 23:59:59.
    """

    date_from, _ = _odoo_range(day)
    date_to = (
        f"{end_day.isoformat()} 23:59:59"
        if end_day is not None
        else f"{day.isoformat()} 23:59:59"
    )
    return {
        "Date": day.isoformat(),
        "Libelle": libelle,
        "date_from": date_from,
        "date_to": date_to,
        "Pays": pays,
        "Site": site,
        "Source": source,
        "Source_ID": source_id,
        "Statut_Reference": statut,
    }


def load_file_holidays():
    """Lit la feuille Jours_Feries du classeur source."""

    df = pd.read_excel(
        SOURCE_WORKBOOK,
        sheet_name=SHEET_NAME,
        engine="openpyxl",
    )
    df["Date"] = pd.to_datetime(df["Date"]).dt.date
    return df


def build_rows():
    """
    Construit les lignes du dataset final et met à jour les
    décisions humaines dans le fichier de propositions.
    Retourne (rows, proposals).
    """

    file_df = load_file_holidays()
    proposals = pd.read_csv(PROPOSALS_PATH, dtype=str).fillna("")
    rows = []

    for _, row in file_df.iterrows():
        rows.append(_row(
            row["Date"],
            row["Libelle"],
            "TG",
            "Lomé",
            FILE_SOURCE_NAME,
            FILE_SOURCE_ID,
            "REFERENCE_SECONDAIRE",
        ))

    for _, prop in proposals.iterrows():
        if "AlAdhan" not in prop["Source"]:
            continue

        libelle = prop["Libelle"]
        uaq_date = date.fromisoformat(prop["Date"])
        event_date, ecart = adjusted_date(libelle, uaq_date)

        # Traçabilité de la décision humaine.
        mask = proposals["Proposition_ID"] == prop["Proposition_ID"]
        proposals.loc[mask, "Decision_Humaine"] = "VALIDE_IMPORT"
        proposals.loc[mask, "Date_Validation"] = date.today().isoformat()

        source = (
            "AlAdhan API (calendrier calculé) + visibilité "
            "lunaire Lomé"
            + (f" ({ecart:+d} j vs Umm al-Qura)" if ecart is not None else "")
        )

        # Korité et Tabaski : une seule ligne couvrant
        # deux jours (date_from → date_to), pas de "2e jour".
        end_day = (
            event_date + timedelta(days=1)
            if libelle in TWO_DAY_EVENTS
            else None
        )
        rows.append(_row(
            event_date,
            libelle,
            prop["Pays"],
            prop["Site"],
            source,
            f"ALADHAN;{LUNAR_SOURCE_ID}",
            "REFERENCE_ASTRONOMIQUE",
            end_day=end_day,
        ))

    return rows, proposals


def build_sources_rows():
    """Référentiel des sources : config + sources locales."""

    sources_config = json.loads(
        (CONFIG_DIR / "sources.json").read_text(encoding="utf-8")
    )

    rows = [{
        "Source_ID": FILE_SOURCE_ID,
        "Nom": FILE_SOURCE_NAME,
        "Type": "classeur_source",
        "URL": "",
        "Fiabilite": "reference_metier",
        "Role": "donnees_import",
    }, {
        "Source_ID": LUNAR_SOURCE_ID,
        "Nom": LUNAR_SOURCE_NAME,
        "Type": "astronomical_calculation",
        "URL": "",
        "Fiabilite": "indicative",
        "Role": "reference",
    }]

    for source in sources_config["TG"]["sources"]:
        rows.append({
            "Source_ID": source["id"],
            "Nom": source["name"],
            "Type": source["type"],
            "URL": source.get(
                "url",
                source.get(
                    "base_url",
                    source.get("date_url_template", ""),
                ),
            ),
            "Fiabilite": source["reliability"],
            "Role": source["role"],
        })

    return rows


def main():
    rows, proposals = build_rows()

    # La décision humaine est persistée dans le fichier de
    # propositions (colonnes prévues à cet effet).
    proposals.to_csv(
        PROPOSALS_PATH, index=False, encoding="utf-8-sig"
    )

    output = (
        pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
        .sort_values("Date")
        .reset_index(drop=True)
    )
    output.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")

    sources = pd.DataFrame(build_sources_rows())
    sources.to_csv(SOURCES_PATH, index=False, encoding="utf-8-sig")

    print(f"Export : {OUTPUT_PATH}")
    print(f"{len(output)} jour(s) férié(s) :")
    print(output.to_string(index=False))
    print(f"\nSources : {SOURCES_PATH} ({len(sources)} sources)")
    print(
        "\nDécision humaine tracée dans "
        "jours_feries_proposes.csv (VALIDE_IMPORT)."
    )
    print(
        "Les dates musulmanes restent des estimations "
        "astronomiques : confirmation officielle à obtenir."
    )


if __name__ == "__main__":
    main()
