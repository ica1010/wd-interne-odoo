# Import Odoo des demandes IT historiques.
# Lit le dataset préparé par le pipeline
# (data/processed/Demandes_IT_Import.csv).
#
#   docker cp data/processed/Demandes_IT_Import.csv \
#       wd-interne-odoo-1:/tmp/
#   docker exec -i wd-interne-odoo-1 odoo shell -d odoo \
#       --db_host=db --db_user=odoo --db_password=odoo \
#       --no-http < scripts/odoo_import_it_history.py
#
# Idempotent : les request_ref déjà présents sont ignorés.
# create_date est ajusté en SQL (champ protégé par l'ORM).
import csv
import datetime

CSV_PATH = "/tmp/Demandes_IT_Import.csv"


def parse_dt(value):
    value = (value or "").strip()
    if not value:
        return False
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(value, fmt)
        except ValueError:
            continue
    return False


# ---- Employés et techniciens via les external IDs __import__ ----
employee_by_code = {}
user_by_code = {}
for data in env["ir.model.data"].search(
    [("module", "=", "__import__"), ("model", "=", "hr.employee")]
):
    emp = env["hr.employee"].browse(data.res_id)
    if emp.exists():
        employee_by_code[data.name] = emp
        if emp.user_id:
            user_by_code[data.name] = emp.user_id

Request = env["wd.it.request"].with_context(tracking_disable=True)
existing = set(Request.search([]).mapped("request_ref"))

created = skipped = errors = 0
error_rows = []
with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))

for row in rows:
    ref = row["request_ref"].strip()
    if ref in existing:
        skipped += 1
        continue

    demandeur = employee_by_code.get(row["employee_code"].strip())
    if not demandeur:
        errors += 1
        error_rows.append((ref, "employe inconnu",
                           row["employee_code"]))
        continue

    tech_code = row.get("technician_code", "").strip()
    tech_user = user_by_code.get(tech_code)

    create_dt = parse_dt(row.get("create_date"))
    first_resp = parse_dt(row.get("date_first_response"))
    resolved = parse_dt(row.get("date_resolved"))

    vals = {
        "request_ref": ref,
        "name": row["name"].strip(),
        "employee_id": demandeur.id,
        "requester_id": (
            demandeur.user_id.id if demandeur.user_id else False
        ),
        "priority": row["priority"].strip(),
        "category": row["category"].strip(),
        "channel": row["channel"].strip(),
        "state": row["state"].strip(),
        "technician_id": tech_user.id if tech_user else False,
        "request_date": (
            create_dt.date() if create_dt else False
        ),
        "date_first_response": first_resp or False,
        "date_resolved": resolved or False,
        "date_assigned": first_resp or False,
        "waiting_seconds": int(float(
            row.get("waiting_seconds") or 0
        )),
        "reopen_count": int(float(row.get("reopen_count") or 0)),
        "resolution_note": "",
        "description": row.get("description", ""),
    }
    rec = Request.create(vals)
    if create_dt:
        env.cr.execute(
            "UPDATE wd_it_request SET create_date = %s "
            "WHERE id = %s",
            (create_dt.strftime("%Y-%m-%d %H:%M:%S"), rec.id),
        )
    created += 1
    if created % 50 == 0:
        print(f"  ... {created} demandes creees")

env.cr.commit()
print(f"IMPORTED created={created} skipped={skipped} "
      f"errors={errors}")
for item in error_rows:
    print("ERREUR:", item)
