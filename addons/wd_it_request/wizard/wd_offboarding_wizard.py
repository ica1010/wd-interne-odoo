# -*- coding: utf-8 -*-

from odoo import _, fields, models
from odoo.exceptions import UserError


class WdItOffboardingWizard(models.TransientModel):
    """Bonus — processus d'offboarding :

    - archivage de la fiche employé ;
    - désactivation du compte utilisateur Odoo ;
    - création d'une demande IT « Restitution matériel »
      listant les équipements affectés à l'employé.
    """

    _name = "wd.it.offboarding.wizard"
    _description = "Assistant de départ collaborateur"

    employee_id = fields.Many2one(
        "hr.employee", string="Employé", required=True,
        default=lambda self: self.env.context.get("active_id"),
    )
    archive_employee = fields.Boolean(
        string="Archiver la fiche employé", default=True,
    )
    disable_user = fields.Boolean(
        string="Désactiver le compte Odoo", default=True,
    )
    return_material = fields.Boolean(
        string="Créer la demande de restitution matériel",
        default=True,
    )
    company_id = fields.Many2one(
        "res.company",
        default=lambda self: self.env.company,
    )

    def action_validate(self):
        self.ensure_one()
        employee = self.employee_id
        if not employee:
            raise UserError(_("Sélectionnez un employé."))

        request = self.env["wd.it.request"].browse()
        if self.return_material:
            equipment = self.env["maintenance.equipment"].search(
                [("employee_id", "=", employee.id)]
            )
            if not equipment:
                raise UserError(_(
                    "Aucun équipement n'est affecté à %s : "
                    "la demande de restitution ne peut pas "
                    "être créée.", employee.name,
                ))
            request = self.env["wd.it.request"].create({
                "name": _("Restitution matériel — %s",
                          employee.name),
                "employee_id": employee.id,
                "category": "hardware",
                "priority": "P2",
                "channel": "other",
                "description": _(
                    "Départ de %s : restitution du "
                    "matériel affecté (offboarding).",
                    employee.name,
                ),
                "line_ids": [
                    (0, 0, {
                        "equipment_id": eq.id,
                        "needed_label": eq.name,
                        "status": "assigned",
                        "note": _("À reprendre lors du "
                                  "départ"),
                    })
                    for eq in equipment
                ],
            })
            request.message_post(
                body=_("Demande créée depuis l'assistant "
                       "d'offboarding.")
            )

        if self.disable_user and employee.user_id:
            employee.user_id.active = False

        if self.archive_employee:
            employee.active = False

        if request:
            return {
                "type": "ir.actions.act_window",
                "res_model": "wd.it.request",
                "res_id": request.id,
                "view_mode": "form",
                "target": "current",
            }
        return {"type": "ir.actions.act_window_close"}
