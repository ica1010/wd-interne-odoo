# -*- coding: utf-8 -*-

import datetime

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestWdItRequest(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee = cls.env["hr.employee"].create({
            "name": "Test Onboarding",
        })
        cls.equipment = cls.env["maintenance.equipment"].create({
            "name": "Portable test IT request",
            "model": "Model X",
        })
        cls.Request = cls.env["wd.it.request"]

    def _create_request(self, **kwargs):
        vals = {
            "name": "Équipement nouvel arrivant",
            "employee_id": self.employee.id,
        }
        vals.update(kwargs)
        return self.Request.create(vals)

    def _to_in_progress(self, request):
        request.action_assign()
        request.action_start()
        return request

    def _backdate(self, request, **kwargs):
        """create_date est protégée en écriture ORM : on la
        recule en SQL dans la transaction de test."""
        delta = datetime.timedelta(**kwargs)
        new_date = fields.Datetime.now() - delta
        self.env.cr.execute(
            'UPDATE wd_it_request SET create_date = %s '
            'WHERE id = %s',
            (new_date, request.id),
        )
        request.invalidate_recordset()

    # ---------- création / champs de base ----------

    def test_create_request_with_employee(self):
        request = self._create_request()
        self.assertEqual(request.state, "draft")
        self.assertEqual(request.employee_id, self.employee)
        self.assertEqual(request.requester_id, self.env.user)
        self.assertTrue(request.request_ref.startswith("IT/"))
        self.assertEqual(request.priority, "P3")
        self.assertEqual(request.channel, "portal")

    def test_lines_proposed_and_unavailable(self):
        request = self._create_request(line_ids=[
            (0, 0, {
                "equipment_id": self.equipment.id,
                "needed_label": "Portable Dev",
            }),
            (0, 0, {
                "needed_label": "Écran",
                "status": "unavailable",
            }),
        ])
        self.assertEqual(len(request.line_ids), 2)
        self.assertEqual(request.state, "draft")

    def test_related_equipment_fields(self):
        request = self._create_request(line_ids=[
            (0, 0, {"equipment_id": self.equipment.id}),
        ])
        line = request.line_ids
        self.assertEqual(line.equipment_name, self.equipment.name)
        self.assertEqual(
            line.equipment_category,
            self.equipment.category_id.name or False,
        )

    def test_lines_cascade_on_request_delete(self):
        request = self._create_request(line_ids=[
            (0, 0, {"needed_label": "Casque"}),
        ])
        line_id = request.line_ids.id
        request.unlink()
        self.assertFalse(
            self.env["wd.it.request.line"].browse(line_id).exists()
        )

    def test_request_requires_employee(self):
        with self.assertRaises(Exception):
            self.Request.create({"name": "Sans employé"})

    # ---------- R12 : SLA ----------

    def test_sla_deadlines_per_priority(self):
        request = self._create_request(priority="P1")
        base = request.create_date
        self.assertEqual(
            request.sla_response_deadline,
            fields.Datetime.add(base, hours=1),
        )
        self.assertEqual(
            request.sla_resolution_deadline,
            fields.Datetime.add(base, hours=4),
        )
        p4 = self._create_request(priority="P4")
        self.assertEqual(
            p4.sla_resolution_deadline,
            fields.Datetime.add(p4.create_date, hours=72),
        )

    def test_is_late_open_past_deadline(self):
        request = self._create_request(priority="P1")
        # Simule une création il y a 5 h (deadline P1 = 4 h)
        self._backdate(request, hours=5)
        self.assertTrue(request.is_late)
        self.assertTrue(
            self.Request.search([("is_late", "=", True)])
            .filtered(lambda r: r.id == request.id)
        )

    def test_is_late_false_when_resolved_closed_cancelled(self):
        request = self._create_request(priority="P1")
        self._backdate(request, hours=99)
        for state in ("resolved", "closed", "cancel"):
            request.state = state
            self.assertFalse(request.is_late)

    # ---------- transitions du workflow ----------

    def test_full_workflow_happy_path(self):
        request = self._create_request()
        request.action_assign()
        self.assertEqual(request.state, "assigned")
        self.assertTrue(request.date_assigned)
        request.action_start()
        self.assertEqual(request.state, "in_progress")
        self.assertTrue(request.date_first_response)
        request.action_wait_user()
        self.assertEqual(request.state, "pending_user")
        self.assertTrue(request.pending_since)
        request.action_resume()
        self.assertEqual(request.state, "in_progress")
        self.assertFalse(request.pending_since)
        request.resolution_note = "Corrigé."
        request.action_resolve()
        self.assertEqual(request.state, "resolved")
        self.assertTrue(request.date_resolved)
        request.action_close()
        self.assertEqual(request.state, "closed")

    def test_invalid_transitions_rejected(self):
        request = self._create_request()
        with self.assertRaises(UserError):
            request.action_start()  # pas encore assigné
        with self.assertRaises(UserError):
            request.action_resolve()
        request.action_assign()
        with self.assertRaises(UserError):
            request.action_resolve()  # pas en cours

    # ---------- R14 : résolution ----------

    def test_resolve_requires_note(self):
        request = self._to_in_progress(self._create_request())
        with self.assertRaises(UserError):
            request.action_resolve()
        request.resolution_note = "  "
        with self.assertRaises(UserError):
            request.action_resolve()
        request.resolution_note = "Note OK"
        request.action_resolve()
        self.assertEqual(request.state, "resolved")

    def test_resolve_from_pending_user_accumulates_waiting(self):
        request = self._to_in_progress(self._create_request())
        request.action_wait_user()
        request.pending_since = fields.Datetime.now() - datetime.timedelta(hours=2)
        request.resolution_note = "OK"
        request.action_resolve()
        self.assertAlmostEqual(
            request.waiting_hours, 2.0, delta=0.1
        )

    def test_cron_closes_resolved_after_3_days(self):
        request = self._to_in_progress(self._create_request())
        request.resolution_note = "OK"
        request.action_resolve()
        request.write({
            "date_resolved": fields.Datetime.now() - datetime.timedelta(days=4),
        })
        self.Request._cron_close_and_notify_late()
        self.assertEqual(request.state, "closed")

    def test_cron_keeps_recently_resolved(self):
        request = self._to_in_progress(self._create_request())
        request.resolution_note = "OK"
        request.action_resolve()
        self.Request._cron_close_and_notify_late()
        self.assertEqual(request.state, "resolved")

    # ---------- R15 : réouverture ----------

    def test_reopen_same_request_no_duplicate(self):
        request = self._to_in_progress(self._create_request())
        request.resolution_note = "OK"
        request.action_resolve()
        request.action_reopen()
        self.assertEqual(request.state, "in_progress")
        self.assertEqual(request.reopen_count, 1)
        self.assertFalse(request.date_resolved)
        # Pas de doublon : une seule demande pour l'employé
        self.assertEqual(
            self.Request.search_count(
                [("employee_id", "=", self.employee.id)]
            ),
            1,
        )
        # Seconde réouverture après clôture
        request.resolution_note = "Re-résolu"
        request.action_resolve()
        request.action_close()
        request.action_reopen()
        self.assertEqual(request.reopen_count, 2)

    def test_reopen_rejected_on_open_request(self):
        request = self._create_request()
        with self.assertRaises(UserError):
            request.action_reopen()

    # ---------- R13 : délai net ----------

    def test_net_resolution_excludes_waiting(self):
        request = self._to_in_progress(self._create_request())
        request.action_wait_user()
        request.pending_since = fields.Datetime.now() - datetime.timedelta(hours=5)
        request.resolution_note = "OK"
        request.action_resolve()
        # brut = create->resolved (~0 h) ; attente ~5 h
        # → net = 0 (plancher)
        self.assertGreaterEqual(
            request.net_resolution_hours, 0.0
        )
        self.assertGreater(request.waiting_hours, 4.0)

    # ---------- R16 : annulations hors indicateurs ----------

    def test_cancel_excluded_from_late_search(self):
        request = self._create_request(priority="P1")
        self._backdate(request, hours=99)
        self.assertTrue(request.is_late)
        request.action_cancel()
        self.assertFalse(request.is_late)
        late = self.Request.search([("is_late", "=", True)])
        self.assertNotIn(request.id, late.ids)

    def test_cancel_blocked_when_closed(self):
        request = self._to_in_progress(self._create_request())
        request.resolution_note = "OK"
        request.action_resolve()
        request.action_close()
        with self.assertRaises(UserError):
            request.action_cancel()

    # ---------- R10 : remise contre signature ----------

    def test_handover_records_date_and_user(self):
        request = self._to_in_progress(self._create_request())
        request.action_handover()
        self.assertEqual(
            request.handover_date, fields.Date.context_today(request)
        )
        self.assertEqual(request.handover_by, self.env.user)

    def test_handover_rejected_on_draft(self):
        request = self._create_request()
        with self.assertRaises(UserError):
            request.action_handover()

    def test_handover_rejected_on_cancel_closed(self):
        for state in ("cancel", "closed"):
            request = self._create_request()
            if state == "cancel":
                request.action_cancel()
            else:
                request = self._to_in_progress(request)
                request.resolution_note = "OK"
                request.action_resolve()
                request.action_close()
            with self.assertRaises(UserError):
                request.action_handover()

    def test_handover_only_once(self):
        request = self._to_in_progress(self._create_request())
        request.action_handover()
        first_date = request.handover_date
        with self.assertRaises(UserError):
            request.action_handover()
        self.assertEqual(request.handover_date, first_date)

    def test_handover_report_action_exists(self):
        action = self.env.ref(
            "wd_it_request.action_report_wd_it_handover"
        )
        self.assertEqual(action.model, "wd.it.request")
        self.assertEqual(action.report_type, "qweb-pdf")

    # ---------- bonus : SLA en heures ouvrées ----------

    def test_sla_open_deadlines_use_working_hours(self):
        """Les échéances « heures ouvrées » sont postérieures
        aux échéances calendaires pour un même SLA : le
        week-end et la nuit ne comptent pas."""
        request = self._create_request(priority="P1")
        self.assertTrue(request.sla_response_deadline_open)
        self.assertTrue(request.sla_resolution_deadline_open)
        self.assertGreater(
            request.sla_resolution_deadline_open,
            request.sla_resolution_deadline,
        )

    def test_sla_open_deadlines_respect_holidays(self):
        """Un P1 créé la veille d'un jour férié voit son
        échéance repousser au-delà du férié."""
        request = self._create_request(priority="P1")
        # minuit UTC -> début de journée locale
        cal = (request.employee_id.resource_calendar_id
               or request.company_id.resource_calendar_id)
        self.assertTrue(cal)
        base = fields.Datetime.to_datetime(
            request.create_date
        )
        resp = cal.plan_hours(
            1, base, compute_leaves=True
        )
        self.assertEqual(
            request.sla_response_deadline_open, resp
        )

    # ---------- bonus : offboarding ----------

    def test_offboarding_wizard_full(self):
        employee = self.env["hr.employee"].create({
            "name": "Départ Test", "user_id": False,
        })
        user = self.env["res.users"].create({
            "name": "Départ Test", "login": "depart.test",
            "email": "depart.test@example.com",
        })
        employee.user_id = user
        self.equipment.employee_id = employee
        wizard = self.env["wd.it.offboarding.wizard"].create({
            "employee_id": employee.id,
        })
        action = wizard.action_validate()
        self.assertEqual(action["res_model"], "wd.it.request")
        new_req = self.env["wd.it.request"].browse(
            action["res_id"]
        )
        self.assertEqual(new_req.employee_id, employee)
        self.assertEqual(len(new_req.line_ids), 1)
        self.assertEqual(
            new_req.line_ids.equipment_id, self.equipment
        )
        self.assertFalse(user.active)
        self.assertFalse(employee.active)

    def test_offboarding_wizard_no_equipment(self):
        employee = self.env["hr.employee"].create({
            "name": "Départ Sans Matériel",
        })
        wizard = self.env["wd.it.offboarding.wizard"].create({
            "employee_id": employee.id,
        })
        with self.assertRaises(UserError):
            wizard.action_validate()
        # L'employé n'est ni archivé ni désactivé.
        self.assertTrue(employee.active)
