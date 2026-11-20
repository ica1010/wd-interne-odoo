# wd_leave_accrual — Acquisition CP 2,5 j/mois (R1)

Implémente la règle **R1** du cas West Digital : *un salarié en
CDI ou CDD acquiert 2,5 jours ouvrés de congé payé par mois de
présence*.

## Fonctionnement

Le module s'appuie sur le mécanisme **natif** d'accrual
d'Odoo 18 (`hr.leave.accrual.plan` / `hr.leave.accrual.level`),
pas sur un cron custom :

- `data/hr_leave_accrual_plan.xml` crée le plan
  « Acquisition CP — 2,5 j/mois (R1) » rattaché au type de
  congé `__import__.CP`, avec un niveau : +2,5 jours tous les
  mois.
- Chaque employé éligible reçoit une allocation
  `allocation_type='accrual'` validée, dont le `date_from` =
  début du contrat le plus ancien actif.
- Le cron natif `hr_leave_allocation_cron_accrual`
  (`_update_accrual`, quotidien) incrémente ensuite le solde
  chaque mois (`nextcall` = 1er du mois suivant pour les
  allocations créées à l'installation).

## Éligibilité

- Contrat actif (`open` ou `draft`) de type **CDI** ou **CDD**
  (`hr.contract.type`, correspondance par nom).
- **Stagiaires exclus** : tout contrat « Stage » actif rend
  l'employé inéligible (cohérent avec `_wd_is_intern` de
  `wd_leave_notice`, mais logique propre au module — pas de
  dépendance croisée).
- Employé inactif ou sans contrat : ignoré.

## Déclencheurs (idempotents)

- `post_init_hook` : à l'installation, rattache le plan à tous
  les employés actifs éligibles (37 allocations créées).
- `hr.contract.create` / `write` : tout nouveau contrat ou
  changement de type/dates recrée le rattachement si absent —
  couvre les futures recrues (N01-N06) dès que leur contrat
  sera saisi.
- `_wd_ensure_cp_accrual` ne duplique jamais : recherche une
  allocation accrual existante pour (employé, plan) avant
  création.

## Limites assumées

- **Pas de retrait automatique** : si un contrat passe de CDI à
  Stage (ou est clôturé), l'allocation accrual existante n'est
  pas annulée — à traiter manuellement ou via évolution future.
- **Prorata** : géré par le moteur natif d'accrual (dates
  `first_day`/`start_count` du niveau) pour les mois partiels
  d'arrivée ; la règle « 2,5 j par mois de présence » est
  approchée par mois calendaires glissants.
- **Coexistence avec les soldes d'ouverture** : les allocations
  « regular » importées (solde au 31/10/2026) restent la base ;
  l'accrual ne produit que les mois futurs — pas de double
  comptage.

## Installation / tests

```bash
docker exec wd-interne-odoo-1 odoo -d odoo --db_host=db \
  --db_user=odoo --db_password=odoo \
  -i wd_leave_accrual --test-enable --stop-after-init \
  --http-port=8071 --log-level=test
```

6 tests : CDI, CDD, stagiaire exclu, sans contrat, idempotence,
configuration du niveau (2,5 j / monthly).
