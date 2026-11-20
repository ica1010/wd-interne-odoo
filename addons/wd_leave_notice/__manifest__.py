{
    "name": "WD - Regles de gestion des conges (R2 a R7)",
    "version": "18.0.2.0.0",
    "category": "Human Resources",
    "summary": (
        "Regles metier des conges West Digital : delai de "
        "prevenance CP (R2), circuits de validation (R3), "
        "justificatifs MAL/exceptionnels (R4/R5), stagiaires "
        "(R6) et chevauchements (R7)."
    ),
    "description": """
Regles metier des conges West Digital (cahier des charges) :

- R2 : delai de prevenance des conges payes ;
- R3 : circuits de validation (manager / RH) ;
- R4 : certificat medical obligatoire (MAL) ;
- R5 : durees legales et justificatifs (EXC-*) ;
- R6 : restriction des conges des stagiaires (AA) ;
- R7 : interdiction des chevauchements de demandes.

Tous les controles sont appliques cote serveur, a la creation
et lors des modifications ulterieures des dates, du type ou de
l'employe. Les types de conges sont identifies par leur External
ID Odoo (prefixe __import__), jamais par identifiant numerique.
""",
    "depends": ["hr_holidays"],
    "data": ["views/hr_leave_views.xml"],
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
