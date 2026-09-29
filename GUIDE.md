# OpsPilot — guide complet

Ce fichier est pour toi. Il dit ce qui marche ce soir, ce qui ne marche pas encore, et comment raconter le projet. Les docs en anglais (`README.md`, `ARCHITECTURE.md`, `SECURITY.md`, `docs/roi.md`) sont la version à montrer. Celle-ci est la version à comprendre.

## Est-ce que tout marche ?

Non. Le code et les tests marchent. La démo de bout en bout ne marche pas encore, parce qu’il manque Notion, une clé de modèle, et le lancement Docker complet.

Vérifié le 28 septembre 2026 sur cette machine :

| Élément | Résultat |
| --- | --- |
| n8n 2.41.3 sur http://localhost:5678 | Répond. `healthz` = 200. |
| Compte n8n | L’ancien propriétaire a été effacé. Au premier écran tu crées un compte local. Ce n’est pas un compte n8n.io. |
| Tests Python | 17 tests passés. |
| API FastAPI sur http://127.0.0.1:8080 | Démarrée pour la vérification. Santé OK. ACME trouvé. Fournisseur inconnu = 404. Politique « payment terms » trouvée. ROI et KPI répondent. |
| Notion | Pas configuré. L’appel `/notion/requests` renvoie 503. C’est le comportement voulu. |
| Fichier `.env` | Absent. Les secrets ne sont pas dans git. |
| Workflow n8n importé | Non. Le fichier existe : `n8n/opspilot-workflow.json`. Il n’est pas encore dans l’éditeur. |
| Docker Compose (n8n + API + Postgres ensemble) | Pas lancé. Le n8n actuel est un conteneur séparé, lancé seulement pour avoir l’éditeur. |
| Agent LLM qui analyse ACME | Pas exécuté. Il faut une clé dans l’écran de credential n8n. |

L’API lancée pour ce contrôle n’est pas celle de Docker. Elle utilise une base SQLite locale et l’authentification est désactivée, parce que `.env` n’existe pas. Notion est volontairement éteint tant que tu n’as pas mis de token.

Le port **8000 est inutilisable sur ce PC**. Windows réserve la plage 7902–8001. Docker ne pourrait pas publier l’API dessus. Le port public est donc **8080**. À l’intérieur du réseau Docker, l’API reste sur le port 8000, et n8n l’appelle par `http://api:8000`. Depuis ton navigateur ou `curl`, tu utilises `http://localhost:8080`.

## La phrase à dire

I built an AI automation workflow where n8n acts as the orchestration layer, an LLM agent decides when to use authorized tools, Notion acts as the business system, FastAPI exposes business data through an API, sensitive actions require human approval, and every execution is logged and measurable.

En français : n8n conduit le parcours. Le modèle choisit quels outils lire. Notion est le dossier métier. FastAPI porte les données, l’écriture Notion, l’audit et le calcul de ROI. Une action sensible ne part qu’après un humain. Chaque exécution laisse une trace.

## Le cas d’usage

Quelqu’un envoie :

> Analyse la demande du fournisseur ACME concernant ses conditions de paiement. Ils demandent 90 jours au lieu de 60.

Le système doit retrouver ACME, lire la règle interne, proposer une action, et s’arrêter. Il ne change pas les conditions de paiement. Les fournisseurs et les règles du repo sont des **données de démonstration**, pas des données Car Data.

Règle de démo, dans `knowledge/payment_terms.md` :

- délai par défaut : 60 jours
- un changement demande l’accord Procurement
- un changement de plus de 15 jours demande le Procurement Director

ACME, dans `api/data/suppliers.json`, est à 60 jours, risque medium, contrat actif. Passer à 90 jours fait +30 jours. La proposition attendue est donc une demande d’accord du Procurement Director, pas une modification automatique.

## Le parcours, dans l’ordre

1. `POST` vers le webhook n8n `/webhook/opspilot`.
2. n8n appelle `POST /operations/intake`. FastAPI crée un id `REQ-2026-001` et une ligne d’audit.
3. n8n appelle `POST /notion/requests`. FastAPI crée la page Notion au statut `Processing`.
4. n8n charge le prompt versionné : `GET /prompts/system`, fichier `prompts/opspilot_system.md`.
5. Le node **AI Agent** peut appeler trois outils, en lecture seule :
   - Supplier API Tool → `GET /suppliers/ACME`
   - Policy Tool → `GET /knowledge/search`
   - Notion Read Tool → `GET /notion/requests`
6. Le modèle rend une proposition structurée : résumé, sources, faits, hypothèses, analyse, action, impact.
7. FastAPI écrit cette proposition sur la page Notion et passe le statut à `Waiting Approval`.
8. Le webhook te répond tout de suite, avec une `approval_url`.
9. Le node **Wait** bloque l’exécution.
10. Tu postes `{"decision":"approved","approved_by":"ton-nom"}` ou `rejected` sur cette URL.
11. Si c’est approuvé, FastAPI passe Notion à `Completed`. Si c’est refusé, `Rejected`. Une décision absente ou inconnue est traitée comme un refus, pour ne pas écrire par défaut.
12. Un événement d’audit est ajouté. `GET /kpi` compte les vraies exécutions. `GET /roi` calcule une simulation à part.

L’agent n’a pas d’outil d’écriture. Les écritures sont des nodes HTTP placés après le Wait. Même si le modèle écrit « pas besoin d’approbation », le workflow attend quand même.

## Pourquoi ces choix

**n8n plutôt qu’un script Python seul.** L’offre demande de voir l’orchestration, l’agent et l’humain dans la boucle. Le canvas montre le webhook, les outils, le Wait et la branche approuvé / refusé. Python reste là où il faut des tests.

**Un agent plutôt qu’un seul appel LLM.** La liste d’outils est la limite de ce qu’il a le droit de faire. `maxIterations` est à 4 pour qu’une boucle d’outils ne tourne pas sans fin. Dans la démo, le workflow charge le fournisseur et la politique avant l’agent, puis lui demande de répondre avec ces données. Les trois outils restent branchés. Sans ce chargement, l’agent utilisait tous ses tours à rappeler les outils et ne rendait pas de proposition.

**FastAPI devant Notion, plutôt que le node Notion de n8n.** Le node Notion de n8n 2.41 dépend de la base ouverte dans l’éditeur. Un export git ne se réimporte pas proprement sur une autre base. Le contrat HTTP, lui, est dans `api/notion_client.py` et se teste. Le token Notion reste dans le conteneur API. Il n’est pas mis dans l’environnement n8n, parce que les expressions n8n peuvent lire les variables d’environnement.

**Recherche par mots, pas un RAG.** Il y a quatre fichiers markdown. Un index vectoriel ajouterait Qdrant et des embeddings sans changer la démo. Syro n’est pas appelé : il n’est pas disponible, et le projet ne prétend pas le contraire. Le jour où un vrai retrieval existe, on remplace le contenu de `GET /knowledge/search` sans changer le workflow.

**Pas de MCP dans cette version.** MCP servirait si plusieurs clients devaient utiliser les mêmes outils. Ici le seul client est n8n, et les outils sont déjà des URL. Le mettre maintenant doublerait l’adaptateur seulement pour afficher le mot MCP. `ARCHITECTURE.md` décrit la version suivante : un serveur MCP devant Notion, l’API fournisseur et la politique.

**Postgres dans Docker, SQLite pour les tests.** Les KPI doivent être des requêtes, pas un fichier log qu’on relit à la main. Les tests n’ont pas besoin de démarrer Postgres. Les mêmes tables SQLAlchemy servent aux deux.

**ROI à part des KPI.** Les KPI comptent les lignes réellement stockées. Le ROI mensuel utilise `config/roi_assumptions.json`. Les deux ne sont pas mélangés. Le ratio d’environ 188 avec les chiffres livrés compare un salaire supposé de 35 euros de l’heure à une facture modèle supposée de 2 euros. Ce n’est pas une économie mesurée. La réponse de l’API le dit : `Simulation based on configurable assumptions.`

## Où est quoi

| Fichier | Rôle |
| --- | --- |
| `n8n/opspilot-workflow.json` | Workflow à importer. Nom : OpsPilot — AI Operations Workflow. |
| `prompts/opspilot_system.md` | Prompt système, version 1. Le workflow le charge, il n’en a pas une deuxième copie. |
| `api/main.py` | Routes HTTP. |
| `api/suppliers.py` | Lecture de `api/data/suppliers.json`. |
| `api/knowledge.py` | Recherche dans `knowledge/`. |
| `api/notion_client.py` | Création et mise à jour de la seule base du POC. Retry sur panne réseau ou HTTP 500. Pas de retry sur une erreur 400. |
| `api/db.py` | Demande courante + événements d’audit. |
| `api/roi.py` | Formules de simulation. |
| `scripts/setup_notion.py` | Crée la base « AI Automation Lab » sous une page que tu désignes. Ne parcourt pas le reste du workspace. |
| `scripts/roi.py` | Affiche la simulation dans le terminal. |
| `config/roi_assumptions.json` | Hypothèses modifiables. |
| `docker-compose.yml` | Postgres 16, API, n8n 2.41.3. |
| `.github/workflows/ci.yml` | À chaque push : dépendances, ruff, pytest, build de l’image API. Pas de déploiement cloud. |
| `tests/test_api.py` | Fournisseur trouvé, absent, id mal formé, politique, refus, approbation, panne Notion, token, retry. |

Statuts Notion : Pending, Processing, Waiting Approval, Approved, Rejected, Completed, Failed.

Le champ `Error` est en plus de la liste minimale. Il sert à garder le message d’erreur sans effacer l’analyse déjà écrite pour le réviseur.

Créer la page au début n’est pas l’action sensible. C’est le ticket. L’action sensible est le passage à `Completed`. `POST /operations/{id}/resolve` refuse cet appel si le statut n’est pas déjà `Waiting Approval`.

## Sécurité, version courte

- `.env` n’est pas dans git. `.env.example` n’a pas de vraie clé.
- `LLM_API_KEY` se colle dans l’écran de credential n8n. Compose ne l’injecte ni dans n8n ni dans l’API.
- `NOTION_API_KEY` va seulement à l’API. L’intégration Notion doit être invitée uniquement sur la page parente du POC, pas sur tout le workspace.
- Les textes récupérés sont marqués `untrusted_data`. Le prompt dit de ne pas suivre les instructions cachées dedans.
- Le webhook n8n n’a pas d’authentification. Il doit rester sur localhost. Sinon n’importe qui peut lancer des appels payants au modèle.
- Le token `INTERNAL_API_TOKEN` prouve seulement que l’appelant connaît le secret local. Il ne prouve pas qu’un humain a cliqué.

## Ce qu’il reste pour la démo de 5 minutes

1. Copier `.env.example` vers `.env`.
2. Créer une intégration Notion, l’inviter seulement sur une page vide, mettre le token et l’id de cette page dans `.env`.
3. Lancer `python scripts/setup_notion.py` et copier le `NOTION_DATABASE_ID` dans `.env`.
4. Mettre une clé de modèle dans `.env` pour t’en souvenir, puis la coller dans n8n. Le node du workflow est un node OpenAI. Pour Anthropic ou Mistral, tu remplaces ce node. La clé ne doit pas être commit.
5. Arrêter l’API de vérification si elle occupe encore le port 8080, et arrêter le conteneur n8n actuel, parce qu’il occupe le port 5678 :

```powershell
docker stop n8n
docker rm n8n
```

6. Depuis le dossier du projet : `docker compose up --build`.
7. Ouvrir http://localhost:5678, créer le compte local, importer `n8n/opspilot-workflow.json`, attacher le credential au node **OpenAI Chat Model**, activer le workflow.
8. Envoyer la demande ACME, montrer les tools, la proposition, approuver ou refuser, ouvrir la page Notion, puis `GET /audit/{request_id}`, `GET /kpi` et `GET /roi`.

Le `docker rm` efface le conteneur, pas forcément le volume. Compose utilise un autre volume, `opspilot_n8n_data`. Tu referas le compte propriétaire dans cette nouvelle instance. C’est normal.

Commande de démo, une fois le workflow actif :

```powershell
curl.exe -X POST http://localhost:5678/webhook/opspilot `
  -H "Content-Type: application/json" `
  -d "{\"request\":\"Analyse la demande du fournisseur ACME concernant ses conditions de paiement. Ils demandent 90 jours au lieu de 60.\",\"requester\":\"portfolio-user\"}"
```

Dans PowerShell, utilise `curl.exe`. `curl` seul est un alias qui ne fait pas la même chose.

## Ce qu’il ne faut pas prétendre

- Que le ROI de 188 est un résultat réel.
- Que Syro, MCP, Slack ou un RAG sont dans le workflow.
- Que les fournisseurs ACME, Nordic Steel et Helix Logistics existent.
- Que Notion a été modifié : aucune page personnelle n’a été touchée. La base n’a pas encore été créée.
- Que l’agent a déjà tourné sur une vraie demande : le modèle n’a pas été appelé.

## Limites à assumer en entretien

- L’écran d’approbation est l’URL du node Wait, pas une interface produit.
- L’action exécutée met à jour Notion. Elle ne parle pas à un ERP.
- Deux demandes envoyées en même temps peuvent se marcher sur le compteur `REQ-YYYY-NNN`. Il n’y a pas de verrou.
- Les appels au modèle ne sont pas retentés, pour ne pas payer trois fois la même panne. Les appels HTTP et Notion sont retentés au plus trois fois.
- Si Notion tombe après le choix humain, le statut local devient `Failed` et l’API répond 502. Elle ne répond pas `Completed`.
