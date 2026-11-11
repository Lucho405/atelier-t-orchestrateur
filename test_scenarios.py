"""Scenarios de bout en bout contre un FAUX Notion en memoire (aucun reseau).

Lancer : python3 -m unittest -v test_scenarios
"""

import datetime as dt
import os
import unittest

import orchestrator as o

DS = "ds-1"
BASE_ENV = {
    "NOTION_TOKEN": "t", "NOTION_DATA_SOURCE_ID": DS,
    "CADRAGE_CLAUDE_PAGE_ID": "cad-claude", "CADRAGE_ARCHIE_PAGE_ID": "cad-archie",
    "ANTHROPIC_API_KEY": "a", "ANTHROPIC_MODEL": "m1",
    "OPENAI_API_KEY": "b", "OPENAI_MODEL": "m2",
    "PRICE_ANTHROPIC_IN": "2", "PRICE_ANTHROPIC_OUT": "10",
    "PRICE_OPENAI_IN": "2", "PRICE_OPENAI_OUT": "12",
    "DRY_RUN": "0",
}
MANAGED = list(BASE_ENV) + ["ATELIER_PAUSE", "MAX_TURNS", "MAX_CALL_USD",
                             "MAX_MONTHLY_USD", "COST_MARGIN"]


def rt(text):
    return [{"plain_text": text}]


class FakeWorld:
    """Notion minimal : pages, filtres, creation, mise a jour, blocs, pannes."""

    def __init__(self):
        self.pages = {}
        self.counter = 0
        self.clock = dt.datetime(2026, 11, 11, 8, 0, tzinfo=dt.timezone.utc)
        self.calls = []          # (methode, chemin)
        self.model_calls = []    # (agent, system, user)
        self.faults = []         # (methode, motif, mode)
        self.model_text = {}     # agent -> texte ou ApiError
        self.cadrages = {"cad-claude": "Cadrage Claude.", "cad-archie": "Cadrage Archie."}

    # -- fabrication de lignes ------------------------------------------------
    def tick(self):
        self.clock += dt.timedelta(minutes=1)
        return self.clock.isoformat().replace("+00:00", "Z")

    def add(self, ident, de, pour, dossier="Architecture", auto=False,
            repond="", tour=None, text="Texte.", cout=None):
        self.counter += 1
        pid = "00000000-0000-0000-0000-%012d" % self.counter
        props = {
            "Titre": {"type": "title", "title": rt("Titre " + ident)},
            "Identifiant": {"type": "rich_text", "rich_text": rt(ident)},
            "De": {"type": "select", "select": {"name": de}},
            "Pour": {"type": "select", "select": {"name": pour}},
            "Dossier": {"type": "select", "select": {"name": dossier}},
            "Relance auto": {"type": "checkbox", "checkbox": auto},
            "Répond à": {"type": "rich_text", "rich_text": rt(repond)},
            "Tour": {"type": "number", "number": tour},
            "Coût": {"type": "number", "number": cout},
        }
        self.pages[pid] = {"id": pid, "created_time": self.tick(),
                           "properties": props, "text": text}
        return pid

    # -- filtres --------------------------------------------------------------
    def match(self, page, f):
        if "and" in f:
            return all(self.match(page, x) for x in f["and"])
        if "or" in f:
            return any(self.match(page, x) for x in f["or"])
        if f.get("timestamp") == "created_time":
            return page["created_time"] >= f["created_time"]["on_or_after"].replace("+00:00", "Z")
        prop = page["properties"].get(f["property"], {})
        if "checkbox" in f:
            return bool(prop.get("checkbox")) == f["checkbox"]["equals"]
        if "select" in f:
            return (prop.get("select") or {}).get("name") == f["select"]["equals"]
        if "rich_text" in f:
            value = "".join(x["plain_text"] for x in prop.get("rich_text", []))
            cond = f["rich_text"]
            if "contains" in cond:
                return cond["contains"] in value
            if "equals" in cond:
                return value == cond["equals"]
            if "starts_with" in cond:
                return value.startswith(cond["starts_with"])
        raise AssertionError("filtre non gere : %r" % (f,))

    # -- conversion ecriture -> lecture ------------------------------------------
    @staticmethod
    def to_read(props):
        out = {}
        for name, v in props.items():
            if "title" in v:
                out[name] = {"type": "title", "title": rt("".join(x["text"]["content"] for x in v["title"]))}
            elif "rich_text" in v:
                out[name] = {"type": "rich_text", "rich_text": rt("".join(x["text"]["content"] for x in v["rich_text"]))}
            elif "select" in v:
                out[name] = {"type": "select", "select": v["select"]}
            elif "checkbox" in v:
                out[name] = {"type": "checkbox", "checkbox": v["checkbox"]}
            elif "number" in v:
                out[name] = {"type": "number", "number": v["number"]}
            else:
                out[name] = {"type": "date"}
        return out

    # -- faux HTTP -------------------------------------------------------------
    def fault(self, method, pattern, mode):
        self.faults.append([method, pattern, mode])

    def pop_fault(self, method, path):
        for item in self.faults:
            if item[0] == method and item[1] in path:
                self.faults.remove(item)
                return item[2]
        return None

    def http(self, service, method, url, headers, body=None, retries=2, timeout=120):
        path = url.replace(o.NOTION_BASE, "")
        if service == "Notion":
            self.calls.append((method, path))
            mode = self.pop_fault(method, path)
            if mode == "avant":
                raise o.ApiError("Notion", "réseau")
            result = self.notion(method, path, body)
            if mode == "apres":    # ecrit cote Notion, mais le client voit un delai depasse
                raise o.ApiError("Notion", "réseau")
            return result
        raise AssertionError("appel inattendu : %s" % service)

    def notion(self, method, path, body):
        if method == "POST" and path.endswith("/query"):
            rows = [p for p in self.pages.values()
                    if "properties" in p and p["id"] not in self.cadrages
                    and (not body.get("filter") or self.match(p, body["filter"]))]
            for s in reversed(body.get("sorts", [])):
                rows.sort(key=lambda p: p["created_time"],
                          reverse=s["direction"] == "descending")
            if not body.get("sorts"):
                rows.sort(key=lambda p: p["created_time"])
            return {"results": rows[:body.get("page_size", 100)], "has_more": False}
        if method == "GET" and path.startswith("/pages/"):
            pid = path.split("/")[2]
            if pid not in self.pages:
                raise o.ApiError("Notion", 404)
            return self.pages[pid]
        if method == "GET" and path.startswith("/blocks/"):
            pid = path.split("/")[2]
            text = self.cadrages.get(pid)
            if text is None:
                text = self.pages[pid]["text"]
            return {"results": [{"type": "paragraph", "paragraph": {"rich_text": rt(text)}}],
                    "has_more": False}
        if method == "POST" and path == "/pages":
            self.counter += 1
            pid = "00000000-0000-0000-0000-%012d" % self.counter
            text = " ".join(
                "".join(x["text"]["content"] for x in blk[blk["type"]]["rich_text"])
                for blk in body.get("children", []))
            self.pages[pid] = {"id": pid, "created_time": self.tick(),
                               "properties": self.to_read(body["properties"]), "text": text}
            return {"id": pid}
        if method == "PATCH" and path.startswith("/pages/"):
            pid = path.split("/")[2]
            self.pages[pid]["properties"].update(self.to_read(body["properties"]))
            return {}
        raise AssertionError("route non geree : %s %s" % (method, path))

    # -- faux modeles ------------------------------------------------------------
    def model(self, agent):
        def call(system, user, max_tokens):
            self.model_calls.append((agent, system, user))
            out = self.model_text.get(agent, "POUR: %s\nTITRE: Suite\n---\n## Acquis\n- ok"
                                      % ("Archie" if agent == "Claude" else "Claude"))
            if isinstance(out, Exception):
                raise out
            return out, 1000, 200
        return call

    # -- aides --------------------------------------------------------------
    def replies_to(self, pid):
        return [p for p in self.pages.values()
                if pid in "".join(x["plain_text"] for x in p["properties"]["Répond à"]["rich_text"])]

    def writes(self):
        return [c for c in self.calls if c[0] in ("POST", "PATCH") and not c[1].endswith("/query")]


def prop(page, name):
    p = page["properties"][name]
    kind = p["type"]
    if kind == "select":
        return (p["select"] or {}).get("name")
    if kind in ("rich_text", "title"):
        return "".join(x["plain_text"] for x in p[kind])
    return p.get(kind)


class Scenarios(unittest.TestCase):
    def setUp(self):
        self.w = FakeWorld()
        self._http, self._sleep = o.http_json, o.time.sleep
        self._ca, self._co = o.call_anthropic, o.call_openai
        o.http_json = self.w.http
        o.time.sleep = lambda s: None
        o.call_anthropic = self.w.model("Claude")
        o.call_openai = self.w.model("Archie")
        for k in MANAGED:
            os.environ.pop(k, None)
        os.environ.update(BASE_ENV)

    def tearDown(self):
        o.http_json, o.time.sleep = self._http, self._sleep
        o.call_anthropic, o.call_openai = self._ca, self._co
        for k in MANAGED:
            os.environ.pop(k, None)

    def run_once(self, **extra):
        os.environ.update(extra)
        return o.main()

    # 1. deux passes portant le meme identifiant ---------------------------------
    def test_identifiants_en_double_ne_confondent_pas_les_passes(self):
        a = self.w.add("H-20261111-05", "Archie", "Claude", text="Passe A")
        reply_a = self.w.add("H-20261111-06", "Claude", "Archie",
                             repond="H-20261111-05 [src:%s]" % a)
        b = self.w.add("H-20261111-05", "Claude", "Archie", auto=True, tour=0, text="Passe B")
        self.assertEqual(self.run_once(), 0)
        self.assertEqual(len(self.w.model_calls), 1)          # B a bien ete traitee
        self.assertEqual(len(self.w.replies_to(b)), 1)
        self.assertEqual(len(self.w.replies_to(a)), 1)        # la reponse a A n'a pas bouge
        self.assertFalse(prop(self.w.pages[b], "Relance auto"))
        self.assertIn(reply_a, self.w.pages)

    # 2. budget restant insuffisant -----------------------------------------------
    def test_budget_restant_insuffisant_arrete_avant_l_appel(self):
        self.w.add("H-20261111-01", "Claude", "Luc", cout=4.99)
        src = self.w.add("H-20261111-02", "Luc", "Archie", auto=True, tour=0)
        self.run_once(MAX_MONTHLY_USD="5")
        self.assertEqual(self.w.model_calls, [])
        alert = self.w.replies_to(src)[0]
        self.assertEqual(prop(alert, "Pour"), "Luc")
        self.assertIn("insuffisant", prop(alert, "Titre"))

    # 3. echec entre creation et acquittement ---------------------------------------
    def test_echec_de_l_acquittement_ne_cree_pas_de_doublon(self):
        src = self.w.add("H-20261111-01", "Luc", "Archie", auto=True, tour=0)
        self.w.fault("PATCH", "/pages/" + src, "avant")
        self.assertEqual(self.run_once(), 0)
        self.assertEqual(len(self.w.replies_to(src)), 1)
        self.assertTrue(prop(self.w.pages[src], "Relance auto"))   # toujours coche
        self.assertEqual(self.run_once(), 0)                         # passage suivant
        self.assertEqual(len(self.w.replies_to(src)), 1)             # pas de 2e reponse
        self.assertEqual(len(self.w.model_calls), 1)                 # pas de 2e appel facture
        self.assertFalse(prop(self.w.pages[src], "Relance auto"))

    # 4. echec reseau a la creation -------------------------------------------------
    def test_delai_depasse_alors_que_notion_a_ecrit(self):
        src = self.w.add("H-20261111-01", "Luc", "Archie", auto=True, tour=0)
        self.w.fault("POST", "/pages", "apres")
        self.run_once()
        self.assertEqual(len(self.w.replies_to(src)), 1)   # exactement une

    def test_echec_avant_ecriture_recree_une_seule_fois(self):
        src = self.w.add("H-20261111-01", "Luc", "Archie", auto=True, tour=0)
        self.w.fault("POST", "/pages", "avant")
        self.run_once()
        self.assertEqual(len(self.w.replies_to(src)), 1)
        self.assertEqual(len(self.w.model_calls), 1)

    # 5. deux conversations dans un meme dossier ----------------------------------------
    def test_le_contexte_suit_le_fil_pas_le_dossier(self):
        x1 = self.w.add("H-20261111-01", "Luc", "Archie", text="FIL-X-DEBUT")
        self.w.add("H-20261111-02", "Luc", "Claude", text="AUTRE-CONVERSATION-Y")
        self.w.add("H-20261111-03", "Archie", "Claude", text="FIL-X-SUITE",
                   repond="H-20261111-01 (page %s)" % x1)
        # passe a traiter : repond a la precedente, par identifiant + UUID
        x2 = [p for p in self.w.pages if self.w.pages[p]["text"] == "FIL-X-SUITE"][0]
        self.w.add("H-20261111-04", "Claude", "Archie", auto=True, tour=1, text="FIL-X-FIN",
                   repond="H-20261111-03 [src:%s]" % x2)
        self.run_once()
        user = self.w.model_calls[0][2]
        self.assertIn("FIL-X-DEBUT", user)
        self.assertIn("FIL-X-SUITE", user)
        self.assertIn("FIL-X-FIN", user)
        self.assertNotIn("AUTRE-CONVERSATION-Y", user)

    def test_passe_isolee_recoit_le_dossier_etiquete_hors_fil(self):
        self.w.add("H-20261111-01", "Luc", "Claude", text="VOISINE")
        self.w.add("H-20261111-02", "Luc", "Archie", auto=True, tour=0, text="NOUVELLE")
        self.run_once()
        user = self.w.model_calls[0][2]
        self.assertIn("hors fil", user)

    # 6. sortie mal formee ------------------------------------------------------------------
    def test_sortie_mal_formee_renvoie_a_luc_sans_relance(self):
        src = self.w.add("H-20261111-01", "Luc", "Archie", auto=True, tour=0)
        self.w.model_text["Archie"] = "Réponse libre, sans en-tête."
        self.run_once()
        reply = self.w.replies_to(src)[0]
        self.assertEqual(prop(reply, "Pour"), "Luc")
        self.assertEqual(prop(reply, "Statut"), "Arbitrage Luc")
        self.assertFalse(prop(reply, "Relance auto"))
        self.assertIn("Format de sortie non respecté", reply["text"])

    def test_retour_explicite_a_luc_respecte(self):
        src = self.w.add("H-20261111-01", "Luc", "Archie", auto=True, tour=0)
        self.w.model_text["Archie"] = "POUR: Luc\nTITRE: Arbitrage\n---\n## Acquis\n- accord"
        self.run_once()
        reply = self.w.replies_to(src)[0]
        self.assertEqual(prop(reply, "Pour"), "Luc")
        self.assertFalse(prop(reply, "Relance auto"))

    def test_adresse_a_soi_meme_renvoie_a_luc(self):
        src = self.w.add("H-20261111-01", "Luc", "Archie", auto=True, tour=0)
        self.w.model_text["Archie"] = "POUR: Archie\nTITRE: x\n---\ncorps"
        self.run_once()
        self.assertEqual(prop(self.w.replies_to(src)[0], "Pour"), "Luc")

    # 7. securite DRY_RUN -------------------------------------------------------------------------
    def test_dry_run_n_ecrit_rien_et_n_appelle_aucun_modele(self):
        self.w.add("H-20261111-01", "Luc", "Archie", auto=True, tour=0)
        self.assertEqual(self.run_once(DRY_RUN="1"), 0)
        self.assertEqual(self.w.writes(), [])
        self.assertEqual(self.w.model_calls, [])

    def test_dry_run_par_defaut(self):
        self.w.add("H-20261111-01", "Luc", "Archie", auto=True, tour=0)
        os.environ.pop("DRY_RUN")
        self.assertEqual(o.main(), 0)
        self.assertEqual(self.w.writes(), [])
        self.assertEqual(self.w.model_calls, [])

    def test_pause_ne_fait_aucun_appel(self):
        self.w.add("H-20261111-01", "Luc", "Archie", auto=True, tour=0)
        self.run_once(ATELIER_PAUSE="1")
        self.assertEqual(self.w.calls, [])

    # 8. couts des echecs -----------------------------------------------------------------------------
    def test_echec_possiblement_facture_est_compte_dans_le_budget(self):
        src = self.w.add("H-20261111-01", "Luc", "Archie", auto=True, tour=0)
        self.w.model_text["Archie"] = o.ApiError("OpenAI", "réseau")
        self.assertEqual(self.run_once(), 1)
        alert = self.w.replies_to(src)[0]
        self.assertGreater(prop(alert, "Coût"), 0)

    def test_echec_non_facture_ne_coute_rien(self):
        src = self.w.add("H-20261111-01", "Luc", "Archie", auto=True, tour=0)
        self.w.model_text["Archie"] = o.ApiError("OpenAI", 429)
        self.run_once()
        self.assertNotIn("Coût", self.w.replies_to(src)[0]["properties"])

    # 9. plafond de tours et chaine normale ---------------------------------------------------------------
    def test_chaine_normale_puis_plafond_de_tours(self):
        src = self.w.add("H-20261111-01", "Luc", "Archie", auto=True, tour=0)
        self.run_once(MAX_TURNS="2")
        r1 = self.w.replies_to(src)[0]
        self.assertEqual((prop(r1, "De"), prop(r1, "Pour"), prop(r1, "Tour")), ("Archie", "Claude", 1))
        self.assertTrue(prop(r1, "Relance auto"))
        self.run_once(MAX_TURNS="2")
        r2 = self.w.replies_to(r1["id"])[0]
        self.assertEqual((prop(r2, "De"), prop(r2, "Pour"), prop(r2, "Tour")), ("Claude", "Luc", 2))
        self.assertFalse(prop(r2, "Relance auto"))
        self.assertEqual(prop(r2, "Statut"), "Arbitrage Luc")

    # 10. journal : pas de contenu ---------------------------------------------------------------------------
    def test_journal_sans_contenu(self):
        import contextlib
        import io
        self.w.add("H-20261111-01", "Luc", "Archie", auto=True, tour=0, text="SECRET-DE-PASSE")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.run_once()
        self.assertNotIn("SECRET-DE-PASSE", buf.getvalue())
        self.assertNotIn("Acquis", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
