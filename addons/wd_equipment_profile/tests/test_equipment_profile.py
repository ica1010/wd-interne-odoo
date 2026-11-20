from lxml import etree

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestEquipmentProfile(TransactionCase):
    """Tests du champ « Profil matériel » (règle R10)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Equipment = cls.env["maintenance.equipment"]
        # Baseline avant tout test : les équipements existants
        # (importés avant l'installation du module) ne doivent
        # avoir aucun profil renseigné.
        cls.baseline_total = cls.Equipment.search_count([])
        cls.baseline_profiled = cls.Equipment.search_count(
            [("wd_material_profile", "!=", False)]
        )

    def test_create_with_dev_profile(self):
        equip = self.Equipment.create({
            "name": "Portable Dev Test",
            "model": "ProBook 450 G10 i5/16Go",
            "wd_material_profile": "dev",
        })
        self.assertEqual(equip.wd_material_profile, "dev")
        # Le libellé affiché correspond à la sélection.
        field = self.Equipment._fields["wd_material_profile"]
        self.assertEqual(
            dict(field.selection)["dev"], "Dev"
        )

    def test_create_with_standard_profile(self):
        equip = self.Equipment.create({
            "name": "Portable Std Test",
            "wd_material_profile": "standard",
        })
        self.assertEqual(equip.wd_material_profile, "standard")

    def test_write_profile_change(self):
        equip = self.Equipment.create({
            "name": "Equipement modifiable",
            "wd_material_profile": "standard",
        })
        equip.write({"wd_material_profile": "support"})
        self.assertEqual(equip.wd_material_profile, "support")

    def test_model_and_category_preserved(self):
        category = self.env["maintenance.equipment.category"].create({
            "name": "Catégorie test profil",
        })
        equip = self.Equipment.create({
            "name": "Equipement données",
            "model": "ProBook 450 G10 i5/16Go",
            "category_id": category.id,
            "wd_material_profile": "commercial",
        })
        equip.write({"wd_material_profile": "dev"})
        self.assertEqual(equip.model, "ProBook 450 G10 i5/16Go")
        self.assertEqual(equip.category_id, category)

    def test_existing_equipment_untouched(self):
        """L'installation n'a renseigné aucun profil existant."""
        self.assertGreater(self.baseline_total, 0)
        self.assertEqual(self.baseline_profiled, 0)

    def test_selection_values(self):
        field = self.Equipment._fields["wd_material_profile"]
        values = {key for key, _label in field.selection}
        self.assertEqual(
            values, {"dev", "standard", "support", "commercial"}
        )

    def test_profile_in_views(self):
        """Le champ est présent dans les vues form, liste, recherche."""
        for xmlid in [
            "maintenance.hr_equipment_view_form",
            "maintenance.hr_equipment_view_tree",
            "maintenance.hr_equipment_view_search",
        ]:
            view = self.env.ref(xmlid)
            # La vue effective (héritée) contient le champ.
            arch = etree.tostring(
                view._get_combined_arch(), encoding="unicode"
            )
            self.assertIn(
                "wd_material_profile", arch,
                f"Champ absent de la vue {xmlid}",
            )
