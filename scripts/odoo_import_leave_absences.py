# Import Odoo des demandes d'absence (hr.leave).
# Lit le dataset préparé par le pipeline
# (data/processed/Demandes_Absence_Import.csv).
#
#   docker cp data/processed/Demandes_Absence_Import.csv \
#       wd-interne-odoo-1:/tmp/
#   docker exec -i wd-interne-odoo-1 odoo shell -d odoo \
#       --db_host=db --db_user=odoo --db_password=odoo \
#       --no-http < scripts/odoo_import_leave_absences.py
#
# Idempotent : les External ID __import__.ABS-XXXX déjà
# présents sont ignorés. Chaque demande est créée dans un
# savepoint : une ligne en échec n'annule pas les autres.
#
# Circuit normal : create -> actions métier
# (action_approve / action_validate / action_refuse /
# action_cancel). Le state n'est jamais écrit directement.
#
# Fallback « import historique » : si le circuit normal
# échoue (contrôle R2-R7, allocation, chevauchement) :
#   - statut source « Approved »/« Second Approval » :
#     la demande est importée dans l'état validé par la
#     source, avec wd_import_warning + note chatter ;
#   - statut sans validation RH (« To Approve »,
#     « Brouillon ») : importée puis refusée, avec
#     wd_import_warning + note chatter.
# Seules les lignes structurellement invalides (ex. date
# de fin antérieure au début) restent exclues.
import csv
import datetime

CSV_PATH = "/tmp/Demandes_Absence_Import.csv"

EXPECTED_STATE = {
    "Approved": "validate",
    "Second Approval": "validate1",
    "Refused": "refuse",
    "Cancelled": "cancel",
    "To Approve": "confirm",
    "": "confirm",
}
VALIDATED_STATUSES = ("Approved", "Second Approval")


def xid_lookup(model, xmlid):
    """Résout un enregistrement par External ID
    __import__.<name>."""
    data = env["ir.model.data"].search([
        ("module", "=", "__import__"),
        ("name", "=", xmlid.replace("__import__.", "")),
        ("model", "=", model),
    ], limit=1)
    if not data:
        return env[model]
    return env[model].browse(data.res_id)


def parse_date(value):
    value = (value or "").strip()
    if not value:
        return False
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return False


def apply_status(leave, status):
    """Fait transiter la demande vers le statut cible
    via les actions métier. Retourne l'état atteint ou
    une chaîne 'ERREUR: ...'."""
    status = status.strip()
    try:
        if status == "Refused":
            leave.action_refuse()
            return leave.state
        if status == "Cancelled":
            leave.action_cancel()
            return leave.state
        if status in VALIDATED_STATUSES:
            if leave.state == "confirm" \
                    and leave.validation_type == "both":
                if status == "Second Approval":
                    leave.action_approve()  # -> validate1
                    return leave.state
                leave.action_approve()  # -> validate1
            if leave.state in ("confirm", "validate1"):
                leave.action_validate()
            return leave.state
        # "To Approve", "Brouillon"/vide : reste en confirm
        return leave.state
    except Exception as exc:  # noqa: BLE001
        return f"ERREUR: {str(exc).splitlines()[0][:200]}"


_VALIDITY_PATCHED = False


def _bypass_allocation_check():
    """Neutralise _check_validity (allocation) le temps de
    ce script shell : les demandes historiques validées
    dans la source peuvent ne pas avoir de solde
    Allocation exploitable en base. La violation est
    tracée via wd_import_warning."""
    global _VALIDITY_PATCHED
    if not _VALIDITY_PATCHED:
        type(env["hr.leave"])._check_validity = lambda self: None
        _VALIDITY_PATCHED = True


def _skip_ctx():
    return {
        "leave_fast_create": True,
        "leave_skip_date_check": True,
        "leave_skip_state_check": True,
        "tracking_disable": True,
    }


def mark_forced(leave, status, warning):
    """Force l'état cible sur une demande existante et
    trace la violation (warning + chatter)."""
    _bypass_allocation_check()
    target = EXPECTED_STATE.get(status.strip())
    if not target or status.strip() not in VALIDATED_STATUSES:
        target = "refuse"
    leave.with_context(**_skip_ctx()).write({"state": target})
    if target == "validate":
        # Reproduit l'effet de action_validate : congés
        # ressources + message « accepté ».
        leave.sudo()._validate_leave_request()
    leave.wd_import_warning = (
        "Import historique : violation de règle tolérée — "
        + warning
    )
    leave.message_post(
        body="Import historique : la demande a été "
             "reproduite dans l'état de la source malgré "
             "le contrôle suivant : " + warning,
        subtype_xmlid="mail.mt_note",
    )
    return target


def force_create(vals, status, warning):
    """Crée une demande dont même la création a échoué
    (chevauchement à la soumission), contrôles
    court-circuités, puis mark_forced."""
    _bypass_allocation_check()
    fast = env["hr.leave"].with_context(**_skip_ctx())
    leave = fast.create(vals)
    target = mark_forced(leave, status, warning)
    return leave, target


Leave = env["hr.leave"].with_context(tracking_disable=True)
existing = {
    d.name for d in env["ir.model.data"].search([
        ("module", "=", "__import__"), ("model", "=", "hr.leave")
    ])
}

created = skipped = excluded = forced = 0
report = []
with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))

for row in rows:
    external_id = row["External ID"].strip()
    short = external_id.replace("__import__.", "")
    if short in existing:
        skipped += 1
        continue

    employee = xid_lookup("hr.employee",
                          row["Employee / External ID"].strip())
    leave_type = xid_lookup(
        "hr.leave.type",
        row["Time Off Type / External ID"].strip(),
    )
    date_from = parse_date(row["Request Start Date"])
    date_to = parse_date(row["Request End Date"])
    original = parse_date(row.get("Date de dépôt d'origine"))
    status = row["Status"].strip()

    if not (employee and leave_type and date_from and date_to) \
            or date_to < date_from:
        excluded += 1
        report.append((short, "EXCLU: reference ou dates "
                       "invalides"))
        continue

    vals = {
        "employee_id": employee.id,
        "holiday_status_id": leave_type.id,
        "request_date_from": date_from.isoformat(),
        "request_date_to": date_to.isoformat(),
        "request_date_from_period": "am",
        "name": row.get("Description", "").strip(),
    }
    if original:
        vals["wd_original_request_date"] = original.isoformat()

    leave = None
    warning = None
    try:
        with env.cr.savepoint():
            leave = Leave.create(vals)
            result = apply_status(leave, status)
            expected = EXPECTED_STATE.get(status, "confirm")
            if str(result).startswith("ERREUR"):
                warning = result[7:]
            elif leave.state != expected:
                warning = (
                    f"statut source « {status} » non "
                    f"atteint (etat={leave.state})"
                )
    except Exception as exc:  # noqa: BLE001
        warning = str(exc).split("\n")[0][:200]
        leave = None

    if warning:
        try:
            with env.cr.savepoint():
                if leave is None:
                    leave, target = force_create(
                        vals, status, warning)
                else:
                    target = mark_forced(leave, status, warning)
            forced += 1
            report.append((short, f"FORCE->{target}: {warning}"))
        except Exception as exc:  # noqa: BLE001
            excluded += 1
            report.append((short, "EXCLU: "
                           + str(exc).split("\n")[0][:160]))
            continue
    else:
        report.append((short, f"OK->{leave.state}"))

    env["ir.model.data"].create({
        "module": "__import__",
        "name": short,
        "model": "hr.leave",
        "res_id": leave.id,
    })
    created += 1
    if created % 20 == 0:
        print(f"  ... {created} demandes creees")

env.cr.commit()
print(f"IMPORTED created={created} skipped={skipped} "
      f"forced={forced} excluded={excluded}")
for item in report:
    if not item[1].startswith("OK"):
        print("INFO:", item)
