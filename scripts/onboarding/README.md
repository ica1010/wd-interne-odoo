# Automatisation onboarding — Partie 4

Script Python (`odoo_onboarding.py`, XML-RPC) qui traite la feuille
`Nouveaux_Arrivants` et automatise, pour chaque recrue :

1. résolution du poste et du **profil matériel** (feuille `Postes` =
   source de vérité, jamais déduit du seul intitulé) ;
2. création de la fiche `hr.employee` (idempotente, External ID
   `__import__.NA-<RecrueID>`) ;
3. création du compte `res.users` (groupe `base.group_user`
   uniquement — jamais d'admin) ;
4. lancement du plan d'activités `Onboarding` sur l'employé à la
   date d'entrée ;
5. création de la demande IT `Équipement nouvel arrivant`
   (module `wd_it_request`) avec le matériel R10 **proposé** depuis
   le stock réel (`maintenance.equipment.employee_id` vide).

## Prérequis

- Odoo 18 Community accessible en XML-RPC (port 8069 par défaut,
  ici `http://localhost:8069` ou `http://localhost:8071` selon
  l'exposition Docker).
- Modules installés : `hr`, `maintenance`, `hr_maintenance`,
  `wd_equipment_profile`, `wd_it_request`.
- L'utilisateur API doit appartenir au groupe
  *Équipements → Manager* (`maintenance.group_equipment_manager`)
  pour créer les demandes IT ; `base.group_user` suffit sinon pour
  la lecture.
- Fichiers préparés par le pipeline : `data/processed/Postes.csv`,
  `data/processed/Nouveaux_Arrivants.csv`.

## Configuration

Identifiants — **uniquement** par variables d'environnement :

```bash
export ODOO_URL=http://localhost:8069
export ODOO_DB=odoo
export ODOO_USER=admin            # ou un utilisateur dédié
export ODOO_PASSWORD=...          # mot de passe ou clé API
```

Aucun secret dans le code ni dans `config/onboarding.json`.

Configuration métier (sans secrets) : `config/onboarding.json`
— sources CSV, alias de postes, délai R8, nom du plan, groupes
d'utilisateurs, domaine email professionnel.

## Correspondance Postes → Profil matériel

| PosteID | Intitulé | Profil |
|---|---|---|
| J01 | Directeur Général | Standard |
| J02 | Chef de projet IT | Standard |
| J03 | Consultant Odoo | Dev |
| J04 | Développeur Odoo | Dev |
| J05 | Data & BI Specialist | Dev |
| J06 | Integration / Automation Specialist | Dev |
| J07 | Technicien Support IT | Support |
| J08 | Responsable Support | Support |
| J09 | Account Manager | Commercial |
| J10 | Commercial terrain | Commercial |
| J11 | Responsable RH | Standard |
| J12 | Comptable | Standard |
| J13 | Assistant(e) administratif(ve) | Standard |
| J14 | Stagiaire | Standard |

Alias explicite (décision actée) : `Stagiaire Data Analyst` →
`Stagiaire` (J14), utilisé par N06. Toute autre variante non listée
dans `poste_aliases` est une anomalie bloquante
`POSTE_NON_RECONNU`.

## Besoins R10 par profil

| Profil | Matériel requis |
|---|---|
| Dev | Portable (profil `dev`) + Écran |
| Standard | Portable (profil `standard`) |
| Support | Portable (profil `standard`) + Casque |
| Commercial | Portable (profil `standard`) + Téléphone |

La sélection dans `maintenance.equipment` exige : bonne catégorie,
`wd_material_profile` attendu pour les portables, `employee_id`
vide (stock), équipement non déjà réservé par une demande IT
ouverte, maintenance non planifiée.

## Exécution

```bash
# Simulation — aucune écriture dans Odoo
python scripts/onboarding/odoo_onboarding.py

# Une seule recrue
python scripts/onboarding/odoo_onboarding.py --apply --recrue N01

# Exécution réelle (toutes les recrues du fichier)
python scripts/onboarding/odoo_onboarding.py --apply
```

Le journal complet est écrit dans
`data/reports/onboarding_<dryrun|apply>_YYYYMMDD_HHMMSS.log`
(exemple : `onboarding_exemple_dryrun.log`).

## Contrôles bloquants

Le script **ne crée rien de silencieusement incomplet** :

- `POSTE_NON_RECONNU` / `INTITULE_POSTE_VIDE` ;
- `PROFIL_MATERIEL_ABSENT` / `PROFIL_MATERIEL_INVALIDE` ;
- manager, département ou site introuvable ;
- email professionnel impossible à construire, email perso
  invalide (signalé, non bloquant sauf si obligatoire) ;
- `R8_DEPASSE` / `R8_IMMINENT` : email + compte + matériel
  doivent être prêts J-2 ouvrés avant l'entrée ;
- `STOCK_INSUFFISANT:<besoin>` : la ligne est créée avec le statut
  `unavailable` et le matériel manquant est explicite — pas
  d'affectation partielle silencieuse.

Une recrue « bloquée » n'empêche pas les suivantes : chaque fiche
est traitée indépendamment et le résumé final compte les statuts.

## Idempotence

- Employé : recherche par External ID `__import__.NA-<RecrueID>`.
- Utilisateur : recherche par login avant création.
- Plan : détection des activités déjà générées pour l'employé à
  partir des summaries du plan (pas de relance).
- Demande IT : recherche d'une demande non terminée/annulée pour
  le même employé et le même objet.
- Matériel : seuls les équipements sans `employee_id` et non
  réservés par une demande IT ouverte sont proposés ; le même
  équipement ne peut pas être proposé deux fois (les demandes
  postérieures excluent le stock déjà réservé).

## Limites connues

- Le plan `Onboarding` contient 14 templates, dont 2 activités
  anglaises non-WD (« Organize knowledge transfer… », « Take Back
  HR Materials ») : elles seront aussi planifiées. À nettoyer dans
  Odoo si souhaité.
- Les lignes `unavailable` restent en `draft` : la décision
  d'achat/réaffectation appartient au service IT.
- Le script **propose** le matériel ; il n'affecte pas
  `employee_id` sur `maintenance.equipment` (acte réservé à l'IT).
- N'a pas été exécuté en production sur les 6 recrues :
  validation dry-run d'abord, puis `--apply --recrue <ID>` un par
  un.

## Tests

```bash
# Tests unitaires du script (FakeClient en mémoire, aucun réseau)
uv run --with pandas --with openpyxl --with pytest \
  python -m pytest tests/test_onboarding.py -q
```

Couverture : 4 profils R10, poste absent, alias N06, profil
invalide, stock insuffisant, équipement déjà affecté, équipement
réservé par une demande ouverte, R8 dépassé, idempotence (2e
exécution), dry-run sans écriture, manager introuvable.
