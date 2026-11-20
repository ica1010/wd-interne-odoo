# -*- coding: utf-8 -*-

from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError

# Champs dont la modification peut changer l'issue du
# controle R2 : dates du conge, employe et type de conge.
R2_WATCHED_FIELDS = {
    "date_from",
    "date_to",
    "request_date_from",
    "request_date_to",
    "request_date_from_period",
    "request_unit_half",
    "request_unit_hours",
    "holiday_status_id",
    "employee_id",
}

R2_MIN_WORK_DAYS = 5
R2_MIN_NOTICE_DAYS = 10

# Types de conge identifies par leur External ID Odoo
# (module __import__). Jamais par identifiant numerique.
LEAVE_TYPE_CP = "CP"
LEAVE_TYPE_SS = "SS"
LEAVE_TYPE_MAL = "MAL"
LEAVE_TYPE_AA = "AA"
LEAVE_TYPES_EXC = ("EXC-MAR", "EXC-NAI", "EXC-DEC")
LEAVE_TYPES_DOCUMENT = (LEAVE_TYPE_MAL,) + LEAVE_TYPES_EXC
LEAVE_TYPES_SCOPED = (
    LEAVE_TYPE_CP, LEAVE_TYPE_SS, LEAVE_TYPE_MAL,
    LEAVE_TYPE_AA,
) + LEAVE_TYPES_EXC

# Duree legale en jours ouvres des conges exceptionnels.
EXC_WORK_DAYS = {
    "EXC-MAR": 4,
    "EXC-NAI": 3,
    "EXC-DEC": 3,
}

# Plafond stagiaires : jours d'absence autorisee par mois.
INTERN_AA_MAX_DAYS_PER_MONTH = 2

# Seuil CP au-dela duquel le circuit passe a double
# validation (manager puis RH).
CP_DOUBLE_VALIDATION_DAYS = 10

# Etats dans lesquels une demande compte pour les controles
# de cumul et de chevauchement.
ACTIVE_LEAVE_STATES = ("confirm", "validate1", "validate")


class HrLeave(models.Model):
    _inherit = "hr.leave"

    wd_original_request_date = fields.Date(
        string="Date de dépôt d'origine",
        readonly=True,
        help="Date de dépôt historique, renseignée uniquement "
        "pour les demandes importées. Le contrôle R2 l'utilise "
        "à la place de create_date ; les justificatifs R4/R5 "
        "ne sont pas exigés pour ces demandes importées.",
    )

    wd_import_warning = fields.Text(
        string="Alerte import historique",
        readonly=True,
        copy=False,
        help="Violation de règle de gestion détectée lors de "
        "l'import historique : la demande a été importée dans "
        "l'état validé par la source, malgré le contrôle.",
    )

    # --------------------------------------------------------
    # Identification du type « Congé payé »
    # --------------------------------------------------------

    @api.model
    def _r2_cp_leave_type(self):
        """
        Retourne le type de congé « Congé payé ».

        Identification par External ID Odoo (paramètre
        wd_leave_notice.cp_leave_type_xmlid, défaut
        __import__.CP) — jamais par l'identifiant numérique
        en base. Repli sur le nom exact si l'External ID est
        introuvable.
        """
        xmlid = self.env["ir.config_parameter"].sudo().get_param(
            "wd_leave_notice.cp_leave_type_xmlid", "__import__.CP"
        )
        leave_type = self.env.ref(xmlid, raise_if_not_found=False)
        if leave_type:
            return leave_type
        return self.env["hr.leave.type"].search(
            [("name", "=", "Congé payé")], limit=1
        )

    # --------------------------------------------------------
    # Calcul de la durée en jours ouvrés
    # --------------------------------------------------------

    def _r2_work_days(self):
        """
        Durée du congé en jours ouvrés, calculée par le
        calendrier de travail de l'employé (jours non
        travaillés et jours fériés déduits).
        """
        self.ensure_one()
        calendar = (
            self.resource_calendar_id
            or self.employee_id.resource_calendar_id
        )
        if not calendar or not self.date_from or not self.date_to:
            return 0.0
        data = calendar.get_work_duration_data(
            self.date_from, self.date_to
        )
        return float(data.get("days", 0.0))

    # --------------------------------------------------------
    # Règle R2
    # --------------------------------------------------------

    def _check_notice_delay_r2(self):
        """
        Applique la règle R2 à chaque demande concernée.

        Congé payé d'au moins 5 jours ouvrés : le début du
        congé doit être au moins 10 jours calendaires après
        la date de soumission de la demande (create_date).
        """
        cp_type = self._r2_cp_leave_type()
        if not cp_type:
            return

        # Après un write, date_from/date_to (champs calculés
        # stockés) peuvent être en attente de recalcul :
        # on force le flush pour évaluer les dates réelles.
        self.env.flush_all()

        for leave in self:
            if leave.state in ("refuse", "cancel"):
                continue
            if leave.holiday_status_id != cp_type:
                continue

            work_days = leave._r2_work_days()
            if work_days < R2_MIN_WORK_DAYS:
                continue

            # Demandes importées : la date de dépôt
            # d'origine fait foi ; sinon create_date.
            submission = fields.Date.to_date(
                leave.wd_original_request_date or leave.create_date
            )
            if not submission or not leave.request_date_from:
                continue

            earliest = submission + timedelta(days=R2_MIN_NOTICE_DAYS)
            if leave.request_date_from < earliest:
                raise ValidationError(_(
                    "Règle R2 : une demande de congé payé de "
                    "%(days).2f jour(s) ouvré(s) doit être "
                    "déposée au moins %(notice)d jours "
                    "calendaires avant son début.\n"
                    "Début demandé : %(start)s — début le plus "
                    "tôt possible : %(earliest)s.",
                    days=work_days,
                    notice=R2_MIN_NOTICE_DAYS,
                    start=leave.request_date_from,
                    earliest=earliest,
                ))

    # =========================================================
    # Regles R3 a R7
    # =========================================================

    # --------------------------------------------------------
    # Identification des types par External ID
    # --------------------------------------------------------

    @api.model
    def _wd_leave_xmlid(self, leave_type):
        """External ID __import__.* d'un type de conge, sinon ''."""
        if not leave_type:
            return ""
        data = self.env["ir.model.data"].sudo().search([
            ("model", "=", "hr.leave.type"),
            ("module", "=", "__import__"),
            ("res_id", "=", leave_type.id),
        ], limit=1)
        return data.name if data else ""

    # --------------------------------------------------------
    # R3 : circuit de validation
    # --------------------------------------------------------
    #
    # hr.leave.validation_type est un champ related vers
    # hr.leave.type.leave_validation_type : il ne peut pas
    # etre rendu dependant de la demande sans casser le
    # mecanisme natif. La strategie retenue :
    # - les types a circuit fixe sont alignes sur la config
    #   native (data/hr_leave_type.xml : SS -> both,
    #   AA -> manager ; MAL/EXC deja 'hr', CP deja 'manager') ;
    # - le seul circuit variable, CP <=/> 10 jours ouvres,
    #   est gere par les overrides action_approve /
    #   action_validate ci-dessous, qui reproduisent la
    #   transition native validate1 puis exigent les RH.

    def _wd_is_scoped(self):
        """La demande porte sur un type gere par R3-R7."""
        self.ensure_one()
        return self._wd_leave_xmlid(
            self.holiday_status_id
        ) in LEAVE_TYPES_SCOPED

    def _wd_required_validation_type(self):
        """
        Circuit de validation exige par les regles R3/R5/R6.

        CP   : <= 10 jours ouvres -> manager ; > 10 -> both
        SS   : both (manager puis RH) dans tous les cas
        MAL  : hr (RH)
        EXC-* : hr (RH)
        AA   : manager
        autre : configuration native du type de conge
        """
        self.ensure_one()
        xmlid = self._wd_leave_xmlid(self.holiday_status_id)

        if xmlid == LEAVE_TYPE_CP:
            if self._r2_work_days() > CP_DOUBLE_VALIDATION_DAYS:
                return "both"
            return "manager"
        if xmlid == LEAVE_TYPE_SS:
            return "both"
        if xmlid in LEAVE_TYPES_DOCUMENT:
            return "hr"
        if xmlid == LEAVE_TYPE_AA:
            return "manager"
        return (
            self.holiday_status_id.leave_validation_type or "hr"
        )

    def _wd_is_leave_manager(self, user=None):
        """L'utilisateur est-il le manager de conges de l'employe ?

        hr.employee.leave_manager_id reference res.users
        (champ natif Odoo 18).
        """
        self.ensure_one()
        user = user or self.env.user
        employee = self.employee_id
        manager_user = employee.leave_manager_id or (
            employee.parent_id.user_id
            if employee.parent_id else self.env["res.users"]
        )
        return manager_user == user

    def _wd_assert_r3_rights(self):
        """
        R3 : aucun utilisateur non autorise ne peut valider
        une etape. Appele avant action_approve/action_validate.
        """
        is_rh = self.env.user.has_group(
            "hr_holidays.group_hr_holidays_manager"
        )
        for leave in self:
            if not leave._wd_is_scoped():
                continue
            required = leave._wd_required_validation_type()

            if required == "both" and leave.state == "confirm":
                # Etape 1 : le manager de l'employe.
                if not leave._wd_is_leave_manager() and not is_rh:
                    raise AccessError(_(
                        "R3 : la premiere validation doit etre "
                        "effectuee par le manager de %(name)s.",
                        name=leave.employee_id.name,
                    ))
            elif required == "both" and leave.state == "validate1":
                # Etape 2 : RH uniquement, apres le manager
                # (l'etat validate1 garantit l'ordre).
                if not is_rh:
                    raise AccessError(_(
                        "R3 : la deuxieme validation doit etre "
                        "effectuee par les RH."
                    ))
            elif required == "manager" \
                    and leave.state == "confirm":
                # Etape unique manager (CP <= 10 j, AA).
                if not leave._wd_is_leave_manager() and not is_rh:
                    raise AccessError(_(
                        "R3/R6 : ce type de conge doit etre "
                        "valide par le manager de %(name)s.",
                        name=leave.employee_id.name,
                    ))
            elif required == "hr" and leave.state == "confirm":
                # Validation RH directe (MAL, EXC-*).
                if not is_rh:
                    raise AccessError(_(
                        "R3/R4/R5 : ce type de conge doit etre "
                        "valide par les RH."
                    ))

    def _wd_first_approval_step(self):
        """
        Demandes 'both' en 'confirm' : la premiere etape
        (manager) est geree ici car le type CP natif est
        configure 'manager'. On reproduit la transition
        native : state -> validate1 + first_approver_id.
        """
        return self.filtered(
            lambda leave: leave._wd_is_scoped()
            and leave.state == "confirm"
            and leave._wd_required_validation_type() == "both"
        )

    def action_approve(self, check_state=True):
        self._wd_check_supporting_document()
        self._wd_assert_r3_rights()

        step1 = self._wd_first_approval_step()
        if not step1:
            return super().action_approve(check_state=check_state)

        # Etape 1 des circuits 'both' geres par la regle.
        approver = self.env.user.employee_id
        step1.sudo().write({
            "state": "validate1",
            "first_approver_id": approver.id,
        })
        step1.activity_update()

        remaining = self - step1
        if not remaining:
            return True
        return remaining.action_approve(check_state=check_state)

    def action_validate(self, check_state=True):
        self._wd_check_supporting_document()
        # Contournement interdit : la validation RH ne peut
        # pas sauter l'etape manager sur un circuit 'both'.
        for leave in self:
            if leave.state == "confirm" \
                    and leave._wd_required_validation_type() == "both":
                raise UserError(_(
                    "R3 : la validation RH n'est possible "
                    "qu'apres la validation du manager."
                ))
        self._wd_assert_r3_rights()

        # Pour un CP > 10 jours (type natif 'manager'), la
        # seconde etape ecrirait first_approver_id = RH :
        # on preserve le manager et on trace le validateur RH.
        first_approvers = {
            leave.id: leave.first_approver_id.id
            for leave in self
        }
        result = super().action_validate(check_state=check_state)

        rh_approver = self.env.user.employee_id
        for leave in self.filtered(
            lambda l: l.state == "validate"
            and l._wd_is_scoped()
            and l._wd_required_validation_type() == "both"
            and l.validation_type != "both"
        ):
            leave.sudo().write({
                "first_approver_id": first_approvers[leave.id],
                "second_approver_id": rh_approver.id,
            })
        return result

    # --------------------------------------------------------
    # R4/R5 : justificatif obligatoire
    # --------------------------------------------------------

    def _wd_requires_document(self):
        """Types exigeant une piece jointe (certificat, acte…)."""
        self.ensure_one()
        return (
            self._wd_leave_xmlid(self.holiday_status_id)
            in LEAVE_TYPES_DOCUMENT
        )

    def _wd_check_supporting_document(self):
        for leave in self:
            if leave.state in ("refuse", "cancel"):
                continue
            # Les demandes importées (date de dépôt
            # d'origine renseignée) ne peuvent pas fournir
            # de pièce jointe historique : contrôle levé.
            if leave.wd_original_request_date:
                continue
            if leave._wd_requires_document() \
                    and not leave.attachment_ids:
                raise ValidationError(_(
                    "R4/R5 : une piece jointe (certificat "
                    "medical ou justificatif) est obligatoire "
                    "pour le type %(type)s.",
                    type=leave.holiday_status_id.name,
                ))

    # --------------------------------------------------------
    # R5 : duree legale des conges exceptionnels
    # --------------------------------------------------------

    def _wd_check_exc_duration(self):
        for leave in self:
            xid = self._wd_leave_xmlid(leave.holiday_status_id)
            if xid not in EXC_WORK_DAYS or leave.state in (
                "refuse", "cancel",
            ):
                continue
            expected = EXC_WORK_DAYS[xid]
            days = leave._r2_work_days()
            if abs(days - expected) > 0.001:
                raise ValidationError(_(
                    "R5 : le conge %(type)s doit durer "
                    "%(expected)d jours ouvres "
                    "(%(days).2f calcule(s)).",
                    type=xid,
                    expected=expected,
                    days=days,
                ))

    # --------------------------------------------------------
    # R6 : stagiaires
    # --------------------------------------------------------

    def _wd_employee_is_intern(self):
        """Stagiaire = contrat actif de type « Stage »."""
        self.ensure_one()
        return self.employee_id._wd_is_intern()

    def _wd_check_intern_rules(self):
        for leave in self:
            xid = self._wd_leave_xmlid(leave.holiday_status_id)
            if not leave._wd_employee_is_intern():
                continue
            if leave.state in ("refuse", "cancel"):
                continue
            if xid == LEAVE_TYPE_CP:
                raise ValidationError(_(
                    "R6 : un stagiaire ne peut pas obtenir de "
                    "conge paye."
                ))
            if xid == LEAVE_TYPE_AA:
                self._wd_check_aa_monthly_cap(leave)

    def _wd_local_day_bounds(self, day):
        """Minuit local -> UTC (naif) pour un jour donne."""
        tz_name = (
            self.employee_id.tz
            or self.env.user.tz
            or "UTC"
        )
        tz = pytz.timezone(tz_name)
        local = tz.localize(datetime.combine(day, time.min))
        return local.astimezone(pytz.UTC).replace(tzinfo=None)

    def _wd_months_between(self, date_from, date_to):
        """Liste des mois calendaires (1er, dernier jour)
        couverts par l'intervalle."""
        months = []
        cursor = date_from.replace(day=1)
        last = date_to.replace(day=1)
        while cursor <= last:
            if cursor.month == 12:
                nxt = cursor.replace(year=cursor.year + 1, month=1)
            else:
                nxt = cursor.replace(month=cursor.month + 1)
            months.append((
                cursor,
                nxt - timedelta(days=1),
            ))
            cursor = nxt
        return months

    def _wd_workdays_between(self, leave, day_from, day_to):
        """Jours ouvres d'une demande sur un intervalle de dates."""
        if not leave.date_from or not leave.date_to:
            return 0.0
        calendar = (
            leave.resource_calendar_id
            or leave.employee_id.resource_calendar_id
        )
        if not calendar:
            return 0.0
        start = max(
            leave.date_from,
            self._wd_local_day_bounds(day_from),
        )
        end = min(
            leave.date_to,
            self._wd_local_day_bounds(day_to + timedelta(days=1)),
        )
        if start >= end:
            return 0.0
        data = calendar.get_work_duration_data(start, end)
        return float(data.get("days", 0.0))

    def _wd_check_aa_monthly_cap(self, leave):
        """
        R6 : 2 jours d'absence autorisee maximum par mois
        (jours ouvres, cumul des demandes actives).
        """
        if not leave.request_date_from:
            return
        date_to = leave.request_date_to or leave.request_date_from

        aa_type = self._wd_leave_type(LEAVE_TYPE_AA)
        if not aa_type:
            return

        months = self._wd_months_between(
            leave.request_date_from, date_to
        )
        # Toutes les demandes AA de l'employe touchant un des
        # mois couverts (le filtrage fin se fait par mois).
        others = self.env["hr.leave"].search([
            ("employee_id", "=", leave.employee_id.id),
            ("id", "!=", leave.id),
            ("state", "in", list(ACTIVE_LEAVE_STATES)),
            ("holiday_status_id", "=", aa_type.id),
            ("request_date_from", "<=", fields.Date.to_string(
                months[-1][1])),
            ("request_date_to", ">=", fields.Date.to_string(
                months[0][0])),
        ])

        for month_start, month_end in months:
            total = self._wd_workdays_between(
                leave, month_start, month_end
            )
            for other in others:
                other_to = (
                    other.request_date_to
                    or other.request_date_from
                )
                if not other.request_date_from or not other_to:
                    continue
                overlap_start = max(month_start, other.request_date_from)
                overlap_end = min(month_end, other_to)
                if overlap_start > overlap_end:
                    continue
                total += self._wd_workdays_between(
                    other, overlap_start, overlap_end
                )
            if total > INTERN_AA_MAX_DAYS_PER_MONTH + 0.001:
                raise ValidationError(_(
                    "R6 : plafond de %(max)d jours d'absence "
                    "autorisee par mois depasse pour %(month)s "
                    "(%(total).2f jours ouvres cumules).",
                    max=INTERN_AA_MAX_DAYS_PER_MONTH,
                    month=month_start.strftime("%Y-%m"),
                    total=total,
                ))

    @api.model
    def _wd_leave_type(self, xmlid_name):
        """Type de conge correspondant a un External ID."""
        data = self.env["ir.model.data"].sudo().search([
            ("module", "=", "__import__"),
            ("name", "=", xmlid_name),
            ("model", "=", "hr.leave.type"),
        ], limit=1)
        if not data:
            return self.env["hr.leave.type"]
        return self.env["hr.leave.type"].browse(data.res_id)

    # --------------------------------------------------------
    # R7 : chevauchements
    # --------------------------------------------------------

    def _wd_check_overlaps(self):
        for leave in self:
            if leave.state in ("refuse", "cancel"):
                continue
            if not leave.date_from or not leave.date_to:
                continue
            overlapping = self.env["hr.leave"].search([
                ("employee_id", "=", leave.employee_id.id),
                ("id", "!=", leave.id),
                ("state", "in", list(ACTIVE_LEAVE_STATES)),
                ("date_from", "<", fields.Datetime.to_string(
                    leave.date_to)),
                ("date_to", ">", fields.Datetime.to_string(
                    leave.date_from)),
            ])
            if overlapping:
                raise ValidationError(_(
                    "R7 : la demande chevauche %(count)d "
                    "demande(s) existante(s) de %(name)s.",
                    count=len(overlapping),
                    name=leave.employee_id.name,
                ))

    # --------------------------------------------------------
    # Controle global R3-R7
    # --------------------------------------------------------

    def _check_wd_leave_rules(self):
        if not self.env.context.get("wd_leave_skip_document_check"):
            self._wd_check_supporting_document()
        self._wd_check_exc_duration()
        self._wd_check_intern_rules()
        self._wd_check_overlaps()

    # --------------------------------------------------------
    # Points d'accroche côté serveur
    # --------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        leaves = super().create(vals_list)
        # leave_skip_state_check : mécanisme natif utilisé
        # par Odoo (ex. découpage de congés) — on le respecte.
        if not self.env.context.get("leave_skip_state_check"):
            leaves._check_notice_delay_r2()
            leaves._check_wd_leave_rules()
        return leaves

    def write(self, values):
        result = super().write(values)
        if (
            R2_WATCHED_FIELDS & set(values)
            and not self.env.context.get("leave_skip_state_check")
        ):
            self._check_notice_delay_r2()
            self._check_wd_leave_rules()
        return result
