#!/usr/bin/env python3
"""
Automatisation de l'onboarding des nouveaux arrivants West Digital
(Partie 4) via l'API externe Odoo (XML-RPC).

Étapes par recrue (feuille Nouveaux_Arrivants) :
  1. résolution du poste et du profil matériel (Postes.csv =
     source de vérité, jamais déduit de l'intitulé seul) ;
  2. création de la fiche employé (idempotente, External ID
     __import__.NA-<RecrueID>) ;
  3. création du compte utilisateur Odoo (groupe
     base.group_user uniquement) ;
  4. lancement du plan d'activités « Onboarding » sur
     l'employé, à la date d'entrée ;
  5. création de la demande IT « Équipement nouvel arrivant »
     (module wd_it_request) avec le matériel R10 proposé
     depuis le stock réel (employee_id vide sur
     maintenance.equipment).

Contrôles bloquants : poste non reconnu, profil invalide,
données obligatoires absentes, manager/département/site
introuvable, matériel insuffisant, R8 (J-2 ouvrés) dépassé.

Usage :
  python scripts/onboarding/odoo_onboarding.py            # dry-run
  python scripts/onboarding/odoo_onboarding.py --apply    # exécution réelle
  python scripts/onboarding/odoo_onboarding.py --apply --recrue N01

Identifiants Odoo : variables d'environnement
  ODOO_URL      (défaut http://localhost:8069)
  ODOO_DB       (défaut odoo)
  ODOO_USER     (obligatoire)
  ODOO_PASSWORD (obligatoire — mot de passe ou clé API)
"""

import argparse
import csv
import json
import os
import re
import sys
import unicodedata
import xmlrpc.client
from datetime import date, datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = BASE_DIR / "config" / "onboarding.json"
REPORT_DIR = BASE_DIR / "data" / "reports"

XID_MODULE = "__import__"
IT_REQUEST_NAME = "Équipement nouvel arrivant"
ONBOARDING_PLAN_NAME = "Onboarding"

PROFILE_LABELS = {
    "dev": "Dev",
    "standard": "Standard",
    "support": "Support",
    "commercial": "Commercial",
}

# Matériel requis par profil R10 : (catégorie équipement,
# profil portable attendu ou None, libellé du besoin).
# Un portable « standard » couvre Standard, Support et
# Commercial ; seul Dev exige un portable « dev ».
EQUIPMENT_NEEDS = {
    "dev": [
        ("Ordinateur portable", "dev", "Portable Dev"),
        ("Écran", None, "Écran"),
    ],
    "support": [
        ("Ordinateur portable", "standard", "Portable Standard"),
        ("Casque", None, "Casque"),
    ],
    "commercial": [
        ("Ordinateur portable", "standard", "Portable Standard"),
        ("Téléphone", None, "Téléphone"),
    ],
    "standard": [
        ("Ordinateur portable", "standard", "Portable Standard"),
    ],
}

DEFAULT_CONFIG = {
    "sources": {
        "postes": "data/processed/Postes.csv",
        "arrivants": "data/processed/Nouveaux_Arrivants.csv",
    },
    "poste_aliases": {
        "Stagiaire Data Analyst": "Stagiaire",
    },
    "r8_workdays_before": 2,
    "it_request_name": IT_REQUEST_NAME,
    "onboarding_plan_name": ONBOARDING_PLAN_NAME,
    "user_default_groups": ["base.group_user"],
    "professional_email_domain": "westdigital.tg",
}


# ============================================================
# 1. CONFIGURATION ET SOURCES
# ============================================================

def load_config(path=None):
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg_path = Path(path) if path else CONFIG_PATH
    if cfg_path.exists():
        with open(cfg_path, encoding="utf-8") as fh:
            user_cfg = json.load(fh)
        for key, value in user_cfg.items():
            if isinstance(value, dict) and isinstance(
                config.get(key), dict
            ):
                config[key].update(value)
            else:
                config[key] = value
    return config


def load_csv(relative_or_absolute):
    path = Path(relative_or_absolute)
    if not path.is_absolute():
        path = BASE_DIR / path
    if not path.exists():
        raise FileNotFoundError(f"Source introuvable : {path}")
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def text(value):
    if value is None:
        return ""
    return str(value).strip()


def slug_email(value):
    """Normalise un nom pour un login email (minuscules, ASCII)."""
    value = unicodedata.normalize("NFD", value.lower())
    value = "".join(c for c in value if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9.]+", "", value)


# ============================================================
# 2. RÈGLES MÉTIER PURES (testables sans Odoo)
# ============================================================

def resolve_poste(intitule, postes, aliases=None):
    """
    Retrouve le poste source par intitulé exact, puis via les
    alias configurés. Retourne (poste, anomalie).
    Aucune correspondance inventée.
    """
    aliases = aliases or {}
    key = text(intitule)
    if not key:
        return None, "INTITULE_POSTE_VIDE"

    exact = [p for p in postes if text(p.get("Intitule")) == key]
    if len(exact) == 1:
        return exact[0], None
    if len(exact) > 1:
        return None, "POSTE_MULTIPLE_CORRESPONDANCE"

    alias_target = aliases.get(key)
    if alias_target:
        aliased = [
            p for p in postes
            if text(p.get("Intitule")) == text(alias_target)
        ]
        if len(aliased) == 1:
            return aliased[0], f"ALIAS:{alias_target}"
        if len(aliased) > 1:
            return None, "POSTE_MULTIPLE_CORRESPONDANCE"
        return None, "ALIAS_SANS_CIBLE"

    return None, "POSTE_NON_RECONNU"


def profile_key(profile_label):
    """Clé technique du profil depuis son libellé source."""
    label = text(profile_label).lower()
    for key, canonical in PROFILE_LABELS.items():
        if label == key or label == canonical.lower():
            return key
    return None


def equipment_needs(profile):
    """Besoins R10 pour un profil : liste de (catégorie,
    profil portable, libellé)."""
    return EQUIPMENT_NEEDS.get(profile, [])


def _is_workday(day):
    return day.weekday() < 5


def previous_workdays(day, count):
    """Date `count` jours ouvrés strictement avant `day`
    (samedi/dimanche non comptés)."""
    result = day
    remaining = count
    while remaining > 0:
        result -= timedelta(days=1)
        if _is_workday(result):
            remaining -= 1
    return result


def r8_status(date_entree, today, workdays=2):
    """
    R8 : email, compte Odoo et matériel prêts `workdays`
    jours ouvrés avant la date d'entrée.
    Retourne (deadline, niveau) avec niveau in
    {'OK','IMMINENT','DEPASSE'}.
    """
    deadline = previous_workdays(date_entree, workdays)
    if today > deadline:
        return deadline, "DEPASSE"
    if today == deadline or (
        (deadline - today).days <= 2 and _is_workday(today)
    ):
        return deadline, "IMMINENT"
    return deadline, "OK"


# ============================================================
# 3. CLIENT ODOO XML-RPC
# ============================================================

class OdooClient:
    def __init__(self, url, db, user, password):
        self.db = db
        self.password = password
        self.common = xmlrpc.client.ServerProxy(
            f"{url}/xmlrpc/2/common"
        )
        self.uid = self.common.authenticate(db, user, password, {})
        if not self.uid:
            raise RuntimeError(
                "Authentification Odoo refusée : vérifier "
                "ODOO_USER / ODOO_PASSWORD."
            )
        self.object = xmlrpc.client.ServerProxy(
            f"{url}/xmlrpc/2/object"
        )
        self.user = user

    def call(self, model, method, args, kwargs=None):
        return self.object.execute_kw(
            self.db, self.uid, self.password,
            model, method, args, kwargs or {},
        )

    # -- helpers métier --
    def search(self, model, domain, fields=None, limit=None):
        kwargs = {}
        if fields:
            kwargs["fields"] = fields
        if limit:
            kwargs["limit"] = limit
        return self.call(model, "search", [domain], kwargs)

    def read(self, model, ids, fields):
        return self.call(model, "read", [ids], {"fields": fields})

    def create(self, model, vals, context=None):
        kwargs = {}
        if context:
            kwargs["context"] = context
        return self.call(model, "create", [vals], kwargs)

    def write(self, model, ids, vals):
        return self.call(model, "write", [ids, vals])

    def xmlid_res_id(self, model, name):
        rows = self.call(
            "ir.model.data", "search_read",
            [[
                ("module", "=", XID_MODULE),
                ("name", "=", name),
                ("model", "=", model),
            ]],
            {"fields": ["res_id"]},
        )
        return rows[0]["res_id"] if rows else None

    def group_id(self, module, name):
        rows = self.call(
            "ir.model.data", "search_read",
            [[
                ("module", "=", module),
                ("name", "=", name),
                ("model", "=", "res.groups"),
            ]],
            {"fields": ["res_id"]},
        )
        return rows[0]["res_id"] if rows else None


# ============================================================
# 4. RÉFÉRENTIELS ODOO
# ============================================================

def find_by_xid(client, model, xid_name):
    return client.xmlid_res_id(model, xid_name)


def find_employee_xid(client, source_id):
    """Employé existant par External ID __import__.<source_id>."""
    if not text(source_id):
        return None
    return find_by_xid(client, "hr.employee", text(source_id))


def find_recruit_employee(client, recrue_id):
    """Employé déjà créé par ce script : __import__.NA-<id>."""
    return find_by_xid(
        client, "hr.employee", f"NA-{text(recrue_id)}"
    )


def find_user_by_login(client, login):
    rows = client.call(
        "res.users", "search_read",
        [[("login", "=", login)]],
        {"fields": ["id", "name", "employee_id"]},
    )
    return rows[0] if rows else None


def find_job(client, intitule):
    rows = client.call(
        "hr.job", "search_read",
        [[("name", "=", text(intitule))]],
        {"fields": ["id", "name"]},
    )
    return rows[0]["id"] if len(rows) == 1 else None


def find_work_location(client, site):
    site = text(site)
    if not site:
        return None
    rows = client.call(
        "hr.work.location", "search_read",
        [[("name", "=", site)]],
        {"fields": ["id"]},
    )
    return rows[0]["id"] if len(rows) == 1 else None


def plan_template_summaries(client, plan_name):
    """Retourne (plan_id, summaries, besoin_compte_manager)."""
    plans = client.call(
        "mail.activity.plan", "search_read",
        [[("name", "=", plan_name)]],
        {"fields": ["id"]},
    )
    if not plans:
        return None, [], False
    templates = client.call(
        "mail.activity.plan.template", "search_read",
        [[("plan_id", "=", plans[0]["id"])]],
        {"fields": ["summary", "responsible_type"]},
    )
    needs_manager = any(
        t.get("responsible_type") == "manager" for t in templates
    )
    return plans[0]["id"], [
        t["summary"] for t in templates if t.get("summary")
    ], needs_manager


def plan_already_launched(client, employee_id, summaries):
    """
    mail.activity ne référence pas le plan : on considère le
    plan lancé si l'employé possède au moins autant
    d'activités dont le résumé correspond à celui des
    templates du plan.
    """
    if not summaries:
        return False
    activities = client.call(
        "mail.activity", "search_read",
        [[
            ("res_model", "=", "hr.employee"),
            ("res_id", "=", employee_id),
            ("summary", "in", summaries),
        ]],
        {"fields": ["summary"]},
    )
    return len(activities) >= len(summaries)


def find_it_request(client, employee_id, request_name):
    rows = client.call(
        "wd.it.request", "search_read",
        [[
            ("employee_id", "=", employee_id),
            ("name", "=", request_name),
            ("state", "!=", "cancel"),
        ]],
        {"fields": ["id", "state"]},
    )
    return rows[0] if rows else None


def equipment_in_open_requests(client):
    """Équipements déjà proposés sur des demandes IT non
    annulées/terminées : jamais proposés deux fois."""
    lines = client.call(
        "wd.it.request.line", "search_read",
        [[
            ("equipment_id", "!=", False),
            ("request_id.state", "not in", ["done", "cancel"]),
        ]],
        {"fields": ["equipment_id"]},
    )
    return {
        line["equipment_id"][0]
        for line in lines
        if line.get("equipment_id")
    }


def equipment_under_maintenance(client, equipment_ids):
    """Équipements avec une demande de maintenance non
    clôturée (proxy « en réparation / indisponible »)."""
    if not equipment_ids:
        return set()
    rows = client.call(
        "maintenance.request", "search_read",
        [[
            ("equipment_id", "in", list(equipment_ids)),
            ("done", "=", False),
        ]],
        {"fields": ["equipment_id"]},
    )
    return {
        row["equipment_id"][0] for row in rows
        if row.get("equipment_id")
    }


def equipment_candidates(client, category, profile, exclude_ids):
    """
    Stock réel = equipment_id vide (non affecté) + actif.
    Pour les portables, le profil matériel R10 filtre les
    candidats ; pour les accessoires, la catégorie suffit
    (leurs profils ne sont pas renseignés pour le stock).
    """
    domain = [
        ("category_id.name", "=", category),
        ("employee_id", "=", False),
        ("active", "=", True),
    ]
    if profile:
        domain.append(("wd_material_profile", "=", profile))
    ids = client.search(
        "maintenance.equipment", domain, limit=50,
    )
    ids = [i for i in ids if i not in exclude_ids]
    if not ids:
        return []
    busy = equipment_under_maintenance(client, ids)
    return [
        i for i in ids if i not in busy
    ]


def equipment_label(client, equipment_id):
    rows = client.read(
        "maintenance.equipment", [equipment_id],
        ["name", "model", "serial_no", "category_id"],
    )
    if not rows:
        return ""
    row = rows[0]
    parts = [row["name"]]
    if row.get("model"):
        parts.append(f"({row['model']})")
    if row.get("serial_no"):
        parts.append(f"n° {row['serial_no']}")
    return " ".join(parts)


# ============================================================
# 5. TRAITEMENT D'UNE RECRUE
# ============================================================

def professional_email(prenom, nom, domain):
    first = slug_email(prenom)
    last = slug_email(nom)
    if first and last:
        return f"{first}.{last}@{domain}"
    return f"{first or last}@{domain}"


def process_recruit(client, recruit, config, plan_ctx, dry_run):
    """
    Traite une recrue et retourne (journal_lines, result).
    result['blocked'] = True si un contrôle bloquant empêche
    la création des fiches.
    """
    journal = []
    result = {
        "recrue": recruit.get("RecrueID", "?"),
        "blocked": False,
        "actions": [],
    }

    rid = text(recruit.get("RecrueID"))
    nom = text(recruit.get("Nom"))
    prenom = text(recruit.get("Prenom"))
    intitule = text(recruit.get("Poste"))
    nom_complet = f"{prenom} {nom}".strip()

    journal.append(f"=== {rid} {nom_complet} — {intitule} ===")

    # --- 1. Poste et profil (Postes.csv = source de vérité) ---
    postes = plan_ctx["postes"]
    poste, poste_anomaly = resolve_poste(
        intitule, postes, config.get("poste_aliases", {})
    )
    if poste is None:
        journal.append(
            f"  POSTE: ANOMALIE BLOQUANTE ({poste_anomaly}) — "
            "aucune correspondance, profil non déduit."
        )
        result["blocked"] = True
        return journal, result

    profile_label = text(poste.get("ProfilMateriel"))
    pkey = profile_key(profile_label)
    journal.append(
        f"  POSTE: {poste['PosteID']} « {poste['Intitule']} »"
        + (f" (alias)" if poste_anomaly else "")
        + f" | PROFIL: {profile_label or '?'}"
    )
    if not pkey:
        journal.append(
            "  PROFIL: ANOMALIE BLOQUANTE — profil matériel "
            "vide ou invalide dans Postes."
        )
        result["blocked"] = True
        return journal, result

    # --- 2. Date d'entrée et R8 ---
    raw_date = text(recruit.get("DateEntree"))
    try:
        date_entree = datetime.strptime(
            raw_date[:10], "%Y-%m-%d"
        ).date()
    except ValueError:
        journal.append(
            f"  DATE_ENTREE: ANOMALIE BLOQUANTE — « {raw_date} » "
            "illisible (attendu AAAA-MM-JJ)."
        )
        result["blocked"] = True
        return journal, result

    deadline, r8 = r8_status(
        date_entree, date.today(),
        config.get("r8_workdays_before", 2),
    )
    journal.append(
        f"  ENTREE: {date_entree.isoformat()} | R8 deadline "
        f"{deadline.isoformat()} : {r8}"
    )
    if r8 == "DEPASSE":
        journal.append(
            "  R8: ALERTE BLOQUANTE — délai de 2 jours ouvrés "
            "déjà dépassé (email, compte et matériel en "
            "retard)."
        )
        result.setdefault("alerts", []).append("R8_DEPASSE")

     # --- 2bis. Email personnel source (traçabilité) ---
    perso_email = text(recruit.get("EmailPerso"))
    if perso_email and not re.match(
        r"^[^@\s]+@[^@\s]+\.[^@\s]+$", perso_email
    ):
        journal.append(
            f"  EMAIL: ALERTE — email personnel source invalide "
            f"« {perso_email} » (non utilisé pour le compte)."
        )
        result.setdefault("alerts", []).append("EMAIL_INVALIDE")

    # --- 3. Référentiels obligatoires ---
    missing = []
    dept_xid = text(recruit.get("DepartementID"))
    manager_src = text(recruit.get("ManagerID"))
    site = text(recruit.get("Site"))

    department_id = find_by_xid(client, "hr.department", dept_xid)
    if department_id is None:
        missing.append(f"département {dept_xid or '?'}")
    manager_id = find_employee_xid(client, manager_src)
    if manager_src and manager_id is None:
        missing.append(f"manager {manager_src}")
    work_location_id = find_work_location(client, site)
    if site and work_location_id is None:
        missing.append(f"site « {site} »")

    job_id = find_job(client, poste["Intitule"])
    if job_id is None:
        missing.append(f"poste « {poste['Intitule']} »")

    if missing:
        journal.append(
            "  RÉFÉRENTIELS: ANOMALIE BLOQUANTE — introuvable : "
            + ", ".join(missing)
        )
        result["blocked"] = True
        return journal, result

    # --- 4. Employé (idempotent) ---
    employee_id = find_recruit_employee(client, rid)
    if employee_id:
        journal.append(
            f"  EMPLOYÉ: existant (id {employee_id}, "
            f"__import__.NA-{rid})"
        )
        result["actions"].append("employee:existing")
    else:
        work_email = professional_email(
            prenom, nom, config["professional_email_domain"]
        )
        employee_vals = {
            "name": nom_complet,
            "job_id": job_id,
            "department_id": department_id,
            "parent_id": manager_id or False,
            "work_email": work_email,
            "mobile_phone": text(recruit.get("Telephone")) or False,
            "work_location_id": work_location_id or False,
        }
        if dry_run:
            journal.append(
                f"  EMPLOYÉ: serait créé — {employee_vals}"
            )
            result["actions"].append("employee:create(dry)")
            # Id fictif pour la suite de la simulation.
            employee_id = -1
        else:
            employee_id = client.create(
                "hr.employee", employee_vals
            )
            client.create("ir.model.data", {
                "module": XID_MODULE,
                "name": f"NA-{rid}",
                "model": "hr.employee",
                "res_id": employee_id,
            })
            journal.append(
                f"  EMPLOYÉ: créé (id {employee_id}, "
                f"__import__.NA-{rid}, email {work_email})"
            )
            result["actions"].append("employee:created")

    # --- 5. Utilisateur Odoo (idempotent) ---
    work_email = professional_email(
        prenom, nom, config["professional_email_domain"]
    )
    existing_user = find_user_by_login(client, work_email)
    if existing_user:
        journal.append(
            f"  UTILISATEUR: existant (login {work_email}, "
            f"id {existing_user['id']})"
        )
        result["actions"].append("user:existing")
    elif dry_run:
        journal.append(
            f"  UTILISATEUR: serait créé — login {work_email}, "
            "groupe(s) " + ",".join(
                config["user_default_groups"]
            )
        )
        result["actions"].append("user:create(dry)")
    else:
        group_ids = [
            gid for gid in (
                client.group_id("base", g.split(".")[-1])
                for g in config["user_default_groups"]
            ) if gid
        ]
        user_id = client.create("res.users", {
            "name": nom_complet,
            "login": work_email,
            "email": work_email,
            "groups_id": [(6, 0, group_ids)],
        })
        client.write(
            "hr.employee", [employee_id],
            {"user_id": user_id},
        )
        journal.append(
            f"  UTILISATEUR: créé (login {work_email}, "
            f"id {user_id}, groupes internes uniquement)"
        )
        result["actions"].append("user:created")

    # --- 6. Plan d'activités Onboarding ---
    plan_id, summaries, plan_needs_manager = plan_template_summaries(
        client, config["onboarding_plan_name"]
    )
    if plan_needs_manager and manager_id:
        mgr_rows = client.read(
            "hr.employee", [manager_id], ["user_id"]
        )
        if mgr_rows and not mgr_rows[0].get("user_id"):
            journal.append(
                "  PLAN: ALERTE — des activités sont assignées au "
                "manager mais celui-ci n'a pas de compte Odoo "
                f"({manager_src}) : le lancement échouera."
            )
            result.setdefault("alerts", []).append(
                "PLAN_MANAGER_SANS_COMPTE"
            )
    if plan_id is None:
        journal.append(
            "  PLAN: ANOMALIE — plan « Onboarding » absent."
        )
        result.setdefault("alerts", []).append("PLAN_ABSENT")
    elif employee_id and employee_id > 0 and plan_already_launched(
        client, employee_id, summaries
    ):
        journal.append("  PLAN: déjà lancé pour cet employé.")
        result["actions"].append("plan:existing")
    elif dry_run:
        journal.append(
            f"  PLAN: serait lancé — {len(summaries)} "
            f"activités, date de référence {date_entree}."
        )
        result["actions"].append("plan:launch(dry)")
    else:
        try:
            company_id = client.call(
                "res.company", "search", [[]], {"limit": 1}
            )[0]
            sched_id = client.create(
                "mail.activity.schedule",
                {
                    "res_model": "hr.employee",
                    "plan_id": plan_id,
                    "plan_date": date_entree.isoformat(),
                    "company_id": company_id,
                },
                context={
                    "active_ids": [employee_id],
                    "active_model": "hr.employee",
                },
            )
            client.call(
                "mail.activity.schedule",
                "action_schedule_plan", [sched_id],
            )
        except xmlrpc.client.Fault as exc:
            journal.append(
                f"  PLAN: ANOMALIE — lancement refusé par Odoo : "
                f"{exc.faultString}"
            )
            result.setdefault("alerts", []).append(
                "PLAN_LANCEMENT_ECHOUE"
            )
        else:
            journal.append(
                f"  PLAN: lancé ({len(summaries)} activités, "
                f"référence {date_entree})."
            )
            result["actions"].append("plan:launched")

    # --- 7. Demande IT « Équipement nouvel arrivant » ---
    existing_req = (
        find_it_request(client, employee_id, config["it_request_name"])
        if employee_id and employee_id > 0 else None
    )
    if existing_req:
        journal.append(
            f"  DEMANDE IT: existante (id {existing_req['id']}, "
            f"état {existing_req['state']})"
        )
        result["actions"].append("it_request:existing")
    else:
        needs = equipment_needs(pkey)
        if not needs:
            journal.append("  DEMANDE IT: profil sans besoins R10.")
        else:
            taken = equipment_in_open_requests(client)
            lines_vals = []
            shortages = []
            for category, equip_profile, label in needs:
                candidates = equipment_candidates(
                    client, category, equip_profile, taken
                )
                if candidates:
                    equip_id = candidates[0]
                    taken.add(equip_id)
                    lines_vals.append({
                        "sequence": 10 * (len(lines_vals) + 1),
                        "needed_label": label,
                        "equipment_id": equip_id,
                        "status": "proposed",
                    })
                    journal.append(
                        f"    MATÉRIEL {label}: "
                        f"{equipment_label(client, equip_id)} "
                        "[stock]"
                    )
                else:
                    lines_vals.append({
                        "sequence": 10 * (len(lines_vals) + 1),
                        "needed_label": label,
                        "equipment_id": False,
                        "status": "unavailable",
                        "note": "Stock insuffisant",
                    })
                    shortages.append(label)
                    journal.append(
                        f"    MATÉRIEL {label}: INDISPONIBLE — "
                        "stock insuffisant"
                    )
            if shortages:
                result.setdefault("alerts", []).append(
                    "STOCK_INSUFFISANT:" + ",".join(shortages)
                )

            note_parts = [
                f"Profil matériel R10 : {profile_label} "
                f"(poste {poste['PosteID']}).",
                f"Date d'entrée : {date_entree.isoformat()}.",
            ]
            besoins = text(recruit.get("BesoinsSpecifiques"))
            if besoins:
                note_parts.append(f"Besoins spécifiques : {besoins}")
            note = "\n".join(note_parts)

            if dry_run:
                journal.append(
                    f"  DEMANDE IT: serait créée — "
                    f"{config['it_request_name']} "
                    f"({len(lines_vals)} ligne(s))"
                )
                result["actions"].append("it_request:create(dry)")
            else:
                req_id = client.create("wd.it.request", {
                    "name": config["it_request_name"],
                    "employee_id": employee_id,
                    "note": note,
                    "line_ids": [
                        (0, 0, line) for line in lines_vals
                    ],
                })
                journal.append(
                    f"  DEMANDE IT: créée (id {req_id}, "
                    f"{len(lines_vals)} ligne(s))"
                )
                result["actions"].append("it_request:created")

    return journal, result


# ============================================================
# 6. EXÉCUTION
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Onboarding automatisé via API Odoo (XML-RPC)."
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Exécute réellement (sinon simulation dry-run).",
    )
    parser.add_argument("--recrue", help="Traiter une seule RecrueID.")
    parser.add_argument("--config", help="Chemin du fichier config JSON.")
    args = parser.parse_args()

    config = load_config(args.config)
    postes = load_csv(config["sources"]["postes"])
    arrivants = load_csv(config["sources"]["arrivants"])

    if args.recrue:
        arrivants = [
            r for r in arrivants
            if text(r.get("RecrueID")) == args.recrue
        ]
        if not arrivants:
            print(f"Recrue {args.recrue} introuvable.")
            sys.exit(1)

    url = os.environ.get("ODOO_URL", "http://localhost:8069")
    db = os.environ.get("ODOO_DB", "odoo")
    user = os.environ.get("ODOO_USER")
    password = os.environ.get("ODOO_PASSWORD")
    if not user or not password:
        print(
            "ODOO_USER et ODOO_PASSWORD requis "
            "(variables d'environnement)."
        )
        sys.exit(1)

    client = OdooClient(url, db, user, password)
    print(
        f"Connecté à {url} (base {db}) — "
        f"{'APPLICATION' if args.apply else 'DRY-RUN'}"
    )

    plan_ctx = {"postes": postes}
    all_journal = []
    summary = {"created": 0, "existing": 0, "blocked": 0}

    for recruit in arrivants:
        journal, result = process_recruit(
            client, recruit, config, plan_ctx, dry_run=not args.apply
        )
        all_journal.extend(journal)
        all_journal.append("")
        for action in result["actions"]:
            if ":created" in action or ":launched" in action:
                summary["created"] += 1
            elif ":existing" in action:
                summary["existing"] += 1
        if result["blocked"]:
            summary["blocked"] += 1
        for line in journal:
            print(line)

    print(
        f"\n=== RESUME : {summary['created']} action(s) nouvelle(s), "
        f"{summary['existing']} déjà existante(s), "
        f"{summary['blocked']} recrue(s) bloquée(s) ==="
    )

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    mode = "apply" if args.apply else "dryrun"
    journal_path = REPORT_DIR / f"onboarding_{mode}_{stamp}.log"
    with open(journal_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(all_journal))
    print(f"Journal : {journal_path}")


if __name__ == "__main__":
    main()
