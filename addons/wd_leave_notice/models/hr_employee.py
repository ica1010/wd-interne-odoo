# -*- coding: utf-8 -*-

from odoo import models


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    def _wd_is_intern(self):
        """
        Stagiaire = contrat actif (open ou draft) dont le
        type de contrat est « Stage » (hr.contract.type).
        """
        self.ensure_one()
        return any(
            "stage" in (contract.contract_type_id.name or "")
            .lower()
            for contract in self.contract_ids
            if contract.state in ("open", "draft")
        )
