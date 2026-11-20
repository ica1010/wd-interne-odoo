# -*- coding: utf-8 -*-

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestLeaveAccrual(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plan = cls.env.ref(
            "wd_leave_accrual.plan_cp_25_monthly"
        )
        cls.cdi = cls.env["hr.contract.type"].search(
            [("name", "=", "CDI")], limit=1
        ) or cls.env["hr.contract.type"].create({
            "name": "CDI",
        })
        cls.cdd = cls.env["hr.contract.type"].search(
            [("name", "=", "CDD")], limit=1
        ) or cls.env["hr.contract.type"].create({
            "name": "CDD",
        })
        cls.stage = cls.env["hr.contract.type"].search(
            [("name", "=", "Stage")], limit=1
        ) or cls.env["hr.contract.type"].create({
            "name": "Stage",
        })

    def _employee_with_contract(self, type_rec, **contract_vals):
        employee = self.env["hr.employee"].create({
            "name": "Test Accrual",
        })
        vals = {
            "name": "Contrat test",
            "employee_id": employee.id,
            "contract_type_id": type_rec.id,
            "date_start": fields.Date.today(),
            "wage": 1000,
        }
        vals.update(contract_vals)
        self.env["hr.contract"].create(vals)
        return employee

    def _accrual_allocations(self, employee):
        return self.env["hr.leave.allocation"].search([
            ("employee_id", "=", employee.id),
            ("accrual_plan_id", "=", self.plan.id),
            ("allocation_type", "=", "accrual"),
        ])

    def test_cdi_employee_gets_accrual_allocation(self):
        employee = self._employee_with_contract(self.cdi)
        allocations = self._accrual_allocations(employee)
        self.assertEqual(len(allocations), 1)
        allocation = allocations[0]
        self.assertEqual(allocation.state, "validate")
        self.assertEqual(
            allocation.holiday_status_id,
            self.env.ref("__import__.CP"),
        )

    def test_cdd_employee_gets_accrual_allocation(self):
        employee = self._employee_with_contract(
            self.cdd, date_end=fields.Date.add(
                fields.Date.today(), months=6
            )
        )
        self.assertEqual(
            len(self._accrual_allocations(employee)), 1
        )

    def test_intern_gets_no_accrual(self):
        employee = self._employee_with_contract(self.stage)
        self.assertFalse(
            self._accrual_allocations(employee)
        )
        self.assertFalse(employee._wd_is_accrual_eligible())

    def test_employee_without_contract_not_eligible(self):
        employee = self.env["hr.employee"].create({
            "name": "Sans contrat",
        })
        employee._wd_ensure_cp_accrual()
        self.assertFalse(self._accrual_allocations(employee))

    def test_idempotent_second_run(self):
        employee = self._employee_with_contract(self.cdi)
        employee._wd_ensure_cp_accrual()
        self.assertEqual(
            len(self._accrual_allocations(employee)), 1
        )

    def test_plan_level_is_25_days_monthly(self):
        level = self.plan.level_ids
        self.assertEqual(len(level), 1)
        self.assertEqual(level.added_value, 2.5)
        self.assertEqual(level.added_value_type, "day")
        self.assertEqual(level.frequency, "monthly")
