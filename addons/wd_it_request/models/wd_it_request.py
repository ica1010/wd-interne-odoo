# -*- coding: utf-8 -*-

import datetime

from odoo import _, api, fields, models
from odoo.exceptions import UserError

# R12 — SLA en heures calendaires depuis la création.
SLA_HOURS = {
    "P1": {"response": 1, "resolution": 4},
    "P2": {"response": 2, "resolution": 8},
    "P3": {"response": 4, "resolution": 24},
    "P4": {"response": 8, "resolution": 72},
}

PRIORITY_LABELS = {
    "P1": "P1 — Bloquant pour plusieurs personnes",
    "P2": "P2 — Bloquant pour une personne",
    "P3": "P3 — Gêne, contournement possible",
    "P4": "P4 — Demande planifiable",
}

CLOSE_AFTER_DAYS = 3


class WdItRequest(models.Model):
    _name = "wd.it.request"
    _description = "Demande IT interne"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    # --- Identification ---
    request_ref = fields.Char(
        string="Référence",
        default=lambda self: self.env[
            "ir.sequence"
        ].next_by_code("wd.it.request") or "/",
        readonly=True,
        copy=False,
    )
    name = fields.Char(string="Objet", required=True)
    description = fields.Text()
    requester_id = fields.Many2one(
        "res.users",
        string="Demandeur",
        default=lambda self: self.env.user,
        readonly=True,
    )
    employee_id = fields.Many2one(
        "hr.employee", string="Employé concerné", required=True,
        index=True,
    )
    arrival_id = fields.Many2one(
        "hr.employee",
        string="Nouvel arrivant concerné",
        help="Renseigné pour les demandes « Équipement "
        "nouvel arrivant ».",
    )
    department_id = fields.Many2one(
        related="employee_id.department_id",
        string="Département",
        readonly=True,
    )
    category = fields.Selection(
        selection=[
            ("hardware", "Matériel"),
            ("software", "Logiciel"),
            ("access", "Accès / compte"),
            ("network", "Réseau"),
            ("account", "Facturation"),
            ("other", "Autre"),
        ],
        default="other",
        required=True,
    )
    priority = fields.Selection(
        selection=[
            ("P1", PRIORITY_LABELS["P1"]),
            ("P2", PRIORITY_LABELS["P2"]),
            ("P3", PRIORITY_LABELS["P3"]),
            ("P4", PRIORITY_LABELS["P4"]),
        ],
        default="P3",
        required=True,
        tracking=True,
    )
    channel = fields.Selection(
        selection=[
            ("portal", "Portail Odoo"),
            ("email", "Email"),
            ("phone", "Téléphone"),
            ("whatsapp", "WhatsApp"),
            ("other", "Autre"),
        ],
        default="portal",
        required=True,
    )
    technician_id = fields.Many2one(
        "res.users", string="Technicien assigné", tracking=True,
    )
    equipment_id = fields.Many2one(
        "maintenance.equipment", string="Équipement concerné",
    )

    # --- Cycle de vie ---
    request_date = fields.Date(
        string="Date de création",
        default=fields.Date.context_today,
    )
    state = fields.Selection(
        selection=[
            ("draft", "Nouveau"),
            ("assigned", "Assigné"),
            ("in_progress", "En cours"),
            ("pending_user", "En attente utilisateur"),
            ("resolved", "Résolu"),
            ("closed", "Clôturé"),
            ("cancel", "Annulé"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    resolution_note = fields.Text(
        string="Note de résolution",
        help="Obligatoire pour passer à « Résolu » (R14).",
    )
    reopen_count = fields.Integer(
        string="Réouvertures", default=0, readonly=True,
    )
    handover_date = fields.Date(
        string="Date de remise", readonly=True,
        help="Date de remise physique du matériel (R10).",
    )
    handover_by = fields.Many2one(
        "res.users", string="Remis par", readonly=True,
    )

    # --- SLA / délais (R12, R13) ---
    sla_response_deadline = fields.Datetime(
        string="Échéance 1re réponse", compute="_compute_sla",
    )
    sla_resolution_deadline = fields.Datetime(
        string="Échéance résolution", compute="_compute_sla",
    )
    is_late = fields.Boolean(
        string="En retard", compute="_compute_sla",
        search="_search_is_late",
    )
    date_assigned = fields.Datetime(readonly=True)
    date_first_response = fields.Datetime(readonly=True)
    date_resolved = fields.Datetime(readonly=True)
    waiting_seconds = fields.Integer(
        string="Secondes d'attente utilisateur",
        default=0, readonly=True,
    )
    pending_since = fields.Datetime(readonly=True)
    waiting_hours = fields.Float(
        string="Heures d'attente utilisateur",
        compute="_compute_waiting_hours",
        store=True,
    )
    net_resolution_hours = fields.Float(
        string="Délai net de résolution (h)",
        compute="_compute_net_resolution",
        store=True,
    )

    # --- SLA en heures ouvrées (bonus : 08h-18h,
    #     lun-ven, hors jours fériés, via le calendrier
    #     de l'employé) ---
    sla_response_deadline_open = fields.Datetime(
        string="Échéance 1re réponse (h. ouvrées)",
        compute="_compute_sla_open",
    )
    sla_resolution_deadline_open = fields.Datetime(
        string="Échéance résolution (h. ouvrées)",
        compute="_compute_sla_open",
    )
    is_late_open = fields.Boolean(
        string="En retard (h. ouvrées)", compute="_compute_sla_open",
    )

    line_ids = fields.One2many(
        "wd.it.request.line", "request_id", string="Matériel",
    )
    note = fields.Text()
    company_id = fields.Many2one(
        "res.company",
        default=lambda self: self.env.company,
    )

    @api.model_create_multi
    def create(self, vals_list):
        requests = super().create(vals_list)
        # Le demandeur suit la demande même s'elle est
        # saisie par un tiers (technicien, RH, import).
        for request in requests:
            partner = request.requester_id.partner_id
            if partner:
                request.message_subscribe(partner_ids=partner.ids)
        return requests

    # =====================================================
    # SLA (R12) et indicateur de retard
    # =====================================================
    @api.depends("priority", "create_date", "state",
                 "date_first_response", "date_resolved")
    def _compute_sla(self):
        now = fields.Datetime.now()
        for request in self:
            sla = SLA_HOURS.get(request.priority, SLA_HOURS["P3"])
            base = request.create_date or now
            request.sla_response_deadline = fields.Datetime.add(
                base, hours=sla["response"]
            )
            request.sla_resolution_deadline = fields.Datetime.add(
                base, hours=sla["resolution"]
            )
            request.is_late = request._wd_is_late(now)

    def _wd_is_late(self, now=None):
        """R12 : en retard si une échéance est dépassée.
        Les demandes annulées, résolues ou clôturées ne
        sont jamais « en retard » (R16)."""
        self.ensure_one()
        if self.state in ("resolved", "closed", "cancel"):
            return False
        now = now or fields.Datetime.now()
        if self.sla_response_deadline and not self.date_first_response:
            if now > self.sla_response_deadline:
                return True
        elif (
            self.date_first_response
            and self.sla_response_deadline
            and self.date_first_response > self.sla_response_deadline
        ):
            return True
        return bool(
            self.sla_resolution_deadline
            and now > self.sla_resolution_deadline
        )

    def _search_is_late(self, operator, value):
        # Recherche sur le booléen calculé : on parcourt les
        # demandes ouvertes (volume faible pour ce module).
        open_states = ("draft", "assigned", "in_progress",
                       "pending_user")
        records = self.search([("state", "in", open_states)])
        late_ids = records.filtered("is_late").ids
        if (operator == "=" and value) or (
            operator == "!=" and not value
        ):
            return [("id", "in", late_ids)]
        return [("id", "not in", late_ids)]

    # =====================================================
    # SLA en heures ouvrées (bonus)
    # =====================================================
    @api.depends("priority", "create_date", "state",
                 "employee_id", "date_first_response",
                 "date_resolved")
    def _compute_sla_open(self):
        """Échéances exprimées en heures ouvrées via le
        calendrier de l'employé (08h-18h, lun-ven, hors
        jours fériés — le calendrier WD intègre les
        congés publics)."""
        now = fields.Datetime.now()
        for request in self:
            calendar = (
                request.employee_id.resource_calendar_id
                or request.company_id.resource_calendar_id
            )
            base = request.create_date or now
            if not calendar:
                request.sla_response_deadline_open = False
                request.sla_resolution_deadline_open = False
                request.is_late_open = False
                continue
            sla = SLA_HOURS.get(request.priority, SLA_HOURS["P3"])
            request.sla_response_deadline_open = calendar.plan_hours(
                sla["response"], base, compute_leaves=True
            )
            request.sla_resolution_deadline_open = (
                calendar.plan_hours(
                    sla["resolution"], base, compute_leaves=True
                )
            )
            request.is_late_open = request._wd_is_late_open(now)

    def _wd_is_late_open(self, now=None):
        self.ensure_one()
        if self.state in ("resolved", "closed", "cancel"):
            return False
        now = now or fields.Datetime.now()
        if self.sla_response_deadline_open \
                and not self.date_first_response:
            if now > self.sla_response_deadline_open:
                return True
        elif (
            self.date_first_response
            and self.sla_response_deadline_open
            and self.date_first_response
            > self.sla_response_deadline_open
        ):
            return True
        return bool(
            self.sla_resolution_deadline_open
            and now > self.sla_resolution_deadline_open
        )

    # =====================================================
    # Attente utilisateur (R13)
    # =====================================================
    def _wd_enter_pending_user(self):
        self.ensure_one()
        if self.state == "pending_user":
            return
        self._wd_leave_pending_user()
        self.pending_since = fields.Datetime.now()
        self.state = "pending_user"

    def _wd_leave_pending_user(self):
        """Cumule le temps passé en attente utilisateur."""
        self.ensure_one()
        if self.pending_since:
            elapsed = (
                fields.Datetime.now() - self.pending_since
            ).total_seconds()
            self.waiting_seconds += max(0, int(elapsed))
            self.pending_since = False

    @api.depends("waiting_seconds", "pending_since")
    def _compute_waiting_hours(self):
        now = fields.Datetime.now()
        for request in self:
            seconds = request.waiting_seconds
            if request.pending_since:
                seconds += max(
                    0,
                    int((now - request.pending_since)
                        .total_seconds()),
                )
            request.waiting_hours = round(seconds / 3600.0, 2)

    @api.depends("create_date", "date_resolved", "waiting_hours")
    def _compute_net_resolution(self):
        for request in self:
            if not request.date_resolved or not request.create_date:
                request.net_resolution_hours = 0.0
                continue
            gross = (
                request.date_resolved - request.create_date
            ).total_seconds() / 3600.0
            request.net_resolution_hours = round(
                max(0.0, gross - request.waiting_hours), 2
            )

    # =====================================================
    # Cycle de vie — transitions
    # =====================================================
    def _wd_mark_first_response(self):
        for request in self:
            if not request.date_first_response:
                request.date_first_response = (
                    fields.Datetime.now()
                )

    def action_assign(self):
        """Nouveau → Assigné."""
        for request in self:
            if request.state != "draft":
                raise UserError(_(
                    "Seule une demande « Nouveau » peut être "
                    "assignée (%s).", request.request_ref
                ))
            request.date_assigned = fields.Datetime.now()
            request.state = "assigned"
        return True

    def action_start(self):
        """Assigné → En cours (première action technician)."""
        for request in self:
            if request.state != "assigned":
                raise UserError(_(
                    "La demande %s doit être « Assigné » pour "
                    "passer « En cours ».", request.request_ref
                ))
            request._wd_mark_first_response()
            request.state = "in_progress"
        return True

    def action_wait_user(self):
        """En cours → En attente utilisateur (R13)."""
        for request in self:
            if request.state != "in_progress":
                raise UserError(_(
                    "La demande %s doit être « En cours ».",
                    request.request_ref,
                ))
            request._wd_mark_first_response()
            request._wd_enter_pending_user()
        return True

    def action_resume(self):
        """En attente utilisateur → En cours."""
        for request in self:
            if request.state != "pending_user":
                raise UserError(_(
                    "La demande %s n'est pas en attente "
                    "utilisateur.", request.request_ref,
                ))
            request._wd_leave_pending_user()
            request.state = "in_progress"
        return True

    def action_resolve(self):
        """→ Résolu. R14 : note de résolution obligatoire."""
        for request in self:
            if request.state not in (
                "in_progress", "pending_user"
            ):
                raise UserError(_(
                    "La demande %s doit être « En cours » ou "
                    "« En attente utilisateur ».",
                    request.request_ref,
                ))
            if not (request.resolution_note or "").strip():
                raise UserError(_(
                    "Note de résolution obligatoire pour "
                    "résoudre la demande %s (R14).",
                    request.request_ref,
                ))
            if request.state == "pending_user":
                request._wd_leave_pending_user()
            request._wd_mark_first_response()
            request.date_resolved = fields.Datetime.now()
            request.state = "resolved"
        return True

    def action_close(self):
        """Résolu → Clôturé (manuellement ou via cron R14)."""
        for request in self:
            if request.state != "resolved":
                raise UserError(_(
                    "Seule une demande « Résolu » peut être "
                    "clôturée (%s).", request.request_ref
                ))
            request.state = "closed"
        return True

    def action_reopen(self):
        """Réouverture sur la même demande (R15)."""
        for request in self:
            if request.state not in ("resolved", "closed"):
                raise UserError(_(
                    "Seules les demandes « Résolu » ou "
                    "« Clôturé » peuvent être rouvertes (%s).",
                    request.request_ref,
                ))
            request.reopen_count += 1
            request.state = "in_progress"
            request.date_resolved = False
            request.message_post(
                body=_("Demande réouverte (réouverture n°%s).",
                       request.reopen_count)
            )
        return True

    def action_cancel(self):
        """→ Annulé (R16 : hors indicateurs)."""
        for request in self:
            if request.state == "closed":
                raise UserError(_(
                    "Une demande clôturée ne peut pas être "
                    "annulée (%s).", request.request_ref
                ))
            if request.state == "pending_user":
                request._wd_leave_pending_user()
            request.state = "cancel"
        return True

    def action_handover(self):
        """Enregistre la remise physique du matériel (R10).

        Autorisée une fois la demande prise en charge ; la
        remise n'est enregistrée qu'une fois (pas d'écrasement
        silencieux de la date et du technicien).
        """
        for request in self:
            if request.state in ("draft", "cancel", "closed"):
                raise UserError(_(
                    "La remise ne peut pas être enregistrée "
                    "pour une demande « %s » (%s).",
                    request.state, request.request_ref,
                ))
            if request.handover_date:
                raise UserError(_(
                    "La remise est déjà enregistrée le %s "
                    "par %s (%s).",
                    request.handover_date,
                    request.handover_by.name or "-",
                    request.request_ref,
                ))
            request.handover_date = fields.Date.context_today(self)
            request.handover_by = self.env.user
        return True

    # =====================================================
    # Cron quotidien : clôture J+3 + alerte retards (R14)
    # =====================================================
    @api.model
    def _cron_close_and_notify_late(self):
        limit = fields.Datetime.now() - datetime.timedelta(
            days=CLOSE_AFTER_DAYS
        )
        to_close = self.search([
            ("state", "=", "resolved"),
            ("date_resolved", "<=", limit),
        ])
        if to_close:
            to_close.action_close()

        late = self.search([
            ("state", "in",
             ("draft", "assigned", "in_progress",
              "pending_user")),
        ]).filtered("is_late")
        if late:
            self._notify_late_requests(late)
        return True

    def _notify_late_requests(self, late_requests):
        """Envoie au Responsable IT la liste des demandes en
        retard (R12). Digest unique par exécution."""
        manager_group = self.env.ref(
            "wd_it_request.group_it_manager",
            raise_if_not_found=False,
        )
        if not manager_group:
            return
        recipients = self.env["res.users"].search([
            ("groups_id", "in", manager_group.ids),
            ("active", "=", True),
        ])
        if not recipients:
            return
        lines = "\n".join(
            f"- {r.request_ref} {r.name} "
            f"({r.employee_id.name}) — {r.priority}"
            for r in late_requests
        )
        body = _(
            "Demandes IT en retard SLA au %(date)s :\n%(lines)s",
            date=fields.Datetime.now(),
            lines=lines,
        )
        for user in recipients:
            self.env["mail.mail"].sudo().create({
                "subject": _("[WD Interne] Demandes IT en retard"),
                "body_html": "<pre>%s</pre>" % body,
                "email_to": user.email or user.login,
                "auto_delete": True,
            })


class WdItRequestLine(models.Model):
    _name = "wd.it.request.line"
    _description = "Ligne matériel d'une demande IT"
    _order = "sequence, id"

    request_id = fields.Many2one(
        "wd.it.request", required=True, ondelete="cascade",
        index=True,
    )
    employee_user_id = fields.Many2one(
        "res.users",
        related="request_id.employee_id.user_id",
        store=True,
        readonly=True,
    )
    sequence = fields.Integer(default=10)
    equipment_id = fields.Many2one(
        "maintenance.equipment", string="Équipement proposé",
    )
    equipment_name = fields.Char(
        related="equipment_id.name", readonly=True,
    )
    equipment_category = fields.Char(
        related="equipment_id.category_id.name", readonly=True,
    )
    needed_label = fields.Char(
        string="Matériel requis",
        help="Description du besoin R10 (ex. « Portable Dev + écran »).",
    )
    status = fields.Selection(
        selection=[
            ("proposed", "Proposé"),
            ("assigned", "Affecté"),
            ("unavailable", "Indisponible"),
        ],
        default="proposed",
        required=True,
    )
    note = fields.Char()
