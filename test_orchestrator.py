"""Tests des fonctions pures (sans réseau). Lancer : python3 -m unittest -v"""

import datetime as dt
import os
import unittest

import orchestrator as o


class ParseOutput(unittest.TestCase):
    def test_format_complet(self):
        raw = "POUR: Claude\nTITRE: Contre-lecture du squelette\n---\n## Acquis\n- un point"
        pour, titre, body, valid = o.parse_output(raw)
        self.assertEqual(pour, "Claude")
        self.assertEqual(titre, "Contre-lecture du squelette")
        self.assertTrue(body.startswith("## Acquis"))
        self.assertTrue(valid)

    def test_pour_luc_avec_gras(self):
        raw = "**POUR: Luc**\n**TITRE: Arbitrage**\n---\ncorps"
        pour, titre, body, valid = o.parse_output(raw)
        self.assertEqual((pour, titre, body, valid), ("Luc", "Arbitrage", "corps", True))

    def test_format_absent_est_invalide(self):
        pour, titre, body, valid = o.parse_output("Une réponse libre.\nSuite.")
        self.assertIsNone(pour)
        self.assertFalse(valid)
        self.assertEqual(titre, "Une réponse libre.")

    def test_destinataire_inconnu_invalide(self):
        self.assertFalse(o.parse_output("POUR: Mistral\nTITRE: x\n---\ny")[3])

    def test_destinataires_multiples_invalides(self):
        self.assertFalse(o.parse_output("POUR: Archie | Claude | Luc\nTITRE: x\n---\ny")[3])

    def test_corps_vide_invalide(self):
        self.assertFalse(o.parse_output("POUR: Claude\nTITRE: x\n---\n")[3])


class Uuids(unittest.TestCase):
    def test_norm_uuid(self):
        self.assertEqual(o.norm_uuid("4154B94292178108843BEFB250B25055"),
                         "4154b942-9217-8108-843b-efb250b25055")

    def test_parent_uuids_formats_archie_et_script(self):
        row = {"properties": {"Répond à": {"type": "rich_text", "rich_text": [
            {"plain_text": "H-20261111-05 (Claude, page 4154b942-9217-81b0-a844-f8a15698cd9b) "
                           "[src:4154b942-9217-81a4-ae9b-f235e633576a]"}]}}}
        self.assertEqual(o.parent_uuids(row), ["4154b942-9217-81b0-a844-f8a15698cd9b",
                                               "4154b942-9217-81a4-ae9b-f235e633576a"])


class Identifiers(unittest.TestCase):
    def test_premier_du_jour(self):
        self.assertEqual(o.next_identifier([], dt.date(2026, 11, 11)), "H-20261111-01")

    def test_suite_avec_doublons(self):
        ids = ["H-20261111-01", "H-20261111-05", "H-20261111-05", "H-20261110-09", None]
        self.assertEqual(o.next_identifier(ids, dt.date(2026, 11, 11)), "H-20261111-06")


class Blocks(unittest.TestCase):
    def test_conversion(self):
        blocks = o.text_to_blocks("## Titre\n\n- puce\nParagraphe")
        kinds = [b["type"] for b in blocks]
        self.assertEqual(kinds, ["heading_2", "bulleted_list_item", "paragraph"])

    def test_segments_longs_coupes(self):
        text = "a" * 4000
        parts = o.split_rich(text)
        self.assertEqual(len(parts), 3)
        self.assertTrue(all(len(p) <= o.RICH_CHUNK for p in parts))
        self.assertEqual("".join(parts), text)

    def test_texte_vide(self):
        self.assertEqual(o.text_to_blocks("  \n\n"), [])


class Costs(unittest.TestCase):
    def setUp(self):
        os.environ["PRICE_ANTHROPIC_IN"] = "2"
        os.environ["PRICE_ANTHROPIC_OUT"] = "10"

    def tearDown(self):
        os.environ.pop("PRICE_ANTHROPIC_IN", None)
        os.environ.pop("PRICE_ANTHROPIC_OUT", None)

    def test_cout_passe_type(self):
        # 20 000 tokens en entrée, 2 000 en sortie : 0,04 + 0,02 = 0,06 $
        self.assertAlmostEqual(o.cost_usd("Claude", 20000, 2000), 0.06, places=6)

    def test_prix_absents_donnent_zero(self):
        os.environ.pop("PRICE_OPENAI_IN", None)
        os.environ.pop("PRICE_OPENAI_OUT", None)
        self.assertEqual(o.prices("Archie"), (0.0, 0.0))

    def test_virgule_decimale(self):
        os.environ["PRICE_OPENAI_IN"] = "0,2"
        try:
            self.assertEqual(o.prices("Archie")[0], 0.2)
        finally:
            os.environ.pop("PRICE_OPENAI_IN", None)


class Misc(unittest.TestCase):
    def test_other_agent(self):
        self.assertEqual(o.other_agent("Claude"), "Archie")
        self.assertEqual(o.other_agent("Archie"), "Claude")

    def test_block_to_text(self):
        block = {"type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "Acquis"}]}}
        self.assertEqual(o.block_to_text(block), "## Acquis")
        empty = {"type": "paragraph", "paragraph": {"rich_text": []}}
        self.assertEqual(o.block_to_text(empty), "")


if __name__ == "__main__":
    unittest.main()
