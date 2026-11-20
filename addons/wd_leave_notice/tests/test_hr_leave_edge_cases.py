# -*- coding: utf-8 -*-

"""Tests complementaires : cas limites, droits, non-regression.

Ciblent les lignes defensives et les scenarios exiges par le
cahier des charges qui n'etaient pas couverts par
test_hr_leave_rules.py.
"""

from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase

from .test_hr_leave_rules import _xdays


class TestLeaveEdgeCases(TransactionCase):
    """Couverture des cas limites R2-R7."""

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

        # Utilisateur de test : manager de conges + RH.
        cls.env.user.groups_id = [
            (4, cls.env.ref(
                "hr_holidays.group_hr_holidays_user").id),
            (4, cls.env.ref(
                "hr_holidays.group_hr_holidays_manager").id),
        ]

        cls.manager = cls.env["hr.employee"].create({
            "name": "Manager edge test",
            "user_id": cls.env.uid,
        })

        # Approbateur sans role RH : le groupe « User » lit et
        # ecrit toutes les demandes sans etre administrateur.
        cls.approver = cls.env["res.users"].create({
            "name": "Approbateur sans RH",
            "login": "approbateur_sans_rh",
            "email": "approbateur@example.com",
            "groups_id": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref(
                    "hr_holidays.group_hr_holidays_user").id,
            ])],
        })

        calendar = cls.env.ref(
            "resource.resource_calendar_std",
            raise_if_not_found=False,
        )
        department = cls.env["hr.department"].create({
            "name": "Departement edge test",
        })

        def make_employee(name, intern=False, with_calendar=True):
            vals = {
                "name": name,
                "department_id": department.id,
                "leave_manager_id": cls.env.uid,
            }
            if with_calendar and calendar:
                vals["resource_calendar_id"] = calendar.id
            employee = cls.env["hr.employee"].create(vals)
            if intern:
                cls._make_intern(employee)
            return employee

        cls.employee = make_employee("Employe edge")
        cls.employee2 = make_employee("Employe edge 2")
        cls.intern = make_employee("Stagiaire edge", intern=True)
        cls.no_cal = make_employee(
            "Employe sans calendrier", with_calendar=False,
        )

        today = fields.Date.today()
        matrix = {
            cls.cp_type: (cls.employee, cls.employee2,
                          cls.no_cal),
            cls.ss_type: (cls.employee, cls.employee2,
                          cls.intern),
            cls.aa_type: (cls.employee, cls.employee2,
                          cls.intern),
        }
        for leave_type, employees in matrix.items():
            for employee in employees:
                allocation = cls.env["hr.leave.allocation"].create({
                    "name": f"Alloc edge {leave_type.name}",
                    "holiday_status_id": leave_type.id,
                    "employee_id": employee.id,
                    "number_of_days": 60,
                    "date_from": today - timedelta(days=365),
                    "date_to": today + timedelta(days=365),
                })
                allocation.write({"state": "validate"})

    @classmethod
    def _make_intern(cls, employee):
        stage_type = cls.env["hr.contract.type"].search([
            ("name", "ilike", "stage"),
        ], limit=1)
        cls.env["hr.contract"].create({
            "name": "Stage edge",
            "employee_id": employee.id,
            "contract_type_id": stage_type.id,
            "wage": 0,
            "state": "open",
            "date_start": fields.Date.today()
            - timedelta(days=30),
        })

    # ----------------------------------------------------
    # Helpers
    # ----------------------------------------------------

    def _create_leave(self, employee=None, leave_type=None,
                      start=10, end=16, with_doc=False,
                      context=None):
        today = fields.Date.today()
        ctx = dict(context or {})
        if with_doc:
            ctx["wd_leave_skip_document_check"] = True
        return self.env["hr.leave"].with_context(**ctx).create({
            "employee_id": (employee or self.employee).id,
            "holiday_status_id": (leave_type or self.cp_type).id,
            "request_date_from": today + timedelta(days=start),
            "request_date_to": today + timedelta(days=end),
            "request_date_from_period": "am",
        })

    def _attach(self, leave, name="justificatif.pdf"):
        return self.env["ir.attachment"].create({
            "name": name,
            "res_model": "hr.leave",
            "res_id": leave.id,
        })

    def _cp_allocation(self, employee=None):
        return self.env["hr.leave.allocation"].search([
            ("employee_id", "=",
             (employee or self.employee).id),
            ("holiday_status_id", "=", self.cp_type.id),
        ], limit=1)

    # ====================================================
    # R2 : cas complementaires
    # ====================================================

    def test_r2_employee_change_rechecks(self):
        # Modifier l'employe ET les dates d'une demande CP
        # re declenche R2.
        leave = self._create_leave(start=10, end=20)
        with self.assertRaises(ValidationError):
            leave.write({
                "employee_id": self.employee2.id,
                "request_date_from":
                    fields.Date.today() + timedelta(days=2),
                "request_date_to":
                    fields.Date.today() + timedelta(days=12),
            })

    def test_r2_refused_leave_not_rechecked(self):
        leave = self._create_leave(start=10, end=12)
        leave.action_refuse()
        self.assertEqual(leave.state, "refuse")
        # Une ecriture ulterieure ne doit pas leve R2.
        leave.write({
            "request_date_from": fields.Date.today(),
        })

    def test_r2_cp_type_fallback_by_name(self):
        # External ID inconnu -> repli sur le nom « Conge paye ».
        param = self.env["ir.config_parameter"].sudo()
        param.set_param(
            "wd_leave_notice.cp_leave_type_xmlid",
            "__import__.INEXISTANT",
        )
        with self.assertRaises(ValidationError):
            # 5 j ouvres, delai < 10 j -> R2 doit quand meme
            # s'appliquer via le repli par nom.
            self._create_leave(start=2, end=8)

    def test_r2_no_calendar_employee(self):
        # Employe sans calendrier propre : hr.leave
        # retombe sur le calendrier de la societe -> R2
        # s'applique normalement (comportement natif).
        with self.assertRaises(ValidationError):
            self._create_leave(
                employee=self.no_cal, start=2, end=8,
            )

    # ====================================================
    # R3 : droits et circuit
    # ====================================================

    def test_r3_approver_cannot_do_step1_both(self):
        # Utilisateur « Time Off User » (lit/ecrit tout, pas
        # RH, pas manager) : refuse a l'etape 1 d'un both.
        leave = self._create_leave(start=10, end=30)
        with self.assertRaises(AccessError):
            leave.with_user(self.approver).action_approve()

    def test_r3_approver_cannot_do_step1_manager(self):
        # CP <= 10 j : validation manager uniquement.
        leave = self._create_leave(start=10, end=14)
        with self.assertRaises(AccessError):
            leave.with_user(self.approver).action_approve()

    def test_r3_approver_cannot_do_hr(self):
        # MAL : validation RH uniquement.
        leave = self._create_leave(
            leave_type=self.mal_type, start=1, end=1,
            with_doc=True,
        )
        self._attach(leave)
        with self.assertRaises(AccessError):
            leave.with_user(self.approver).action_approve()

    def test_r3_approver_cannot_do_step2(self):
        # Apres etape 1 manager, un non-RH ne peut pas
        # faire l'etape 2.
        leave = self._create_leave(start=10, end=30)
        leave.action_approve()
        self.assertEqual(leave.state, "validate1")
        with self.assertRaises(AccessError):
            leave.with_user(self.approver).action_validate()
        # Puis la RH (env.user) valide.
        leave.action_validate()
        self.assertEqual(leave.state, "validate")

    def test_r3_manager_refuse_blocks_circuit(self):
        leave = self._create_leave(start=10, end=30)
        leave.action_refuse()
        self.assertEqual(leave.state, "refuse")
        with self.assertRaises(UserError):
            leave.action_validate()

    def test_r3_rh_refuse_after_manager(self):
        leave = self._create_leave(start=10, end=30)
        leave.action_approve()
        self.assertEqual(leave.state, "validate1")
        leave.action_refuse()
        self.assertEqual(leave.state, "refuse")

    def test_r3_history_tracked(self):
        # Les transitions d'etat sont tracees dans le chatter.
        leave = self._create_leave(start=10, end=30)
        leave.action_approve()
        leave.action_validate()
        # Historique conserve : le chatter trace les actions.
        self.assertTrue(leave.message_ids)

    def test_r3_unscoped_type_uses_native(self):
        # Type hors perimetre R3-R7 : circuit natif conserve.
        custom = self.env["hr.leave.type"].create({
            "name": "Type hors perimetre test",
            "leave_validation_type": "hr",
            "requires_allocation": "no",
        })
        leave = self.env["hr.leave"].create({
            "employee_id": self.employee.id,
            "holiday_status_id": custom.id,
            "request_date_from":
                fields.Date.today() + timedelta(days=10),
            "request_date_to":
                fields.Date.today() + timedelta(days=12),
        })
        # Le controle R3 ne s'applique pas : la validation
        # suit la config native du type.
        self.assertEqual(
            leave._wd_required_validation_type(), "hr",
        )
        leave.action_approve()
        self.assertEqual(leave.state, "validate")

    def test_r3_mixed_recordset_approve(self):
        # action_approve sur un recordset mixte : both +
        # manager simple dans le meme appel.
        leave_both = self._create_leave(start=10, end=30)
        leave_mgr = self._create_leave(
            employee=self.employee2, start=10, end=14,
        )
        (leave_both | leave_mgr).action_approve()
        self.assertEqual(leave_both.state, "validate1")
        self.assertEqual(leave_mgr.state, "validate")

    # ====================================================
    # R4 : complement MAL
    # ====================================================

    def test_r4_attachment_blocked_after_submission(self):
        # Soumission sans certificat -> refus ; avec
        # certificat -> OK ; suppression avant validation
        # -> bloquee.
        leave = self._create_leave(
            leave_type=self.mal_type, start=1, end=1,
            with_doc=True,
        )
        attachment = self._attach(leave)
        with self.assertRaises(ValidationError):
            attachment.unlink()

    def test_r4_cp_balance_unchanged(self):
        allocation = self._cp_allocation()
        before = allocation.leaves_taken
        today = fields.Date.today()
        monday = today + timedelta(
            days=(7 - today.weekday()) % 7 or 7
        )
        start = (monday - today).days
        leave = self._create_leave(
            leave_type=self.mal_type, start=start, end=start,
            with_doc=True,
        )
        self._attach(leave)
        leave.action_approve()
        self.assertEqual(leave.state, "validate")
        self.assertEqual(allocation.leaves_taken, before)

    # ====================================================
    # R5 : complement exceptionnels
    # ====================================================

    def test_r5_exc_nai_wrong_duration(self):
        with self.assertRaises(ValidationError):
            self._create_leave(
                leave_type=self.exc_nai, start=10, end=15,
                with_doc=True,
            )

    def test_r5_exc_dec_wrong_duration(self):
        with self.assertRaises(ValidationError):
            self._create_leave(
                leave_type=self.exc_dec, start=10, end=11,
                with_doc=True,
            )

    def test_r5_attachment_blocked_after_submission(self):
        # EXC-MAR = 4 jours ouvres, week-end compris dans la
        # periode : dim.->jeu = 4 jours travailles.
        today = fields.Date.today()
        monday = today + timedelta(
            days=(7 - today.weekday()) % 7 or 7
        )
        start = (monday - today).days - 1  # dimanche
        leave = self._create_leave(
            leave_type=self.exc_mar, start=start, end=start + 4,
            with_doc=True,
        )
        self.assertEqual(leave._r2_work_days(), 4)
        attachment = self._attach(leave)
        with self.assertRaises(ValidationError):
            attachment.unlink()

    def test_r5_exc_mar_spanning_weekend(self):
        # 4 jours ouvres calcules sur une periode incluant
        # le week-end : dim.->jeu = 4 jours travailles.
        today = fields.Date.today()
        monday = today + timedelta(
            days=(7 - today.weekday()) % 7 or 7
        )
        start = (monday - today).days - 1
        leave = self._create_leave(
            leave_type=self.exc_mar, start=start, end=start + 4,
            with_doc=True,
        )
        self._attach(leave)
        self.assertEqual(leave.state, "confirm")

    # ====================================================
    # R6 : complement stagiaires
    # ====================================================

    def test_r6_intern_with_existing_cp_allocation(self):
        # Stagiaire ayant DEJA une allocation CP (cas
        # legacy) : la demande CP est bloquee par R6, pas
        # par le controle natif de solde.
        employee = self.env["hr.employee"].create({
            "name": "Stagiaire avec CP",
            "department_id": self.employee.department_id.id,
            "resource_calendar_id":
                self.employee.resource_calendar_id.id,
        })
        today = fields.Date.today()
        allocation = self.env["hr.leave.allocation"].create({
            "name": "Alloc CP legacy",
            "holiday_status_id": self.cp_type.id,
            "employee_id": employee.id,
            "number_of_days": 10,
            "date_from": today - timedelta(days=365),
            "date_to": today + timedelta(days=365),
        })
        allocation.write({"state": "validate"})
        self._make_intern(employee)
        with self.assertRaises(ValidationError) as cm:
            self.env["hr.leave"].create({
                "employee_id": employee.id,
                "holiday_status_id": self.cp_type.id,
                "request_date_from": today + timedelta(days=10),
                "request_date_to": today + timedelta(days=12),
            })
        self.assertIn("R6", str(cm.exception))

    def test_r6_aa_single_day_allowed(self):
        today = fields.Date.today()
        monday = today + timedelta(
            days=(7 - today.weekday()) % 7 or 7
        )
        start = (monday - today).days
        leave = self._create_leave(
            employee=self.intern, leave_type=self.aa_type,
            start=start, end=start,
        )
        self.assertEqual(leave.state, "confirm")

    def test_r6_aa_two_months_cap_per_month(self):
        # Demande a cheval sur deux mois calendaire : le
        # plafond est evalue par mois. On construit les dates
        # dynamiquement pour rester robuste.
        today = fields.Date.today()
        calendar = self.intern.resource_calendar_id

        def is_workday(day):
            return calendar.get_work_duration_data(
                fields.Datetime.to_datetime(day),
                fields.Datetime.to_datetime(
                    day + timedelta(days=1)),
            )["days"] > 0

        # Premier jour du mois prochain.
        next_month = (
            today.replace(day=28) + timedelta(days=7)
        ).replace(day=1)
        # Dernier jour ouvre du mois courant.
        anchor = next_month - timedelta(days=1)
        while not is_workday(anchor):
            anchor -= timedelta(days=1)
        # Premier jour ouvre du mois prochain.
        first_next = next_month
        while not is_workday(first_next):
            first_next += timedelta(days=1)
        # Deuxieme jour ouvre du mois prochain.
        second_next = first_next + timedelta(days=1)
        while not is_workday(second_next):
            second_next += timedelta(days=1)

        # Demande AA existante, entierement dans le mois
        # prochain (jour isole, sans chevauchement).
        other = self._create_leave(
            employee=self.intern, leave_type=self.aa_type,
            start=(second_next - today).days,
            end=(second_next - today).days,
        )
        self.assertEqual(other.state, "confirm")

        # Demande a cheval sur les deux mois : le plafond
        # est evalue par mois calendaire.
        start = (anchor - today).days
        end = (first_next - today).days
        leave = self._create_leave(
            employee=self.intern, leave_type=self.aa_type,
            start=start, end=end,
        )
        self.assertEqual(leave.state, "confirm")

    def test_r6_aa_refused_frees_quota(self):
        today = fields.Date.today()
        monday = today + timedelta(
            days=(7 - today.weekday()) % 7 or 7
        )
        start = (monday - today).days
        leave = self._create_leave(
            employee=self.intern, leave_type=self.aa_type,
            start=start, end=start + 1,
        )
        leave.action_refuse()
        # Le cumul ignore les demandes refusees : une autre
        # demande de 2 jours passe.
        leave2 = self._create_leave(
            employee=self.intern, leave_type=self.aa_type,
            start=start + 7, end=start + 8,
        )
        self.assertEqual(leave2.state, "confirm")

    def test_r6_aa_write_rechecks_cap(self):
        today = fields.Date.today()
        monday = today + timedelta(
            days=(7 - today.weekday()) % 7 or 7
        )
        start = (monday - today).days
        self._create_leave(
            employee=self.intern, leave_type=self.aa_type,
            start=start, end=start + 1,
        )
        leave2 = self._create_leave(
            employee=self.intern, leave_type=self.aa_type,
            start=start + 14, end=start + 14,
        )
        # Deplacer leave2 dans le meme mois (dates adjacentes,
        # sans chevauchement) fait depasser le plafond AA.
        with self.assertRaises(ValidationError) as cm:
            leave2.write({
                "request_date_from":
                    fields.Date.today() + timedelta(days=start + 2),
                "request_date_to":
                    fields.Date.today() + timedelta(days=start + 3),
            })
        self.assertIn("R6", str(cm.exception))

    def test_r6_allocation_write_blocks_cp(self):
        # Changer le type d'une allocation existante vers CP
        # pour un stagiaire est bloque.
        ss_alloc = self.env["hr.leave.allocation"].create({
            "name": "Alloc SS stagiaire write",
            "holiday_status_id": self.ss_type.id,
            "employee_id": self.intern.id,
            "number_of_days": 5,
            "date_from": fields.Date.today(),
            "date_to": fields.Date.today() + timedelta(days=30),
        })
        with self.assertRaises(ValidationError):
            ss_alloc.write({
                "holiday_status_id": self.cp_type.id,
            })

        # Une allocation refusee n'est pas re-controlee.
        refused = self.env["hr.leave.allocation"].create({
            "name": "Alloc refusee",
            "holiday_status_id": self.ss_type.id,
            "employee_id": self.intern.id,
            "number_of_days": 5,
            "date_from": fields.Date.today(),
            "date_to": fields.Date.today() + timedelta(days=30),
        })
        refused.write({"state": "refuse"})
        refused.write({
            "holiday_status_id": self.cp_type.id,
        })

    def test_r6_aa_requires_manager(self):
        # AA : circuit manager ; l'approbateur non RH et non
        # manager est refuse.
        today = fields.Date.today()
        monday = today + timedelta(
            days=(7 - today.weekday()) % 7 or 7
        )
        leave = self._create_leave(
            employee=self.intern, leave_type=self.aa_type,
            start=(monday - today).days,
            end=(monday - today).days + 1,
        )
        with self.assertRaises(AccessError):
            leave.with_user(self.approver).action_approve()

    # ====================================================
    # R7 : complement chevauchements
    # ====================================================

    def test_r7_overlap_with_skip_date_check(self):
        # Le controle natif _check_date respecte le contexte
        # leave_skip_date_check ; notre controle R7 reste
        # actif : defense en profondeur.
        self._create_leave(start=10, end=14)
        with self.assertRaises(ValidationError):
            self._create_leave(
                start=12, end=16,
                context={"leave_skip_date_check": True},
            )

    def test_r7_overlap_ignores_refused(self):
        leave1 = self._create_leave(start=10, end=14)
        leave2 = self._create_leave(
            employee=self.employee2, start=20, end=22,
        )
        leave2.action_refuse()
        # Une demande refusee ne bloque pas : leave1 peut
        # etre annulee puis recreee sur les memes dates.
        leave1.action_refuse()
        leave3 = self._create_leave(start=10, end=14)
        self.assertEqual(leave3.state, "confirm")

    def test_r7_validated_overlap_blocked(self):
        # Une demande validee compte aussi dans le cumul.
        leave1 = self._create_leave(start=10, end=14)
        leave1.action_approve()
        self.assertEqual(leave1.state, "validate")
        with self.assertRaises(ValidationError):
            self._create_leave(start=12, end=16)

    def test_r7_multi_year_span(self):
        # Demande sur plus d'un an : pas de chevauchement,
        # creation acceptee (MAL : pas d'allocation requise).
        leave = self._create_leave(
            leave_type=self.mal_type, start=10, end=400,
            with_doc=True,
        )
        self.assertEqual(leave.state, "confirm")

    # ====================================================
    # Pieces jointes : cas limites ir.attachment
    # ====================================================

    def test_attachment_other_model_unaffected(self):
        partner = self.env["res.partner"].create({
            "name": "Partenaire test",
        })
        attachment = self.env["ir.attachment"].create({
            "name": "doc.pdf",
            "res_model": "res.partner",
            "res_id": partner.id,
        })
        attachment.unlink()  # ne doit rien lever

    def test_attachment_on_non_doc_leave(self):
        # Justificatif sur un CP : aucune regle R4/R5.
        leave = self._create_leave(start=10, end=12)
        attachment = self._attach(leave)
        leave.action_approve()
        attachment.unlink()

    def test_attachment_reparent_blocked(self):
        # Deplacer la seule piece jointe d'une demande MAL
        # validee vers un autre enregistrement est bloque.
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
        leave.action_approve()
        partner = self.env["res.partner"].create({
            "name": "Autre enregistrement",
        })
        with self.assertRaises(ValidationError):
            attachment.write({"res_id": partner.id})

    # ====================================================
    # Methodes utilitaires / sync
    # ====================================================

    def test_leave_type_unknown_xmlid(self):
        self.assertFalse(
            self.env["hr.leave"]._wd_leave_type("INEXISTANT")
        )

    def test_leave_xmlid_empty_type(self):
        self.assertEqual(
            self.env["hr.leave"]._wd_leave_xmlid(
                self.env["hr.leave.type"]
            ),
            "",
        )

    def test_sync_validation_types_writes(self):
        # Remettre SS en 'hr' puis relancer la sync : le
        # module doit le repasser a 'both'.
        ss = self.ss_type
        ss.leave_validation_type = "hr"
        self.env["hr.leave.type"]._wd_sync_validation_types()
        self.assertEqual(ss.leave_validation_type, "both")

    def test_attachment_on_refused_leave(self):
        # Demande MAL refusee : le justificatif peut etre
        # supprime (la regle ne vise que les demandes
        # soumises ou validees).
        leave = self._create_leave(
            leave_type=self.mal_type, start=1, end=1,
            with_doc=True,
        )
        attachment = self._attach(leave)
        leave.action_refuse()
        attachment.unlink()

    def test_r6_intern_refused_write_rechecks(self):
        # Une demande AA de stagiaire refusee : les ecritures
        # ulterieures ne relevent plus le controle R6.
        today = fields.Date.today()
        monday = today + timedelta(
            days=(7 - today.weekday()) % 7 or 7
        )
        start = (monday - today).days
        leave = self._create_leave(
            employee=self.intern, leave_type=self.aa_type,
            start=start, end=start + 1,
        )
        leave.action_refuse()
        leave.write({
            "request_date_from":
                fields.Date.today() + timedelta(days=start + 7),
            "request_date_to":
                fields.Date.today() + timedelta(days=start + 8),
        })

    def test_r7_refused_leave_write_dates(self):
        # Ecrire des dates sur une demande refusee ne leve
        # ni R7 ni les autres controles.
        leave = self._create_leave(
            employee=self.employee2, start=20, end=22,
        )
        self._create_leave(start=10, end=14)
        leave.action_refuse()
        leave.write({
            "request_date_from":
                fields.Date.today() + timedelta(days=11),
            "request_date_to":
                fields.Date.today() + timedelta(days=13),
        })
