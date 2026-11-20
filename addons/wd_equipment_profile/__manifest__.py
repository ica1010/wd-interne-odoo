{
    "name": "WD - Profil materiel des equipements (R10)",
    "version": "18.0.1.0.0",
    "category": "Maintenance",
    "summary": (
        "Ajoute le champ « Profil materiel » sur les "
        "equipements (maintenance.equipment) pour la regle "
        "R10 West Digital."
    ),
    "description": """
Champ personnalise « Profil materiel » sur maintenance.equipment.

Valeurs : Dev, Standard, Support, Commercial. Le champ decrit
la destination du materiel (regle R10), pas son modele
physique ni sa categorie :

- Dev : portable Dev + ecran ;
- Support : portable Standard + casque ;
- Commercial : portable Standard + telephone ;
- Standard : portable Standard.

Aucune donnee existante n'est modifiee : les equipements
deja importes conservent leur valeur et le champ reste vide
lorsque le profil n'est pas encore determine.
""",
    "depends": ["maintenance"],
    "data": [
        "views/maintenance_equipment_views.xml",
    ],
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
