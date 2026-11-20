# -*- coding: utf-8 -*-

from odoo import fields, models


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    def _wd_active_contracts(self):
        self.ensure_one()
        return self.contract_ids.filtered(
            lambda c: c.state in ("open", "draft")
        )

    def _wd_is_accrual_eligible(self):
        """
        R1 : CDI ou CDD en poste, hors stagiaires.
        Un contrat « Stage » actif rend le salarié
        inéligible même si d'autres contrats existent.
        """
        self.ensure_one()
        if not self.active:
            return False
        contracts = self._wd_active_contracts()
        if not contracts:
            return False
        type_names = [
            (c.contract_type_id.name or "").lower()
            for c in contracts
        ]
        if any("stage" in name for name in type_names):
            return False
        return any(
            "cdi" in name or "cdd" in name for name in type_names
        )

    def _wd_cp_accrual_plan(self):
        return self.env.ref(
            "wd_leave_accrual.plan_cp_25_monthly",
            raise_if_not_found=False,
        )

    def _wd_cp_leave_type(self):
        return self.env.ref(
            "__import__.CP", raise_if_not_found=False
        )

    def _wd_ensure_cp_accrual(self):
        """
        Crée l'allocation accrual R1 de l'employé si éligible
        et pas déjà rattachée au plan. Idempotent.
        """
        plan = self._wd_cp_accrual_plan()
        cp_type = self._wd_cp_leave_type()
        if not plan or not cp_type:
            return self.env["hr.leave.allocation"]

        Allocation = self.env["hr.leave.allocation"]
        created = Allocation.browse()
        for employee in self:
            if not employee._wd_is_accrual_eligible():
                continue
            existing = Allocation.search(
                [
                    ("employee_id", "=", employee.id),
                    ("accrual_plan_id", "=", plan.id),
                    ("allocation_type", "=", "accrual"),
                    ("state", "not in", ("cancel", "refuse")),
                ],
                limit=1,
            )
            if existing:
                continue
            contracts = employee._wd_active_contracts()
            date_from = min(
                contracts.mapped("date_start")
            ) if contracts else fields.Date.today()
            allocation = Allocation.create({
                "name": "Acquisition CP 2,5 j/mois (R1)",
                "employee_id": employee.id,
                "holiday_status_id": cp_type.id,
                "allocation_type": "accrual",
                "accrual_plan_id": plan.id,
                "date_from": date_from,
            })
            if allocation.state == "confirm":
                allocation.action_validate()
            created |= allocation
        return created
