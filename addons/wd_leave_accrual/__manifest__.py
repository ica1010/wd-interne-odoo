{
    "name": "WD - Acquisition congés payés (R1)",
    "version": "18.0.1.0.0",
    "category": "Human Resources",
    "summary": (
        "Acquisition mensuelle de 2,5 jours de congé payé "
        "pour les salariés CDI/CDD (règle R1 West Digital), "
        "via le mécanisme natif d'accrual d'Odoo 18."
    ),
    "description": """
Règle R1 : un salarié en CDI ou CDD acquiert 2,5 jours
ouvrés de congé payé par mois de présence.

Le module crée un plan d'accrual natif
(`hr.leave.accrual.plan`, 2,5 jours / mois) et rattache
chaque employé éligible via une allocation
`allocation_type='accrual'` : le cron natif
`_update_accrual` (hr_holidays) génère ensuite les
allocations mensuelles automatiquement.

Éligibilité : contrat actif (open/draft) de type CDI ou
CDD, hors stagiaires (contrat « Stage »). L'affectation
est idempotente : une allocation accrual existante pour
le plan n'est jamais dupliquée.

Les allocations d'ouverture (soldes au 31/10/2026)
importées par le pipeline coexistent : l'accrual ne
couvre que les mois à venir.
""",
    "depends": ["hr_holidays", "hr_contract"],
    "data": [
        "data/hr_leave_accrual_plan.xml",
    ],
    "post_init_hook": "post_init_assign_accrual",
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
