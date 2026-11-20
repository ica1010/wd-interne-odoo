{
    "name": "WD - Demandes IT",
    "version": "18.0.1.0.0",
    "category": "Productivity",
    "summary": (
        "Demandes internes IT West Digital : équipement "
        "nouvel arrivant, matériel proposé ou manquant."
    ),
    "description": """
Module minimal de demandes IT pour l'automatisation
d'onboarding (Partie 4).

- wd.it.request : demande interne (ex. « Équipement nouvel
  arrivant ») rattachée à un employé ;
- wd.it.request.line : ligne de matériel requis, avec
  l'équipement proposé (maintenance.equipment) ou le statut
  « indisponible » lorsque le stock ne permet pas de
  proposer d'équipement.

Les équipements proposés restent des propositions : leur
affectation effective (employee_id sur maintenance.equipment)
est réalisée par le service IT, pas par ce module.
""",
    "depends": ["hr", "maintenance", "hr_maintenance"],
    "data": [
        "security/wd_it_request_security.xml",
        "security/ir.model.access.csv",
        "report/wd_it_request_handover.xml",
        "wizard/wd_offboarding_wizard_views.xml",
        "views/wd_it_request_views.xml",
    ],
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
