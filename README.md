# Atelier T — tour automatique Archie · Claude (prototype V0.2)

V0.2 : corrections issues de la revue d'Archie (H-20261111-08). Idempotence par UUID Notion, budget combiné avant appel, création de réponse sans doublon, contexte limité au fil de réponses, routage strict, aucun nouvel essai automatique sur les écritures. **Toujours aucun test contre les vrais services.**

## Ce que fait ce prototype, et ce qu'il ne fait pas

Une fois installé, un petit script se lance tout seul sur GitHub. À chaque passage, il regarde la base **PASSES — test** dans Notion. S'il trouve une ligne adressée à Archie ou à Claude avec la case **« Relance auto »** cochée, il fait répondre la voix concernée par son API, écrit la réponse comme une nouvelle ligne, puis passe la main à l'autre voix. La voix ne reçoit que **le fil de réponses** menant à la passe (retrouvé par l'identifiant interne Notion de la page), pas tout le dossier : deux conversations parallèles d'un même dossier ne se mélangent pas. Une passe sans fil (nouvelle ligne de Luc) reçoit en plus deux passes récentes du dossier, étiquetées « hors fil ». Au bout de 3 tours, ou si une voix écrit « Pour : Luc », il s'arrête et te rend la main.

**Ce qu'il ne fait pas :** il ne fait pas dialoguer tes conversations ChatGPT et Claude habituelles. Il appelle deux modèles par leurs API : ce sont deux voix apparentées à Archie et à Claude, avec le cadrage écrit dans Notion, sans leur mémoire. Tes conversations normales restent comme aujourd'hui. Il n'écrit jamais sur WordPress ni sur Refuge404.

**Rythme :** un passage toutes les 5 minutes environ (GitHub peut retarder ou sauter un passage). C'est un atelier asynchrone, pas une conversation instantanée.

## Ce qu'il y a dans ce dossier

| Fichier | Rôle |
| --- | --- |
| `orchestrator.py` | Le script. Aucune installation de paquet. |
| `.github/workflows/atelier.yml` | Le lanceur GitHub (manuel d'abord, planifié ensuite). |
| `.env.example` | La liste de tous les réglages, sans clé. |
| `test_orchestrator.py` | Tests des fonctions internes. |
| `test_scenarios.py` | 17 scénarios de bout en bout contre un faux Notion (doublons, pannes, budget, DRY_RUN…). |
| `README.md` | Ce guide. |

Dans Notion, sous ATELIER T, deux pages **« CADRAGE — voix Claude (API) »** et **« CADRAGE — voix Archie (API) »** décrivent le rôle de chaque voix. Tu peux les modifier à tout moment : le script les relit à chaque appel.

La base PASSES a trois colonnes de plus : **Relance auto** (case à cocher), **Tour** et **Coût**.

## Installation, étape par étape (environ 30 minutes, tout dans un navigateur)

### 1. Notion : créer l'intégration (5 min)

1. Va sur `notion.so/my-integrations`, crée une intégration interne (par exemple « Atelier T »), avec les droits de lecture, d'écriture et d'insertion de contenu.
2. Copie le **jeton** (il commence par `ntn_` ou `secret_`). Ne le colle nulle part ailleurs que dans GitHub (étape 4).
3. Ouvre la base **PASSES — test**, puis le menu **•••** en haut à droite, puis **Connexions**, et ajoute ton intégration. Fais la même chose sur chacune des deux pages de **CADRAGE**. Sans cette étape, Notion répond « introuvable » même avec un bon jeton.

### 2. GitHub : le dépôt (15 min)

1. Crée un compte GitHub si tu n'en as pas.
2. Crée un **nouveau dépôt public** nommé par exemple `atelier-t-orchestrateur`. Public est nécessaire : les minutes d'exécution sont gratuites sans limite en public, alors qu'en privé elles sont limitées à environ 2 000 par mois, ce qui ne suffit pas à une exécution toutes les 5 minutes.
3. **Add file > Upload files** : dépose `orchestrator.py`, `.env.example`, `README.md` et `test_orchestrator.py`.
4. Le fichier du lanceur doit se trouver dans un dossier précis. **Add file > Create new file**, et dans le champ du nom tape exactement `.github/workflows/atelier.yml` (les barres créent les dossiers). Colle le contenu de `atelier.yml`, puis valide (« Commit changes »).

### 3. Les clés des modèles (5 min)

1. Crée une clé chez Anthropic et une chez OpenAI, chacune sur la console du fournisseur. Ces clés sont facturées à la consommation, séparément de tes abonnements ChatGPT et Claude.
2. **Avant tout usage, fixe un plafond de dépense mensuel** chez chacun des deux fournisseurs (cherche le réglage de limite ou de budget dans la facturation). Un plafond fournisseur n'est pas toujours une coupure instantanée : le script a aussi les siens (voir plus bas).
3. Note l'**identifiant exact** de chaque modèle (par exemple `claude-sonnet-5-5` pour Claude) et, pour GPT, l'identifiant exact du modèle que tu choisis, tel qu'il apparaît dans ton compte OpenAI.
4. Relève les **prix par million de tokens** (entrée et sortie) sur la page de tarifs officielle de chaque fournisseur, le jour de l'installation. Sans ces prix, le script refuse de tourner : il ne pourrait pas appliquer ses plafonds.

### 4. GitHub : secrets et variables (5 min)

Dans le dépôt : **Settings > Secrets and variables > Actions**.

**Onglet Secrets** (valeurs confidentielles) :

| Nom | Valeur |
| --- | --- |
| `NOTION_TOKEN` | le jeton de l'étape 1 |
| `NOTION_DATA_SOURCE_ID` | `13de520e-2ab8-4263-b80b-14e416a4d28a` |
| `CADRAGE_CLAUDE_PAGE_ID` | `4154b942-9217-81ad-9a54-d60129b2b9d0` |
| `CADRAGE_ARCHIE_PAGE_ID` | `4154b942-9217-8111-b275-eee82d46294a` |
| `ANTHROPIC_API_KEY` | ta clé Anthropic |
| `OPENAI_API_KEY` | ta clé OpenAI |

**Onglet Variables** :

| Nom | Valeur |
| --- | --- |
| `ANTHROPIC_MODEL` | l'identifiant du modèle Claude |
| `OPENAI_MODEL` | l'identifiant du modèle GPT |
| `PRICE_ANTHROPIC_IN`, `PRICE_ANTHROPIC_OUT` | prix en dollars par million de tokens |
| `PRICE_OPENAI_IN`, `PRICE_OPENAI_OUT` | idem pour OpenAI |
| `MAX_MONTHLY_USD` | plafond mensuel du script, par exemple `5` |
| `MAX_CALL_USD` | coût maximal estimé par appel, par exemple `0.5` |
| `COST_MARGIN` | marge de sécurité sur le coût estimé avant appel, `1.25` par défaut |
| `MAX_TURNS` | tours automatiques avant retour à Luc, `3` (mets `1` pour les premiers essais) |

Les autres réglages (`MAX_OUTPUT_TOKENS`, `CONTEXT_PASSES`, `MAX_CHARS_PER_PASS`, `OPENAI_REASONING_EFFORT`) ont des valeurs par défaut prudentes : voir `.env.example`.

## Les essais, dans cet ordre

### Essai 1 : à blanc (aucun appel de modèle, aucune écriture)

1. Dans Notion, crée dans PASSES une ligne de test : **De** = Luc, **Pour** = Archie, **Dossier** = un dossier, un titre quelconque, une phrase dans le corps, et coche **Relance auto**.
2. Dans GitHub, onglet **Actions > Atelier T - tour automatique > Run workflow**, avec `dry_run` = `1`.
3. Ouvre l'exécution : tu dois lire « passe à traiter : H-… pour Archie (mode essai) » et un coût maximal estimé. Rien n'a changé dans Notion.

Si le script répond « introuvable » ou une erreur 401/404, c'est presque toujours l'étape 1.3 (connexion non ajoutée à la base ou aux pages de cadrage).

### Essai 2 : un vrai tour, plafonné à 1

1. Mets la variable `MAX_TURNS` à `1`. Relance avec `dry_run` = `0`.
2. Une nouvelle ligne apparaît, écrite par Archie, adressée à **Luc** (statut « Arbitrage Luc »), avec le coût en dollars. La ligne de test est décochée.
3. Relis la réponse : est-ce bien la voix et le fond que tu attends ? Sinon, corrige la page de CADRAGE correspondante et recommence.

### Essai 3 : un échange entre les deux voix

1. Mets `MAX_TURNS` à `2`. Crée une ligne de test, coche Relance auto, adressée à Archie.
2. Lance deux fois le workflow (`dry_run` = `0`) : la première réponse est écrite par Archie pour Claude, la seconde par Claude, renvoyée à Luc.

### Mise en route automatique

Quand les essais te conviennent : dans le dépôt, ouvre `.github/workflows/atelier.yml`, enlève les `#` devant les deux lignes `schedule:` et `- cron:`, valide, puis crée la variable `DRY_RUN` = `0` (sinon les passages planifiés restent en essai, par sécurité). Remets `MAX_TURNS` à `3`.

## Au quotidien

- **Lancer un échange :** crée une ligne dans PASSES, **Pour** = Archie ou Claude, un **Dossier**, ta demande dans le corps, et coche **Relance auto**. Au prochain passage, la voix répond et passe la main à l'autre.
- **Voir où on en est :** vue « Récentes » de la base. Les lignes à ton attention ont le statut **Arbitrage Luc**.
- **Reprendre la main :** réponds dans une nouvelle ligne (avec ou sans Relance auto), ou parle simplement à Archie ou à moi dans nos conversations habituelles : la base fait foi du fil.
- **Les lignes écrites à la main** (par toi ou par une conversation habituelle) ne déclenchent rien tant que **Relance auto** n'est pas cochée.

## Arrêts

- **Arrêt d'urgence :** variable `ATELIER_PAUSE` = `1`, ou bouton **Disable workflow** dans l'onglet Actions. Rien ne tourne plus.
- **Arrêts automatiques :** retour à Luc après `MAX_TURNS` tours ; plafond mensuel (le script refuse l'appel si **dépense déjà faite + coût estimé avec marge** dépasse `MAX_MONTHLY_USD`) ; plafond par appel `MAX_CALL_USD` ; prix non renseignés ; échec d'un appel ; **sortie du modèle sans l'en-tête `POUR / TITRE / ---` valide, ou adressée à elle-même** (la réponse est quand même écrite, adressée à Luc, sans relance). Dans les cas d'arrêt, le script écrit une courte ligne « Arrêt automatique : … » pour Luc et ne réessaie pas tout seul. Un appel qui échoue de façon incertaine (réseau, erreur 5xx) est compté au coût estimé dans le plafond ; un refus clair (401, 429…) n'est pas compté.
- **Pas de doublon :** la colonne « Répond à » reçoit l'identifiant lisible **et** l'identifiant interne de la page source (`[src:…]`). Une réponse déjà écrite est reconnue par cet identifiant interne, jamais par le numéro H-…. Si la création échoue ou dépasse le délai, le script vérifie d'abord si la réponse existe avant de recréer. Si l'acquittement (décocher la case) échoue, le passage suivant le refait sans rappeler le modèle.
- **Un dépôt public sans activité pendant 60 jours voit ses tâches planifiées désactivées par GitHub.** Si les réponses cessent, vérifie l'onglet Actions et réactive.

## Coût

Aucun coût fixe : l'API Notion et GitHub public sont gratuits. Tu ne paies que les tokens, au tarif de chaque fournisseur.

Formule : `tokens d'entrée × prix d'entrée + tokens de sortie × prix de sortie`, par million de tokens.

Estimation d'un test de **six réponses** (deux chaînes de trois tours), avec les prix de Claude Sonnet 5.5 (2 $ en entrée, 10 $ en sortie) et ceux annoncés pour GPT-5.6 Terra (2 $ et 12 $, à confirmer chez OpenAI) :

| Contexte envoyé à chaque appel | Sortie | Coût d'un appel | Six appels |
| --- | --- | --- | --- |
| 15 000 tokens (petit sujet) | 2 000 | environ 0,05 $ | environ 0,30 $ |
| 40 000 tokens (dossier lourd comme la Fresque) | 2 000 | environ 0,10 $ | environ 0,60 $ |

Ce sont mes estimations, pas des garanties : le coût réel est écrit dans la colonne **Coût** de chaque réponse, et c'est lui qui alimente le plafond mensuel. **Fais ton premier test sur un petit sujet.** Un dossier de plusieurs dizaines de milliers de tokens renvoyé à chaque tour est le principal risque de dérapage : le script limite le contexte (les 6 dernières passes du dossier, 6 000 caractères chacune) et refuse un appel dont le coût estimé dépasse `MAX_CALL_USD`.

## Sécurité et confidentialité

- **Le dépôt public ne contient que du code.** Jamais d'archives, de prompts privés, d'extraits de Notion, de sorties de modèles ni de clés. Les cadrages restent dans Notion.
- **Les journaux d'un dépôt public sont publics.** Le script n'y écrit que des identifiants de passes, des tours et des montants, jamais le contenu. Si tu modifies le script, garde cette règle.
- Les clés ne sont que dans les **Secrets** GitHub. Si l'une est exposée par erreur, révoque-la tout de suite chez le fournisseur.
- L'intégration Notion n'a accès qu'aux pages auxquelles tu l'as connectée. Ne la connecte pas à l'Atelier tout entier.
- Le script ne contient aucun code qui écrit sur WordPress ou Refuge404, et n'en contiendra pas dans ce prototype.

## Dépannage

| Symptôme | Cause probable |
| --- | --- |
| Erreur Notion 404 ou « introuvable » | La connexion de l'intégration n'est pas ajoutée à la base ou à une page de cadrage |
| Erreur Notion 400 | Version d'API ou nom de propriété : vérifie que les colonnes de la base portent exactement les noms d'origine, ou fixe la variable `NOTION_VERSION` |
| « prix des tokens non renseignés » | Variables `PRICE_*` vides |
| Réponse vide ou tronquée de GPT | Le raisonnement consomme les tokens de sortie : fixe `OPENAI_REASONING_EFFORT` = `low` et/ou monte `MAX_OUTPUT_TOKENS` à `8000` |
| Rien ne se passe | Case Relance auto décochée, `Pour` = Luc, `ATELIER_PAUSE` actif, workflow désactivé, ou dépôt resté inactif 60 jours |
| Deux lignes portent le même numéro H-… | Arrive quand une conversation habituelle et le script écrivent en même temps. Le script ne s'appuie plus sur ce numéro pour savoir ce qui est traité (il utilise l'identifiant interne de la page), mais renumérote à la main pour garder la base lisible |

## Limites connues, à lire avant de se fier au prototype

- **Le script n'a pas été exécuté contre les vrais services.** Il a été testé avec un faux Notion en mémoire et de faux modèles (35 tests : doublons d'identifiant, budget insuffisant, panne entre création et acquittement, délai dépassé à la création, deux fils dans un dossier, sortie mal formée, DRY_RUN, pause, journal sans contenu), pas avec les vrais. Le faux Notion reproduit ce que j'ai compris de l'API : un écart avec le vrai est possible.
- Le coût enregistré vient des tokens que l'API annonce ; le plafond est une protection du script, pas un arrêt garanti de la facturation. Le plafond fixé chez chaque fournisseur reste la vraie limite.
- Si une réponse dépasse 90 blocs Notion, la suite est ajoutée en deuxième requête sans nouvel essai : en cas d'échec, la réponse peut être tronquée.
- Le routage s'appuie sur un en-tête texte strict, pas sur une sortie JSON structurée.
- Les journaux d'un dépôt public contiennent des numéros H-…, le nom de la voix, des tokens et des montants. Aucun titre, dossier ni contenu. Les appels réels aux API peuvent révéler un écart (version d'API Notion, format de réponse d'un modèle). C'est pour ça que les essais 1 à 3 existent.
- Les prix et les limites cités viennent de sources tierces consultées le 11 novembre 2026. Vérifie-les chez chaque fournisseur le jour de l'installation.
- Les horaires de GitHub sont indicatifs : un passage planifié peut être retardé ou sauté.
- L'identité des voix est reconstruite par le cadrage. Elles n'ont pas la mémoire de tes conversations habituelles et ne voient que le contexte envoyé à chaque appel.
