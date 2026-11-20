# -*- coding: utf-8 -*-

from odoo import api, models, _
from odoo.exceptions import ValidationError


class HrLeaveAllocation(models.Model):
    _inherit = "hr.leave.allocation"

    @api.model
    def _wd_check_intern_cp(self, employee, holiday_status):
        """R6 : un stagiaire ne peut pas acquérir de CP."""
        if not employee or not holiday_status:
            return
        if not employee._wd_is_intern():
            return
        xmlid = self.env["hr.leave"]._wd_leave_xmlid(
            holiday_status
        )
        if xmlid == "CP":
            raise ValidationError(_(
                "R6 : un stagiaire ne peut pas acquérir de "
                "congé payé."
            ))

    @api.model_create_multi
    def create(self, vals_list):
        allocations = super().create(vals_list)
        for allocation in allocations:
            if allocation.state in ("refuse", "cancel"):
                continue
            self._wd_check_intern_cp(
                allocation.employee_id,
                allocation.holiday_status_id,
            )
        return allocations

    def write(self, vals):
        result = super().write(vals)
        if {"employee_id", "holiday_status_id"} & set(vals):
            for allocation in self:
                if allocation.state in ("refuse", "cancel"):
                    continue
                self._wd_check_intern_cp(
                    allocation.employee_id,
                    allocation.holiday_status_id,
                )
        return result
