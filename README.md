# WD-Interne — Préparation des données Odoo

Préparation, validation et contrôle du jeu de données West Digital
pour import dans **Odoo Community 17+** (instance réelle :
**Odoo 18.0** via `docker-compose.yml`).

## Module personnalisé : règles de gestion des congés (R2–R7)

`addons/wd_leave_notice` — installé sur l'instance `odoo`
(Odoo 18.0, base `odoo`).

### R2 — Délai de prévenance (Congé payé)

Toute demande de **Congé payé** (`__import__.CP`)
d'une durée ≥ **5 jours ouvrés** doit être déposée ≥
**10 jours calendaires** avant son début.

- Contrôle **côté serveur** dans `hr.leave` : à la création
  (soumission) et à chaque `write` touchant dates, durée,
  type de congé ou employé — aucun contournement par
  modification ultérieure.
- Durée calculée en jours ouvrés via le calendrier de
  l'employé (`get_work_duration_data`) : week-ends, jours
  non travaillés et jours fériés déduits.
- Type « Congé payé » identifié par **External ID**
  (`ir.config_parameter` `wd_leave_notice.cp_leave_type_xmlid`,
  défaut `__import__.CP`), jamais par ID numérique ;
  repli sur le nom exact.
- Message de refus explicite : raison + début le plus tôt
  possible.
- Respecte `leave_skip_state_check` (mécanisme natif,
  ex. découpage de congés).

### R3 — Circuits de validation

`hr.leave.validation_type` est un *related* vers
`hr.leave.type.leave_validation_type` : le circuit est porté
par le **type** de congé, pas par la demande. Stratégie :

- **Config native des types** (alignée par `init()` du module,
  `models/hr_leave_type.py`) : `SS → both`, `AA → manager` ;
  `MAL/EXC-* → hr` et `CP → manager` déjà conformes.
- **CP à circuit variable** : `action_approve`/`action_validate`
  reproduisent la transition native `confirm → validate1`
  (manager) puis exigent le groupe RH pour `validate1 → validate`.
  Seuil : **≤ 10 jours ouvrés = manager seul ; > 10 = manager + RH**.
- `SS` : double validation manager → RH dans tous les cas.
- `action_validate` en `confirm` sur un circuit `both` est
  **bloqué** (contournement interdit).
- Historique préservé : `first_approver_id`/`second_approver_id`
  tracés (pour CP > 10 j, le validateur RH est rétabli en
  `second_approver_id` après la validation native).

### R4 — Congé maladie (MAL)

- Pièce jointe **obligatoire** (`attachment_ids`), vérifiée à la
  création, à la validation et à chaque `write` de dates.
- Validation **RH uniquement** (groupe `hr_holidays_manager`).
- Le justificatif ne peut pas être supprimé ni détaché tant que
  la demande est soumise ou validée (`ir.attachment` unlink/write).
- MAL ne consomme pas le solde CP (type distinct, pas
  d'allocation CP) — vérifié par test.

### R5 — Congés exceptionnels (EXC-MAR / EXC-NAI / EXC-DEC)

| Type | Durée exigée | Justificatif | Validation |
|---|---|---|---|
| EXC-MAR mariage | 4 jours ouvrés | obligatoire | RH |
| EXC-NAI naissance | 3 jours ouvrés | obligatoire | RH |
| EXC-DEC décès | 3 jours ouvrés | obligatoire | RH |

- Durée calculée en **jours ouvrés** via le calendrier de
  l'employé (`get_work_duration_data`) : week-ends et jours
  fériés **non décomptés**. Une demande chevauchant un week-end
  ou un férié doit donc couvrir la durée légale en jours
  ouvrés (ex. EXC-MAR = 4 jours travaillés, week-end ignoré).
- Même protection anti-suppression du justificatif que R4.

### R6 — Stagiaires

- Stagiaire = employé avec un **contrat actif** (`open`/`draft`)
  dont le type de contrat est « Stage »
  (`hr.employee._wd_is_intern()`).
- Interdictions : demande de **CP** et **allocation CP**
  (contrôles à la création et au `write` des allocations).
- **AA** autorisée : max **2 jours ouvrés par mois calendaire**,
  cumul des demandes actives (`confirm`/`validate1`/`validate`)
  — fractionner en plusieurs demandes ne contourne pas le
  plafond. Validation **manager** uniquement.
- Interprétation documentée : « 2 jours par mois » = jours
  **ouvrés** (cohérent avec le reste du référentiel).

### R7 — Chevauchements

- Deux demandes d'un même employé ne peuvent pas se chevaucher
  (dates identiques, inclusion, chevauchement partiel bloqués ;
  demandes adjacentes autorisées).
- Contrôle sur `date_from`/`date_to` calculés, états
  `confirm`/`validate1`/`validate` ; s'applique à la création
  et aux modifications de dates.
- Employés différents : aucune restriction.
- Week-ends et fériés exclus des durées (calendrier réel).

### Tests

3 fichiers de tests exécutés dans le conteneur,
**75/75 verts** (`0 failed, 0 error(s)`) :

- `tests/test_hr_leave_notice.py` — R2 (9 tests) ;
- `tests/test_hr_leave_rules.py` — R3–R7 (27 tests) ;
- `tests/test_hr_leave_edge_cases.py` — cas limites, droits
  et non-régression (39 tests).

```bash
docker exec wd-interne-odoo-1 odoo -d odoo \
  --db_host=db --db_user=odoo --db_password=odoo \
  -u wd_leave_notice --test-enable --stop-after-init \
  --http-port=8071 --log-level=test
```

### Couverture de code

Mesurée avec **Coverage.py** (pip `--user`, core `ctracer`)
pendant l'exécution réelle des tests, périmètre = code
personnalisé du module (hors code Odoo standard) :

```bash
docker exec wd-interne-odoo-1 bash -c "\
cd /tmp && /var/lib/odoo/.local/bin/coverage run \
  --branch \
  --include='/mnt/extra-addons/wd_leave_notice/*' \
  --data-file=/tmp/cov_wd /usr/bin/odoo -d odoo \
  --db_host=db --db_user=odoo --db_password=odoo \
  -u wd_leave_notice --test-enable --stop-after-init \
  --http-port=8071 --log-level=test && \
/var/lib/odoo/.local/bin/coverage report \
  --data-file=/tmp/cov_wd -m \
  --include='/mnt/extra-addons/wd_leave_notice/models/*'"
```

Résultats (`models/` uniquement — périmètre métier) :

| Fichier | Lignes | Branches |
|---|---|---|
| `hr_leave.py` | 243 stmt., 10 miss → **96 %** | 122 br., 12 part. → ~90 % |
| `hr_leave_allocation.py` | 29 stmt., 2 miss → **93 %** | — |
| `hr_leave_type.py` | 100 % | 100 % |
| `ir_attachment.py` | 100 % | ~97 % |
| `hr_employee.py` | 100 % | 100 % |
| **TOTAL models/** | **322 stmt., 12 miss → 96 %** | **154 br., 15 part. → ~90 %** |

TOTAL module (tests inclus) : 862 stmt., **96 %**.

Lignes non couvertes : uniquement des gardes défensives
inaccessibles via les chemins métier (`calendar`/`request_date`
toujours renseignés par le fallback natif, type CP toujours
résolvable, `aa_type` toujours présent, allocations sans
employé/type impossibles). Aucun test n'a été écrit pour
gonfler artificiellement le score.

Rapports générés : `reports/coverage/html/index.html`,
`reports/coverage/coverage.xml`, `coverage.json`,
`coverage_report.txt`.

Cas couverts : 4 j ouvrés/délai court autorisé, 5 j/9 j
refusé, 5 j/10 j autorisé, 6 j/10 j autorisé, week-end non
compté, jour férié déduit, autre type non concerné, et
re-contrôle après modification de dates ; CP 5/10 j = manager,
CP >10 j et SS = manager+RH, RH après manager, refus bloquant,
droits d'approbation, MAL/EXC sans justificatif refusés,
justificatif non supprimable, durées EXC, stagiaire sans CP,
plafond AA mensuel cumulatif, chevauchements et adjacence.

### Installation / mise à jour

```bash
docker compose up -d          # addons/ est monté dans le conteneur
# ou pour installer/màj + tests :
docker exec wd-interne-odoo-1 odoo -d odoo \
  --db_host=db --db_user=odoo --db_password=odoo \
  -i wd_leave_notice --test-enable --stop-after-init --http-port=8071
```

### Vérification manuelle dans l'interface

1. *Congés → Nouvelle demande* pour un employé avec
   allocation CP suffisante.
2. Type « Congé payé », période ≥ 5 jours ouvrés débutant à
   moins de 10 jours calendaires → **erreur de validation**
   avec la date de début minimale autorisée.
3. Même demande avec début ≥ 10 jours → acceptée.
4. Demande « Congé maladie » équivalente → acceptée
   (R2 ne s'applique pas).
5. Modifier les dates d'une demande existante vers une
   configuration non conforme → refusée.

### Limites et hypothèses

- La « date de soumission » = `create_date` de la demande ;
  une modification ne réinitialise pas le délai (volontaire,
  anti-contournement).
- Les congés déjà existants ne sont pas re-contrôlés
  rétroactivement (ni soldes ni demandes passées modifiés).
- Congés horaires/demi-journées : la durée fractionnaire est
  comparée à 5 j ouvrés ; le message affiche la valeur
  calculée.
- `request_date_from` absent → contrôle ignoré pour la
  demande (cas non prévu par le métier).
- Un contournement resterait possible via un import avec le
  contexte `leave_skip_state_check` ou un accès SQL direct —
  hors périmètre applicatif.

Limites R3–R7 :

- `hr.leave.validation_type` est un *related* non stocké : le
  circuit ne peut pas être porté par la demande. Les types à
  circuit fixe sont alignés par `init()` (`SS→both`,
  `AA→manager`) ; CP > 10 j ouvrés est géré par override de
  `action_approve`/`action_validate` (transition `validate1`
  reproduite à l'identique du natif).
- Un administrateur RH (`group_hr_holidays_manager`) peut
  approuver l'étape manager des circuits `both` — pouvoir
  d'intervention RH, documenté ; un utilisateur sans rôle ne
  peut rien valider.
- Le justificatif requis = présence d'au moins une pièce jointe
  `attachment_ids` ; le contenu du fichier n'est pas analysé.
  Contexte `wd_leave_skip_document_check` = hook de test /
  migration, à n'utiliser qu'en contexte contrôlé.
- AA : « 2 jours par mois » interprété en **jours ouvrés** sur
  mois calendaire ; les demandes `refuse`/`cancel` ne comptent
  pas dans le cumul.
- R7 compare `date_from`/`date_to` (datetimes calculés) : deux
  demandes le même jour mais à des horaires différents ne
  peuvent pas exister en pratique (une demande = plage
  continue).
- Les demandes et allocations existants ne sont pas
  re-contrôlés rétroactivement ; seules les config SS/AA
  (`leave_validation_type`) ont été alignées.

## Module matériel : profil équipement (R10)

`addons/wd_equipment_profile` — installé sur l'instance
`odoo`. Ajoute le champ `wd_material_profile` (« Profil
matériel », sélection `dev`/`standard`/`support`/`commercial`)
sur `maintenance.equipment`, avec la correspondance R10
documentée dans le `help` du champ et le README du module.

- Présent dans le formulaire (après `Catégorie`), la liste
  et la recherche (champ + regroupement).
- Aucune donnée existante modifiée : les 85 équipements
  importés gardent `Modèle`, `Catégorie` et profil vide.
- Aucun droit nouveau : suit les accès `maintenance`.
- Tests : 7/7 verts (`0 failed, 0 error(s)`).

```bash
docker exec wd-interne-odoo-1 odoo -d odoo \
  --db_host=db --db_user=odoo --db_password=odoo \
  -u wd_equipment_profile --test-enable --stop-after-init \
  --http-port=8071 --log-level=test
```

## Principe fondateur

- Le classeur source `data/raw/jeu_donnees_westdigital_odoo_interne.xlsx`
  n'est **jamais modifié**.
- `validate.py` détecte et rapporte les anomalies sans rien changer.
- `clean.py` produit des **copies préparées** dans `data/processed/`.
- Les anomalies restent traçables dans `data/reports/`.
- Aucune valeur métier n'est inventée (départements, dates,
  certificats, identités, identifiants).

## Contrôle des jours fériés (Togo / Lomé)

Module `scripts/holidays/` — périmètre strict : **TG, site Lomé**.
Aucune logique multi-pays.

### Sources et hiérarchie

| Source | Type | Statut produit |
|---|---|---|
| Nager.Date API v4 | `secondary_api` | `REFERENCE_SECONDAIRE` — jamais officiel |
| AlAdhan API (calendrier Umm al-Qura) | `estimated_calendar` | `DATE_ESTIMEE` |
| Calcul astronomique local (Lomé) | `astronomical_calculation` | `REFERENCE_ASTRONOMIQUE` |
| Registre manuel (`config/holidays/lunar.json`) | `human_registry` | `OBSERVATION_RAPPORTEE` / `CONFIRME_OFFICIELLEMENT` |
| jo.gouv.tg, Ambassade | `manual` | `CONSULTER_MANUELLEMENT` |

Nager.Date est une **source secondaire** : une donnée qui en
provient n'est jamais présentée comme juridiquement confirmée.
Les pages institutionnelles non lisibles automatiquement sont
journalisées comme références à consulter, sans prétendre avoir
été vérifiées.

### Couche lunaire pour les fêtes musulmanes

Les fêtes musulmanes (Korité, Tabaski, Awal Moharem, Maouloud)
sont des dates **estimées** : le calendrier Umm al-Qura est
calculé et peut différer d'un jour de l'observation locale du
croissant. Trois niveaux sont distingués, jamais confondus :

1. **`DATE_ESTIMEE`** — date calculée Umm al-Qura (AlAdhan).
2. **`REFERENCE_ASTRONOMIQUE`** — calcul local de l'âge lunaire
   au coucher du soleil à Lomé (6.13°N, 1.22°E, UTC+0).
   Le programme cherche le premier coucher de soleil où le
   jeune croissant est probablement visible (âge lunaire
   15–72 h), en déduit le 1er du mois local, puis la date de
   fête. La fenêtre ±3 jours couvre l'incertitude du calendrier.
3. **`OBSERVATION_RAPPORTEE`** — observation réelle saisie
   manuellement dans `config/holidays/lunar.json`.
4. **`CONFIRME_OFFICIELLEMENT`** — annonce officielle compétente,
   également saisie manuellement.

#### Choix techniques et pourquoi

- **Calcul local plutôt qu'API dédiée** : USNO et Farmsense
  étaient injoignables depuis le réseau de travail, et AlAdhan
  timings ne fournit pas l'altitude lunaire. Une formule
  astronomique de basse précision (âge synodique depuis la
  nouvelle lune de référence J2000) suffit à détecter un
  décalage d'un jour — c'est le seul besoin métier.
- **Critère 15–72 h** : en zone équatoriale, le jeune croissant
  n'est typiquement visible qu'après ~15 h de lune ; au-delà de
  ~72 h, la lune n'est plus « nouvelle » et le signal perd son
  sens. Le croissant **décroissant** est explicitement exclu.
- **Fenêtre autour du 1er du mois, pas du jour férié** : pour
  Tabaski (10 Dhu al-Hijjah), c'est le croissant ~9 jours avant
  qui détermine la date — tester la visibilité au jour férié
  reviendrait à évaluer une lune pleine, sans signification.
- **Registre manuel plutôt que scraping** : aucune API publique
  ne fournit d'observations validées par les autorités
  togolaises. Le fichier `lunar.json` rend la saisie explicite,
  datée et traçable ; tant qu'il est vide, tout reste
  `A_CONFIRMER`.

#### Limites connues

- L'âge lunaire seul ignore l'altitude, l'azimut et la clarté
  atmosphérique : c'est une **indication**, pas une prévision
  de visibilité fiable à 100 %.
- Le coucher du soleil est une valeur indicative (~18:07 locale)
  ; une variation de quelques minutes ne change pas le verdict
  dans la grande majorité des cas.
- La couche lunaire **ne tranche pas la durée légale** de
  Korité/Tabaski (1 ou 2 jours chômés) : cela relève d'une
  source juridique (Code du travail togolais, art. 199 →
  décret en Conseil des ministres), qui reste à obtenir.
- Aucune observation réelle n'est inventée à partir des calculs.

#### Avantages

- Détecte automatiquement les dates probablement décalées d'un
  jour par rapport à Umm al-Qura **avant** la décision humaine.
- Rendu visible dans le rapport Excel (`Fetes_Musulmanes` avec
  `Date_Visibilite` et `Ecart_Jours`) et le journal d'audit.
- Évolutif : dès qu'une observation ou une annonce officielle
  est saisie dans `lunar.json`, le statut monte à
  `OBSERVATION_RAPPORTEE` ou `CONFIRME_OFFICIELLEMENT` sans
  changer le code.
- Hors période (années adjacentes), les dates restent dans le
  cache pour un contrôle ultérieur.

### Cycle d'exécution

```bash
# Contrôle complet (live)
python scripts/holidays/compare_holidays.py

# Depuis le cache local (tests, reproductibilité)
python scripts/holidays/compare_holidays.py --offline

# Tests
pytest tests/

# Export Odoo du calendrier (dataset final Jours_Feries.csv
# + référentiel Jours_Feries_Sources.csv)
python scripts/holidays/export_odoo_holidays.py
```

`export_odoo_holidays.py` est **propriétaire du calendrier** :
il produit `data/processed/Jours_Feries.csv` (17 fériés TG/Lomé)
avec les colonnes `Date`, `Libelle`, `date_from`, `date_to`
(journée complète, format Odoo), `Pays`, `Site`, `Source`,
`Source_ID`, `Statut_Reference`, ainsi que
`Jours_Feries_Sources.csv` (référentiel des sources).

- Les 13 fériés du classeur sont repris sans modification
  (`Source_ID = FICHIER_WD`).
- Les fêtes musulmanes utilisent les dates ajustées Lomé
  (`Source_ID = ALADHAN;LUNAR_LOME`) ; Korité et Tabaski
  sont une seule ligne chacun couvrant 2 jours via
  `date_from`/`date_to`. La décision humaine est tracée
  dans `jours_feries_proposes.csv`.
- `clean.py` n'exporte plus `Jours_Feries` : le module
  holidays en est l'unique producteur.

### Sorties

| Fichier | Rôle |
|---|---|
| `data/processed/jours_feries_source.csv` | Copie conforme de la feuille source |
| `data/processed/Jours_Feries.csv` | **Dataset final d'import** : 17 fériés avec date_from/date_to et sources |
| `data/processed/Jours_Feries_Sources.csv` | Référentiel des sources (ID, type, fiabilité) |
| `data/processed/jours_feries_proposes.csv` | Propositions — `Decision_Humaine` vide tant que non validé |
| `data/reports/comparaison_jours_feries.csv` | Comparaison append-only |
| `data/reports/journal_controle_jours_feries.csv` | Journal d'audit append-only (verrou + écriture atomique) |
| `data/reports/rapport_controle_jours_feries.xlsx` | Rapport : Journal, Comparaison, Fetes_Musulmanes, Synthese |
| `data/raw/holidays_cache/` | Réponses API brutes horodatées |

### Garanties

- **Append-only réel** : verrou `O_EXCL` + écriture atomique
  (`os.replace`) sur le journal et la comparaison.
- `proposition_id` stable (`PROP-AAAAMMJJ-slug`) : les décisions
  humaines saisies sont préservées entre exécutions.
- Empreinte SHA-256 du classeur source dans chaque ligne de journal.
- Aucune proposition n'est présentée comme validée sans décision
  humaine.
- **Aucun import Odoo avant validation humaine du calendrier.**

## Préparation des données métier

```bash
python scripts/data/validate.py   # détection + rapport
python scripts/data/clean.py      # CSV préparés + journal anomalies
```

Sorties principales : `data/processed/*.csv`,
`data/reports/journal_anomalies.csv`,
`data/reports/materiel_exclu.csv`,
`data/reports/rapport_validation.csv`.

Cas traités : doublons exacts d'historique IT, `ManagerID` et
`AffecteA` orphelins (vidés dans le CSV, valeur d'origine
conservée au journal), numéros de série dupliqués (règle
déterministe d'exclusion), catégories et noms d'équipements,
statuts d'affectation incohérents, dataset des contrats
employés (`Employes_Contrats.csv` + `Types_Contrats.csv`,
40 contrats — E041 exclu comme doublon présumé inexistant —
avec `Contract Type / External ID` → `__import__.TYPE_*`,
`Start Date`/`End Date` et références External ID ;
`Wage = 0` par défaut, salaire renseigné manuellement à
l'import ; `DateSortie` retenue comme fin de contrat pour
les employés sortis).

### Dataset « Profil matériel » (R10)

`clean.py` produit aussi `data/processed/Materiel_IT_Profils.csv`
— dataset de **mise à jour** des équipements Odoo pour le
champ `wd_material_profile` du module `wd_equipment_profile`
(2 colonnes : `External ID` = `__import__.` + AssetID,
`Profil matériel` = libellé de sélection Dev/Standard/
Support/Commercial, reconnu par l'import Odoo).

Règles de déduction (confirmées avec le métier) :

- **Bundle de l'employé** : portable Dev → `Dev` ; portable
  Standard seul → `Standard` ; Standard + casque → `Support` ;
  Standard + téléphone → `Commercial`. Tous les équipements
  du bundle (portable + écran/casques/téléphone) reçoivent le
  profil du poste.
- **Matériel non affecté** : le portable reprend son
  `ProfilPortable` source (Dev/Standard) ; les accessoires en
  stock restent sans profil.
- **Ambiguïtés** (employé sans portable, bundle Standard +
  écran, Standard + casque + téléphone, plusieurs portables)
  : ERREUR au journal, aucune valeur inventée.

Résultat actuel : 74 équipements profilés (Dev 33,
Support 15, Commercial 12, Standard 14) ; les 11 équipements
non affectés non-portables (écrans, casques, imprimantes,
matériel réseau) restent sans profil. `validate.py` contrôle
que chaque portable a un `ProfilPortable` Dev/Standard
(`PROFIL_PORTABLE_MANQUANT` / `PROFIL_PORTABLE_INCONNU`).

`clean.py` produit aussi
`data/raw/jeu_donnees_westdigital_odoo_interne_corrige.xlsx` :
copie du classeur source (jamais modifié) avec la colonne
calculée `Solde_a_allouer` dans `Soldes_Conges`
(`= Report2025_jours + Acquis2026_au_31_10_jours
- Pris2026_au_31_10_jours`, formule Excel par ligne) pour la
préparation des allocations de congés Odoo. Les valeurs
sources non numériques/manquantes et les soldes négatifs
sont consignés dans `journal_anomalies.csv`.

Le même run exporte aussi `data/processed/Allocations_Conges.csv`
(prêt pour `hr.leave.allocation`) avec exactement les colonnes
du mapping Odoo : `External ID`, `Employee / External ID`
(`__import__.E0XX`), `Holiday Status / External ID`
(`__import__.CP`, format `__import__.` + code source,
référencé via le référentiel
`Types_Conges_Import.csv` — `External ID` + `Name` par type de
congé) et `Solde_a_allouer` (→ Nombre de jours). Les colonnes
`EmployeeID`, `TypeConge` et les 3 sources de calcul restent
en traçabilité, non mappées.
La règle de calcul est centralisée dans `leave_balance()` —
aucune duplication entre le journal et l'export. Les soldes
négatifs sont exclus du CSV et consignés au journal
(`SOLDE_NEGATIF_EXCLU_IMPORT`, ex. E014 = −4 en attente
d'arbitrage RH) : un solde négatif n'est jamais importé.

Le même run prépare aussi la feuille `Plan_Integration` pour
le plan d'activités natif Odoo (modèle Employé) directement
dans `data/processed/Plan_Integration.csv` : les colonnes
sources (`TacheID`, `Tache`, `Responsable`, `Echeance`,
`ApplicationOdoo`) sont conservées et les colonnes Odoo
ajoutées (`TypeActivite` = `À faire (To-Do)`, `Assignation`
= `Utilisateur par défaut` pour RH/IT/Manager ou `Employé`,
`Intervalle`, `Unite` = `jours`, `Declencher` =
`Avant/Après la date du plan`). Les échéances
J-10/J-7/J-3/J-2 → avant, J1/J2/J5/J60 → après ; PI11
« Manager + RH » est éclatée en deux activités (Manager puis
RH, lignes distinctes avec le même `TacheID` PI11 pour la
traçabilité) : **12 lignes** pour 11 tâches sources.

`validate.py` contrôle en amont : identifiants PI01-PI11
présents et uniques (`TacheID` clé métier), champs
obligatoires non vides, échéances du référentiel
(J-10/J-7/J-3/J-2/J1/J2/J5/J60) et responsables reconnus
(RH/IT/Manager/Employé/Manager + RH). Toute tâche non
convertible est exclue du CSV Odoo et consignée au journal
(`PLAN_INTEGRATION_INVALIDE`, ERREUR). Les champs à
renseigner manuellement dans Odoo : l'utilisateur réel
correspondant à chaque rôle (`Utilisateur par défaut`) et
la date de référence du plan.

## Automatisation onboarding (Partie 4)

`scripts/onboarding/odoo_onboarding.py` (XML-RPC) lit
`Nouveaux_Arrivants` et automatise par recrue : fiche employé
(idempotente via `__import__.NA-<RecrueID>`), compte utilisateur
(groupe `base.group_user`), lancement du plan d'activités
`Onboarding`, et demande IT `Équipement nouvel arrivant`
(module `wd_it_request`) avec le matériel R10 proposé depuis le
stock réel (`maintenance.equipment.employee_id` vide).

Le profil matériel est résolu depuis la feuille `Postes`
(source de vérité — jamais déduit de l'intitulé seul) ; l'alias
explicite `Stagiaire Data Analyst → Stagiaire` couvre N06. Les
contrôles bloquants (poste/profil invalide, référentiels
introuvables, R8 J-2 ouvrés, stock insuffisant) sont signalés
dans le journal sans création silencieuse de fiches incomplètes.

Prérequis : `ODOO_URL`, `ODOO_DB`, `ODOO_USER`, `ODOO_PASSWORD`
en variables d'environnement ; configuration métier sans
secrets dans `config/onboarding.json`. Mode simulation par
défaut (`--apply` pour exécuter, `--recrue N01` pour cibler) ;
journal dans `data/reports/onboarding_<mode>_<timestamp>.log`
(exemple : `onboarding_exemple_dryrun.log`).

Le module `wd_it_request` (`wd.it.request` + lignes matériel)
est installé : demande rattachée à l'employé, lignes
`proposed`/`assigned`/`unavailable`. Le cycle de vie complet est
implémenté (Partie 3) :

- **États** : `draft → assigned → in_progress → pending_user →
  resolved → closed`, plus `cancel`.
- **R12 — SLA** : priorités P1-P4, première réponse et résolution
  en heures calendaires depuis la création (P1 : 1 h / 4 h,
  P2 : 2 h / 8 h, P3 : 4 h / 24 h, P4 : 8 h / 72 h), échéances
  et indicateur « en retard ».
- **R13 — attente utilisateur** : le temps passé en
  `pending_user` est cumulé (`waiting_seconds`) ; délai net de
  résolution = résolution − création − attente.
- **R14 — résolution/clôture** : la résolution exige une note ;
  un cron quotidien clôture les demandes `resolved` depuis 3
  jours, sauf si elles ont été réouvertes.
- **R15 — réouverture** : même demande, sans doublon, compteur
  `reopen_count` incrémenté, retour à `in_progress`.
- **R16 — indicateurs** : les demandes `cancel` sont exclues des
  vues statistiques (pivot/graph) et du calcul de retard.
- **R10 — bon de remise** : rapport PDF « Bon de remise
  matériel » (`action_report_wd_it_handover`) listant les
  lignes de la demande + zones signatures employé/technicien ;
  `action_handover` enregistre `handover_date`/`handover_by`
  une seule fois, depuis les états assigned → resolved.
- **Sécurité R11** : groupes « Technicien IT » et « Responsable
  IT », règles d'enregistrement (technicien = ses demandes,
  responsable = tout, RH/employé = ses propres demandes), ACL
  dédiées sur `wd.it.request` et `wd.it.request.line`.
- **Vues** : formulaire avec statusbar/chatter, liste, kanban,
  recherche, pivot, graph.
- **Références** : séquence `IT/<année>/<numéro>` (`request_ref`),
  backfillées sur les 6 demandes d'onboarding.
- **Bonus — SLA heures ouvrées** : échéances `sla_*_deadline_open`
  et indicateur `is_late_open` calculés via
  `resource.calendar.plan_hours` sur le calendrier de l'employé
  (horaires WD, lun-ven, hors jours fériés).
- **Bonus — offboarding** : wizard `wd.it.offboarding.wizard`
  accessible depuis la fiche employé (« Départ collaborateur ») :
  archivage de la fiche, désactivation du compte Odoo et création
  d'une demande IT « Restitution matériel » listant les
  équipements affectés.
- **Bonus — formulaire public** : page `/it/request` (auth public,
  sans licence) permettant à un collaborateur de déposer une
  demande (nom, objet, catégorie, priorité, description) ; la
  demande est créée en `draft` avec `channel = portal`.

### Import historique (420 demandes)

La feuille `Demandes_IT_Historique` passe par le pipeline :
`prepare_it_requests()` dans `scripts/data/clean.py` valide les
référentiels (employé, technicien, catégorie, canal, statut,
priorité), la cohérence des dates et produit
`data/processed/Demandes_IT_Import.csv` (colonnes Odoo +
traçabilité source). 14 anomalies AVERTISSEMENT consignées au
journal (dates incohérentes conservées telles quelles, technicien
déjà exclu, etc.). L'import effectif se fait via
`scripts/odoo_import_it_history.py` dans `odoo shell` :
`request_ref` = `DemandeID` source (idempotent), `create_date`
backdaté en SQL, employé résolu via `__import__.E###`,
`technician_id` renseigné seulement si le technicien a un
compte Odoo (E021–E024 n'en ont pas → conservés en texte dans
la description). Résultat : 420 créées, 0 erreur ; 3 demandes
historiques ouvertes correctement « en retard », les 6 demandes
d'onboarding préservées. Backup :
`backups/db/odoo_pre_it_history_20261119.dump`.

Le matériel proposé reste une proposition — l'affectation
effective (`employee_id` sur `maintenance.equipment`) reste un
acte du service IT. 24 tests Odoo verts.

Documentation complète et tests :
`scripts/onboarding/README.md`,
`tests/test_onboarding.py` (27 tests, FakeClient sans réseau).

## Module congés : acquisition R1 (`wd_leave_accrual`)

`addons/wd_leave_accrual` implémente la règle R1 (2,5 j ouvrés
de CP par mois pour CDI/CDD) via le mécanisme **natif**
`hr.leave.accrual.plan` d'Odoo 18 — pas de cron custom : le
cron `_update_accrual` de `hr_holidays` génère les allocations
mensuelles. Le `post_init_hook` a rattaché le plan aux 37
employés éligibles (allocations `accrual` validées, `nextcall`
au 1er du mois) ; `hr.contract.create/write` couvre les
futures recrues, stagiaires exclus. Les allocations d'ouverture
importées coexistent sans double comptage. Limites : pas de
retrait si le contrat change de type, prorata géré par le
moteur natif. 6 tests verts. Voir
`addons/wd_leave_accrual/README.md`.
