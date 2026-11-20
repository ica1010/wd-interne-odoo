# -*- coding: utf-8 -*-

from odoo import api, models


class HrLeaveType(models.Model):
    _inherit = "hr.leave.type"

    # Circuits de validation fixes exiges par la regle R3.
    # hr.leave.validation_type est un related sur ce champ :
    # le circuit est porte par le type de conge. Les types a
    # circuit variable (CP <=/> 10 jours ouvres) sont geres
    # par wd_leave_notice.models.hr_leave.
    WD_VALIDATION_TYPE_BY_XMLID = {
        "SS": "both",      # double validation manager puis RH
        "AA": "manager",   # validation manager uniquement
    }

    def init(self):
        """Aligne leave_validation_type sur les regles R3.

        Execute a l'installation et a chaque mise a jour du
        module (-u). Les types sont identifies par leur
        External ID __import__.* ; les types absents sont
        ignores (le module reste installable sur une base ou
        les types de conges n'ont pas encore ete importes).
        """
        super().init()
        self._wd_sync_validation_types()

    @api.model
    def _wd_sync_validation_types(self):
        xmlids = list(self.WD_VALIDATION_TYPE_BY_XMLID)
        data = self.env["ir.model.data"].sudo().search([
            ("module", "=", "__import__"),
            ("model", "=", "hr.leave.type"),
            ("name", "in", xmlids),
        ])
        for record in data:
            expected = self.WD_VALIDATION_TYPE_BY_XMLID[
                record.name
            ]
            leave_type = self.env["hr.leave.type"].browse(
                record.res_id
            )
            if leave_type.exists() \
                    and leave_type.leave_validation_type \
                    != expected:
                leave_type.leave_validation_type = expected
