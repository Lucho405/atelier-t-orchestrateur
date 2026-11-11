#!/usr/bin/env python3
"""Atelier T - orchestrateur minimal Notion <-> API Claude / API Archie (GPT).

A chaque execution, le script traite AU PLUS UNE passe :
  1. cherche, dans la base PASSES, la plus ancienne ligne dont "Relance auto"
     est cochee et dont "Pour" est Archie ou Claude ;
  2. lit le cadrage de l'agent, puis le FIL de reponses menant a la passe
     (par UUID Notion, jamais par identifiant seul) ;
  3. appelle UN modele (API Anthropic ou API OpenAI) ;
  4. ecrit la reponse comme une nouvelle ligne ("Repond a" renseigne),
     decoche la ligne traitee, enregistre le tour et le cout.

Garde-fous : mode essai par defaut, pause globale, 3 tours automatiques
au maximum, plafond mensuel et plafond par appel, aucune ecriture ailleurs
que dans la base PASSES, jamais de contenu dans les journaux.

Bibliotheque standard uniquement (aucune installation de paquet).
"""

import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

# ----------------------------------------------------------------------------
# Constantes
# ----------------------------------------------------------------------------

NOTION_BASE = "https://api.notion.com/v1"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENAI_URL = "https://api.openai.com/v1/responses"

VERSION = "0.2"

AGENTS = ("Archie", "Claude")
VALID_POUR = ("Archie", "Claude", "Luc")

P_TITRE = "Titre"
P_ID = "Identifiant"
P_DE = "De"
P_POUR = "Pour"
P_DOSSIER = "Dossier"
P_STATUT = "Statut"
P_DATE = "Date"
P_REPOND = "Répond à"
P_AUTO = "Relance auto"
P_TOUR = "Tour"
P_COUT = "Coût"

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}")

RICH_CHUNK = 1800          # limite Notion : 2000 caracteres par segment
BLOCKS_PER_REQUEST = 90    # limite Notion : 100 blocs par requete

RULES = """Règles fixes de l'Atelier (elles s'ajoutent au cadrage ci-dessus).

Tu réponds à UNE passe. Format de sortie OBLIGATOIRE, dans cet ordre :

POUR: Archie | Claude | Luc
TITRE: <titre court, sans identifiant, sans flèche>
---
<corps de la passe>

Le corps distingue trois parties, avec ces intertitres exacts : "Acquis",
"Contesté ou incertain", "Action suivante". Il pose au plus une question à Luc,
à la fin, sous l'intertitre "Question à Luc". Écris POUR: Luc si tu as besoin
d'un arbitrage, ou si l'accord entre Archie et Claude est atteint. Sinon
adresse la passe à l'autre voix.

Tu ne présentes jamais une proposition comme une décision de Luc. Tu n'affirmes
pas avoir lu un document qui ne figure pas dans le contexte fourni. Tu ne cites
pas de chiffre ni de source que tu ne peux pas vérifier : tu dis ce que tu
supposes. Tu n'écris jamais sur WordPress ni sur Refuge404.
"""


# ----------------------------------------------------------------------------
# Configuration et journal
# ----------------------------------------------------------------------------

def env(name, default=None, required=False):
    value = os.environ.get(name, default)
    if isinstance(value, str):
        value = value.strip()
    if required and not value:
        raise SystemExit("Variable manquante : %s" % name)
    return value


def env_float(name, default):
    raw = env(name, "")
    if raw in (None, ""):
        return float(default)
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        raise SystemExit("Valeur numérique invalide pour %s" % name)


def env_int(name, default):
    return int(env_float(name, default))


def log(message):
    """Journal : jamais de contenu des passes, seulement des identifiants."""
    print("[atelier] %s" % message, flush=True)


def other_agent(agent):
    return "Claude" if agent == "Archie" else "Archie"


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------

class ApiError(Exception):
    """Erreur d'API sans le corps de la reponse (qui pourrait contenir du contenu)."""

    def __init__(self, service, status):
        super().__init__("%s : erreur HTTP %s" % (service, status))
        self.service = service
        self.status = status


def http_json(service, method, url, headers, body=None, retries=2, timeout=120):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    last_status = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            last_status = error.code
            retryable = error.code in (429, 500, 502, 503, 504, 529)
            if retryable and attempt < retries:
                wait = 2 ** attempt * 3
                retry_after = error.headers.get("Retry-After") if error.headers else None
                if retry_after and retry_after.isdigit():
                    wait = min(int(retry_after), 30)
                time.sleep(wait)
                continue
            raise ApiError(service, error.code)
        except (urllib.error.URLError, TimeoutError):
            last_status = "réseau"
            if attempt < retries:
                time.sleep(2 ** attempt * 3)
                continue
            raise ApiError(service, last_status)
    raise ApiError(service, last_status)


# ----------------------------------------------------------------------------
# Notion
# ----------------------------------------------------------------------------

class Notion:
    def __init__(self, token, data_source_id, version):
        self.data_source_id = data_source_id
        self.headers = {
            "Authorization": "Bearer " + token,
            "Notion-Version": version,
            "Content-Type": "application/json",
        }

    def call(self, method, path, body=None, retries=2):
        time.sleep(0.35)  # limite Notion : environ 3 requetes par seconde
        return http_json("Notion", method, NOTION_BASE + path, self.headers, body,
                         retries=retries)

    def get_page(self, page_id):
        return self.call("GET", "/pages/%s" % page_id)

    def query(self, flt=None, sorts=None, limit=100):
        rows, cursor = [], None
        while len(rows) < limit:
            body = {"page_size": min(100, limit - len(rows))}
            if flt:
                body["filter"] = flt
            if sorts:
                body["sorts"] = sorts
            if cursor:
                body["start_cursor"] = cursor
            result = self.call("POST", "/data_sources/%s/query" % self.data_source_id, body)
            rows.extend(result.get("results", []))
            if not result.get("has_more"):
                break
            cursor = result.get("next_cursor")
        return rows[:limit]

    def page_text(self, page_id, max_chars):
        """Texte brut d'une page (un niveau de blocs), tronque a max_chars."""
        lines, cursor = [], None
        while True:
            path = "/blocks/%s/children?page_size=100" % page_id
            if cursor:
                path += "&start_cursor=" + cursor
            result = self.call("GET", path)
            for block in result.get("results", []):
                line = block_to_text(block)
                if line:
                    lines.append(line)
            if not result.get("has_more"):
                break
            cursor = result.get("next_cursor")
            if sum(len(line) for line in lines) > max_chars * 2:
                break
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n[... tronqué ...]"
        return text

    def create_row(self, properties, blocks):
        body = {
            "parent": {"type": "data_source_id", "data_source_id": self.data_source_id},
            "properties": properties,
            "children": blocks[:BLOCKS_PER_REQUEST],
        }
        # Ecritures NON idempotentes : jamais de nouvel essai automatique ici.
        # Le doublon eventuel est gere par la verification "la reponse existe-t-elle ?".
        page = self.call("POST", "/pages", body, retries=0)
        rest = blocks[BLOCKS_PER_REQUEST:]
        while rest:
            chunk, rest = rest[:BLOCKS_PER_REQUEST], rest[BLOCKS_PER_REQUEST:]
            self.call("PATCH", "/blocks/%s/children" % page["id"], {"children": chunk},
                      retries=0)
        return page

    def update_row(self, page_id, properties):
        return self.call("PATCH", "/pages/%s" % page_id, {"properties": properties})


def rich_plain(items):
    return "".join(item.get("plain_text", "") for item in (items or []))


def block_to_text(block):
    kind = block.get("type")
    content = block.get(kind) or {}
    text = rich_plain(content.get("rich_text"))
    if not text:
        return ""
    if kind == "heading_1":
        return "# " + text
    if kind == "heading_2":
        return "## " + text
    if kind == "heading_3":
        return "### " + text
    if kind in ("bulleted_list_item", "to_do"):
        return "- " + text
    if kind == "numbered_list_item":
        return "1. " + text
    if kind == "quote":
        return "> " + text
    return text


def prop_text(page, name):
    prop = (page.get("properties") or {}).get(name) or {}
    if prop.get("type") == "title":
        return rich_plain(prop.get("title"))
    if prop.get("type") == "rich_text":
        return rich_plain(prop.get("rich_text"))
    if prop.get("type") == "select":
        return (prop.get("select") or {}).get("name", "")
    return ""


def prop_checkbox(page, name):
    prop = (page.get("properties") or {}).get(name) or {}
    return bool(prop.get("checkbox"))


def prop_number(page, name):
    prop = (page.get("properties") or {}).get(name) or {}
    value = prop.get("number")
    return float(value) if value is not None else 0.0


def split_rich(text, size=RICH_CHUNK):
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


def rich_array(text):
    return [{"type": "text", "text": {"content": chunk}} for chunk in split_rich(text)]


def text_to_blocks(text):
    """Convertit un texte simple en blocs Notion : titres '## ', puces '- ', paragraphes."""
    blocks = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        match = re.match(r"^(#{1,3})\s+(.*)$", line)
        if match:
            kind = "heading_%d" % len(match.group(1))
            blocks.append({"object": "block", "type": kind,
                           kind: {"rich_text": rich_array(match.group(2))}})
        elif re.match(r"^[-*]\s+", line):
            content = re.sub(r"^[-*]\s+", "", line)
            blocks.append({"object": "block", "type": "bulleted_list_item",
                           "bulleted_list_item": {"rich_text": rich_array(content)}})
        else:
            blocks.append({"object": "block", "type": "paragraph",
                           "paragraph": {"rich_text": rich_array(line)}})
    return blocks


# ----------------------------------------------------------------------------
# Modeles
# ----------------------------------------------------------------------------

def call_anthropic(system, user, max_tokens):
    key = env("ANTHROPIC_API_KEY", required=True)
    model = env("ANTHROPIC_MODEL", required=True)
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    result = http_json("Anthropic", "POST", ANTHROPIC_URL, headers, body)
    text = "".join(part.get("text", "") for part in result.get("content", [])
                   if part.get("type") == "text")
    usage = result.get("usage") or {}
    return text, usage.get("input_tokens"), usage.get("output_tokens")


def call_openai(system, user, max_tokens):
    key = env("OPENAI_API_KEY", required=True)
    model = env("OPENAI_MODEL", required=True)
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    body = {
        "model": model,
        "instructions": system,
        "input": user,
        "max_output_tokens": max_tokens,
    }
    effort = env("OPENAI_REASONING_EFFORT", "")
    if effort:
        body["reasoning"] = {"effort": effort}
    result = http_json("OpenAI", "POST", OPENAI_URL, headers, body)
    text = result.get("output_text") or ""
    if not text:
        pieces = []
        for item in result.get("output", []):
            if item.get("type") == "message":
                for part in item.get("content", []):
                    if part.get("type") in ("output_text", "text"):
                        pieces.append(part.get("text", ""))
        text = "".join(pieces)
    usage = result.get("usage") or {}
    return text, usage.get("input_tokens"), usage.get("output_tokens")


def prices(agent):
    """Prix en dollars par million de tokens. Obligatoires : sans eux, pas de plafond."""
    prefix = "ANTHROPIC" if agent == "Claude" else "OPENAI"
    return (env_float("PRICE_%s_IN" % prefix, 0), env_float("PRICE_%s_OUT" % prefix, 0))


def cost_usd(agent, tokens_in, tokens_out):
    price_in, price_out = prices(agent)
    return (tokens_in or 0) * price_in / 1e6 + (tokens_out or 0) * price_out / 1e6


def estimate_tokens(text):
    return int(len(text) / 3) + 1   # estimation prudente pour du francais


# ----------------------------------------------------------------------------
# Lecture de la sortie du modele
# ----------------------------------------------------------------------------

def parse_output(raw):
    """Retourne (pour, titre, corps, valide).

    Strict : si l'en-tete POUR / TITRE / --- manque, si POUR n'est pas UN seul
    destinataire connu, la sortie est declaree non valide. Le script ne devine
    alors jamais un destinataire : il renvoie la main a Luc.
    """
    pour, titre, body, valid = None, "", raw.strip(), False
    head, sep, rest = raw.partition("\n---")
    if sep:
        body = rest.lstrip("-").strip()
        for line in head.splitlines():
            line = line.strip().lstrip("*# ").strip()
            low = line.lower()
            if low.startswith("pour:") or low.startswith("pour :"):
                value = line.split(":", 1)[1].strip().strip("*").strip()
                found = [n for n in VALID_POUR if value.lower() == n.lower()]
                pour = found[0] if len(found) == 1 else None
            elif low.startswith("titre:") or low.startswith("titre :"):
                titre = line.split(":", 1)[1].strip().strip("*").strip()
        valid = pour is not None and bool(body)
    if not titre:
        first = next((ln.strip() for ln in body.splitlines() if ln.strip()), "Réponse")
        titre = first.lstrip("#* ").strip()[:90]
    return pour, titre[:140], body, valid


def next_identifier(existing_ids, today):
    prefix = "H-%s-" % today.strftime("%Y%m%d")
    highest = 0
    for value in existing_ids:
        match = re.match(r"^%s(\d+)" % re.escape(prefix), value or "")
        if match:
            highest = max(highest, int(match.group(1)))
    return "%s%02d" % (prefix, highest + 1)


# ----------------------------------------------------------------------------
# Logique principale
# ----------------------------------------------------------------------------

def month_start_iso(now):
    return dt.datetime(now.year, now.month, 1, tzinfo=dt.timezone.utc).isoformat()


def monthly_spend(notion, now):
    flt = {"timestamp": "created_time",
           "created_time": {"on_or_after": month_start_iso(now)}}
    rows = notion.query(flt=flt, limit=500)
    return sum(prop_number(row, P_COUT) for row in rows)


def find_pending(notion):
    flt = {"and": [
        {"property": P_AUTO, "checkbox": {"equals": True}},
        {"or": [{"property": P_POUR, "select": {"equals": name}} for name in AGENTS]},
    ]}
    sorts = [{"timestamp": "created_time", "direction": "ascending"}]
    rows = notion.query(flt=flt, sorts=sorts, limit=20)
    for row in rows:
        if prop_text(row, P_DE) != prop_text(row, P_POUR):
            return row
    return None


def norm_uuid(text):
    """Met un UUID au format avec tirets (minuscules)."""
    h = re.sub(r"[^0-9a-fA-F]", "", text).lower()
    return "%s-%s-%s-%s-%s" % (h[:8], h[8:12], h[12:16], h[16:20], h[20:32])


def source_marker(row):
    """Texte ecrit dans 'Repond a' : identifiant lisible + UUID Notion de la source."""
    ident = prop_text(row, P_ID) or "sans identifiant"
    return "%s [src:%s]" % (ident, row["id"])


def parent_uuids(row):
    text = prop_text(row, P_REPOND)
    return [norm_uuid(m) for m in UUID_RE.findall(text)]


def reply_already_exists(notion, row, agent):
    """Une reponse de `agent` pointe-t-elle deja vers CETTE page (UUID, pas identifiant) ?"""
    dashed = row["id"]
    plain = dashed.replace("-", "")
    flt = {"and": [
        {"property": P_DE, "select": {"equals": agent}},
        {"or": [
            {"property": P_REPOND, "rich_text": {"contains": dashed}},
            {"property": P_REPOND, "rich_text": {"contains": plain}},
        ]},
        {"timestamp": "created_time",
         "created_time": {"on_or_after": row["created_time"]}},
    ]}
    return bool(notion.query(flt=flt, limit=1))


def resolve_parent(notion, row):
    """Passe a laquelle `row` repond : par UUID, sinon par identifiant unique."""
    for uid in parent_uuids(row):
        try:
            page = notion.get_page(uid)
        except ApiError:
            continue
        if not page.get("archived") and not page.get("in_trash"):
            return page
    text = prop_text(row, P_REPOND)
    match = re.match(r"\s*(H-\d{8}-\d+)", text)
    if match:
        found = notion.query(flt={"property": P_ID, "rich_text": {"equals": match.group(1)}},
                             limit=3)
        if len(found) == 1:
            return found[0]
        log("ancêtre ambigu ou introuvable pour %s : fil interrompu" % match.group(1))
    return None


def thread_of(notion, row, depth):
    """Fil de reponses (de la plus ancienne a la passe a traiter), `depth` passes au plus."""
    chain, seen, current = [row], {row["id"]}, row
    while len(chain) < depth:
        parent = resolve_parent(notion, current)
        if parent is None or parent["id"] in seen:
            break
        chain.append(parent)
        seen.add(parent["id"])
        current = parent
    chain.reverse()
    return chain


def build_context(notion, row, passes, max_chars):
    chain = thread_of(notion, row, passes)
    parts = []
    for item in chain:
        header = "[%s | %s -> %s | %s | fil]" % (
            prop_text(item, P_ID) or "sans identifiant",
            prop_text(item, P_DE) or "?", prop_text(item, P_POUR) or "?",
            item.get("created_time", "")[:10])
        parts.append(header + "\n" + notion.page_text(item["id"], max_chars))
    dossier = prop_text(row, P_DOSSIER)
    if len(chain) < 2 and dossier:
        flt = {"property": P_DOSSIER, "select": {"equals": dossier}}
        sorts = [{"timestamp": "created_time", "direction": "descending"}]
        known = {c["id"] for c in chain}
        extra = [r for r in notion.query(flt=flt, sorts=sorts, limit=4) if r["id"] not in known][:2]
        extra.sort(key=lambda r: r["created_time"])
        for item in extra:
            header = "[%s | %s -> %s | %s | hors fil, même dossier]" % (
                prop_text(item, P_ID) or "sans identifiant",
                prop_text(item, P_DE), prop_text(item, P_POUR),
                item.get("created_time", "")[:10])
            parts.insert(0, header + "\n" + notion.page_text(item["id"], max_chars // 2))
    return "\n\n".join(parts)


def alert_luc(notion, source, agent, reason, now, dry_run, cost=0.0):
    """Ecrit une courte ligne a destination de Luc et decoche la ligne source.

    `cost` : cout (pessimiste) d'un appel peut-etre facture, compte dans le plafond.
    """
    log("arrêt de la passe %s : %s" % (prop_text(source, P_ID) or source["id"], reason))
    if dry_run:
        return
    ident = next_identifier(existing_ids_today(notion, now), now)
    properties = {
        P_TITRE: {"title": rich_array("Arrêt automatique : " + reason)},
        P_ID: {"rich_text": rich_array(ident)},
        P_DE: {"select": {"name": agent}},
        P_POUR: {"select": {"name": "Luc"}},
        P_STATUT: {"select": {"name": "Arbitrage Luc"}},
        P_DATE: {"date": {"start": now.date().isoformat()}},
        P_REPOND: {"rich_text": rich_array(source_marker(source))},
        P_AUTO: {"checkbox": False},
    }
    if cost > 0:
        properties[P_COUT] = {"number": round(cost, 4)}
    dossier = prop_text(source, P_DOSSIER)
    if dossier:
        properties[P_DOSSIER] = {"select": {"name": dossier}}
    body = ("Le script s'est arrêté sans écrire de réponse. "
            "Raison : %s.\nRien n'a été publié nulle part. Relis la passe %s, "
            "puis recoche « Relance auto » si tu veux relancer." %
            (reason, prop_text(source, P_ID) or "sans identifiant"))
    write_once(notion, source, agent, properties, text_to_blocks(body))
    acknowledge(notion, source)


def write_once(notion, source, agent, properties, blocks):
    """Cree la ligne de reponse SANS doublon.

    La creation n'est jamais rejouee a l'aveugle : si elle echoue (y compris par
    delai depasse alors que Notion l'a peut-etre enregistree), on verifie d'abord
    si la reponse existe ; on ne recree qu'une fois, apres avoir constate l'absence.
    """
    try:
        notion.create_row(properties, blocks)
        return
    except ApiError as error:
        log("création de la réponse incertaine (%s) : vérification" % error)
    if reply_already_exists(notion, source, agent):
        log("la réponse existe : pas de doublon")
        return
    notion.create_row(properties, blocks)


def acknowledge(notion, source):
    """Decoche la ligne source. Idempotent ; en cas d'echec, l'execution suivante le refera."""
    try:
        notion.update_row(source["id"], {P_AUTO: {"checkbox": False}})
    except ApiError as error:
        log("acquittement échoué (%s) : sera refait au prochain passage" % error)


def existing_ids_today(notion, now):
    prefix = "H-%s-" % now.strftime("%Y%m%d")
    flt = {"property": P_ID, "rich_text": {"starts_with": prefix}}
    return [prop_text(r, P_ID) for r in notion.query(flt=flt, limit=100)]


# statuts HTTP pour lesquels un appel n'a certainement pas ete facture
NOT_BILLED = (400, 401, 403, 404, 422, 429)


def main():
    dry_run = env("DRY_RUN", "1") != "0"
    log("version %s" % VERSION)
    if env("ATELIER_PAUSE", "") not in ("", "0"):
        log("pause demandée (ATELIER_PAUSE) : rien à faire")
        return 0

    notion = Notion(
        env("NOTION_TOKEN", required=True),
        env("NOTION_DATA_SOURCE_ID", required=True),
        env("NOTION_VERSION", "2025-09-03"),
    )
    now = dt.datetime.now(dt.timezone.utc)

    row = find_pending(notion)
    if row is None:
        log("aucune passe à traiter")
        return 0

    ident = prop_text(row, P_ID) or row["id"]
    agent = prop_text(row, P_POUR)
    log("passe à traiter : %s, pour %s (mode %s)" % (ident, agent, "essai" if dry_run else "réel"))

    if reply_already_exists(notion, row, agent):
        log("une réponse de %s existe déjà pour cette page : la ligne est simplement décochée" % agent)
        if not dry_run:
            acknowledge(notion, row)
        return 0

    if not dry_run:
        price_in, price_out = prices(agent)
        if price_in <= 0 or price_out <= 0:
            alert_luc(notion, row, agent, "prix des tokens non renseignés (plafond impossible)", now, False)
            return 1

    max_month = env_float("MAX_MONTHLY_USD", 5)
    max_call = env_float("MAX_CALL_USD", 0.5)
    max_turns = env_int("MAX_TURNS", 3)
    max_out = env_int("MAX_OUTPUT_TOKENS", 4000)
    passes = env_int("CONTEXT_PASSES", 6)
    max_chars = env_int("MAX_CHARS_PER_PASS", 6000)
    margin = max(1.0, env_float("COST_MARGIN", 1.25))

    spent = monthly_spend(notion, now)
    if spent >= max_month:
        alert_luc(notion, row, agent,
                  "plafond mensuel atteint (%.2f $ sur %.2f $)" % (spent, max_month), now, dry_run)
        return 0

    cadrage_id = env("CADRAGE_CLAUDE_PAGE_ID" if agent == "Claude" else "CADRAGE_ARCHIE_PAGE_ID",
                     required=True)
    cadrage = notion.page_text(cadrage_id, 12000)
    context = build_context(notion, row, passes, max_chars)
    turn = int(prop_number(row, P_TOUR))
    system = cadrage + "\n\n" + RULES
    user = ("Dossier : %s\nTour automatique : %d sur %d\nPasse à traiter : %s\n\n"
            "Contexte (fil de réponses menant à la passe, la plus ancienne d'abord) :\n\n%s" %
            (prop_text(row, P_DOSSIER) or "non précisé", turn + 1, max_turns, ident, context))

    est_in = estimate_tokens(system + user)
    # estimation pessimiste : heuristique de tokens + marge, sortie maximale
    est_cost = cost_usd(agent, est_in, max_out) * margin
    log("contexte estimé : %d tokens en entrée, coût maximal estimé %.3f $ (marge x%.2f)"
        % (est_in, est_cost, margin))

    if dry_run:
        log("mode essai : aucun appel de modèle, aucune écriture")
        return 0
    if est_cost > max_call:
        alert_luc(notion, row, agent,
                  "coût estimé %.2f $ au-dessus du plafond par appel (%.2f $)" % (est_cost, max_call),
                  now, False)
        return 0
    if spent + est_cost > max_month:
        alert_luc(notion, row, agent,
                  "budget mensuel restant insuffisant (déjà %.2f $, appel estimé jusqu'à %.2f $, plafond %.2f $)"
                  % (spent, est_cost, max_month), now, False)
        return 0

    try:
        caller = call_anthropic if agent == "Claude" else call_openai
        raw, tokens_in, tokens_out = caller(system, user, max_out)
    except ApiError as error:
        billed = est_cost if error.status not in NOT_BILLED else 0.0
        alert_luc(notion, row, agent, "appel au modèle échoué (%s)" % error, now, False, cost=billed)
        return 1
    if tokens_in is None:
        tokens_in = est_in
    if tokens_out is None:
        tokens_out = estimate_tokens(raw)
    cost = cost_usd(agent, tokens_in, tokens_out)
    log("appel facturé environ %.4f $ (à enregistrer dans la colonne Coût)" % cost)
    if not raw.strip():
        alert_luc(notion, row, agent, "réponse vide du modèle", now, False, cost=cost)
        return 1

    pour, titre, body, valid = parse_output(raw)
    new_turn = turn + 1
    notice = ""
    if not valid:
        notice = "Format de sortie non respecté (POUR / TITRE / ---) : pas de relance automatique."
        pour = "Luc"
    elif pour == agent:
        notice = "La voix s'est adressée à elle-même : pas de relance automatique."
        pour = "Luc"
    to_luc = (pour == "Luc") or (new_turn >= max_turns)
    if to_luc:
        pour = "Luc"
    if notice:
        body = "[%s]\n\n%s" % (notice, body)

    new_id = next_identifier(existing_ids_today(notion, now), now)
    full_title = "%s — %s → %s — %s" % (new_id, agent.upper(), pour.upper(), titre)
    properties = {
        P_TITRE: {"title": rich_array(full_title)},
        P_ID: {"rich_text": rich_array(new_id)},
        P_DE: {"select": {"name": agent}},
        P_POUR: {"select": {"name": pour}},
        P_STATUT: {"select": {"name": "Arbitrage Luc" if to_luc else "Proposé"}},
        P_DATE: {"date": {"start": now.date().isoformat()}},
        P_REPOND: {"rich_text": rich_array(source_marker(row))},
        P_AUTO: {"checkbox": not to_luc},
        P_TOUR: {"number": new_turn},
        P_COUT: {"number": round(cost, 4)},
    }
    dossier = prop_text(row, P_DOSSIER)
    if dossier:
        properties[P_DOSSIER] = {"select": {"name": dossier}}

    write_once(notion, row, agent, properties, text_to_blocks(body))
    acknowledge(notion, row)
    log("réponse écrite : %s (%s -> %s), tour %d, coût %.4f $%s"
        % (new_id, agent, pour, new_turn, cost, " [format invalide]" if notice else ""))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ApiError as error:
        log("erreur : %s" % error)
        sys.exit(1)
