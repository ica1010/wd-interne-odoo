# wd_equipment_profile — Profil matériel des équipements (R10)

Module Odoo 18 ajouté au parc matériel West Digital. Il ajoute
le champ **« Profil matériel »** sur `maintenance.equipment`
 pour porter la destination fonctionnelle du matériel
(règle R10), indépendamment de sa catégorie et de son modèle
physique.

## Champ créé

`wd_material_profile` — `fields.Selection`, libellé
« Profil matériel » :

| Clé technique | Libellé | Composition R10 |
|---|---|---|
| `dev` | Dev | portable Dev + écran |
| `standard` | Standard | portable Standard |
| `support` | Support | portable Standard + casque |
| `commercial` | Commercial | portable Standard + téléphone |

Le champ est **vide par défaut** : les équipements existants
conservent leurs données et leur profil reste à déterminer.

## Vues

- **Formulaire** : champ inséré juste après `Catégorie`
  (premier groupe de la fiche équipement).
- **Liste** : colonne ajoutée après `Catégorie`.
- **Recherche** : champ cherchable + filtre de regroupement
  « Profil matériel ».

Aucun droit nouveau : le champ suit les accès existants de
`maintenance.equipment` (`maintenance.group_equipment_user` /
`group_equipment_manager`).

## Installation / mise à jour

```bash
# depuis la racine du projet, conteneur odoo
docker exec wd-interne-odoo-1 odoo -d odoo \
  --db_host=db --db_user=odoo --db_password=odoo \
  -i wd_equipment_profile --stop-after-init --http-port=8071

# mise à jour ultérieure
docker exec wd-interne-odoo-1 odoo -d odoo \
  --db_host=db --db_user=odoo --db_password=odoo \
  -u wd_equipment_profile --stop-after-init --http-port=8071

docker restart wd-interne-odoo-1
```

Effectuer une sauvegarde `pg_dump` avant installation sur une
instance réelle.

## Tests

```bash
docker exec wd-interne-odoo-1 odoo -d odoo \
  --db_host=db --db_user=odoo --db_password=odoo \
  -u wd_equipment_profile --test-enable --stop-after-init \
  --http-port=8071 --log-level=test
```

Couverture : création Dev/Standard, modification du profil,
conservation de `model`/`category_id`, absence de profil sur
les équipements préexistants, valeurs de sélection, présence
du champ dans les vues form/liste/recherche.

## Limites

- Le profil est déclaratif : aucune règle automatique ne
  déduit le profil depuis la catégorie ou le modèle.
- Aucune donnée existante n'est modifiée par le module ;
  le renseignement des 85 équipements est une saisie
  manuelle ou un import ultérieur validé.
