# -*- coding: utf-8 -*-

from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase


class TestLeaveNoticeR2(TransactionCase):
    """Tests de la règle R2 : délai de prévenance des congés payés."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # Type « Congé payé » : External ID Odoo __import__.CP
        # s'il existe (données West Digital), sinon un type de
        # test est créé et enregistré sous un xmlid dédié.
        cp_type = cls.env.ref("__import__.CP", raise_if_not_found=False)
        if not cp_type:
            cp_type = cls.env["hr.leave.type"].create({
                "name": "Congé payé",
                "request_unit": "day",
                "leave_validation_type": "manager",
            })
            cls.env["ir.model.data"].create({
                "module": "wd_leave_notice",
                "name": "test_leave_type_cp",
                "model": "hr.leave.type",
                "res_id": cp_type.id,
            })
            cls.env["ir.config_parameter"].sudo().set_param(
                "wd_leave_notice.cp_leave_type_xmlid",
                "wd_leave_notice.test_leave_type_cp",
            )
        cls.cp_type = cp_type

        cls.other_type = cls.env["hr.leave.type"].create({
            "name": "Congé maladie R2",
            "request_unit": "day",
            "leave_validation_type": "manager",
        })

        calendar = cls.env.ref(
            "resource.resource_calendar_std",
            raise_if_not_found=False,
        )
        department = cls.env["hr.department"].create({
            "name": "Departement test R2",
        })
        employee_vals = {
            "name": "Employe test R2",
            "department_id": department.id,
        }
        if calendar:
            employee_vals["resource_calendar_id"] = calendar.id
        cls.employee = cls.env["hr.employee"].create(employee_vals)

        # Allocations validées : hr.leave.create exige un solde
        # disponible pour le type de congé demandé.
        today = fields.Date.today()
        for leave_type in (cls.cp_type, cls.other_type):
            allocation = cls.env["hr.leave.allocation"].create({
                "name": f"Allocation test {leave_type.name}",
                "holiday_status_id": leave_type.id,
                "employee_id": cls.employee.id,
                "number_of_days": 60,
                "date_from": today - timedelta(days=365),
                "date_to": today + timedelta(days=365),
            })
            # state est readonly : l'approbation passe par write.
            allocation.write({"state": "validate"})

    # ----------------------------------------------------
    # Helpers
    # ----------------------------------------------------

    def _today(self):
        return fields.Date.today()

    def _create_leave(self, start_offset, end_offset, leave_type=None):
        today = self._today()
        return self.env["hr.leave"].create({
            "employee_id": self.employee.id,
            "holiday_status_id": (leave_type or self.cp_type).id,
            "request_date_from": today + timedelta(days=start_offset),
            "request_date_to": today + timedelta(days=end_offset),
            "request_date_from_period": "am",
        })

    def _next_weekday(self, weekday):
        """Prochain jour de la semaine donné (0=lundi)."""
        today = self._today()
        delta = (weekday - today.weekday()) % 7 or 7
        return today + timedelta(days=delta)

    # ----------------------------------------------------
    # Cas du cahier des charges
    # ----------------------------------------------------

    def test_04_workdays_short_notice_allowed(self):
        """4 jours ouvrés, délai < 10 j : autorisé par R2."""
        monday = self._next_weekday(0)
        delay = (monday - self._today()).days
        self.assertLess(delay, 10)
        # lundi → jeudi = 4 jours ouvrés
        leave = self._create_leave(
            (monday - self._today()).days,
            (monday - self._today()).days + 3,
        )
        self.assertEqual(leave.state, "confirm")

    def test_05_workdays_09_days_refused(self):
        """5 jours ouvrés, délai de 9 jours : refusé."""
        with self.assertRaises(ValidationError):
            # plage de 7 jours calendaires : toujours >= 5 ouvrés
            self._create_leave(9, 15)

    def test_05_workdays_10_days_allowed(self):
        """5 jours ouvrés, délai de 10 jours : autorisé."""
        leave = self._create_leave(10, 16)
        self.assertEqual(leave.state, "confirm")

    def test_06_workdays_10_days_allowed(self):
        """6 jours ouvrés, délai de 10 jours : autorisé."""
        leave = self._create_leave(10, 19)
        self.assertEqual(leave.state, "confirm")
        self.assertGreaterEqual(leave._r2_work_days(), 5)

    def test_weekend_not_counted_as_workdays(self):
        """Jeu–lun = 3 jours ouvrés (week-end exclu) : R2 non
        déclenchée même avec un délai < 10 jours."""
        thursday = self._next_weekday(3)
        delay = (thursday - self._today()).days
        self.assertLess(delay, 10)
        # jeudi → lundi suivant = 3 jours ouvrés
        start = (thursday - self._today()).days
        leave = self._create_leave(start, start + 4)
        self.assertLess(leave._r2_work_days(), 5)
        self.assertEqual(leave.state, "confirm")

    def test_public_holiday_reduces_workdays(self):
        """Lundi férié : lun–ven = 4 jours ouvrés, R2 non
        déclenchée avec délai < 10 jours."""
        monday = self._next_weekday(0)
        start = (monday - self._today()).days

        self.env["resource.calendar.leaves"].create({
            "name": "Jour ferié test R2",
            "calendar_id": self.employee.resource_calendar_id.id,
            "date_from": fields.Datetime.to_datetime(monday),
            "date_to": fields.Datetime.to_datetime(
                monday + timedelta(days=1)
            ),
            "time_type": "leave",
        })

        # lundi → vendredi = 5 jours calendaires ouvrés,
        # mais 4 jours ouvrés une fois le férié déduit.
        leave = self._create_leave(start, start + 4)
        self.assertLess(leave._r2_work_days(), 5)
        self.assertEqual(leave.state, "confirm")

    def test_other_leave_type_not_concerned(self):
        """Un autre type de congé n'est pas bloqué par R2."""
        leave = self._create_leave(1, 8, leave_type=self.other_type)
        self.assertEqual(leave.state, "confirm")

    # ----------------------------------------------------
    # Anti-contournement : modification après soumission
    # ----------------------------------------------------

    def test_write_dates_rechecks_rule(self):
        """Modifier les dates d'une demande conforme vers une
        durée >= 5 j ouvrés avec délai < 10 j est bloqué."""
        monday = self._next_weekday(0)
        start = (monday - self._today()).days
        # 4 jours ouvrés, délai < 10 : conforme à la création
        leave = self._create_leave(start, start + 3)

        # Extension à vendredi (5 jours ouvrés) : doit être refusée
        with self.assertRaises(ValidationError):
            leave.write({
                "request_date_to": monday + timedelta(days=4),
            })

    def test_write_compliant_dates_allowed(self):
        """Une modification respectant R2 reste possible."""
        monday = self._next_weekday(0)
        start = (monday - self._today()).days
        leave = self._create_leave(start, start + 3)
        leave.write({
            "request_date_to": monday + timedelta(days=3),
        })
        self.assertEqual(leave.state, "confirm")

    # ----------------------------------------------------
    # Import historique : date de dépôt d'origine (R2)
    # ----------------------------------------------------

    def test_r2_uses_original_request_date(self):
        """Demande importée : R2 se base sur la date de
        dépôt d'origine, pas sur create_date."""
        monday = self._next_weekday(0)
        start = (monday - self._today()).days
        # délai < 10 j -> R2 échouerait sur create_date,
        # mais la date d'origine est ancienne : autorisé.
        leave = self.env["hr.leave"].create({
            "employee_id": self.employee.id,
            "holiday_status_id": self.cp_type.id,
            "request_date_from": monday,
            "request_date_to": monday + timedelta(days=4),
            "request_date_from_period": "am",
            "wd_original_request_date": (
                self._today() - timedelta(days=30)
            ),
        })
        self.assertEqual(leave.state, "confirm")
        self.assertGreaterEqual(leave._r2_work_days(), 5)

    def test_r2_original_date_still_enforced(self):
        """Date d'origine récente : R2 reste bloquante."""
        monday = self._next_weekday(0)
        start = (monday - self._today()).days
        with self.assertRaises(ValidationError):
            self.env["hr.leave"].create({
                "employee_id": self.employee.id,
                "holiday_status_id": self.cp_type.id,
                "request_date_from": monday,
                "request_date_to": monday + timedelta(days=4),
                "request_date_from_period": "am",
                "wd_original_request_date": self._today(),
            })

    def test_r4_document_exempt_for_imported(self):
        """R4/R5 : pas de pièce jointe exigée pour une
        demande importée (date d'origine renseignée)."""
        mal_type = self.env.ref(
            "__import__.MAL", raise_if_not_found=False
        )
        if not mal_type:
            self.skipTest("Type MAL absent")
        monday = self._next_weekday(0)
        start = (monday - self._today()).days
        leave = self.env["hr.leave"].create({
            "employee_id": self.employee.id,
            "holiday_status_id": mal_type.id,
            "request_date_from": monday,
            "request_date_to": monday + timedelta(days=2),
            "request_date_from_period": "am",
            "wd_original_request_date": (
                self._today() - timedelta(days=30)
            ),
        })
        self.assertTrue(leave.id)

    def test_r4_document_required_without_original_date(self):
        """R4/R5 : sans date d'origine, la pièce jointe
        reste obligatoire pour le type MAL."""
        mal_type = self.env.ref(
            "__import__.MAL", raise_if_not_found=False
        )
        if not mal_type:
            self.skipTest("Type MAL absent")
        monday = self._next_weekday(0)
        start = (monday - self._today()).days
        with self.assertRaises(ValidationError):
            self.env["hr.leave"].create({
                "employee_id": self.employee.id,
                "holiday_status_id": mal_type.id,
                "request_date_from": monday,
                "request_date_to": monday + timedelta(days=2),
                "request_date_from_period": "am",
            })
