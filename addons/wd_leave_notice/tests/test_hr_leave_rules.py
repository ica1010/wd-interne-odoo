# -*- coding: utf-8 -*-

from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase


def _xdays(env, xmlid):
    """Type de congé par External ID __import__.*."""
    data = env["ir.model.data"].sudo().search([
        ("module", "=", "__import__"),
        ("name", "=", xmlid),
        ("model", "=", "hr.leave.type"),
    ], limit=1)
    return env["hr.leave.type"].browse(data.res_id)


class TestLeaveRulesR3R7(TransactionCase):
    """Tests des règles R3 à R7."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.cp_type = _xdays(cls.env, "CP")
        cls.ss_type = _xdays(cls.env, "SS")
        cls.mal_type = _xdays(cls.env, "MAL")
        cls.exc_mar = _xdays(cls.env, "EXC-MAR")
        cls.exc_nai = _xdays(cls.env, "EXC-NAI")
        cls.exc_dec = _xdays(cls.env, "EXC-DEC")
        cls.aa_type = _xdays(cls.env, "AA")

        # L'utilisateur de test cumule manager de congés
        # (leave_manager) et rôle RH (groupe Administrator).
        cls.env.user.groups_id = [
            (4, cls.env.ref(
                "hr_holidays.group_hr_holidays_user"
            ).id),
            (4, cls.env.ref(
                "hr_holidays.group_hr_holidays_manager"
            ).id),
        ]

        cls.manager = cls.env["hr.employee"].create({
            "name": "Manager conges test",
            "user_id": cls.env.uid,
        })

        calendar = cls.env.ref(
            "resource.resource_calendar_std",
            raise_if_not_found=False,
        )
        department = cls.env["hr.department"].create({
            "name": "Departement test regles",
        })

        def make_employee(name, intern=False):
            vals = {
                "name": name,
                "department_id": department.id,
                # leave_manager_id reference res.users (natif).
                "leave_manager_id": cls.env.uid,
            }
            if calendar:
                vals["resource_calendar_id"] = calendar.id
            employee = cls.env["hr.employee"].create(vals)
            if intern:
                stage_type = cls.env["hr.contract.type"].search([
                    ("name", "ilike", "stage"),
                ], limit=1)
                cls.env["hr.contract"].create({
                    "name": "Stage test",
                    "employee_id": employee.id,
                    "contract_type_id": stage_type.id,
                    "wage": 0,
                    "state": "open",
                    "date_start": fields.Date.today()
                    - timedelta(days=30),
                })
            return employee

        cls.employee = make_employee("Employe test regles")
        cls.intern = make_employee("Stagiaire test", intern=True)
        cls.employee2 = make_employee("Employe test 2")

        # Allocations validées pour les types exigeant un
        # solde. Pas de CP pour le stagiaire (bloqué par R6).
        today = fields.Date.today()
        allocation_matrix = {
            cls.cp_type: (cls.employee, cls.employee2),
            cls.ss_type: (cls.employee, cls.employee2,
                          cls.intern),
            cls.aa_type: (cls.employee, cls.employee2,
                          cls.intern),
        }
        for leave_type, employees in allocation_matrix.items():
            for employee in employees:
                allocation = cls.env["hr.leave.allocation"].create({
                    "name": f"Alloc {leave_type.name} {employee.name}",
                    "holiday_status_id": leave_type.id,
                    "employee_id": employee.id,
                    "number_of_days": 60,
                    "date_from": today - timedelta(days=365),
                    "date_to": today + timedelta(days=365),
                })
                allocation.write({"state": "validate"})

    # ----------------------------------------------------
    # Helpers
    # ----------------------------------------------------

    def _create_leave(self, employee=None, leave_type=None,
                      start=10, end=16, with_doc=False):
        today = fields.Date.today()
        ctx = {}
        if with_doc:
            ctx["wd_leave_skip_document_check"] = True
        return self.env["hr.leave"].with_context(**ctx).create({
            "employee_id": (employee or self.employee).id,
            "holiday_status_id": (leave_type or self.cp_type).id,
            "request_date_from": today + timedelta(days=start),
            "request_date_to": today + timedelta(days=end),
            "request_date_from_period": "am",
        })

    def _attach(self, leave):
        return self.env["ir.attachment"].create({
            "name": "justificatif.pdf",
            "res_model": "hr.leave",
            "res_id": leave.id,
        })

    # ====================================================
    # R3 : circuit de validation
    # ====================================================

    def test_r3_cp_5_days_manager_only(self):
        leave = self._create_leave(start=10, end=14)
        self.assertEqual(leave._wd_required_validation_type(), "manager")
        self.assertLessEqual(leave._r2_work_days(), 10)

    def test_r3_cp_10_days_manager_only(self):
        leave = self._create_leave(start=10, end=23)
        self.assertGreaterEqual(leave._r2_work_days(), 5)
        self.assertLessEqual(leave._r2_work_days(), 10)
        self.assertEqual(leave._wd_required_validation_type(), "manager")

    def test_r3_cp_over_10_days_both(self):
        leave = self._create_leave(start=10, end=30)
        self.assertGreater(leave._r2_work_days(), 10)
        self.assertEqual(leave._wd_required_validation_type(), "both")

    def test_r3_ss_always_both(self):
        leave = self._create_leave(
            leave_type=self.ss_type, start=10, end=12,
        )
        self.assertEqual(leave._wd_required_validation_type(), "both")

    def test_r3_exc_and_mal_hr(self):
        mal = self._create_leave(
            leave_type=self.mal_type, start=1, end=1,
            with_doc=True,
        )
        self._attach(mal)
        self.assertEqual(mal._wd_required_validation_type(), "hr")

    def test_r3_aa_manager(self):
        leave = self._create_leave(
            employee=self.intern, leave_type=self.aa_type,
            start=1, end=2,
        )
        self.assertEqual(leave._wd_required_validation_type(), "manager")

    def test_r3_second_step_requires_rh(self):
        leave = self._create_leave(start=10, end=30)
        self.assertEqual(leave.state, "confirm")
        self.assertEqual(leave._wd_required_validation_type(), "both")
        # Etape 1 : le manager (env.user est leave_manager)
        leave.action_approve()
        self.assertEqual(leave.state, "validate1")
        # Etape 2 : RH (env.user a le groupe manager)
        leave.action_validate()
        self.assertEqual(leave.state, "validate")

    def test_r3_validate_before_manager_blocked(self):
        leave = self._create_leave(start=10, end=30)
        with self.assertRaises(UserError):
            leave.action_validate()
        self.assertEqual(leave.state, "confirm")

    def test_r3_refuse_stops_circuit(self):
        leave = self._create_leave(start=10, end=30)
        leave.action_approve()
        self.assertEqual(leave.state, "validate1")
        leave.action_refuse()
        self.assertEqual(leave.state, "refuse")
        with self.assertRaises(UserError):
            leave.action_validate()

    def test_r3_first_step_requires_manager(self):
        # Un utilisateur qui n'est ni le manager ni RH
        # ne peut pas lancer la premiere etape.
        other = self.env["res.users"].create({
            "name": "User sans role",
            "login": "user_sans_role_r3",
            "email": "sans.role@example.com",
        })
        # Les nouveaux utilisateurs heritent de groupes
        # admin dans cette base : on le reduit a un
        # utilisateur interne sans role conges.
        other.groups_id = [
            (6, 0, [self.env.ref("base.group_user").id]),
        ]
        leave = self._create_leave(start=10, end=30)
        with self.assertRaises(AccessError):
            leave.with_user(other).action_approve()

    # ====================================================
    # R4 : certificat medical
    # ====================================================

    def test_r4_mal_without_document_refused(self):
        with self.assertRaises(ValidationError):
            self._create_leave(
                leave_type=self.mal_type, start=1, end=1,
            )

    def test_r4_mal_with_document_allowed(self):
        leave = self._create_leave(
            leave_type=self.mal_type, start=1, end=1,
            with_doc=True,
        )
        self._attach(leave)
        self.assertEqual(leave.state, "confirm")

    def test_r4_document_cannot_be_removed(self):
        today = fields.Date.today()
        monday = today + timedelta(
            days=(7 - today.weekday()) % 7 or 7
        )
        start = (monday - today).days
        leave = self._create_leave(
            leave_type=self.mal_type, start=start, end=start,
            with_doc=True,
        )
        attachment = self._attach(leave)
        leave.action_approve()  # validation RH directe
        self.assertEqual(leave.state, "validate")
        with self.assertRaises(ValidationError):
            attachment.unlink()

    # ====================================================
    # R5 : conges exceptionnels
    # ====================================================

    def test_r5_exc_mar_4_days_ok(self):
        # Prochain lundi -> vendredi+1 = 4 jours ouvres
        # piles si l'on prend lun-jeu.
        today = fields.Date.today()
        monday = today + timedelta(
            days=(7 - today.weekday()) % 7 or 7
        )
        start = (monday - today).days
        leave = self._create_leave(
            leave_type=self.exc_mar, start=start, end=start + 3,
            with_doc=True,
        )
        self._attach(leave)
        self.assertEqual(leave._wd_required_validation_type(), "hr")

    def test_r5_exc_wrong_duration_refused(self):
        with self.assertRaises(ValidationError):
            # EXC-MAR sur 5 jours calendaires ~= 5 ouvres
            self._create_leave(
                leave_type=self.exc_mar, start=10, end=16,
                with_doc=True,
            )

    def _exc_3day_window(self):
        """Fenêtre de 3 jours ouvrés ancrée sur le prochain
        lundi (insensible au jour d'exécution)."""
        today = fields.Date.today()
        monday = today + timedelta(
            days=(7 - today.weekday()) % 7 or 7
        )
        start = (monday - today).days
        return start, start + 2

    def test_r5_exc_nai_3_days_ok(self):
        start, end = self._exc_3day_window()
        leave = self._create_leave(
            leave_type=self.exc_nai, start=start, end=end,
            with_doc=True,
        )
        self._attach(leave)
        self.assertEqual(leave._r2_work_days(), 3)

    def test_r5_exc_dec_3_days_ok(self):
        start, end = self._exc_3day_window()
        leave = self._create_leave(
            leave_type=self.exc_dec, start=start, end=end,
            with_doc=True,
        )
        self._attach(leave)
        self.assertEqual(leave._r2_work_days(), 3)

    def test_r5_exc_without_document_refused(self):
        with self.assertRaises(ValidationError):
            self._create_leave(
                leave_type=self.exc_mar, start=10, end=13,
            )

    # ====================================================
    # R6 : stagiaires
    # ====================================================

    def test_r6_intern_cannot_request_cp(self):
        with self.assertRaises(ValidationError):
            self._create_leave(employee=self.intern)

    def test_r6_intern_cannot_get_cp_allocation(self):
        with self.assertRaises(ValidationError):
            self.env["hr.leave.allocation"].create({
                "name": "Alloc CP stagiaire",
                "holiday_status_id": self.cp_type.id,
                "employee_id": self.intern.id,
                "number_of_days": 10,
                "date_from": fields.Date.today(),
                "date_to": fields.Date.today()
                + timedelta(days=365),
            })

    def test_r6_aa_2_days_allowed(self):
        # 2 jours ouvres dans le mois courant.
        today = fields.Date.today()
        monday = today + timedelta(
            days=(7 - today.weekday()) % 7 or 7
        )
        leave = self._create_leave(
            employee=self.intern, leave_type=self.aa_type,
            start=(monday - today).days,
            end=(monday - today).days + 1,
        )
        self.assertEqual(leave.state, "confirm")
        self.assertEqual(leave._wd_required_validation_type(), "manager")

    def test_r6_aa_monthly_cap_cumulative(self):
        today = fields.Date.today()
        monday = today + timedelta(
            days=(7 - today.weekday()) % 7 or 7
        )
        start = (monday - today).days
        # Premiere demande : 2 jours ouvres.
        self._create_leave(
            employee=self.intern, leave_type=self.aa_type,
            start=start, end=start + 1,
        )
        # Seconde demande : 1 jour de plus dans le meme mois
        # -> cumul 3 > 2 -> refus.
        with self.assertRaises(ValidationError):
            self._create_leave(
                employee=self.intern, leave_type=self.aa_type,
                start=start + 2, end=start + 2,
            )

    def test_r6_non_intern_unaffected(self):
        leave = self._create_leave(start=10, end=12)
        self.assertEqual(leave.state, "confirm")

    # ====================================================
    # R7 : chevauchements
    # ====================================================

    def test_r7_overlap_same_employee_refused(self):
        self._create_leave(start=10, end=14)
        with self.assertRaises(ValidationError):
            self._create_leave(start=12, end=16)

    def test_r7_adjacent_leaves_allowed(self):
        self._create_leave(start=10, end=14)
        # Demande qui commence le lendemain de la fin.
        leave2 = self._create_leave(start=15, end=17)
        self.assertEqual(leave2.state, "confirm")

    def test_r7_different_employees_allowed(self):
        self._create_leave(start=10, end=14)
        leave2 = self._create_leave(
            employee=self.employee2, start=10, end=14,
        )
        self.assertEqual(leave2.state, "confirm")

    def test_r7_write_creating_overlap_refused(self):
        leave1 = self._create_leave(start=10, end=14)
        leave2 = self._create_leave(start=20, end=22)
        with self.assertRaises(ValidationError):
            leave2.write({
                "request_date_from":
                    fields.Date.today() + timedelta(days=12),
                "request_date_to":
                    fields.Date.today() + timedelta(days=13),
            })
        self.assertTrue(leave1.exists())
