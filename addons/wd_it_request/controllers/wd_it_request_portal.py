# -*- coding: utf-8 -*-

import html
import logging

from odoo import _, http
from odoo.exceptions import UserError
from odoo.http import request

_logger = logging.getLogger(__name__)

PAGE = """<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<title>Demande IT — West Digital</title>
<style>
body{font-family:Arial,sans-serif;max-width:640px;margin:2em auto;
padding:0 1em;color:#222}
h1{font-size:1.4em}label{display:block;margin:.8em 0 .2em;
font-weight:bold}input,select,textarea{width:100%;padding:.5em;
border:1px solid #bbb;border-radius:4px;box-sizing:border-box}
button{margin-top:1.2em;padding:.7em 1.6em;background:#714B67;
color:#fff;border:0;border-radius:4px;cursor:pointer}
.msg{padding:.8em;border-radius:4px;margin:1em 0}
.ok{background:#dff0d8}.ko{background:#f8d7da}
small{color:#666}
</style></head><body>
<h1>Demande IT interne — West Digital</h1>
<p><small>Formulaire pour les collaborateurs sans licence
Odoo. Votre demande est transmise au service IT.</small></p>
%%MESSAGE%%
<form method="post" action="/it/request">
<input type="hidden" name="csrf_token" value="%%CSRF%%">
<label for="employee_id">Votre nom</label>
<select name="employee_id" id="employee_id" required>
<option value="">— Sélectionner —</option>
%%EMPLOYEES%%
</select>
<label for="name">Objet de la demande</label>
<input name="name" id="name" required maxlength="200"/>
<label for="category">Catégorie</label>
<select name="category" id="category">
%%CATEGORIES%%
</select>
<label for="priority">Priorité</label>
<select name="priority" id="priority">
%%PRIORITIES%%
</select>
<label for="description">Description</label>
<textarea name="description" id="description" rows="5"
placeholder="Décrivez votre besoin, le matériel concerné, le message d'erreur éventuel…"></textarea>
<button type="submit">Envoyer la demande</button>
</form>
</body></html>"""


class WdItRequestPortal(http.Controller):
    """Bonus — formulaire public de dépôt de demande IT
    pour les collaborateurs sans licence Odoo."""

    @http.route("/it/request", type="http", auth="public",
                methods=["GET", "POST"], csrf=True)
    def it_request_form(self, **post):
        if request.httprequest.method == "POST":
            return self._submit(post)
        return request.make_response(
            self._render(""), headers=[("Content-Type", "text/html")],
        )

    # -------------------------------------------------
    def _submit(self, post):
        employee = request.env["hr.employee"].sudo().browse(
            int(post.get("employee_id") or 0)
        )
        if not employee.exists():
            return self._error(
                "Merci de sélectionner votre nom dans la "
                "liste.",
            )
        name = (post.get("name") or "").strip()
        if not name:
            return self._error("L'objet de la demande est "
                               "obligatoire.")
        vals = {
            "name": name[:200],
            "employee_id": employee.id,
            "description": (post.get("description") or "")
            .strip(),
            "category": post.get("category")
            if post.get("category")
            in dict(self._model()._fields["category"].selection)
            else "other",
            "priority": post.get("priority")
            if post.get("priority")
            in dict(self._model()._fields["priority"].selection)
            else "P3",
            "channel": "portal",
            "requester_id": False,
        }
        try:
            rec = self._model().sudo().create(vals)
        except UserError as exc:
            return self._error(str(exc))
        _logger.info("Demande IT publique creee : %s (%s)",
                     rec.request_ref, employee.name)
        return request.make_response(
            self._render(
                '<div class="msg ok"><b>Demande envoyée.</b> '
                "Référence : <b>%s</b>. Le service IT vous "
                "recontactera.</div>" % html.escape(
                    rec.request_ref
                ),
            ),
            headers=[("Content-Type", "text/html")],
        )

    # -------------------------------------------------
    def _model(self):
        return request.env["wd.it.request"]

    def _error(self, message):
        return request.make_response(
            self._render(
                '<div class="msg ko">%s</div>'
                % html.escape(message)
            ),
            headers=[("Content-Type", "text/html")],
        )

    def _render(self, message):
        employees = "".join(
            '<option value="%s">%s</option>'
            % (e.id, html.escape(e.name))
            for e in self._employees()
        )
        model = self._model()
        categories = "".join(
            '<option value="%s">%s</option>'
            % (key, html.escape(label))
            for key, label in model._fields["category"].selection
        )
        priorities = "".join(
            '<option value="%s"%s>%s</option>'
            % (
                key,
                " selected" if key == "P3" else "",
                html.escape(label),
            )
            for key, label in model._fields["priority"].selection
        )
        return (
            PAGE
            .replace("%%MESSAGE%%", message)
            .replace("%%CSRF%%", request.csrf_token())
            .replace("%%EMPLOYEES%%", employees)
            .replace("%%CATEGORIES%%", categories)
            .replace("%%PRIORITIES%%", priorities)
        )

    def _employees(self):
        return request.env["hr.employee"].sudo().search(
            [("active", "=", True)], order="name"
        )
