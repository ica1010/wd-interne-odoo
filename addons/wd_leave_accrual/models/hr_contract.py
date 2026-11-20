# -*- coding: utf-8 -*-

from odoo import api, models


class HrContract(models.Model):
    _inherit = "hr.contract"

    @api.model_create_multi
    def create(self, vals_list):
        contracts = super().create(vals_list)
        contracts.mapped("employee_id")._wd_ensure_cp_accrual()
        return contracts

    def write(self, vals):
        result = super().write(vals)
        if {"contract_type_id", "state", "employee_id",
                "date_start"} & set(vals):
            self.mapped("employee_id")._wd_ensure_cp_accrual()
        return result
