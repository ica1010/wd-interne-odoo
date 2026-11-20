from odoo import fields, models


class MaintenanceEquipment(models.Model):
    _inherit = "maintenance.equipment"

    wd_material_profile = fields.Selection(
        selection=[
            ("dev", "Dev"),
            ("standard", "Standard"),
            ("support", "Support"),
            ("commercial", "Commercial"),
        ],
        string="Profil matériel",
        help=(
            "Profil de destination du matériel (règle R10) : "
            "Dev = portable Dev + écran, Support = portable "
            "Standard + casque, Commercial = portable "
            "Standard + téléphone, Standard = portable "
            "Standard."
        ),
    )
