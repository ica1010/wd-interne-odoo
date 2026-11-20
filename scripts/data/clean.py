
from pathlib import Path

import pandas as pd
import phonenumbers
from email_validator import validate_email, EmailNotValidError
from openpyxl import load_workbook as load_xlsx
from openpyxl.utils import get_column_letter


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

# Copie corrigée du classeur source : ajoute la colonne calculée
# Solde_a_allouer dans Soldes_Conges pour la préparation des
# allocations de congés Odoo. Le fichier source n'est jamais
# modifié.
CORRECTED_WORKBOOK = SOURCE.with_name(
    SOURCE.stem + "_corrige.xlsx"
)

LEAVE_BALANCE_SHEET = "Soldes_Conges"
LEAVE_BALANCE_COLUMN = "Solde_a_allouer"
LEAVE_BALANCE_SOURCES = [
    "Report2025_jours",
    "Acquis2026_au_31_10_jours",
    "Pris2026_au_31_10_jours",
]

ALLOCATIONS_FILE = "Allocations_Conges.csv"
LEAVE_TYPES_FILE = "Types_Conges_Import.csv"

# Préparation de la feuille Plan_Integration pour le plan
# d'activités natif Odoo (modèle Employé) : les colonnes
# sources sont conservées et les colonnes Odoo ajoutées
# dans le même CSV (Plan_Integration.csv).
PLAN_SHEET = "Plan_Integration"
PLAN_TYPE_ACTIVITE = "À faire (To-Do)"

# Échéances relatives source -> paramètres d'activité Odoo :
# (Intervalle, Unite, Declencher).
PLAN_ECHEANCES = {
    "J-10": (10, "jours", "Avant la date du plan"),
    "J-7": (7, "jours", "Avant la date du plan"),
    "J-3": (3, "jours", "Avant la date du plan"),
    "J-2": (2, "jours", "Avant la date du plan"),
    "J1": (1, "jours", "Après la date du plan"),
    "J2": (2, "jours", "Après la date du plan"),
    "J5": (5, "jours", "Après la date du plan"),
    "J60": (60, "jours", "Après la date du plan"),
}

# Responsable source -> mode d'assignation de l'activité Odoo.
# « Manager + RH » est éclaté en deux activités (Manager puis RH)
# dans prepare_plan_integration, TacheID conservé pour la
# traçabilité.
PLAN_RESPONSABLES = {
    "RH": "Utilisateur par défaut",
    "IT": "Utilisateur par défaut",
    "Manager": "Utilisateur par défaut",
    "Employé": "Employé",
}

# Dataset d'import du « Profil matériel » (R10) sur les
# équipements Odoo : mise à jour par External ID
# (__import__. + AssetID), une ligne par équipement concerné.
EQUIPMENT_PROFILES_FILE = "Materiel_IT_Profils.csv"
EQUIPMENT_PROFILE_LABELS = {
    "dev": "Dev",
    "standard": "Standard",
    "support": "Support",
    "commercial": "Commercial",
}
LAPTOP_CATEGORY = "Ordinateur portable"

# Dataset d'import des demandes IT historiques (Partie 3) :
# mappings source -> valeurs du module wd_it_request.
IT_REQUESTS_SHEET = "Demandes_IT_Historique"
IT_REQUESTS_IMPORT_FILE = "Demandes_IT_Import.csv"
IT_CATEGORY_MAP = {
    "Matériel": "hardware",
    "Logiciel": "software",
    "Accès & comptes": "access",
    "Réseau & Internet": "network",
    "Odoo": "software",
    "Onboarding": "other",
}
IT_CHANNEL_MAP = {
    "WhatsApp": "whatsapp",
    "Email": "email",
    "Téléphone": "phone",
    "Oral": "other",
}
IT_STATE_MAP = {
    "Clôturée": "closed",
    "Résolue": "resolved",
    "Annulée": "cancel",
    "En attente utilisateur": "pending_user",
    "En cours": "in_progress",
    "Nouveau": "draft",
}
IT_PRIORITIES = {"P1", "P2", "P3", "P4"}

# Dataset d'import des demandes d'absence (hr.leave) :
# mapping Statut source -> libellé de sélection Odoo.
# « Brouillon » n'a pas d'équivalent hr.leave (pas d'état
# draft en v18) : la colonne Status reste vide et Odoo
# applique son état par défaut.
LEAVE_ABSENCES_SHEET = "Demandes_Absence"
LEAVE_ABSENCES_IMPORT_FILE = "Demandes_Absence_Import.csv"
LEAVE_STATUT_MAP = {
    "Validée": "Approved",
    "Soumise": "To Approve",
    "Refusée": "Refused",
    "Brouillon": "",
}
LEAVE_TYPES_KNOWN = {
    "CP", "MAL", "SS", "EXC-NAI", "EXC-MAR", "EXC-DEC", "AA",
}


def intern_employee_ids(employes):
    """EmployeeID des stagiaires (TypeContrat contenant
    « stage ») — R6 : pas de congé payé pour eux."""
    if "TypeContrat" not in employes.columns:
        return set()
    mask = employes["TypeContrat"].astype(str).str.contains(
        "stage", case=False, na=False
    )
    return set(employes.loc[mask, "EmployeeID"].astype(str))


def leave_type_external_id(code):
    """
    External ID d'un type de congé dans Odoo : préfixe
    __import__. + code source (ex. __import__.CP),
    format des identifiants créés par l'import Odoo.
    """
    return f"__import__.{str(code).strip()}"


def leave_balance(df):
    """
    Solde à allouer = report 2025 + acquis 2026 - pris 2026.

    Règle unique partagée par le journal d'anomalies et
    l'export des allocations Odoo. Les valeurs non
    numériques donnent NaN : elles sont signalées au
    journal, jamais corrigées silencieusement.
    """

    numeric = df[LEAVE_BALANCE_SOURCES].apply(
        pd.to_numeric, errors="coerce"
    )
    return (
        numeric["Report2025_jours"]
        + numeric["Acquis2026_au_31_10_jours"]
        - numeric["Pris2026_au_31_10_jours"]
    )

PROCESSED_DIR = BASE_DIR / "data" / "processed"
REPORT_DIR = BASE_DIR / "data" / "reports"

# Anomalies détectées pendant la préparation des données.
# Elles sont intégrées au journal des anomalies.
PREPARATION_ANOMALIES = []

# Équipements exclus du CSV d'import pour anomalies bloquantes.
EQUIPMENT_EXCLUSIONS = []

# Feuilles contenant des données métier.
# README est volontairement exclue.
SHEETS_TO_EXPORT = [
    "Departements",
    "Postes",
    "Employes",
    "Types_Conges",
    "Soldes_Conges",
    "Demandes_Absence",
    "Plan_Integration",
    "Nouveaux_Arrivants",
    "Materiel_IT",
    "Demandes_IT_Historique",
    "Tickets_Support",
]


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
# 3. EXPORT DES DONNEES PREPAREES
# ============================================================

def export_sheets(workbook):
    """
    Exporte les feuilles métier en CSV dans data/processed.

    Les données sont dédupliquées et normalisées avant
    l'export. Les anomalies non résolues restent signalées
    par validate.py et ne sont pas corrigées ici.
    """

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    for sheet_name in SHEETS_TO_EXPORT:

        if sheet_name not in workbook:
            print(f"Feuille absente : {sheet_name}")
            continue

        df = workbook[sheet_name].copy()

        # Suppression des doublons strictement identiques
        # uniquement dans l'historique des demandes IT.
        if sheet_name == "Demandes_IT_Historique":
            df, removed_count = remove_exact_duplicates(df, sheet_name)

        # Normalisation des colonnes textuelles.
        df = normalize_text_columns(df)

        # Normalisation des coordonnées des employés.
        if sheet_name == "Employes":
            df = normalize_employee_contacts(df)

            # Préparation des employés pour Odoo.
            df, employee_anomalies = prepare_employees(
                df,
                workbook["Postes"]
            )
            PREPARATION_ANOMALIES.extend(employee_anomalies)

        # Préparation du matériel pour Odoo.
        if sheet_name == "Materiel_IT":
            df, equipment_exclusions, equipment_anomalies = (
                prepare_equipment(df, workbook["Employes"])
            )
            EQUIPMENT_EXCLUSIONS.extend(equipment_exclusions)
            PREPARATION_ANOMALIES.extend(equipment_anomalies)

            # Dataset d'import du « Profil matériel » (R10) :
            # profil du bundle de chaque employé, profil du
            # portable pour le matériel non affecté.
            profile_anomalies = export_equipment_profiles(df)
            PREPARATION_ANOMALIES.extend(profile_anomalies)

        # Préparation du plan d'intégration pour le plan
        # d'activités Odoo : colonnes sources conservées,
        # colonnes Odoo ajoutées dans le même CSV.
        if sheet_name == PLAN_SHEET:
            df, plan_anomalies = prepare_plan_integration(df)
            PREPARATION_ANOMALIES.extend(plan_anomalies)

        # Dataset d'import Odoo des demandes IT historiques
        # (module wd_it_request) : validations référentielles
        # et mapping des états/priorités/canaux.
        if sheet_name == IT_REQUESTS_SHEET:
            request_anomalies = prepare_it_requests(
                df, workbook["Employes"]
            )
            PREPARATION_ANOMALIES.extend(request_anomalies)

        # Dataset d'import Odoo des demandes d'absence
        # (hr.leave) : External IDs, mapping Statut ->
        # sélection Odoo, colonnes sources conservées.
        if sheet_name == LEAVE_ABSENCES_SHEET:
            absence_anomalies = prepare_leave_absences(
                df, workbook["Employes"]
            )
            PREPARATION_ANOMALIES.extend(absence_anomalies)

        output = PROCESSED_DIR / f"{sheet_name}.csv"

        df.to_csv(
            output,
            index=False,
            encoding="utf-8-sig"
        )

        print(
            f"{sheet_name} : {len(df)} lignes "
            f"exportées vers {output.name}"
        )

        # Second import Odoo : rattachement hiérarchique
        # via les External IDs, après création des employés.
        if sheet_name == "Employes":
            export_manager_hierarchy(df)

            # Dataset des contrats (données source brutes).
            contract_anomalies = export_contracts(
                workbook["Employes"]
            )
            PREPARATION_ANOMALIES.extend(contract_anomalies)

        # Référentiel des catégories de matériel pour Odoo.
        if sheet_name == "Materiel_IT":
            export_equipment_categories(df)


def prepare_equipment(df, employes):
    """
    Prépare la feuille Materiel_IT pour l'import Odoo.

    Règles appliquées :
    - exclusion des lignes dont AffecteA ne correspond à aucun
      employé existant ;
    - déduplication des NumSerie : une seule ligne fiable
      (affectation valide) est conservée ; en cas d'ambiguïté
      ou d'absence de ligne fiable, toutes les lignes du groupe
      sont exclues ;
    - ajout de la colonne Nom = Marque + modèle complet.

    Retourne (df, exclusions, anomalies).
    """

    employe_ids = set(employes["EmployeeID"].dropna())
    df = df.copy()
    exclusions = []
    anomalies = []
    excluded_indexes = set()

    def has_assignment(row):
        value = row.get("AffecteA")
        return pd.notna(value) and str(value).strip() != ""

    def is_valid_assignment(row):
        return (
            has_assignment(row)
            and str(row["AffecteA"]).strip() in employe_ids
        )

    def exclude(index, colonne, valeur, motif):
        excluded_indexes.add(index)
        exclusions.append({
            "Feuille": "Materiel_IT",
            "Ligne_Excel": index + 2,
            "AssetID": df.at[index, "AssetID"],
            "Categorie": df.at[index, "Categorie"],
            "Motif_Exclusion": motif,
        })
        anomalies.append({
            "Feuille": "Materiel_IT",
            "Ligne_Excel": index + 2,
            "Identifiant": df.at[index, "AssetID"],
            "Colonne": colonne,
            "Valeur": valeur,
            "Anomalie": "EXCLUSION_MATERIEL",
            "Gravite": "ERREUR",
            "Decision": "Exclu du CSV d'import",
            "Justification": motif,
        })

    # 1. Affectations vers des employés inexistants : exclusion.
    for index, row in df.iterrows():
        if has_assignment(row) and not is_valid_assignment(row):
            exclude(
                index,
                "AffecteA",
                f"{row['AssetID']} → {row['AffecteA']}",
                "Affectation vers un employé inexistant"
            )

    # 2. Doublons de numéro de série sur les lignes restantes.
    candidates = df[
        ~df.index.isin(excluded_indexes)
        & df["NumSerie"].notna()
        & (df["NumSerie"].astype(str).str.strip() != "")
    ]
    doublons = candidates[candidates["NumSerie"].duplicated(keep=False)]

    for _, groupe in doublons.groupby("NumSerie"):
        fiables = [
            index for index, row in groupe.iterrows()
            if is_valid_assignment(row)
        ]

        if len(fiables) == 1:
            for index in groupe.index:
                if index != fiables[0]:
                    exclude(
                        index,
                        "NumSerie",
                        groupe.at[index, "NumSerie"],
                        "Numéro de série en doublon ; "
                        "un autre exemplaire est retenu"
                    )
        else:
            motif = (
                "Numéro de série en doublon ; plusieurs lignes "
                "fiables, choix non déterministe"
                if len(fiables) > 1
                else "Numéro de série en doublon ; "
                     "aucune ligne fiable"
            )
            for index in groupe.index:
                exclude(index, "NumSerie", groupe.at[index, "NumSerie"], motif)

    # 3. Ajout du nom de l'équipement sur les lignes conservées.
    cleaned_df = df[~df.index.isin(excluded_indexes)].copy()

    marque = cleaned_df["Marque"].fillna("").astype(str).str.strip()
    modele = cleaned_df["Modele"].fillna("").astype(str).str.strip()

    cleaned_df.insert(
        cleaned_df.columns.get_loc("NumSerie"),
        "Nom",
        (marque + " " + modele).str.strip()
    )

    return cleaned_df, exclusions, anomalies


def _parse_contract_date(value):
    """Parse une date de contrat (jj/mm/aaaa prioritaire)."""

    if pd.isna(value):
        return None
    parsed = pd.to_datetime(value, dayfirst=True, errors="coerce")
    return None if pd.isna(parsed) else parsed.date()


def _contract_type_id(type_contrat):
    """External ID déterministe pour un type de contrat."""

    code = "".join(
        ch for ch in str(type_contrat).upper() if ch.isalnum()
    )
    return f"__import__.TYPE_{code}"


def export_contracts(employes):
    """
    Prépare le dataset des contrats des employés existants
    pour l'import Odoo (hr.contract).

    Produit :
    - Employes_Contrats.csv : une ligne par employé, avec
      Employee / External ID, Contract Type / External ID,
      Name, Start Date, End Date, Wage (= 0 par défaut,
      salaire renseigné manuellement à l'import) et colonnes
      de traçabilité ;
    - Types_Contrats.csv : référentiel des types distincts
      (External ID + Name).

    Règles :
    - Start Date = DateEntree ;
    - End Date = DateFinContrat si renseignée ; pour un
      employé Sorti, DateSortie est utilisée et signalée ;
    - CDI actif sans date de fin : normal, pas d'anomalie ;
    - CDD/Stage sans DateFinContrat : anomalie AVERTISSEMENT ;
    - doublon (même nom + date d'entrée) : seule la première
      occurrence est conservée, les suivantes exclues (ERREUR) ;
    - Wage = 0 (valeur par défaut, salaire renseigné
      manuellement à l'import).

    Retourne la liste des anomalies de préparation.
    """

    anomalies = []
    rows = []
    type_names = {}
    seen_identities = set()

    fixed_term_types = {"CDD", "Stage"}

    for index, row in employes.iterrows():
        employee_id = str(row.get("EmployeeID", "")).strip()
        if not employee_id:
            continue

        type_contrat = str(row.get("TypeContrat", "")).strip()
        line = index + 2

        def anomaly(colonne, valeur, problem, decision, reason,
                    gravite="AVERTISSEMENT"):
            anomalies.append({
                "Feuille": "Employes",
                "Ligne_Excel": line,
                "Identifiant": employee_id,
                "Colonne": colonne,
                "Valeur": valeur,
                "Anomalie": problem,
                "Gravite": gravite,
                "Decision": decision,
                "Justification": reason,
            })

        if not type_contrat:
            anomaly(
                "TypeContrat", "",
                "TYPE_CONTRAT_MANQUANT",
                "Exclu du dataset contrats",
                "Aucun type de contrat dans la source ; "
                "aucune valeur inventée.",
                gravite="ERREUR",
            )
            continue

        date_debut = _parse_contract_date(row.get("DateEntree"))
        if date_debut is None:
            anomaly(
                "DateEntree", str(row.get("DateEntree", "")),
                "DATE_ENTREE_INVALIDE",
                "Start Date vide",
                "Date d'entrée illisible ; à corriger à la source.",
                gravite="ERREUR",
            )

        raw_fin = row.get("DateFinContrat")
        date_fin = _parse_contract_date(raw_fin)
        date_sortie = _parse_contract_date(row.get("DateSortie"))
        statut = str(row.get("Statut", "")).strip().lower()

        observation = ""
        if date_fin is None and statut == "sorti" and date_sortie:
            date_fin = date_sortie
            observation = (
                "DateSortie utilisée comme date de fin de contrat."
            )
            anomaly(
                "DateFinContrat", "",
                "FIN_CONTRAT_DEDUITE_SORTIE",
                "End Date = DateSortie (confirmé)",
                "Employé sorti sans DateFinContrat : la date de "
                "sortie est retenue comme fin de contrat "
                "(confirmé : DateSortie fait office de fin).",
                gravite="INFORMATION",
            )
        elif (
            date_fin is None
            and type_contrat in fixed_term_types
        ):
            anomaly(
                "DateFinContrat", "",
                "FIN_CONTRAT_ABSENTE",
                "End Date vide",
                f"Contrat {type_contrat} sans date de fin : "
                "cohérence à vérifier.",
            )

        raw_entree = row.get("DateEntree")
        if isinstance(raw_entree, str) and "/" in raw_entree:
            anomaly(
                "DateEntree", raw_entree,
                "DATE_TEXTE_AMBIGUE",
                "Interprété jj/mm/aaaa",
                "Date stockée en texte ; format jj/mm/aaaa "
                "supposé (convention française).",
                gravite="INFORMATION",
            )

        type_names[type_contrat] = _contract_type_id(type_contrat)

        prenom = str(row.get("Prenom", "")).strip()
        nom = str(row.get("Nom", "")).strip()

        # Doublon (même nom + date d'entrée) : seule la première
        # occurrence est conservée ; les suivantes sont exclues —
        # l'identifiant supplémentaire est présumé inexistant
        # (erreur de saisie possible).
        dup_key = (
            f"{prenom} {nom}".strip(),
            date_debut.isoformat() if date_debut else "",
        )
        if dup_key in seen_identities:
            anomaly(
                "EmployeeID", employee_id,
                "DOUBLON_EXCLU_IMPORT",
                "Exclu du dataset contrats",
                "Même nom et même date d'entrée qu'un autre "
                "employé : l'identifiant est présumé "
                "inexistant (erreur de saisie).",
                gravite="ERREUR",
            )
            continue
        seen_identities.add(dup_key)

        rows.append({
            "External ID": f"__import__.CTR-{employee_id}",
            "Employee / External ID": f"__import__.{employee_id}",
            "Contract Type / External ID": _contract_type_id(
                type_contrat
            ),
            "Name": f"{type_contrat} - {prenom} {nom}".strip(),
            "Start Date": (
                date_debut.isoformat() if date_debut else ""
            ),
            "End Date": (
                date_fin.isoformat() if date_fin else ""
            ),
            "Wage": "0",
            "EmployeeID": employee_id,
            "TypeContrat": type_contrat,
            "DateEntree": str(row.get("DateEntree", "")),
            "DateFinContrat": (
                "" if pd.isna(raw_fin) else str(raw_fin)
            ),
            "DateSortie": (
                "" if pd.isna(row.get("DateSortie"))
                else str(row.get("DateSortie"))
            ),
            "Observation": observation,
        })

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    contracts_path = PROCESSED_DIR / "Employes_Contrats.csv"
    pd.DataFrame(rows).to_csv(
        contracts_path, index=False, encoding="utf-8-sig"
    )

    types_path = PROCESSED_DIR / "Types_Contrats.csv"
    pd.DataFrame([
        {
            "External ID": type_id,
            "Name": name,
        }
        for name, type_id in sorted(type_names.items())
    ]).to_csv(types_path, index=False, encoding="utf-8-sig")

    print(
        f"Employes_Contrats : {len(rows)} contrat(s) exporté(s) "
        f"vers {contracts_path.name} ; "
        f"{len(type_names)} type(s) vers {types_path.name}"
    )

    return anomalies


def export_manager_hierarchy(df):
    """
    Exporte les références ManagerID sous forme d'External IDs
    Odoo pour le second import de hiérarchie.
    Les employés sans manager valide sont exclus.
    """

    managers = df[
        df["ManagerID"].notna()
        & (df["ManagerID"].astype(str).str.strip() != "")
    ]

    result = pd.DataFrame({
        "External ID": "__import__." + managers["EmployeeID"].astype(str),
        "Manager / External ID": (
            "__import__." + managers["ManagerID"].astype(str)
        ),
    })

    output = PROCESSED_DIR / "Employes_Managers.csv"

    result.to_csv(output, index=False, encoding="utf-8-sig")

    print(
        f"Employes_Managers : {len(result)} lignes "
        f"exportées vers {output.name}"
    )


def export_equipment_categories(df):
    """
    Exporte le référentiel des catégories de matériel
    distinctes, trié alphabétiquement (accents ignorés).
    """

    categories = (
        df["Categorie"]
        .dropna()
        .astype(str)
        .str.strip()
        .replace("", pd.NA)
        .dropna()
        .unique()
    )
    import unicodedata

    def sort_key(value):
        return "".join(
            char for char in unicodedata.normalize("NFD", value)
            if unicodedata.category(char) != "Mn"
        ).lower()

    categories = sorted(categories, key=sort_key)

    result = pd.DataFrame({"Categorie": categories})

    output = PROCESSED_DIR / "Materiel_IT_Categories.csv"

    result.to_csv(output, index=False, encoding="utf-8-sig")

    print(
        f"Materiel_IT_Categories : {len(result)} catégories "
        f"exportées vers {output.name}"
    )


def prepare_plan_integration(df):
    """
    Prépare la feuille Plan_Integration pour le plan
    d'activités natif Odoo.

    Pour chaque tâche source, conserve les colonnes
    d'origine (TacheID, Tache, Responsable, Echeance,
    ApplicationOdoo) et ajoute les colonnes Odoo :
    TypeActivite, Assignation, Intervalle, Unite, Declencher.
    Le responsable « Manager + RH » est éclaté en deux
    activités (Manager et RH) ; TacheID est conservé à
    l'identique pour retrouver la tâche d'origine.

    Une tâche dont l'échéance ou le responsable est absent
    ou non reconnu ne peut pas produire d'activité fiable :
    elle est exclue du CSV Odoo et consignée en anomalie
    (ERREUR). Aucune valeur n'est inventée.

    Retourne (df_odoo, anomalies).
    """

    anomalies = []
    required = ["TacheID", "Tache", "Responsable", "Echeance"]
    missing_cols = [c for c in required if c not in df.columns]
    if missing_cols:
        anomalies.append({
            "Feuille": PLAN_SHEET,
            "Ligne_Excel": "",
            "Identifiant": "",
            "Colonne": ", ".join(missing_cols),
            "Valeur": "",
            "Anomalie": "COLONNES_PLAN_ABSENTES",
            "Gravite": "ERREUR",
            "Decision": "Feuille non exploitable",
            "Justification": (
                "Colonnes requises absentes : "
                f"{', '.join(missing_cols)}."
            ),
        })
        return pd.DataFrame(), anomalies

    def text(value):
        return "" if pd.isna(value) else str(value).strip()

    rows = []

    for index, row in df.iterrows():
        line = index + 2
        tache_id = text(row["TacheID"])
        tache = text(row["Tache"])
        responsable = text(row["Responsable"])
        echeance = text(row["Echeance"])
        application = text(row.get("ApplicationOdoo"))

        def anomaly(colonne, valeur, motif):
            anomalies.append({
                "Feuille": PLAN_SHEET,
                "Ligne_Excel": line,
                "Identifiant": tache_id,
                "Colonne": colonne,
                "Valeur": valeur,
                "Anomalie": "PLAN_INTEGRATION_INVALIDE",
                "Gravite": "ERREUR",
                "Decision": "Exclu du CSV d'import Odoo",
                "Justification": motif,
            })

        if not tache_id or not tache or not responsable or not echeance:
            vide = [
                name for name, value in [
                    ("TacheID", tache_id), ("Tache", tache),
                    ("Responsable", responsable),
                    ("Echeance", echeance),
                ]
                if not value
            ]
            anomaly(
                ", ".join(vide), "",
                f"Champ(s) obligatoire(s) vide(s) : {', '.join(vide)}."
            )
            continue

        if echeance not in PLAN_ECHEANCES:
            anomaly(
                "Echeance", echeance,
                f"Échéance non reconnue : {echeance}. "
                "Aucune conversion inventée."
            )
            continue

        if responsable == "Manager + RH":
            responsables = ["Manager", "RH"]
        elif responsable in PLAN_RESPONSABLES:
            responsables = [responsable]
        else:
            anomaly(
                "Responsable", responsable,
                f"Responsable non reconnu : {responsable}."
            )
            continue

        intervalle, unite, declencher = PLAN_ECHEANCES[echeance]

        for resp in responsables:
            rows.append({
                "TacheID": tache_id,
                "Tache": tache,
                "Responsable": resp,
                "Echeance": echeance,
                "ApplicationOdoo": application,
                "TypeActivite": PLAN_TYPE_ACTIVITE,
                "Assignation": PLAN_RESPONSABLES[resp],
                "Intervalle": intervalle,
                "Unite": unite,
                "Declencher": declencher,
            })

    return pd.DataFrame(rows), anomalies


def prepare_it_requests(df, employes):
    """
    Prépare Demandes_IT_Historique pour l'import Odoo
    (module wd_it_request).

    Valide les référentiels (employé demandeur, technicien,
    catégorie, canal, statut, priorité), la cohérence des
    dates et produit data/processed/Demandes_IT_Import.csv
    avec les colonnes Odoo. Les lignes bloquantes sont
    exclues et consignées en anomalie ; les incohérences
    mineures sont signalées sans correction silencieuse.

    Retourne la liste des anomalies.
    """

    anomalies = []
    required = [
        "DemandeID", "DateCreation", "DemandeurID", "Categorie",
        "Objet", "Priorite", "Canal", "Statut",
    ]
    missing_cols = [c for c in required if c not in df.columns]
    if missing_cols:
        return [{
            "Feuille": IT_REQUESTS_SHEET,
            "Ligne_Excel": "",
            "Identifiant": "",
            "Colonne": ", ".join(missing_cols),
            "Valeur": "",
            "Anomalie": "COLONNES_DEMANDES_IT_ABSENTES",
            "Gravite": "ERREUR",
            "Decision": "Feuille non exploitable",
            "Justification": (
                "Colonnes requises absentes : "
                f"{', '.join(missing_cols)}."
            ),
        }]

    employee_ids = {
        str(v).strip()
        for v in employes["EmployeeID"].dropna()
    }

    def text(value):
        return "" if pd.isna(value) else str(value).strip()

    def parse_dt(value):
        value = text(value)
        if not value:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                    "%Y-%m-%d"):
            try:
                return pd.Timestamp(
                    pd.to_datetime(value, format=fmt)
                )
            except (ValueError, TypeError):
                continue
        return "INVALID"

    seen_ids = set()
    rows = []

    for index, row in df.iterrows():
        line = index + 2
        demande_id = text(row["DemandeID"])

        def anomaly(colonne, valeur, problem, decision, reason,
                    gravite="ERREUR"):
            anomalies.append({
                "Feuille": IT_REQUESTS_SHEET,
                "Ligne_Excel": line,
                "Identifiant": demande_id,
                "Colonne": colonne,
                "Valeur": valeur,
                "Anomalie": problem,
                "Gravite": gravite,
                "Decision": decision,
                "Justification": reason,
            })

        def reject(colonne, valeur, problem, reason):
            anomaly(colonne, valeur, problem,
                    "Exclu du dataset Demandes_IT_Import",
                    reason)

        if not demande_id:
            reject("DemandeID", "", "DEMANDE_ID_MANQUANT",
                   "Identifiant de demande absent.")
            continue
        if demande_id in seen_ids:
            reject("DemandeID", demande_id,
                   "DEMANDE_ID_DOUBLON",
                   "DemandeID déjà rencontré : doublon "
                   "d'identifiant.")
            continue
        seen_ids.add(demande_id)

        demandeur = text(row["DemandeurID"])
        if demandeur not in employee_ids:
            reject("DemandeurID", demandeur,
                   "DEMANDEUR_INCONNU",
                   "Aucun employé avec cet EmployeeID dans "
                   "la feuille Employes.")
            continue

        creation = parse_dt(row.get("DateCreation"))
        if creation in (None, "INVALID"):
            reject("DateCreation", text(row.get("DateCreation")),
                   "DATE_CREATION_INVALIDE",
                   "Date de création illisible ; aucune "
                   "conversion inventée.")
            continue

        categorie = text(row["Categorie"])
        if categorie not in IT_CATEGORY_MAP:
            reject("Categorie", categorie,
                   "CATEGORIE_INCONNUE",
                   f"Catégorie non mappée : {categorie}.")
            continue

        canal = text(row["Canal"])
        if canal not in IT_CHANNEL_MAP:
            reject("Canal", canal,
                   "CANAL_INCONNU",
                   f"Canal non mappé : {canal}.")
            continue

        statut = text(row["Statut"])
        if statut not in IT_STATE_MAP:
            reject("Statut", statut,
                   "STATUT_INCONNU",
                   f"Statut non mappé : {statut}.")
            continue

        priorite = text(row["Priorite"]) or "P3"
        if priorite not in IT_PRIORITIES:
            reject("Priorite", priorite,
                   "PRIORITE_INCONNUE",
                   f"Priorité hors P1-P4 : {priorite}.")
            continue

        reponse = parse_dt(row.get("DatePremiereReponse"))
        resolution = parse_dt(row.get("DateResolution"))
        for col, parsed in (
            ("DatePremiereReponse", reponse),
            ("DateResolution", resolution),
        ):
            if parsed == "INVALID":
                anomaly(
                    col, text(row.get(col)),
                    "DATE_INVALIDE",
                    "Conservé sans la date fautive",
                    "Date illisible : champ Odoo laissé vide.",
                    gravite="AVERTISSEMENT",
                )
        if reponse == "INVALID":
            reponse = None
        if resolution == "INVALID":
            resolution = None

        state = IT_STATE_MAP[statut]
        if state in ("resolved", "closed") and not resolution:
            anomaly(
                "DateResolution", "",
                "RESOLUTION_SANS_DATE",
                "Conservé tel quel",
                "Statut clos/résolu sans date de résolution "
                "dans la source.",
                gravite="AVERTISSEMENT",
            )
        if (
            resolution is not None
            and resolution < creation
        ):
            anomaly(
                "DateResolution", text(row.get("DateResolution")),
                "RESOLUTION_AVANT_CREATION",
                "Conservé tel quel",
                "Date de résolution antérieure à la création.",
                gravite="AVERTISSEMENT",
            )
        if (
            reponse is not None
            and resolution is not None
            and reponse > resolution
        ):
            anomaly(
                "DatePremiereReponse",
                text(row.get("DatePremiereReponse")),
                "REPONSE_APRES_RESOLUTION",
                "Conservé tel quel",
                "Première réponse postérieure à la résolution.",
                gravite="AVERTISSEMENT",
            )

        tech = text(row.get("TechnicienID"))
        if tech and tech not in employee_ids:
            anomaly(
                "TechnicienID", tech,
                "TECHNICIEN_INCONNU",
                "Technicien conservé en texte",
                "Aucun employé avec cet EmployeeID : le "
                "technicien ne sera pas lié à un compte.",
                gravite="AVERTISSEMENT",
            )

        reouverte = text(row.get("Reouverte")).lower()
        if reouverte not in ("oui", "non", ""):
            anomaly(
                "Reouverte", text(row.get("Reouverte")),
                "REOUVERTURE_VALEUR_INCONNUE",
                "Traité comme Non",
                "Valeur non Oui/Non : reopen_count = 0.",
                gravite="AVERTISSEMENT",
            )

        try:
            wait_hours = float(
                text(row.get("HeuresAttenteUtilisateur")) or 0
            )
        except ValueError:
            wait_hours = 0.0
            anomaly(
                "HeuresAttenteUtilisateur",
                text(row.get("HeuresAttenteUtilisateur")),
                "ATTENTE_NON_NUMERIQUE",
                "Secondes d'attente = 0",
                "Valeur non numérique : attente nulle.",
                gravite="AVERTISSEMENT",
            )
        if wait_hours < 0:
            wait_hours = 0.0
            anomaly(
                "HeuresAttenteUtilisateur",
                text(row.get("HeuresAttenteUtilisateur")),
                "ATTENTE_NEGATIVE",
                "Secondes d'attente = 0",
                "Heures d'attente négatives : ramenées à 0.",
                gravite="AVERTISSEMENT",
            )

        objet = text(row["Objet"])
        if not objet:
            reject("Objet", "", "OBJET_MANQUANT",
                   "Objet de la demande absent.")
            continue

        description = " — ".join([
            f"Import historique {demande_id}",
            f"Technicien: {tech}" if tech else "",
            f"Canal source: {canal}",
        ])

        rows.append({
            "request_ref": demande_id,
            "name": objet,
            "employee_code": demandeur,
            "technician_code": tech,
            "priority": priorite,
            "category": IT_CATEGORY_MAP[categorie],
            "channel": IT_CHANNEL_MAP[canal],
            "state": state,
            "request_date": creation.date().isoformat(),
            "create_date": creation.strftime("%Y-%m-%d %H:%M:%S"),
            "date_first_response": (
                reponse.strftime("%Y-%m-%d %H:%M:%S")
                if reponse is not None else ""
            ),
            "date_resolved": (
                resolution.strftime("%Y-%m-%d %H:%M:%S")
                if resolution is not None else ""
            ),
            "waiting_seconds": int(wait_hours * 3600),
            "reopen_count": 1 if reouverte == "oui" else 0,
            "description": description,
            # Colonnes sources conservées pour traçabilité.
            "Categorie_source": categorie,
            "Canal_source": canal,
            "Statut_source": statut,
            "Objet": objet,
        })

    import_path = PROCESSED_DIR / IT_REQUESTS_IMPORT_FILE
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(
        import_path, index=False, encoding="utf-8-sig"
    )
    print(
        f"{IT_REQUESTS_SHEET} : {len(rows)} lignes "
        f"préparées vers {import_path.name} "
        f"({len(anomalies)} anomalies)"
    )
    return anomalies


def prepare_leave_absences(df, employes):
    """
    Prépare Demandes_Absence pour l'import Odoo hr.leave.

    Produit data/processed/Demandes_Absence_Import.csv avec :
    - External ID de la demande (__import__. + DemandeID) ;
    - Employee / External ID (__import__. + EmployeeID) ;
    - Time Off Type / External ID (__import__. + TypeConge) ;
    - Request Start/End Date ;
    - Status mappé sur les libellés de sélection Odoo
      (Approved / To Approve / Refused ; Brouillon laissé
      vide, hr.leave n'a pas d'état draft) ;
    - Description (Commentaire + contexte justificatif/
      validateurs) ;
    - colonnes sources conservées non mappées
      (NbJoursDeclares, DateDepot, Justificatif,
      ValidateurManager, ValidateurRH).

    Les lignes bloquantes (employé/type/statut/date
    inconnus) sont exclues et consignées en anomalie.

    Retourne la liste des anomalies.
    """

    anomalies = []
    required = [
        "DemandeID", "EmployeeID", "TypeConge", "DateDebut",
        "DateFin", "Statut",
    ]
    missing_cols = [c for c in required if c not in df.columns]
    if missing_cols:
        return [{
            "Feuille": LEAVE_ABSENCES_SHEET,
            "Ligne_Excel": "",
            "Identifiant": "",
            "Colonne": ", ".join(missing_cols),
            "Valeur": "",
            "Anomalie": "COLONNES_ABSENCES_ABSENTES",
            "Gravite": "ERREUR",
            "Decision": "Feuille non exploitable",
            "Justification": (
                "Colonnes requises absentes : "
                f"{', '.join(missing_cols)}."
            ),
        }]

    employee_ids = {
        str(v).strip()
        for v in employes["EmployeeID"].dropna()
    }
    interns = intern_employee_ids(employes)

    def text(value):
        return "" if pd.isna(value) else str(value).strip()

    def parse_date(value):
        value = text(value)
        if not value:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return pd.Timestamp(
                    pd.to_datetime(value, format=fmt)
                ).date().isoformat()
            except (ValueError, TypeError):
                continue
        return "INVALID"

    seen_ids = set()
    rows = []

    for index, row in df.iterrows():
        line = index + 2
        demande_id = text(row["DemandeID"])

        def anomaly(colonne, valeur, problem, decision, reason,
                    gravite="ERREUR"):
            anomalies.append({
                "Feuille": LEAVE_ABSENCES_SHEET,
                "Ligne_Excel": line,
                "Identifiant": demande_id,
                "Colonne": colonne,
                "Valeur": valeur,
                "Anomalie": problem,
                "Gravite": gravite,
                "Decision": decision,
                "Justification": reason,
            })

        def reject(colonne, valeur, problem, reason):
            anomaly(colonne, valeur, problem,
                    "Exclu du dataset Demandes_Absence_Import",
                    reason)

        if not demande_id:
            reject("DemandeID", "", "DEMANDE_ID_MANQUANT",
                   "Identifiant de demande absent.")
            continue
        if demande_id in seen_ids:
            reject("DemandeID", demande_id,
                   "DEMANDE_ID_DOUBLON",
                   "DemandeID déjà rencontré.")
            continue
        seen_ids.add(demande_id)

        employee = text(row["EmployeeID"])
        if employee not in employee_ids:
            reject("EmployeeID", employee,
                   "EMPLOYE_INCONNU",
                   "Aucun employé avec cet EmployeeID.")
            continue

        type_conge = text(row["TypeConge"])
        if type_conge not in LEAVE_TYPES_KNOWN:
            reject("TypeConge", type_conge,
                   "TYPE_CONGE_INCONNU",
                   "Type de congé sans External ID Odoo.")
            continue

        # R6 : un stagiaire ne peut pas poser de congé payé.
        if type_conge == "CP" and employee in interns:
            reject("TypeConge", type_conge,
                   "STAGIAIRE_CP_INTERDIT",
                   "Congé payé exclu : l'employé est "
                   "stagiaire (R6). À requalifier en AA "
                   "si justifié.")
            continue

        statut = text(row["Statut"])
        if statut not in LEAVE_STATUT_MAP:
            reject("Statut", statut,
                   "STATUT_INCONNU",
                   f"Statut non mappé : {statut}.")
            continue

        debut = parse_date(row.get("DateDebut"))
        fin = parse_date(row.get("DateFin"))
        if debut in (None, "INVALID") or fin in (None, "INVALID"):
            reject("DateDebut/DateFin",
                   f"{text(row.get('DateDebut'))} -> "
                   f"{text(row.get('DateFin'))}",
                   "DATE_INVALIDE",
                   "Date de début ou de fin illisible.")
            continue
        if fin < debut:
            anomaly(
                "DateFin", fin,
                "FIN_AVANT_DEBUT",
                "Conservé tel quel",
                "Date de fin antérieure au début : "
                "à vérifier dans Odoo.",
                gravite="AVERTISSEMENT",
            )

        commentaire = text(row.get("Commentaire"))
        description_parts = []
        if commentaire:
            description_parts.append(commentaire)
        justificatif = text(row.get("Justificatif"))
        if justificatif and justificatif != "N.A.":
            description_parts.append(f"Justificatif: {justificatif}")
        validateurs = [
            v for v in (
                text(row.get("ValidateurManager")),
                text(row.get("ValidateurRH")),
            ) if v and v != "N.A."
        ]
        if validateurs:
            description_parts.append(
                "Validateur(s): " + ", ".join(validateurs)
            )
        description_parts.append(f"DemandeID: {demande_id}")

        rows.append({
            "External ID": f"__import__.{demande_id}",
            "Employee / External ID": f"__import__.{employee}",
            "Time Off Type / External ID": (
                f"__import__.{type_conge}"
            ),
            "Request Start Date": debut,
            "Request End Date": fin,
            "Status": LEAVE_STATUT_MAP[statut],
            "Description": " — ".join(description_parts),
            # Date de dépôt historique -> wd_original_request_date
            # (contrôle R2 + exemption justificatifs R4/R5).
            "Date de dépôt d'origine": text(
                row.get("DateDepot")
            )[:10],
            # Colonnes sources conservées (non mappées).
            "DemandeID": demande_id,
            "TypeConge": type_conge,
            "NbJoursDeclares": text(row.get("NbJoursDeclares")),
            "DateDepot": text(row.get("DateDepot")),
            "Justificatif": justificatif,
            "ValidateurManager": text(row.get("ValidateurManager")),
            "ValidateurRH": text(row.get("ValidateurRH")),
            "Statut_source": statut,
        })

    import_path = PROCESSED_DIR / LEAVE_ABSENCES_IMPORT_FILE
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(
        import_path, index=False, encoding="utf-8-sig"
    )
    print(
        f"{LEAVE_ABSENCES_SHEET} : {len(rows)} lignes "
        f"préparées vers {import_path.name} "
        f"({len(anomalies)} anomalies)"
    )
    return anomalies


def _equipment_profile_key(value):
    """Normalise un profil portable source vers sa clé."""

    if pd.isna(value):
        return ""
    key = str(value).strip().lower()
    return key if key in EQUIPMENT_PROFILE_LABELS else ""


def export_equipment_profiles(df):
    """
    Prépare le dataset d'import du « Profil matériel » (R10)
    pour maintenance.equipment.

    Règles (confirmées avec le métier) :
    - profil du bundle de l'employé : portable Dev -> Dev ;
      portable Standard seul -> Standard ; Standard + casque
      -> Support ; Standard + téléphone -> Commercial ;
      tous les équipements du bundle (portable + accessoires)
      reçoivent le profil du poste ;
    - matériel non affecté : le portable reprend son
      ProfilPortable (Dev/Standard), les accessoires non
      affectés restent sans profil ;
    - ambiguïté (ex. Standard + écran, Standard + casque +
      téléphone, employé sans portable) : ERREUR au journal,
      aucune valeur inventée.

    Produit Materiel_IT_Profils.csv : External ID
    (__import__. + AssetID) + « Profil matériel » (libellé de
    sélection, reconnu par l'import Odoo).

    Retourne la liste des anomalies de préparation.
    """

    anomalies = []
    rows = []

    def anomaly(index, row, colonne, valeur, motif,
                gravite="ERREUR"):
        anomalies.append({
            "Feuille": "Materiel_IT",
            "Ligne_Excel": index + 2,
            "Identifiant": row.get("AssetID", ""),
            "Colonne": colonne,
            "Valeur": valeur,
            "Anomalie": "PROFIL_MATERIEL_NON_DETERMINE",
            "Gravite": gravite,
            "Decision": "Exclu du CSV d'import",
            "Justification": motif,
        })

    grouped = {}
    for index, row in df.iterrows():
        affecte_a = row.get("AffecteA")
        key = "" if pd.isna(affecte_a) else str(affecte_a).strip()
        grouped.setdefault(key, []).append((index, row))

    def emit(index, row, profile_key):
        rows.append({
            "External ID": f"__import__.{row['AssetID']}",
            "Profil matériel": EQUIPMENT_PROFILE_LABELS[profile_key],
        })

    for employe, items in grouped.items():
        laptops = [
            (i, r) for i, r in items
            if r.get("Categorie") == LAPTOP_CATEGORY
        ]
        accessories = [
            (i, r) for i, r in items
            if r.get("Categorie") != LAPTOP_CATEGORY
        ]

        if not employe:
            # Matériel non affecté : profil du portable
            # uniquement ; les accessoires restent vides.
            for index, row in laptops:
                profile_key = _equipment_profile_key(
                    row.get("ProfilPortable")
                )
                if not profile_key:
                    anomaly(
                        index, row, "ProfilPortable",
                        row.get("ProfilPortable"),
                        "Portable non affecté sans profil "
                        "reconnu (attendu : Dev ou Standard)."
                    )
                    continue
                emit(index, row, profile_key)
            continue

        if not laptops:
            for index, row in accessories:
                anomaly(
                    index, row, "AffecteA", employe,
                    "Employé sans ordinateur portable : "
                    "profil du bundle indéterminable."
                )
            continue

        laptop_index, laptop = laptops[0]
        laptop_key = _equipment_profile_key(
            laptop.get("ProfilPortable")
        )
        if not laptop_key:
            anomaly(
                laptop_index, laptop, "ProfilPortable",
                laptop.get("ProfilPortable"),
                "Profil portable absent ou non reconnu ; "
                "profil du bundle indéterminable."
            )
            continue
        if len(laptops) > 1:
            for index, row in laptops[1:]:
                anomaly(
                    index, row, "AffecteA", employe,
                    "Employé avec plusieurs portables : "
                    "bundle ambigu, à qualifier manuellement.",
                    gravite="AVERTISSEMENT",
                )

        accessory_categories = {
            str(r.get("Categorie", "")).strip()
            for _, r in accessories
        }

        if laptop_key == "dev":
            profile_key = "dev"
            if accessory_categories - {"Écran"}:
                for index, row in accessories:
                    anomaly(
                        index, row, "Categorie",
                        row.get("Categorie"),
                        "Accessoire inattendu dans un bundle "
                        "Dev ; profil Dev conservé par "
                        "défaut, à vérifier.",
                        gravite="AVERTISSEMENT",
                    )
        else:
            has_phone = "Téléphone" in accessory_categories
            has_headset = "Casque" in accessory_categories
            other = accessory_categories - {"Téléphone", "Casque"}

            if other or (has_phone and has_headset):
                for index, row in items:
                    anomaly(
                        index, row, "AffecteA", employe,
                        "Composition du bundle ambiguë "
                        f"({sorted(accessory_categories)}) : "
                        "profil non déterminé automatiquement."
                    )
                continue
            if has_phone:
                profile_key = "commercial"
            elif has_headset:
                profile_key = "support"
            else:
                profile_key = "standard"

        for index, row in items:
            emit(index, row, profile_key)

    profiles = pd.DataFrame(rows)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    path = PROCESSED_DIR / EQUIPMENT_PROFILES_FILE
    profiles.to_csv(path, index=False, encoding="utf-8-sig")
    print(
        f"Materiel_IT_Profils : {len(profiles)} profil(s) "
        f"exporté(s) vers {EQUIPMENT_PROFILES_FILE}"
    )

    return anomalies


def export_excluded_equipment():
    """
    Exporte le rapport des équipements exclus du CSV d'import
    avec leur motif d'exclusion.
    """

    if not EQUIPMENT_EXCLUSIONS:
        return

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    output = REPORT_DIR / "materiel_exclu.csv"

    pd.DataFrame(EQUIPMENT_EXCLUSIONS).to_csv(
        output, index=False, encoding="utf-8-sig"
    )

    print(
        f"Rapport des équipements exclus : {len(EQUIPMENT_EXCLUSIONS)} "
        f"ligne(s) vers {output.name}"
    )


def remove_exact_duplicates(df, sheet_name):
    """
    Supprime uniquement les lignes strictement identiques.
    Retourne le DataFrame préparé et le nombre de doublons supprimés.
    """

    initial_count = len(df)

    cleaned_df = df.drop_duplicates().copy()

    removed_count = initial_count - len(cleaned_df)

    if removed_count > 0:
        print(
            f"{sheet_name} : {removed_count} doublon(s) exact(s) supprimé(s)."
        )

    return cleaned_df, removed_count


def normalize_text_columns(df):
    """
    Nettoie les colonnes textuelles :
    - supprime les espaces au début et à la fin ;
    - remplace les chaînes composées uniquement d'espaces par des valeurs vides ;
    - conserve les valeurs manquantes.
    """

    cleaned_df = df.copy()

    for column in cleaned_df.select_dtypes(include=["object", "str"]).columns:
        cleaned_df[column] = cleaned_df[column].apply(
            lambda value: value.strip()
            if isinstance(value, str)
            else value
        )

        cleaned_df[column] = cleaned_df[column].replace(
            r"^\s*$",
            pd.NA,
            regex=True
        )

    return cleaned_df


def validate_employee_emails(df):
    """Vérifie les adresses email professionnelles sans les inventer."""

    cleaned_df = df.copy()

    if "EmailPro" not in cleaned_df.columns:
        return cleaned_df

    for index, value in cleaned_df["EmailPro"].items():

        if pd.isna(value):
            continue

        email = str(value).strip().lower()

        try:
            validated = validate_email(
                email,
                check_deliverability=False
            )

            cleaned_df.at[index, "EmailPro"] = validated.normalized

        except EmailNotValidError as error:

            print(
                f"AVERTISSEMENT - Employé "
                f"{cleaned_df.at[index, 'EmployeeID']} : "
                f"email non valide ({email}) - {error}"
            )

    return cleaned_df


def normalize_employee_contacts(df):
    """
    Normalise les coordonnées des employés.

    EmailPro :
    - supprime les espaces ;
    - convertit en minuscules.

    Telephone :
    - interprète les numéros locaux avec la région TG ;
    - convertit le préfixe 00 en + ;
    - vérifie la validité ;
    - standardise les numéros valides au format international.
    - conserve les numéros invalides sans les modifier.
    """

    cleaned_df = df.copy()

    cleaned_df = validate_employee_emails(cleaned_df)

    if "Telephone" in cleaned_df.columns:

        for index, value in cleaned_df["Telephone"].items():

            if pd.isna(value):
                continue

            original_number = str(value).strip()

            if not original_number:
                continue

            # Conversion du préfixe international 00 en +
            if original_number.startswith("00"):
                number_to_parse = "+" + original_number[2:]
                region = None

            elif original_number.startswith("+"):
                number_to_parse = original_number
                region = None

            else:
                number_to_parse = original_number
                region = "TG"

            try:
                phone = phonenumbers.parse(
                    number_to_parse,
                    region
                )

                if phonenumbers.is_valid_number(phone):

                    cleaned_df.at[index, "Telephone"] = (
                        phonenumbers.format_number(
                            phone,
                            phonenumbers.PhoneNumberFormat.INTERNATIONAL
                        )
                    )

                else:
                    print(
                        f"AVERTISSEMENT - Employé "
                        f"{cleaned_df.at[index, 'EmployeeID']} : "
                        f"numéro non valide ({original_number})"
                    )

            except phonenumbers.NumberParseException as error:

                print(
                    f"ERREUR - Employé "
                    f"{cleaned_df.at[index, 'EmployeeID']} : "
                    f"{original_number} - {error}"
                )

    return cleaned_df


def normalize_key(value):
    """Normalise une clé de correspondance : minuscules, espaces unifiés."""

    if pd.isna(value):
        return ""

    return " ".join(str(value).split()).strip().lower()


def normalize_work_location(df):
    """
    Harmonise les noms de lieux de travail pour Odoo.
    Les variantes d'écriture sont ramenées aux noms
    exacts des lieux créés dans Odoo. Les valeurs
    inconnues sont conservées sans modification.
    """

    mapping = {
        "LOME": "Lomé",
        "LOMÉ": "Lomé",
        "COTONOU": "Cotonou",
    }

    if "Site" in df.columns:
        df["Site"] = df["Site"].apply(
            lambda value: mapping.get(
                str(value).strip().upper(),
                str(value).strip()
            )
            if pd.notna(value)
            else value
        )

    return df


def prepare_employees(df, postes):
    """
    Prépare la feuille Employes pour l'import Odoo.

    - ajoute la colonne Nom complet (Prenom + Nom) ;
    - convertit Genre en Male / Female ;
    - ajoute PosteID par correspondance avec Postes.Intitule.

    Retourne le DataFrame préparé et la liste des anomalies.
    """

    cleaned_df = df.copy()
    anomalies = []

    # Harmonisation des lieux de travail pour Odoo.
    cleaned_df = normalize_work_location(cleaned_df)

    # Doublons d'identité (même Prénom + Nom + date d'entrée) :
    # seule la première occurrence est conservée ; les suivantes
    # sont exclues — l'identifiant est présumé inexistant
    # (erreur de saisie possible).
    seen_identities = set()
    drop_indices = []

    for index, row in cleaned_df.iterrows():
        key = (
            str(row.get("Prenom", "")).strip().lower(),
            str(row.get("Nom", "")).strip().lower(),
            _parse_contract_date(row.get("DateEntree")),
        )
        if key in seen_identities:
            drop_indices.append(index)
            anomalies.append({
                "Feuille": "Employes",
                "Ligne_Excel": index + 2,
                "Identifiant": row.get("EmployeeID", ""),
                "Colonne": "EmployeeID",
                "Valeur": row.get("EmployeeID", ""),
                "Anomalie": "DOUBLON_EXCLU",
                "Gravite": "ERREUR",
                "Decision": "Exclu du CSV d'import",
                "Justification": (
                    "Même Prénom, Nom et date d'entrée qu'un "
                    "autre employé : identifiant présumé "
                    "inexistant (erreur de saisie)."
                ),
            })
        else:
            seen_identities.add(key)

    if drop_indices:
        cleaned_df = cleaned_df.drop(index=drop_indices)
        print(
            f"Employes : {len(drop_indices)} doublon(s) "
            "d'identité exclu(s)."
        )

    # Nom complet : Prénom + Nom, sans modifier les colonnes sources.
    cleaned_df["Nom complet"] = (
        cleaned_df["Prenom"].fillna("").astype(str).str.strip()
        + " "
        + cleaned_df["Nom"].fillna("").astype(str).str.strip()
    ).str.strip()

    # Genre : M -> Male, F -> Female.
    genre_map = {
        "m": "Male",
        "f": "Female",
    }

    def map_genre(value):
        if pd.isna(value):
            return value

        key = str(value).strip().lower()

        if key in genre_map:
            return genre_map[key]

        return value

    cleaned_df["Genre"] = cleaned_df["Genre"].apply(map_genre)

    # Correspondance des postes : Employes.Poste -> Postes.PosteID
    poste_map = {}

    for _, poste in postes.iterrows():
        key = normalize_key(poste["Intitule"])

        if key:
            poste_map[key] = poste["PosteID"]

    poste_ids = []

    for index, row in cleaned_df.iterrows():
        key = normalize_key(row["Poste"])
        poste_id = poste_map.get(key, "")

        poste_ids.append(poste_id)

        if key and not poste_id:
            anomalies.append({
                "Feuille": "Employes",
                "Ligne_Excel": index + 2,
                "Identifiant": row.get("EmployeeID", ""),
                "Colonne": "Poste",
                "Anomalie": "Poste sans correspondance",
                "Decision": "En attente de confirmation RH",
                "Justification": (
                    "Aucun PosteID trouvé pour l'intitulé : "
                    f"{row['Poste']}. Aucune correspondance inventée."
                )
            })

    cleaned_df["PosteID"] = poste_ids

    # Statut : Actif -> 1, Sorti -> 0 pour le champ Active d'Odoo.
    statut_map = {
        "actif": 1,
        "sorti": 0,
    }

    if "Statut" in cleaned_df.columns:
        statut_values = []

        for index, row in cleaned_df.iterrows():
            value = row["Statut"]

            if pd.isna(value):
                statut_values.append(value)
                continue

            key = str(value).strip().lower()

            if key in statut_map:
                statut_values.append(statut_map[key])
            else:
                statut_values.append(value)

                anomalies.append({
                    "Feuille": "Employes",
                    "Ligne_Excel": index + 2,
                    "Identifiant": row.get("EmployeeID", ""),
                    "Colonne": "Statut",
                    "Anomalie": "Statut non reconnu",
                    "Decision": "En attente de confirmation RH",
                    "Justification": (
                        "Valeur conservée sans modification : "
                        f"{value}. Aucune correspondance inventée."
                    )
                })

        cleaned_df["Statut"] = statut_values

    # Colonnes d'import Odoo pour les anciens salariés :
    # - Actif : 1/0 (champ hr.employee.active) ;
    # - Date de départ : hr.employee.departure_date (ISO).
    if "Statut" in cleaned_df.columns:
        cleaned_df["Actif"] = cleaned_df["Statut"].apply(
            lambda v: 1 if str(v).strip() == "1" else 0
            if not pd.isna(v) else ""
        )

    if "DateSortie" in cleaned_df.columns:
        def _to_iso_date(value):
            parsed = _parse_contract_date(value)
            if parsed is None or pd.isna(parsed):
                return ""
            return pd.Timestamp(parsed).strftime("%Y-%m-%d")

        cleaned_df["Date de départ"] = cleaned_df["DateSortie"].apply(
            _to_iso_date
        )

    # Genres non reconnus : valeur conservée, anomalie signalée.
    for index, row in cleaned_df.iterrows():
        genre = row["Genre"]

        if pd.isna(genre) or genre in ["Male", "Female"]:
            continue

        anomalies.append({
            "Feuille": "Employes",
            "Ligne_Excel": index + 2,
            "Identifiant": row.get("EmployeeID", ""),
            "Colonne": "Genre",
            "Anomalie": "Genre non reconnu",
            "Decision": "En attente de confirmation RH",
            "Justification": (
                "Valeur conservée sans modification : "
                f"{genre}. Aucune correspondance inventée."
            )
        })

    # Contrôle de référentialité : ManagerID doit référencer
    # un employé existant, DepartementID un département existant.
    employee_ids = set(cleaned_df["EmployeeID"].dropna())
    departement_ids = set(postes["DepartementID"].dropna())

    for index, row in cleaned_df.iterrows():
        for column, valid_ids in [
            ("ManagerID", employee_ids),
            ("DepartementID", departement_ids),
        ]:
            value = row.get(column)

            if pd.isna(value) or str(value).strip() == "":
                continue

            if str(value).strip() not in valid_ids:
                anomalies.append({
                    "Feuille": "Employes",
                    "Ligne_Excel": index + 2,
                    "Identifiant": row.get("EmployeeID", ""),
                    "Colonne": column,
                    "Anomalie": "RÉFÉRENCE_SANS_CORRESPONDANCE",
                    "Decision": "En attente de confirmation RH",
                    "Justification": (
                        f"La valeur {value} ne correspond à aucun "
                        "enregistrement existant."
                    )
                })

                # Un manager inexistant ne peut pas être importé :
                # la référence est vidée, la trace reste au journal.
                if column == "ManagerID":
                    cleaned_df.at[index, column] = pd.NA

    # Les colonnes Nom et Prenom ne sont plus nécessaires
    # une fois le nom complet construit.
    cleaned_df = cleaned_df.drop(columns=["Nom", "Prenom"])

    return cleaned_df, anomalies


# ============================================================
# 4. CLASSEUR CORRIGE : SOLDE A ALLOUER
# ============================================================

def export_leave_balance_workbook():
    """
    Écrit une copie corrigée du classeur source avec la colonne
    calculée Solde_a_allouer dans Soldes_Conges.

    La formule Excel =C+D-E est écrite pour chaque ligne de
    données : Report2025 + Acquis2026 - Pris2026. Le fichier
    source et les autres feuilles ne sont pas modifiés.
    """

    if LEAVE_BALANCE_SHEET not in pd.ExcelFile(SOURCE).sheet_names:
        print(
            f"Feuille {LEAVE_BALANCE_SHEET} absente : "
            "export du classeur corrigé ignoré."
        )
        return

    xlsx = load_xlsx(SOURCE)
    sheet = xlsx[LEAVE_BALANCE_SHEET]

    headers = {
        cell.column: cell.value
        for cell in sheet[1]
        if cell.value is not None
    }
    columns = {name: col for col, name in headers.items()}

    missing = [
        name for name in LEAVE_BALANCE_SOURCES
        if name not in columns
    ]
    if missing:
        print(
            f"Colonnes sources absentes ({', '.join(missing)}) : "
            "export du classeur corrigé ignoré."
        )
        xlsx.close()
        return

    if LEAVE_BALANCE_COLUMN in headers.values():
        print(
            f"Colonne {LEAVE_BALANCE_COLUMN} déjà présente : "
            "classeur corrigé régénéré à l'identique."
        )

    report_col = columns["Report2025_jours"]
    acquis_col = columns["Acquis2026_au_31_10_jours"]
    pris_col = columns["Pris2026_au_31_10_jours"]
    balance_col = max(columns.values()) + 1

    target = sheet.cell(row=1, column=balance_col)
    target.value = LEAVE_BALANCE_COLUMN

    for row in range(2, sheet.max_row + 1):
        sheet.cell(
            row=row,
            column=balance_col,
            value=(
                f"={get_column_letter(report_col)}{row}"
                f"+{get_column_letter(acquis_col)}{row}"
                f"-{get_column_letter(pris_col)}{row}"
            ),
        )

    xlsx.save(CORRECTED_WORKBOOK)
    xlsx.close()

    rows = sheet.max_row - 1
    print(
        f"Classeur corrigé enregistré : {CORRECTED_WORKBOOK} "
        f"({rows} ligne(s), colonne {LEAVE_BALANCE_COLUMN})"
    )


def export_leave_allocations(workbook):
    """
    Exporte les allocations de congés pour Odoo
    (hr.leave.allocation) à partir de Soldes_Conges.

    Colonnes Odoo : Employee / External ID, Holiday Status /
    External ID (référentiel Types_Conges_Import.csv),
    Solde_a_allouer (Nombre de jours). Les colonnes de calcul
    restent en
    traçabilité et ne sont pas importées comme allocations
    séparées. Les anomalies sont consignées au journal.
    """

    if LEAVE_BALANCE_SHEET not in workbook:
        return

    balances = workbook[LEAVE_BALANCE_SHEET]
    if not all(
        column in balances.columns
        for column in LEAVE_BALANCE_SOURCES
    ):
        return

    # R6 : les stagiaires n'acquièrent pas de congé payé —
    # leurs lignes CP sont exclues du CSV d'import.
    interns = (
        intern_employee_ids(workbook["Employes"])
        if "Employes" in workbook else set()
    )

    employees_file = PROCESSED_DIR / "Employes.csv"
    valid_ids = set()
    if employees_file.exists():
        valid_ids = set(
            pd.read_csv(employees_file)["EmployeeID"].astype(str)
        )

    type_labels = {}
    if "Types_Conges" in workbook:
        types = workbook["Types_Conges"]
        type_labels = dict(
            zip(types["Code"].astype(str), types["Libelle"])
        )

        # Référentiel Odoo des types de congés : External ID
        # = __import__. + code source (format existant dans
        # Odoo), le libellé sert de nom.
        referential = pd.DataFrame({
            "External ID": [
                leave_type_external_id(code)
                for code in types["Code"].astype(str)
            ],
            "Name": types["Libelle"],
            "Code": types["Code"].astype(str),
        })
        referential.to_csv(
            PROCESSED_DIR / LEAVE_TYPES_FILE,
            index=False,
            encoding="utf-8-sig",
        )

    solde = leave_balance(balances)
    rows = []

    for index, row in balances.iterrows():
        employee_id = str(row.get("EmployeeID", ""))
        type_code = str(row.get("TypeConge", ""))
        line = index + 2
        value = solde.at[index]

        employee_ref = ""
        if employee_id in valid_ids:
            employee_ref = f"__import__.{employee_id}"
        else:
            PREPARATION_ANOMALIES.append({
                "Feuille": LEAVE_BALANCE_SHEET,
                "Ligne_Excel": str(line),
                "Identifiant": employee_id,
                "Colonne": "EmployeeID",
                "Valeur": employee_id,
                "Anomalie": "SOLDE_SANS_EMPLOYE",
                "Gravite": "ERREUR",
                "Decision": "Ligne exclue du CSV d'import",
                "Justification": (
                    "Employé absent du dataset Employes préparé."
                ),
            })
            continue

        holiday_status_id = ""
        if type_code in type_labels:
            holiday_status_id = leave_type_external_id(type_code)
        else:
            PREPARATION_ANOMALIES.append({
                "Feuille": LEAVE_BALANCE_SHEET,
                "Ligne_Excel": str(line),
                "Identifiant": employee_id,
                "Colonne": "TypeConge",
                "Valeur": type_code,
                "Anomalie": "TYPE_CONGE_INCONNU",
                "Gravite": "ERREUR",
                "Decision": "Type de congé non résolu",
                "Justification": (
                    "Code absent de Types_Conges : "
                    "Holiday Status laissé vide."
                ),
            })

        # R6 : un stagiaire n'acquiert pas de congé payé.
        if type_code == "CP" and employee_id in interns:
            PREPARATION_ANOMALIES.append({
                "Feuille": LEAVE_BALANCE_SHEET,
                "Ligne_Excel": str(line),
                "Identifiant": employee_id,
                "Colonne": "TypeConge",
                "Valeur": type_code,
                "Anomalie": "STAGIAIRE_CP_INTERDIT",
                "Gravite": "ERREUR",
                "Decision": "Allocation CP exclue du CSV",
                "Justification": (
                    "Employé stagiaire : pas d'acquisition "
                    "de congé payé (R6)."
                ),
            })
            continue

        # Un solde négatif n'est jamais importé : il masque
        # un dépassement ou une erreur RH. La ligne est
        # exclue et consignée, la valeur reste calculée
        # dans le classeur corrigé.
        if pd.notna(value) and value < 0:
            PREPARATION_ANOMALIES.append({
                "Feuille": LEAVE_BALANCE_SHEET,
                "Ligne_Excel": str(line),
                "Identifiant": employee_id,
                "Colonne": LEAVE_BALANCE_COLUMN,
                "Valeur": str(value),
                "Anomalie": "SOLDE_NEGATIF_EXCLU_IMPORT",
                "Gravite": "ERREUR",
                "Decision": "En attente d'arbitrage RH",
                "Justification": (
                    "Solde à allouer négatif : allocation "
                    "exclue du CSV d'import."
                ),
            })
            continue

        rows.append({
            "External ID": f"__import__.ALLOC-{employee_id}-{type_code}",
            "Employee / External ID": employee_ref,
            "Holiday Status / External ID": holiday_status_id,
            "Solde_a_allouer": value,
            "EmployeeID": employee_id,
            "TypeConge": type_code,
            "Report2025_jours": row.get("Report2025_jours"),
            "Acquis2026_au_31_10_jours": row.get(
                "Acquis2026_au_31_10_jours"
            ),
            "Pris2026_au_31_10_jours": row.get(
                "Pris2026_au_31_10_jours"
            ),
        })

    allocations = pd.DataFrame(rows)
    path = PROCESSED_DIR / ALLOCATIONS_FILE
    allocations.to_csv(path, index=False, encoding="utf-8-sig")
    print(
        f"Allocations_Conges : {len(allocations)} allocation(s) "
        f"exportée(s) vers {ALLOCATIONS_FILE}"
    )


# ============================================================
# 5. JOURNAL DES ANOMALIES
# ============================================================

def generate_anomaly_journal():
    report_path = REPORT_DIR / "rapport_validation.csv"
    journal_path = REPORT_DIR / "journal_anomalies.csv"

    if not report_path.exists():
        print("Rapport de validation introuvable. Lance d'abord validate.py.")
        return

    rapport = pd.read_csv(report_path, dtype=str).fillna("")
    workbook = load_workbook()
    anomalies = []

    def get_identifier(sheet_name, excel_row):
        if sheet_name not in workbook:
            return ""

        df = workbook[sheet_name]
        try:
            index = int(excel_row) - 2
            if index < 0 or index >= len(df):
                return ""
            row = df.iloc[index]

            for column in [
                "EmployeeID", "DemandeID", "MaterielID", "AssetID"
            ]:
                if column in df.columns and pd.notna(row[column]):
                    return str(row[column])
        except (ValueError, IndexError):
            pass

        return ""

    def add_anomaly(sheet, line, identifier, problem, decision, reason,
                    column="", valeur="", gravite="AVERTISSEMENT"):
        anomalies.append({
            "Feuille": sheet,
            "Ligne_Excel": line,
            "Identifiant": identifier,
            "Colonne": column,
            "Valeur": valeur,
            "Anomalie": problem,
            "Gravite": gravite,
            "Decision": decision,
            "Justification": reason
        })

    # 1. Récupération des anomalies du rapport de validation
    warnings = rapport[
        rapport["Gravite"].isin(["AVERTISSEMENT", "ERREUR"])
    ]

    for _, issue in warnings.iterrows():
        sheet = issue["Feuille"]
        lines = [
            value.strip()
            for value in issue["Ligne_Excel"].split(",")
            if value.strip()
        ]

        identifiers = [
            get_identifier(sheet, line)
            for line in lines
        ]
        identifiers = list(dict.fromkeys(
            value for value in identifiers if value
        ))

        identifier = " / ".join(identifiers)
        problem_type = issue["Type"]
        description = issue["Description"]

        if problem_type == "Doublon":
            decision = "Un exemplaire conservé dans le CSV préparé"
        elif problem_type == "JUSTIFICATIF_MANQUANT":
            decision = "À régulariser auprès des RH"
        elif problem_type == "VALIDATION_RH_MANQUANTE":
            decision = "En attente de validation RH"
        elif problem_type == "ANOMALIE_METIER":
            decision = "En attente de confirmation RH"
        elif problem_type == "COORDONNEE_PARTAGEE":
            decision = "En attente de confirmation RH"
        elif problem_type == "DATE_MANQUANTE_TICKET_CLOTURE":
            decision = "En attente de vérification du support"
        elif problem_type == "NUMERO_SERIE_DUPLIQUE":
            decision = (
                "Exclusion selon la règle de déduplication "
                "des numéros de série"
            )
        elif problem_type == "STATUT_INCOHERENT":
            decision = "En attente de confirmation de l'affectation"
        elif problem_type == "AFFECTATION_SANS_CORRESPONDANCE":
            decision = "Équipement exclu du CSV d'import"
        else:
            decision = "À vérifier"

        add_anomaly(
            sheet,
            issue["Ligne_Excel"],
            identifier,
            problem_type,
            decision,
            description,
            column=issue["Colonne"],
            valeur=issue["Valeur"],
            gravite=issue["Gravite"]
        )

    # 2. Email incomplet connu : E031
    employees = workbook["Employes"]

    if "EmployeeID" in employees.columns:
        employee = employees[employees["EmployeeID"] == "E031"]

        if not employee.empty:
            line = str(employee.index[0] + 2)
            email = str(employee.iloc[0].get("EmailPro", ""))

            add_anomaly(
                "Employes",
                line,
                "E031",
                "Email professionnel incomplet",
                "En attente de confirmation RH",
                f"Adresse à vérifier : {email}. Aucune correction automatique.",
                column="EmailPro",
                valeur=email
            )

    # 3. Numéros de téléphone signalés comme invalides
    if "Telephone" in employees.columns:
        for index, row in employees.iterrows():
            raw_phone = row["Telephone"]

            if pd.isna(raw_phone) or str(raw_phone).strip() == "":
                continue

            phone = str(raw_phone).strip()

            if phone.startswith("00"):
                phone = "+" + phone[2:]

            try:
                parsed = phonenumbers.parse(phone, "TG")

                if not phonenumbers.is_valid_number(parsed):
                    employee_id = row.get("EmployeeID", "")
                    add_anomaly(
                        "Employes",
                        str(index + 2),
                        str(employee_id),
                        "Numéro de téléphone à vérifier",
                        "En attente de confirmation RH",
                        f"Numéro conservé sans modification : {raw_phone}",
                        column="Telephone",
                        valeur=str(raw_phone)
                    )
            except phonenumbers.NumberParseException:
                employee_id = row.get("EmployeeID", "")
                add_anomaly(
                    "Employes",
                    str(index + 2),
                    str(employee_id),
                    "Format de téléphone à vérifier",
                    "En attente de confirmation RH",
                    f"Numéro conservé sans modification : {raw_phone}",
                    column="Telephone",
                    valeur=str(raw_phone)
                )

    # 4. Soldes de congés : valeurs sources non numériques
    #    et soldes à allouer négatifs.
    if LEAVE_BALANCE_SHEET in workbook:
        balances = workbook[LEAVE_BALANCE_SHEET]

        for column in LEAVE_BALANCE_SOURCES:
            if column not in balances.columns:
                continue

            numeric = pd.to_numeric(
                balances[column], errors="coerce"
            )
            invalid = balances[numeric.isna()]

            for index, row in invalid.iterrows():
                raw = row[column]
                missing = pd.isna(raw) or str(raw).strip() == ""
                add_anomaly(
                    LEAVE_BALANCE_SHEET,
                    str(index + 2),
                    str(row.get("EmployeeID", "")),
                    (
                        "Valeur de solde manquante"
                        if missing
                        else "Valeur de solde non numérique"
                    ),
                    "En attente de confirmation RH",
                    (
                        "Cellule vide : traitée comme 0 par la "
                        "formule Excel, à confirmer."
                        if missing
                        else (
                            f"Valeur conservée sans modification : "
                            f"{raw}"
                        )
                    ),
                    column=column,
                    valeur="" if missing else str(raw)
                )

        if all(
            column in balances.columns
            for column in LEAVE_BALANCE_SOURCES
        ):
            solde = leave_balance(balances)

            for index, value in solde.items():
                if pd.isna(value) or value >= 0:
                    continue

                add_anomaly(
                    LEAVE_BALANCE_SHEET,
                    str(index + 2),
                    str(balances.at[index, "EmployeeID"]),
                    "Solde à allouer négatif",
                    "En attente de confirmation RH",
                    (
                        "Report + acquis - pris est négatif : "
                        "vérifier le report ou les jours pris."
                    ),
                    column=LEAVE_BALANCE_COLUMN,
                    valeur=str(value)
                )

    # 5. Anomalies détectées pendant la préparation des données.
    anomalies.extend(PREPARATION_ANOMALIES)

    for anomaly in anomalies:
        anomaly.setdefault("Valeur", "")
        anomaly.setdefault("Gravite", "AVERTISSEMENT")

    journal = pd.DataFrame(anomalies)

    if not journal.empty:
        journal = journal.drop_duplicates()
        journal = journal.sort_values(
            by=["Feuille", "Ligne_Excel"],
            kind="stable"
        )

    journal.to_csv(journal_path, index=False, encoding="utf-8-sig")

    print(f"Journal des anomalies enregistré : {journal_path}")
    print(f"Nombre d'anomalies consignées : {len(journal)}")


# ============================================================
# 6. EXECUTION PRINCIPALE
# ============================================================

def main():

    print("Démarrage de la préparation des données...\n")

    workbook = load_workbook()

    export_sheets(workbook)

    export_leave_balance_workbook()

    export_leave_allocations(workbook)

    generate_anomaly_journal()

    export_excluded_equipment()

    print("\nPréparation terminée.")


if __name__ == "__main__":
    main()
