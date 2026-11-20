
from pathlib import Path
from datetime import datetime

import pandas as pd


# ============================================================
# 1. CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parents[2]

SOURCE = (
    BASE_DIR
    / "data"
    / "raw"
    / "jeu_donnees_westdigital_odoo_interne.xlsx"
)

REPORT_DIR = BASE_DIR / "data" / "reports"

# Feuilles contenant des données métier.
# README est volontairement exclue.
SHEETS_TO_VALIDATE = [
    "Departements",
    "Postes",
    "Employes",
    "Types_Conges",
    "Soldes_Conges",
    "Demandes_Absence",
    "Jours_Feries",
    "Plan_Integration",
    "Nouveaux_Arrivants",
    "Materiel_IT",
    "Demandes_IT_Historique",
    "Tickets_Support",
]

# Clé métier connue à ce stade.
UNIQUE_KEYS = {
    "Demandes_IT_Historique": ["DemandeID"],
    "Plan_Integration": ["TacheID"],
}

# Valeurs reconnues par le prétraitement Odoo du plan
# d'intégration (voir clean.py : PLAN_ECHEANCES et
# PLAN_RESPONSABLES). Doivent rester cohérentes.
PLAN_ECHEANCES_CONNUES = {
    "J-10", "J-7", "J-3", "J-2", "J1", "J2", "J5", "J60",
}
PLAN_RESPONSABLES_CONNUS = {
    "RH", "IT", "Manager", "Employé", "Manager + RH",
}


# ============================================================
# 2. CHARGEMENT DU CLASSEUR
# ============================================================

def load_workbook():
    """Charge toutes les feuilles du classeur Excel."""

    if not SOURCE.exists():
        raise FileNotFoundError(
            f"Fichier source introuvable : {SOURCE}"
        )

    return pd.read_excel(
        SOURCE,
        sheet_name=None,
        engine="openpyxl"
    )


# ============================================================
# 3. OUTIL DE CENTRALISATION DES ANOMALIES
# ============================================================

def add_issue(results, sheet, issue_type, severity,
              column="", excel_row="", value="", message=""):
    """Ajoute une anomalie au rapport."""

    results.append({
        "Feuille": sheet,
        "Type": issue_type,
        "Gravite": severity,
        "Colonne": column,
        "Ligne_Excel": excel_row,
        "Valeur": str(value),
        "Description": message,
    })


# ============================================================
# 4. CONTROLE DES VALEURS MANQUANTES
# ============================================================

def check_missing_values(df, sheet_name, results):
    """
    Recense les valeurs manquantes.
    Elles sont signalées comme informations à examiner :
    une cellule vide n'est pas toujours une anomalie.
    """

    for column in df.columns:

        missing_rows = df.index[df[column].isna()].tolist()

        if missing_rows:
            excel_lines = [row + 2 for row in missing_rows]

            add_issue(
                results=results,
                sheet=sheet_name,
                issue_type="Valeur manquante",
                severity="INFORMATION",
                column=column,
                excel_row=", ".join(map(str, excel_lines)),
                value=f"{len(missing_rows)} cellule(s)",
                message=(
                    f"{len(missing_rows)} valeur(s) manquante(s) "
                    f"dans la colonne {column}. "
                    "Vérifier si ces absences sont légitimes."
                )
            )


# ============================================================
# 5. CONTROLE DES DOUBLONS
# ============================================================

def check_duplicates(df, sheet_name, results, subset=None):
    """
    Détecte les lignes identiques et les doublons de clé.

    subset=None : comparaison de toutes les colonnes.
    subset=[...] : comparaison sur une ou plusieurs colonnes.
    """

    columns = subset if subset else list(df.columns)

    # Vérifier que les colonnes de comparaison existent.
    missing_columns = [
        col for col in columns if col not in df.columns
    ]

    if missing_columns:
        add_issue(
            results,
            sheet_name,
            "Colonne de contrôle absente",
            "ERREUR",
            value=", ".join(missing_columns),
            message="Impossible d'effectuer ce contrôle."
        )
        return

    duplicates = df[
        df.duplicated(subset=columns, keep=False)
    ]

    if duplicates.empty:
        return

    # Les lignes identiques sont regroupées par valeur.
    for _, group in duplicates.groupby(
        columns,
        dropna=False,
        sort=False
    ):
        excel_lines = [
            int(index) + 2 for index in group.index
        ]

        add_issue(
            results,
            sheet_name,
            "Doublon",
            "AVERTISSEMENT",
            column=", ".join(columns),
            excel_row=", ".join(map(str, excel_lines)),
            value=f"{len(group)} occurrences",
            message=(
                "Même valeur détectée plusieurs fois "
                "sur les colonnes de contrôle."
            )
        )


# ============================================================
# 6. CONTROLE DES COLONNES OBLIGATOIRES
# ============================================================

def check_required_columns(
    df, sheet_name, required_columns, results
):
    """Vérifie que les colonnes attendues existent."""

    missing_columns = [
        column
        for column in required_columns
        if column not in df.columns
    ]

    for column in missing_columns:
        add_issue(
            results,
            sheet_name,
            "Colonne obligatoire absente",
            "ERREUR",
            column=column,
            message=(
                f"La colonne obligatoire {column} "
                "n'existe pas dans cette feuille."
            )
        )


# ============================================================
# 7. GENERATION DU RAPPORT
# ============================================================

def generate_report(results):
    """Enregistre les anomalies dans un fichier CSV."""

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    report_path = REPORT_DIR / "rapport_validation.csv"

    columns = [
        "Feuille",
        "Type",
        "Gravite",
        "Colonne",
        "Ligne_Excel",
        "Valeur",
        "Description",
    ]

    report = pd.DataFrame(results, columns=columns)

    report.to_csv(
        report_path,
        index=False,
        encoding="utf-8-sig"
    )

    print("\n=== RAPPORT DE VALIDATION ===")
    print(f"Nombre d'anomalies recensées : {len(report)}")

    if not report.empty:
        print("\nRépartition par gravité :")
        print(report["Gravite"].value_counts().to_string())

        print("\nRépartition par feuille :")
        print(report["Feuille"].value_counts().to_string())

    print(f"\nRapport enregistré : {report_path}")



def inspect_excel_rows(workbook, sheet_name, excel_rows):
    """Affiche les lignes Excel demandées sans modifier les données."""

    if sheet_name not in workbook:
        print(f"Feuille introuvable : {sheet_name}")
        return

    df = workbook[sheet_name]

    # Conversion des numéros Excel en index pandas
    indexes = [row - 2 for row in excel_rows]
    valid_indexes = [
        index for index in indexes
        if 0 <= index < len(df)
    ]

    print(f"\n=== {sheet_name} ===")

    if not valid_indexes:
        print("Aucune ligne trouvée.")
        return

    result = df.iloc[valid_indexes].copy()
    result.insert(
        0,
        "Ligne_Excel",
        [index + 2 for index in valid_indexes]
    )

    print(result.to_string(index=False))

# ============================================================
# 9. CONTROLE DES REGLES METIER
# ============================================================

def check_business_rules(workbook, results):
    """Contrôle quelques règles métier du cas West Digital."""

    # 1. Employés actifs sans département
    df = workbook["Employes"]

    for index, row in df.iterrows():
        if row["Statut"] == "Actif" and pd.isna(row["DepartementID"]):
            add_issue(
                results,
                "Employes",
                "ANOMALIE_METIER",
                "AVERTISSEMENT",
                "DepartementID",
                index + 2,
                "",
                "Un employé actif doit être rattaché à un département."
            )

    # 2. Contrôle des demandes d'absence validées
    df = workbook["Demandes_Absence"]

    for index, row in df.iterrows():
        if row["Statut"] != "Validée":
            continue

        type_conge = row["TypeConge"]
        jours = row["NbJoursDeclares"]

        # Congé maladie : certificat obligatoire et validation RH
        if type_conge == "MAL":
            if row["Justificatif"] != "Oui":
                add_issue(
                    results,
                    "Demandes_Absence",
                    "JUSTIFICATIF_MANQUANT",
                    "AVERTISSEMENT",
                    "Justificatif",
                    index + 2,
                    row["Justificatif"],
                    "Un congé maladie validé doit comporter un justificatif."
                )

            if pd.isna(row["ValidateurRH"]):
                add_issue(
                    results,
                    "Demandes_Absence",
                    "VALIDATION_RH_MANQUANTE",
                    "AVERTISSEMENT",
                    "ValidateurRH",
                    index + 2,
                    "",
                    "Un congé maladie doit être validé par les RH."
                )

        # Congé payé : manager obligatoire, RH si plus de 10 jours
        elif type_conge == "CP":
            if pd.isna(row["ValidateurManager"]):
                add_issue(
                    results,
                    "Demandes_Absence",
                    "VALIDATION_MANAGER_MANQUANTE",
                    "AVERTISSEMENT",
                    "ValidateurManager",
                    index + 2,
                    "",
                    "Un congé payé validé doit avoir un validateur manager."
                )

            if pd.notna(jours) and jours > 10 and pd.isna(row["ValidateurRH"]):
                add_issue(
                    results,
                    "Demandes_Absence",
                    "VALIDATION_RH_MANQUANTE",
                    "AVERTISSEMENT",
                    "ValidateurRH",
                    index + 2,
                    "",
                    "Un congé payé de plus de 10 jours doit être validé par les RH."
                )

        # Congé sans solde : manager et RH obligatoires
        elif type_conge == "SS":
            for colonne, libelle in [
                ("ValidateurManager", "manager"),
                ("ValidateurRH", "RH")
            ]:
                if pd.isna(row[colonne]):
                    add_issue(
                        results,
                        "Demandes_Absence",
                        "VALIDATION_MANQUANTE",
                        "AVERTISSEMENT",
                        colonne,
                        index + 2,
                        "",
                        f"Un congé sans solde doit être validé par les {libelle}."
                    )

    # 3. Tickets clôturés sans dates de suivi
    df = workbook["Demandes_IT_Historique"]

    for index, row in df.iterrows():
        if row["Statut"] == "Clôturée":
            for colonne in ["DatePremiereReponse", "DateResolution"]:
                if pd.isna(row[colonne]):
                    add_issue(
                        results,
                        "Demandes_IT_Historique",
                        "DATE_MANQUANTE_TICKET_CLOTURE",
                        "AVERTISSEMENT",
                        colonne,
                        index + 2,
                        "",
                        "Un ticket clôturé doit avoir cette date renseignée."
                    )

    # 4. Identifiants IT utilisés plusieurs fois avec des données différentes
    # Les doublons exacts sont déjà signalés par le contrôle des lignes identiques.
    df = workbook["Demandes_IT_Historique"]

    doublons = df[
        df["DemandeID"].duplicated(keep=False)
    ]

    for demande_id, groupe in doublons.groupby("DemandeID"):
        if groupe.drop_duplicates().shape[0] <= 1:
            continue

        lignes = (groupe.index + 2).tolist()

        add_issue(
            results,
            "Demandes_IT_Historique",
            "IDENTIFIANT_DUPLIQUE_NON_IDENTIQUE",
            "AVERTISSEMENT",
            "DemandeID",
            ", ".join(map(str, lignes)),
            demande_id,
            "Identifiant réutilisé avec des données différentes."
        )

def check_plan_integration_rules(workbook, results):
    """
    Contrôle la feuille Plan_Integration avant sa préparation
    pour le plan d'activités Odoo : identifiants PI attendus,
    champs obligatoires, échéances et responsables reconnus.
    """

    sheet_name = "Plan_Integration"
    if sheet_name not in workbook:
        return

    df = workbook[sheet_name]

    expected_ids = {f"PI{i:02d}" for i in range(1, 12)}
    actual_ids = set(
        df["TacheID"].dropna().astype(str).str.strip()
    ) if "TacheID" in df.columns else set()

    missing_ids = expected_ids - actual_ids
    unexpected_ids = actual_ids - expected_ids

    if missing_ids or unexpected_ids:
        add_issue(
            results,
            sheet_name,
            "IDENTIFIANTS_PLAN_INATTENDUS",
            "ERREUR",
            "TacheID",
            "",
            ", ".join(sorted(actual_ids)),
            "Identifiants attendus PI01-PI11 ; "
            f"manquants : {sorted(missing_ids) or 'aucun'}, "
            f"inattendus : {sorted(unexpected_ids) or 'aucun'}."
        )

    for index, row in df.iterrows():
        line = index + 2
        tache_id = row.get("TacheID", "")

        for column in ["Tache", "Responsable", "Echeance"]:
            value = row.get(column)
            if pd.isna(value) or str(value).strip() == "":
                add_issue(
                    results,
                    sheet_name,
                    "CHAMP_PLAN_VIDE",
                    "ERREUR",
                    column,
                    line,
                    "",
                    f"{tache_id} : le champ {column} est vide ; "
                    "la tâche ne pourra pas être convertie."
                )

        echeance = row.get("Echeance")
        if (
            pd.notna(echeance)
            and str(echeance).strip() not in PLAN_ECHEANCES_CONNUES
        ):
            add_issue(
                results,
                sheet_name,
                "ECHEANCE_NON_RECONNUE",
                "ERREUR",
                "Echeance",
                line,
                echeance,
                "Échéance hors du référentiel J-10/J-7/J-3/J-2/"
                "J1/J2/J5/J60 ; conversion impossible."
            )

        responsable = row.get("Responsable")
        if (
            pd.notna(responsable)
            and str(responsable).strip() not in PLAN_RESPONSABLES_CONNUS
        ):
            add_issue(
                results,
                sheet_name,
                "RESPONSABLE_NON_RECONNU",
                "ERREUR",
                "Responsable",
                line,
                responsable,
                "Responsable hors des rôles RH/IT/Manager/"
                "Employé/Manager + RH ; assignation impossible."
            )


def check_duplicate_employee_contacts(workbook, results):
    """
    Signale les emails et téléphones partagés
    par plusieurs employés.
    """

    sheet_name = "Employes"
    df = workbook[sheet_name]

    for column in ["EmailPro", "Telephone"]:

        if column not in df.columns:
            continue

        values_to_check = df[column]

        if column == "EmailPro":
            values_to_check = values_to_check.str.strip().str.lower()

        duplicates = df[
            values_to_check.notna()
            & values_to_check.duplicated(keep=False)
        ].copy()

        duplicates["_ValeurControle"] = values_to_check.loc[duplicates.index]

        for value, group in duplicates.groupby("_ValeurControle"):

            if len(group) < 2:
                continue

            lines = (group.index + 2).tolist()
            employees = group["EmployeeID"].tolist()

            add_issue(
                results,
                sheet_name,
                "COORDONNEE_PARTAGEE",
                "AVERTISSEMENT",
                column,
                ", ".join(map(str, lines)),
                value,
                f"Coordonnée partagée par les employés : "
                f"{', '.join(employees)}."
            )

# ============================================================
# 10. EXECUTION PRINCIPALE
# ============================================================

def check_equipment_rules(workbook, results):
    """
    Contrôle les règles métier de la feuille Materiel_IT :
    unicité du numéro de série, cohérence statut/affectation
    et référence de l'agent affecté.
    """

    sheet_name = "Materiel_IT"
    df = workbook[sheet_name]

    employe_ids = set(workbook["Employes"]["EmployeeID"].dropna())

    # 1. Numéros de série dupliqués : anomalie bloquante,
    # le champ serial_no doit être unique dans Odoo.
    doublons = df[
        df["NumSerie"].notna()
        & (df["NumSerie"].astype(str).str.strip() != "")
        & df["NumSerie"].duplicated(keep=False)
    ]

    for _, groupe in doublons.groupby("NumSerie"):
        details = "; ".join(
            f"{row['AssetID']} ({row['Statut']}, ligne {index + 2})"
            for index, row in groupe.iterrows()
        )

        add_issue(
            results,
            sheet_name,
            "NUMERO_SERIE_DUPLIQUE",
            "ERREUR",
            "NumSerie",
            ", ".join(str(i + 2) for i in groupe.index),
            groupe.iloc[0]["NumSerie"],
            "Numéro de série partagé par plusieurs équipements : "
            f"{details}."
        )

    # 2. Cohérence entre le statut et l'affectation
    for index, row in df.iterrows():
        statut = row["Statut"]
        affecte_a = row["AffecteA"]
        a_affectation = pd.notna(affecte_a) and str(affecte_a).strip() != ""

        if statut == "En stock" and a_affectation:
            add_issue(
                results,
                sheet_name,
                "STATUT_INCOHERENT",
                "AVERTISSEMENT",
                "AffecteA",
                index + 2,
                f"{row['AssetID']} → {affecte_a}",
                "Un équipement en stock ne devrait pas avoir d'affectation."
            )

        if statut == "Affecté" and not a_affectation:
            add_issue(
                results,
                sheet_name,
                "STATUT_INCOHERENT",
                "AVERTISSEMENT",
                "AffecteA",
                index + 2,
                row["AssetID"],
                "Un équipement affecté devrait indiquer son détenteur."
            )

        if statut == "En réparation" and a_affectation:
            add_issue(
                results,
                sheet_name,
                "STATUT_INCOHERENT",
                "AVERTISSEMENT",
                "AffecteA",
                index + 2,
                f"{row['AssetID']} → {affecte_a}",
                "Un équipement en réparation ne devrait pas avoir "
                "d'affectation active."
            )

        # 3. Agent affecté inconnu des employés : bloquant,
        # la ligne sera exclue du CSV d'import.
        if a_affectation and str(affecte_a).strip() not in employe_ids:
            add_issue(
                results,
                sheet_name,
                "AFFECTATION_SANS_CORRESPONDANCE",
                "ERREUR",
                "AffecteA",
                index + 2,
                f"{row['AssetID']} → {affecte_a}",
                "L'agent affecté ne correspond à aucun employé connu ; "
                "l'équipement est exclu du CSV d'import."
            )

    # 4. Profil portable absent ou non reconnu : le profil
    # matériel R10 du bundle ne pourra pas être déduit.
    profils_valides = {"Dev", "Standard", "dev", "standard"}
    portables = df[
        df["Categorie"] == "Ordinateur portable"
    ] if "Categorie" in df.columns else df.iloc[0:0]

    for index, row in portables.iterrows():
        valeur = row.get("ProfilPortable")
        if pd.isna(valeur) or str(valeur).strip() == "":
            add_issue(
                results,
                sheet_name,
                "PROFIL_PORTABLE_MANQUANT",
                "ERREUR",
                "ProfilPortable",
                index + 2,
                row.get("AssetID", ""),
                "Portable sans profil reconnu ; "
                "le profil matériel R10 ne peut pas être déduit."
            )
        elif str(valeur).strip() not in profils_valides:
            add_issue(
                results,
                sheet_name,
                "PROFIL_PORTABLE_INCONNU",
                "ERREUR",
                "ProfilPortable",
                index + 2,
                valeur,
                "Profil portable hors Dev/Standard ; "
                "à qualifier manuellement."
            )


def main():

    print("Démarrage de la validation des données...\n")

    workbook = load_workbook()
    results = []
    

    # Inspection ciblée des anomalies à analyser
    inspect_excel_rows(workbook, "Employes", [2, 37])

    inspect_excel_rows(
        workbook,
        "Types_Conges",
        [2, 3, 7]
    )

    inspect_excel_rows(
        workbook,
        "Demandes_Absence",
        [2, 6, 8, 11, 13]
    )

    inspect_excel_rows(
        workbook,
        "Demandes_IT_Historique",
        [7, 55, 61, 62, 243, 245, 249, 250]
    )


    for sheet_name in SHEETS_TO_VALIDATE:

        if sheet_name not in workbook:
            add_issue(
                results,
                sheet_name,
                "Feuille absente",
                "ERREUR",
                message="La feuille attendue est absente du classeur."
            )
            continue

        df = workbook[sheet_name]

        print(f"Contrôle de la feuille : {sheet_name}")

        # Contrôles généraux
        check_missing_values(df, sheet_name, results)

        # Détection des lignes strictement identiques
        check_duplicates(df, sheet_name, results)

        # Contrôle des clés métier connues
        if sheet_name in UNIQUE_KEYS:
            # Contrôle des identifiants sur les données originales.
            # Les doublons exacts sont déjà signalés par le contrôle précédent.
            key_columns = UNIQUE_KEYS[sheet_name]

            duplicate_keys = df[
                df.duplicated(subset=key_columns, keep=False)
            ]

            for key, group in duplicate_keys.groupby(key_columns[0]):
                if group.drop_duplicates().shape[0] > 1:
                    lines = (group.index + 2).tolist()

                    add_issue(
                        results,
                        sheet_name,
                        "IDENTIFIANT_DUPLIQUE_NON_IDENTIQUE",
                        "AVERTISSEMENT",
                        key_columns[0],
                        ", ".join(map(str, lines)),
                        key,
                        "Identifiant réutilisé avec des données différentes."
                    )

    # Contrôles complémentaires des règles métier
    check_business_rules(workbook, results)

    # Détection des coordonnées partagées
    check_duplicate_employee_contacts(workbook, results)

    # Contrôle des règles métier du matériel IT
    check_equipment_rules(workbook, results)

    # Contrôle du plan d'intégration avant conversion Odoo
    check_plan_integration_rules(workbook, results)

    generate_report(results)

    print("\nValidation terminée.")


if __name__ == "__main__":
    main()
