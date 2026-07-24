# Audit — plan d'epesnel confronté au sujet et à la moulinette

Sources : `en.subject.pdf` (v1.1, 40 p.) et le code de `moulinette/` (source de vérité pour tout
ce qui est validé automatiquement). Chaque point est référencé à la page du sujet ou au fichier
de la moulinette.

Verdict global : le plan est solide, l'architecture est la bonne, le découpage en 3 streams tient.
Ce qui suit est une liste de corrections et de trous — pas une remise en cause.

---

## 1. Bloquants — à corriger avant `SETUP-1`

### 1.1 `python -m agent_mbpp` est incompatible avec la layout proposée

Le sujet impose (p. 19 et p. 22) :

```
uv run python -m agent_mbpp --task-file ... --output ... --model-name ... --provider-url ...
uv run python -m agent_swebench ...
```

`python -m agent_mbpp` exige un module/package **top-level** nommé `agent_mbpp` sur `sys.path`.
Le plan les place dans `src/agent_smith/cli/` (§3, layout proposée) : `python -m agent_mbpp`
échouera avec `No module named agent_mbpp`.

**Correction :** deux packages minces au niveau supérieur, qui ne font que déléguer.

```
src/agent_mbpp/__main__.py        # from agent_smith.cli.mbpp import main; main()
src/agent_swebench/__main__.py
src/agent_smith/…                 # tout le vrai code
```

et dans `pyproject.toml`, déclarer les trois packages dans le wheel. À vérifier explicitement
dans le *Done when* de `SETUP-1`, au même titre que `uv run sandbox --help`.

### 1.2 `SolutionOutput.task_id` doit être une chaîne, même pour MBPP

`models_public.py:34` → `task_id: str`. Or `MBPPTaskInput.task_id` est un `int`
(`models_public.py:66`), et la moulinette fait `SolutionOutput.model_validate(solution_data)`
(`moulinette/__main__.py:174`) **avant toute autre chose**.

Pydantic v2 ne convertit pas `int` → `str` en mode lax. Vérifié :

```
282   -> REJECTED (string_type)
'282' -> ok
```

Écrire `"task_id": 282` dans `solution.json` fait planter la validation avant même le test de
correction — la tâche est comptée échouée. Il faut `str(task.task_id)`.
À mettre dans les tests de `SETUP-2`, pas dans une revue de code.

### 1.3 Copier `models_public.py`, ne pas le retaper

`SETUP-2` dit « type out ... exactly as in the subject ». Il existe mieux :
`moulinette/models_public.py`, dont l'en-tête dit littéralement
*« Students copy this file into their project — it defines the JSON schema the moulinette expects »*.
C'est le fichier que la moulinette importe elle-même (`moulinette/models.py:10`).

**Correction :** `SETUP-2` = copier ce fichier tel quel dans `src/agent_smith/models/contract.py`,
avec un commentaire « copie verbatim de moulinette/models_public.py — ne pas modifier »,
et dériver nos modèles internes à côté. Zéro risque de divergence de nom de champ.

---

## 2. Trous fonctionnels — modes d'échec non couverts par le plan

### 2.1 MBPP : les tests visibles ne sont pas les tests d'évaluation

`InteractMBPP.get_task()` (`mbpp/interact.py:210-211`) ne donne au student que
`test_list[1:]` et `test_imports[1:]` — **le premier test est caché**. La validation, elle,
tourne avec `skip_first_k_tests=0` (`__main__.py:194`), donc sur la liste complète.

Deux conséquences que le plan ne mentionne nulle part :

- **Un agent qui « fait passer les tests » peut échouer à la validation.** Le mode d'échec
  classique de MBPP — l'agent qui hardcode les valeurs des asserts visibles — est ici
  systématiquement fatal. Le prompt MBPP doit interdire explicitement le fitting sur les asserts
  et exiger une implémentation générale. À ajouter à `CORE-6` et à la grille de `MBPP-5`.
- **`test_imports` peut être vide alors que la solution en a besoin.** Le slicing `[1:]` s'applique
  aussi aux imports : si la tâche a un seul import, l'agent n'en voit aucun. La solution rendue
  doit donc porter ses propres `import`, jamais compter sur ceux du harnais de test.

### 2.2 L'historique glissant est nécessaire dès MBPP, pas seulement pour SWE-bench

`CORE-7` (compaction) est cadré « For SWE-bench ». Or le calcul quadratique du §2 du plan
(6 400 tokens à l'itération 4) suppose un historique append-only. Le plan en conclut « il faut
résoudre en 2–3 itérations » — c'est traiter le symptôme.

La vraie réponse est la même que pour SWE-bench : **ne pas renvoyer tout l'historique**.
Système + tâche + dernière observation (+ éventuellement un résumé d'une ligne des steps élidés)
tient à budget ~constant par itération, ce qui rend les 10 itérations réellement utilisables au
lieu de 3. `CORE-7` doit être une dépendance de `MBPP-3`, pas un travail SWE-bench.

### 2.3 `get_patch()` : le vrai risque n'est pas les `.pyc`

`TOOL-8` prévoit de filtrer les `.pyc`. Le mode d'échec réel est ailleurs : l'agent crée un
`reproduce.py` ou un `test_bug.py` dans `/testbed` pour reproduire le bug (c'est exactement la
méthodologie qu'on va lui enseigner), et ce fichier se retrouve dans le diff. La moulinette
applique le patch puis lance l'`eval_script` — un fichier de repro parasite peut casser la
collecte pytest.

**Correction :** imposer un répertoire de scratch (`/tmp/agent`, déjà dans `allowed_directories`)
pour tout fichier de reproduction, et faire de `get_patch()` un diff restreint aux fichiers suivis
par git. À écrire dans `TOOL-8` et dans le prompt SWE-bench.

### 2.4 Sécurité / anti-triche : absent du plan

Le sujet y consacre une section entière (VI.4.1, p. 36) et la sanction est **0**, pas un échec de
tâche : interdiction d'aller chercher la solution dans les PR/issues, d'utiliser un patch mémorisé
sans exploration réelle, d'accéder à des ressources hors contexte de tâche.

Trois décisions concrètes à prendre, aucune n'est dans le plan :

- Le container SWE-bench doit-il avoir le réseau ? Par défaut non → un `run_command("pip install …")`
  ou un `curl github.com` devient impossible. C'est cohérent avec `SWE-2` (« tolerate failure »),
  mais il faut le décider, pas le subir.
- `run_command` autorise-t-il `git log` / `git show` dans `/testbed` ? À trancher et à documenter.
- Le prompt ne doit rien contenir qui invite le modèle à « se souvenir » du fix upstream.

Comme les évaluateurs lisent `llm_output` et `sandbox_input` pour tracer le raisonnement, c'est
aussi un argument pour ne jamais tronquer ces champs-là dans `StepMetrics`.

### 2.5 `allowed_directories` avec le sandbox sur l'hôte

La décision ouverte n°1 du plan (sandbox sur l'hôte + bridge MCP) est validée par le sujet
(p. 21 : « both approaches are valid »). Mais p. 15 précise que `allowed_directories` désigne
des chemins **que le processus sandbox lui-même voit**, évalués dans le sandbox — et les défauts
sont `/testbed` et `/tmp/agent`.

Si notre sandbox tourne sur l'hôte, `/testbed` n'existe pas côté hôte. `exam_sandbox.sh` teste
« path restrict » sur une config qui sera probablement celle par défaut. Il faut donc que le test
de restriction de chemin ait un comportement correct et lisible pour un répertoire autorisé mais
inexistant, et prévoir de créer `/tmp/agent` au démarrage. À ajouter à `SBX-4`.

---

## 3. Erreurs de cadrage / points d'organisation

### 3.1 `exam_sandbox.sh` n'est pas dans la moulinette qu'on a

`moulinette/README.md:84-86` référence `./exams/exam_*.sh` — ces scripts ne sont pas livrés.
Les 7 tests de sécurité (import, builtin, network, path, timeout, memory, MCP protocol) sont une
boîte noire.

Conséquence de design qui manque au plan : la surface testée sera très probablement **la CLI**
(`uv run sandbox …`), pas notre API interne. Tout — configuration, connexion MCP, exécution de
code, message d'erreur — doit être atteignable et lisible depuis `uv run sandbox [--mcp-stdio …] config.json`.
`DOC-2` doit être re-cadré : notre `exam_sandbox.sh` maison doit piloter la CLI en boîte noire,
pas appeler nos classes.

### 3.2 Les limites MBPP : le README de la moulinette contredit son propre code

- `moulinette/README.md:140-147` : 4 000 in / 1 000 out / 60 s
- `moulinette/models.py:95-99` : 6 000 in / 1 500 out / 120 s (= le sujet, p. 34)

Le code fait foi, donc 6k/1,5k/120s. Mais la doc annonce des valeurs plus dures, ce qui suggère
qu'elles ont bougé et peuvent rebouger. **Recommandation : concevoir pour 4 000 / 1 000 / 60 s.**
Ça donne 33 % de marge sur la cible réelle et ça nous immunise contre une mise à jour de la
moulinette entre maintenant et la soutenance.

### 3.3 `--model-name` et `--provider-url` viennent du correcteur, pas de nous

Tout le plan est construit sur « on choisit Groq ». Mais les deux flags sont imposés par le sujet
et le `.env` est fourni en argument aux scripts d'exam (p. 33). On ne sait pas ce que
`exam_mbpp.sh` passera, ni sous quel nom les clés arrivent — le sujet donne
`OPENROUTER_API_KEY` en exemple (p. 33 et p. 36), le plan a codé `GROQ_API_KEY`.

**Correction :** la résolution de clé doit balayer plusieurs conventions (`<PROVIDER>_API_KEY`,
`<PROVIDER>_API_KEY_N`, `<PROVIDER>_API_KEYS`, et une clé générique), le provider doit être
déduit de `--provider-url` et non d'une constante, et un défaut Groq ne doit s'appliquer que si
rien n'est passé. À ajouter à `SETUP-3` et `CORE-1`.

### 3.4 Collision de répertoire sur `evaluations/`

Le sujet (VI.5, p. 36) réserve `./evaluations/EVAL_TYPE/YYYY-MM-DD_HH-MM-SS/task_id/` aux runs
officiels — la moulinette y écrit déjà (`moulinette/evaluations/mbpp/2026-03-01_13-12-36/282/`).
`BENCH-1` prévoit d'y ranger nos propres runs de benchmark : on va mélanger nos artefacts avec
la structure attendue par le correcteur. Mettre nos runs dans `benchmarks/`.

### 3.5 Contradiction interne du sujet sur les `solution.json`

- p. 32 : « The backing solution.json files must be present in your repository »
- p. 39 : « Do not include ... generated outputs »

Résolution raisonnable : committer uniquement les `solution.json` qui étayent le
`BENCHMARK_REPORT.md` (15 fichiers pour 5 modèles × 3 tâches), et rien d'autre. À écrire dans
`BENCH-4` pour qu'on ait la réponse prête en soutenance.

### 3.6 Docker est aussi nécessaire pour MBPP

`InteractMBPP.run_code_in_docker()` exécute les solutions dans `python:3.11-slim`
(`mbpp/interact.py:74`). Le plan associe Docker au seul stream C / SWE-bench. Les trois machines
ont besoin de Docker dès le jour 1 pour valider quoi que ce soit en MBPP.

### 3.7 Le coût en temps de `BENCH-1` est sous-estimé

5 modèles × 3 tâches SWE-bench, à 900 s de plafond par run, plus les pulls d'images Docker
(plusieurs Go chacune) et les échecs à relancer : c'est une demi-journée de wall-clock minimum,
sur une seule machine, et ça dépend de quotas free-tier journaliers. Le plan le place en M5.
**Recommandation : lancer le premier passage du matrice dès que `SWE-5` est vert**, quitte à le
rejouer ensuite ; sinon on découvre le mur de quotas la dernière semaine.

### 3.8 Le dépôt lui-même

`en.subject.pdf` et `moulinette/` sont non suivis (`git status` → `??`) et absents de toutes les
branches — ils n'existent que sur ta machine. À décider explicitement :

- la moulinette ne doit **pas** être commitée (les scripts d'exam attendent `./student` et
  `./moulinette` côte à côte, et p. 39 interdit d'embarquer les sorties générées) → l'ajouter au
  `.gitignore`, avec une ligne du README expliquant où la récupérer ;
- même chose pour `en.subject.pdf`, ou alors le commiter une bonne fois sur `main` pour que les
  trois branches partagent la même version du sujet (v1.1).

En l'état, `jerome`, `eliott` et `quentin` sont sur le même commit et seule `main` a le plan.

---

## 4. Points où le plan a raison et où il faut le défendre

- **MCP client dans le processus parent, stubs dans le worker.** Le sujet dit « the sandbox wraps
  the MCP client » (p. 17) et, deux phrases plus loin, « the sandbox and MCP tools are independent
  security domains ... MCP tool actions happen outside the sandbox ». Les deux phrases ensemble
  décrivent exactement le design du plan : c'est le *sous-système* sandbox qui possède le client,
  pas le processus qui exécute le code non fiable — lequel ne peut pas l'avoir, puisqu'il n'a pas
  le réseau. C'est la bonne lecture, et la question tombera en soutenance (`DOC-3` le prévoit).
- **Double couche de timeout** (soft in-worker + hard parent). Nécessaire : le sujet exige de
  rapporter « execution hit the timeout and output is partial » (p. 11), ce que seule la couche
  soft peut produire.
- **Résoudre `sympy__sympy-14711` à la main et faire du transcript le prompt système** (`SWE-4`).
  Le sujet le suggère deux fois (p. 12 et p. 24). C'est la tâche à plus fort levier du projet.
- **`edit_file` qui échoue bruyamment si `old_str` apparaît 0 ou ≥ 2 fois** (`TOOL-2`). Correct,
  et c'est effectivement le premier mode d'échec silencieux des agents de ce type.

---

## 5. Récapitulatif des modifications à apporter au plan

| Carte | Modification |
|---|---|
| `SETUP-1` | ajouter les packages top-level `agent_mbpp` / `agent_swebench` ; *Done when* inclut `uv run python -m agent_mbpp --help` |
| `SETUP-2` | copier `moulinette/models_public.py` verbatim au lieu de le retaper ; test sur `task_id` sérialisé en `str` |
| `SETUP-3` | résolution multi-conventions des noms de clés ; provider déduit de `--provider-url` |
| `SBX-4` | comportement défini pour un `allowed_directory` inexistant côté hôte ; création de `/tmp/agent` |
| `CORE-6` | prompt MBPP : interdiction explicite du fitting sur les asserts visibles ; imports portés par la solution |
| `CORE-7` | dépendance de `MBPP-3` et pas seulement de SWE-bench (historique glissant dès MBPP) |
| `TOOL-8` | diff restreint aux fichiers suivis ; scratch imposé dans `/tmp/agent` |
| `DOC-2` | notre `exam_sandbox.sh` pilote la CLI en boîte noire, pas nos classes |
| `BENCH-1` | sortir de `evaluations/` (→ `benchmarks/`) ; premier passage dès `SWE-5`, pas en M5 |
| `BENCH-4` | statuer sur quels `solution.json` sont commités |
| *nouveau* | carte « politique anti-triche » : réseau du container, périmètre de `run_command`, formulation du prompt |
| *global* | cibler 4 000 / 1 000 / 60 s en MBPP au lieu de 6 000 / 1 500 / 120 s |
