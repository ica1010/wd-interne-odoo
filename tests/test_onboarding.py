"""
Tests de l'automatisation onboarding (Partie 4).

Le client Odoo est remplacé par un FakeClient en mémoire :
aucun appel réseau, aucune donnée réelle modifiée.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "scripts" / "onboarding"))

import odoo_onboarding as ob  # noqa: E402


# ============================================================
# FakeClient : mini-ORM en mémoire pour les modèles utilisés
# ============================================================

OPS = {
    "=": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "in": lambda a, b: a in (b or []),
    "not in": lambda a, b: a not in (b or []),
    ">": lambda a, b: a is not None and a > b,
    "<": lambda a, b: a is not None and a < b,
    ">=": lambda a, b: a is not None and a >= b,
    "<=": lambda a, b: a is not None and a <= b,
}


def field_value(rec, field):
    """Résout les champs simples et deux relations utilisées."""
    if field in rec:
        return rec[field]
    if field == "category_id.name":
        return rec.get("_category")
    if field == "request_id.state":
        req = STORE["wd.it.request"].get(rec.get("request_id"))
        return req.get("state") if req else None
    if field == "plan_id":
        return rec.get("plan_id")
    return rec.get(field)


STORE = {}
NEXT_ID = {}
IMD = {}
CONTEXT = {}


def reset_store():
    global STORE, NEXT_ID, IMD, CONTEXT
    STORE = {}
    NEXT_ID = {}
    IMD = {}
    CONTEXT = {}


def nid(model):
    NEXT_ID[model] = NEXT_ID.get(model, 0) + 1
    return NEXT_ID[model]


def match_domain(rec, domain):
    for leaf in domain:
        if not isinstance(leaf, (list, tuple)):
            continue
        field, op, value = leaf
        actual = field_value(rec, field)
        if op == "=" and value is False:
            ok = actual in (False, None, 0, "")
        elif op == "!=" and value is False:
            ok = actual not in (False, None, 0, "")
        else:
            ok = OPS.get(op, lambda a, b: False)(actual, value)
        if not ok:
            return False
    return True


M2O_FIELDS = {"equipment_id", "employee_id", "request_id",
              "plan_id", "parent_id", "job_id", "department_id",
              "user_id"}


def _fmt_m2o(field, value):
    """Many2one : int -> [id, label] comme le fait Odoo."""
    if field in M2O_FIELDS and isinstance(value, int) and value:
        return [value, f"rec{value}"]
    return value


class FakeClient:
    def __init__(self):
        self._seed()

    def _seed(self):
        # --- référentiels ---
        STORE["hr.department"] = {
            1: {"id": 1, "name": "D01"},
        }
        # departments sont résolus par xmlid, pas par nom
        STORE["hr.job"] = {
            10: {"id": 10, "name": "Développeur Odoo"},
            11: {"id": 11, "name": "Stagiaire"},
            12: {"id": 12, "name": "Technicien Support IT"},
            13: {"id": 13, "name": "Account Manager"},
            14: {"id": 14, "name": "Comptable"},
        }
        STORE["hr.work.location"] = {
            1: {"id": 1, "name": "Lomé"},
            2: {"id": 2, "name": "Cotonou"},
        }
        STORE["hr.employee"] = {
            100: {"id": 100, "name": "Manager Test", "user_id": False},
        }
        STORE["res.company"] = {1: {"id": 1, "name": "WD"}}
        STORE["res.groups"] = {50: {"id": 50, "name": "User"}}
        STORE["res.users"] = {}
        STORE["maintenance.equipment"] = {}
        STORE["maintenance.request"] = {}
        STORE["mail.activity.plan"] = {
            7: {"id": 7, "name": "Onboarding"},
        }
        STORE["mail.activity.plan.template"] = {
            i: {
                "id": i, "plan_id": 7,
                "summary": f"Activité {i}",
            }
            for i in range(1, 4)
        }
        STORE["mail.activity.schedule"] = {}
        STORE["mail.activity"] = {}
        STORE["wd.it.request"] = {}
        STORE["wd.it.request.line"] = {}
        STORE["ir.model.data"] = {}

        # External IDs
        IMD[("hr.employee", "E002")] = 100
        IMD[("hr.department", "D02")] = 1
        IMD[("res.groups", "group_user")] = 50

    # ---- interface OdooClient ----
    def call(self, model, method, args, kwargs=None):
        kwargs = kwargs or {}
        store = STORE.setdefault(model, {})
        if method in ("search", "search_read"):
            domain = args[0] if args else []
            fields = kwargs.get("fields")
            limit = kwargs.get("limit")
            matched = [
                rec for rec in store.values() if match_domain(rec, domain)
            ]
            matched.sort(key=lambda r: r["id"])
            if limit:
                matched = matched[:limit]
            if method == "search":
                return [r["id"] for r in matched]
            return [
                {f: _fmt_m2o(f, field_value(r, f)) for f in fields}
                if fields else dict(r)
                for r in matched
            ]
        if method == "read":
            ids, fields = args[0], kwargs.get("fields", [])
            return [
                {f: _fmt_m2o(f, field_value(store[i], f))
                 for f in fields}
                for i in ids if i in store
            ]
        if method == "create":
            vals = args[0]
            if model == "wd.it.request" and "line_ids" in vals:
                lines = vals.pop("line_ids")
                rid = self._create(model, vals)
                for _cmd, _lid, line_vals in lines:
                    line_vals = dict(line_vals)
                    line_vals["request_id"] = rid
                    self._create("wd.it.request.line", line_vals)
                return rid
            return self._create(model, vals)
        if method == "write":
            ids, vals = args[0], args[1]
            for i in ids:
                if i in store:
                    store[i].update(vals)
            return True
        if method == "action_schedule_plan":
            sched = store[args[0]]
            for i, summary in enumerate(
                STORE["mail.activity.plan.template"].values(), start=1
            ):
                self._create("mail.activity", {
                    "res_model": "hr.employee",
                    "res_id": sched["_res_id"],
                    "summary": summary["summary"],
                })
            return True
        raise AssertionError(f"méthode non supportée : {model}.{method}")

    def _create(self, model, vals):
        vals = dict(vals)
        i = nid(model)
        vals.setdefault("id", i)
        if model == "ir.model.data":
            IMD[(vals["model"], vals["name"])] = vals["res_id"]
        if model == "mail.activity.schedule":
            vals["_res_id"] = CONTEXT.get("active_ids", [None])[0]
        STORE.setdefault(model, {})[i] = vals
        return i

    def search(self, model, domain, fields=None, limit=None):
        return self.call(
            model, "search", [domain],
            {"fields": fields, "limit": limit},
        )

    def read(self, model, ids, fields):
        return self.call(model, "read", [ids], {"fields": fields})

    def create(self, model, vals, context=None):
        global CONTEXT
        old = CONTEXT
        if context:
            CONTEXT = context
        try:
            if model == "wd.it.request" and "line_ids" in vals:
                vals = dict(vals)
                lines = vals.pop("line_ids")
                rid = self._create(model, vals)
                for _cmd, _lid, line_vals in lines:
                    line_vals = dict(line_vals)
                    line_vals["request_id"] = rid
                    self._create("wd.it.request.line", line_vals)
                return rid
            return self._create(model, vals)
        finally:
            CONTEXT = old

    def write(self, model, ids, vals):
        return self.call(model, "write", [ids, vals])

    def xmlid_res_id(self, model, name):
        return IMD.get((model, name))

    def group_id(self, module, name):
        return IMD.get(("res.groups", name))


@pytest.fixture(autouse=True)
def clean_store():
    reset_store()
    yield


# ============================================================
# Sources factices
# ============================================================

POSTES = [
    {"PosteID": "J03", "Intitule": "Consultant Odoo",
     "ProfilMateriel": "Dev"},
    {"PosteID": "J04", "Intitule": "Développeur Odoo",
     "ProfilMateriel": "Dev"},
    {"PosteID": "J07", "Intitule": "Technicien Support IT",
     "ProfilMateriel": "Support"},
    {"PosteID": "J09", "Intitule": "Account Manager",
     "ProfilMateriel": "Commercial"},
    {"PosteID": "J14", "Intitule": "Stagiaire",
     "ProfilMateriel": "Standard"},
    {"PosteID": "J12", "Intitule": "Comptable",
     "ProfilMateriel": ""},  # profil invalide pour le test
]

CONFIG = {
    "poste_aliases": {"Stagiaire Data Analyst": "Stagiaire"},
    "r8_workdays_before": 2,
    "it_request_name": "Équipement nouvel arrivant",
    "onboarding_plan_name": "Onboarding",
    "user_default_groups": ["base.group_user"],
    "professional_email_domain": "westdigital.tg",
}

FUTURE = (date.today() + timedelta(days=30)).isoformat()


def recruit(poste="Développeur Odoo", rid="N01", **over):
    base = {
        "RecrueID": rid,
        "Nom": "TEST", "Prenom": "Awa",
        "Poste": poste,
        "DepartementID": "D02",
        "ManagerID": "E002",
        "Site": "Lomé",
        "TypeContrat": "CDI",
        "DateEntree": FUTURE,
        "EmailPerso": "awa.test@gmail.com",
        "Telephone": "+228 90 00 00 00",
        "BesoinsSpecifiques": "",
    }
    base.update(over)
    return base


def add_equipment(client, category, profile=None, employee_id=False,
                  equip_id=None):
    i = equip_id or nid("maintenance.equipment")
    STORE["maintenance.equipment"][i] = {
        "id": i,
        "name": f"{category} {i}",
        "model": "Modele X",
        "serial_no": f"SN{i}",
        "_category": category,
        "wd_material_profile": profile,
        "employee_id": employee_id,
        "active": True,
    }
    return i


PLAN_CTX = {"postes": POSTES}


# ============================================================
# Tests des règles pures
# ============================================================

class TestResolvePoste:
    def test_exact_match(self):
        poste, anomaly = ob.resolve_poste(
            "Développeur Odoo", POSTES, CONFIG["poste_aliases"]
        )
        assert poste["PosteID"] == "J04"
        assert anomaly is None

    def test_alias_match(self):
        poste, anomaly = ob.resolve_poste(
            "Stagiaire Data Analyst", POSTES, CONFIG["poste_aliases"]
        )
        assert poste["PosteID"] == "J14"
        assert anomaly and anomaly.startswith("ALIAS")

    def test_unknown_poste(self):
        poste, anomaly = ob.resolve_poste(
            "Poste inexistant", POSTES, CONFIG["poste_aliases"]
        )
        assert poste is None
        assert anomaly == "POSTE_NON_RECONNU"

    def test_empty_intitule(self):
        poste, anomaly = ob.resolve_poste("", POSTES)
        assert poste is None
        assert anomaly == "INTITULE_POSTE_VIDE"


class TestProfile:
    @pytest.mark.parametrize("label,key", [
        ("Dev", "dev"), ("dev", "dev"),
        ("Standard", "standard"), ("Support", "support"),
        ("Commercial", "commercial"),
    ])
    def test_valid_labels(self, label, key):
        assert ob.profile_key(label) == key

    def test_invalid(self):
        assert ob.profile_key("") is None
        assert ob.profile_key("Expert") is None

    def test_needs_per_profile(self):
        assert [n[2] for n in ob.equipment_needs("dev")] == [
            "Portable Dev", "Écran",
        ]
        assert [n[2] for n in ob.equipment_needs("support")] == [
            "Portable Standard", "Casque",
        ]
        assert [n[2] for n in ob.equipment_needs("commercial")] == [
            "Portable Standard", "Téléphone",
        ]
        assert [n[2] for n in ob.equipment_needs("standard")] == [
            "Portable Standard",
        ]
        # Un portable standard couvre Standard/Support/Commercial
        needs = ob.equipment_needs("standard")
        assert needs[0][1] == "standard"


class TestR8:
    def test_previous_workdays_skips_weekend(self):
        # lundi 2026-11-23 -> 2 ouvrés avant = jeudi 19
        deadline = ob.previous_workdays(date(2026, 11, 23), 2)
        assert deadline == date(2026, 11, 19)

    def test_r8_ok(self):
        deadline, level = ob.r8_status(
            date.today() + timedelta(days=30), date.today(), 2
        )
        assert level == "OK"

    def test_r8_exceeded(self):
        deadline, level = ob.r8_status(
            date.today() + timedelta(days=2), date.today(), 2
        )
        assert level in ("IMMINENT", "DEPASSE")


# ============================================================
# Tests de process_recruit (FakeClient)
# ============================================================

class TestProcessRecruit:
    def test_dev_profile_full_flow(self):
        client = FakeClient()
        add_equipment(client, "Ordinateur portable", "dev")
        add_equipment(client, "Écran")
        journal, result = ob.process_recruit(
            client, recruit(), CONFIG, PLAN_CTX, dry_run=False
        )
        assert not result["blocked"]
        reqs = STORE["wd.it.request"]
        assert len(reqs) == 1
        lines = [
            l for l in STORE["wd.it.request.line"].values()
        ]
        assert len(lines) == 2
        assert all(l["status"] == "proposed" for l in lines)
        # employé + utilisateur + plan créés
        assert len(STORE["hr.employee"]) == 2  # manager + nouveau
        assert len(STORE["res.users"]) == 1
        assert len(STORE["mail.activity"]) == 3
        assert any("PLAN: lancé" in j for j in journal)

    def test_second_run_is_idempotent(self):
        client = FakeClient()
        add_equipment(client, "Ordinateur portable", "dev")
        add_equipment(client, "Écran")
        r = recruit()
        ob.process_recruit(client, r, CONFIG, PLAN_CTX, dry_run=False)
        journal, result = ob.process_recruit(
            client, r, CONFIG, PLAN_CTX, dry_run=False
        )
        assert not result["blocked"]
        assert len(STORE["hr.employee"]) == 2  # pas de doublon
        assert len(STORE["res.users"]) == 1
        assert len(STORE["wd.it.request"]) == 1
        assert any("PLAN: déjà lancé" in j for j in journal)
        assert any("DEMANDE IT: existante" in j for j in journal)

    def test_unknown_poste_blocked(self):
        client = FakeClient()
        journal, result = ob.process_recruit(
            client, recruit(poste="Métier inconnu"),
            CONFIG, PLAN_CTX, dry_run=False,
        )
        assert result["blocked"]
        assert not STORE["hr.employee"] or len(
            STORE["hr.employee"]
        ) == 1  # seul le manager seed

    def test_invalid_profile_blocked(self):
        client = FakeClient()
        journal, result = ob.process_recruit(
            client, recruit(poste="Comptable"),
            CONFIG, PLAN_CTX, dry_run=False,
        )
        assert result["blocked"]
        assert any("PROFIL" in j for j in journal)

    def test_alias_stagiaire_standard(self):
        client = FakeClient()
        add_equipment(client, "Ordinateur portable", "standard")
        journal, result = ob.process_recruit(
            client,
            recruit(poste="Stagiaire Data Analyst"),
            CONFIG, PLAN_CTX, dry_run=False,
        )
        assert not result["blocked"]
        lines = list(STORE["wd.it.request.line"].values())
        assert len(lines) == 1
        assert lines[0]["needed_label"] == "Portable Standard"

    def test_stock_insufficient(self):
        client = FakeClient()
        # Dev sans écran en stock
        add_equipment(client, "Ordinateur portable", "dev")
        journal, result = ob.process_recruit(
            client, recruit(), CONFIG, PLAN_CTX, dry_run=False
        )
        assert not result["blocked"]
        lines = list(STORE["wd.it.request.line"].values())
        écran = [l for l in lines if l["needed_label"] == "Écran"]
        assert écran and écran[0]["status"] == "unavailable"
        assert écran[0]["equipment_id"] is False
        assert any("STOCK_INSUFFISANT" in a
                   for a in result.get("alerts", []))

    def test_assigned_equipment_not_proposed(self):
        client = FakeClient()
        taken = add_equipment(
            client, "Ordinateur portable", "dev", employee_id=100
        )
        add_equipment(client, "Écran")
        journal, result = ob.process_recruit(
            client, recruit(), CONFIG, PLAN_CTX, dry_run=False
        )
        lines = list(STORE["wd.it.request.line"].values())
        portable = [
            l for l in lines if l["needed_label"] == "Portable Dev"
        ]
        assert portable[0]["status"] == "unavailable"
        assert taken not in [
            l["equipment_id"] for l in lines
        ]

    def test_equipment_on_open_request_excluded(self):
        client = FakeClient()
        e1 = add_equipment(client, "Ordinateur portable", "dev")
        e2 = add_equipment(client, "Ordinateur portable", "dev")
        add_equipment(client, "Écran")
        # une demande IT existante consomme e1
        client._create("wd.it.request", {
            "id": 1, "name": "Équipement nouvel arrivant",
            "employee_id": 100, "state": "draft",
        })
        client._create("wd.it.request.line", {
            "id": 1, "request_id": 1, "equipment_id": e1,
            "status": "proposed",
        })
        journal, result = ob.process_recruit(
            client, recruit(), CONFIG, PLAN_CTX, dry_run=False
        )
        lines = list(STORE["wd.it.request.line"].values())
        def req_id(line):
            rid = line.get("request_id")
            return rid[0] if isinstance(rid, list) else rid
        portable_lines = [
            l for l in lines
            if l.get("needed_label") == "Portable Dev"
            and req_id(l) != 1
        ]
        assert portable_lines[0]["equipment_id"] == e2

    def test_commercial_no_phone_blocked_material(self):
        client = FakeClient()
        add_equipment(client, "Ordinateur portable", "standard")
        # pas de téléphone en stock
        journal, result = ob.process_recruit(
            client, recruit(poste="Account Manager"),
            CONFIG, PLAN_CTX, dry_run=False,
        )
        assert not result["blocked"]
        lines = list(STORE["wd.it.request.line"].values())
        phone = [l for l in lines if l["needed_label"] == "Téléphone"]
        assert phone and phone[0]["status"] == "unavailable"

    def test_support_profile(self):
        client = FakeClient()
        add_equipment(client, "Ordinateur portable", "standard")
        add_equipment(client, "Casque")
        journal, result = ob.process_recruit(
            client, recruit(poste="Technicien Support IT"),
            CONFIG, PLAN_CTX, dry_run=False,
        )
        assert not result["blocked"]
        labels = {
            l["needed_label"]
            for l in STORE["wd.it.request.line"].values()
        }
        assert labels == {"Portable Standard", "Casque"}

    def test_r8_exceeded_alert(self):
        client = FakeClient()
        add_equipment(client, "Ordinateur portable", "standard")
        journal, result = ob.process_recruit(
            client,
            recruit(
                poste="Stagiaire Data Analyst",
                DateEntree=(date.today() + timedelta(days=1)
                            ).isoformat(),
            ),
            CONFIG, PLAN_CTX, dry_run=False,
        )
        assert "R8_DEPASSE" in result.get("alerts", []) or \
            "R8_DEPASSE" in str(result.get("alerts", []))

    def test_dry_run_creates_nothing(self):
        client = FakeClient()
        add_equipment(client, "Ordinateur portable", "dev")
        add_equipment(client, "Écran")
        journal, result = ob.process_recruit(
            client, recruit(), CONFIG, PLAN_CTX, dry_run=True
        )
        assert not result["blocked"]
        assert len(STORE["hr.employee"]) == 1  # manager seul
        assert not STORE["wd.it.request"]
        assert any("serait créé" in j for j in journal)

    def test_invalid_personal_email_flagged(self):
        client = FakeClient()
        add_equipment(client, "Ordinateur portable", "dev")
        add_equipment(client, "Écran")
        journal, result = ob.process_recruit(
            client,
            recruit(EmailPerso="serge.kougblenou@gmail"),
            CONFIG, PLAN_CTX, dry_run=True,
        )
        assert "EMAIL_INVALIDE" in result.get("alerts", [])
        assert any("EMAIL" in j for j in journal)

    def test_missing_manager_blocks(self):
        client = FakeClient()
        journal, result = ob.process_recruit(
            client, recruit(ManagerID="E999"),
            CONFIG, PLAN_CTX, dry_run=False,
        )
        assert result["blocked"]
        assert any("manager E999" in j for j in journal)
