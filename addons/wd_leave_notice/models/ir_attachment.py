# -*- coding: utf-8 -*-

from odoo import models, _
from odoo.exceptions import ValidationError


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    def _wd_check_document_removal(self):
        """
        R4/R5 : le justificatif d'une demande MAL/EXC ne peut
        pas etre retire alors que la demande est soumise ou
        validee.
        """
        for attachment in self:
            if attachment.res_model != "hr.leave" \
                    or not attachment.res_id:
                continue
            leave = self.env["hr.leave"].browse(
                attachment.res_id
            )
            if not leave.exists() or not leave._wd_requires_document():
                continue
            if leave.state in ("refuse", "cancel"):
                continue
            remaining = self.env["ir.attachment"].search_count([
                ("res_model", "=", "hr.leave"),
                ("res_id", "=", leave.id),
                ("id", "not in", self.ids),
            ])
            if remaining == 0:
                raise ValidationError(_(
                    "R4/R5 : le justificatif ne peut pas etre "
                    "retire d'une demande %(type)s soumise ou "
                    "validee.",
                    type=leave.holiday_status_id.name,
                ))

    def unlink(self):
        self._wd_check_document_removal()
        return super().unlink()

    def write(self, vals):
        if "res_model" in vals or "res_id" in vals:
            self._wd_check_document_removal()
        return super().write(vals)
