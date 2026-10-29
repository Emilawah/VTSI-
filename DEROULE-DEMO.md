# Déroulé des démonstrations — Vector Databases (pgvector vs ClickHouse)

Marcus Sabon · Emilio Stien — Polytech Grenoble INFO5, veille technologique et stratégique.

Deux démos de 6 minutes chacune, insérées dans la présentation (slides 18 et 22).
Tout tourne en local dans Docker : **aucune clé d'API, aucune connexion internet le jour J** (le modèle d'embeddings est téléchargé au build).

---

## 0. Préparation (la veille, puis 15 min avant)

### La veille (une seule fois, ~10 min et ~3 Go téléchargés)

Prérequis : Docker Desktop (Windows/macOS) ou Docker Engine (Linux), 6 Go de RAM alloués à Docker.

```bash
cd demo
docker compose up -d --build
docker compose logs -f app          # attendre « [boot] {'documents': 91, ...} » puis Ctrl+C
```

Puis lancer le benchmark (2 à 5 min pour 100 000 vecteurs) :

```bash
docker compose exec app python bench.py
```

Le résultat est écrit dans `demo/results/bench.md` : **recopier ce tableau dans la slide 23** (`presentation/slides.md`, puis `./build.sh`).
Avec une machine puissante : `python bench.py --n 1000000` (prévoir 8 Go de RAM pour Docker).

### Enregistrer le plan B

La consigne autorise une démo pré-enregistrée : enregistrer chaque démo avec OBS Studio (ou la Xbox Game Bar : Win+Alt+R) en suivant exactement ce déroulé.
Fichiers : `demo1.mp4`, `demo2.mp4`. Si le direct échoue, on bascule sur la vidéo sans se justifier longuement.

### 15 min avant

```bash
cd demo
docker compose up -d                 # démarre en ~20 s, le corpus est déjà chargé
```

Ouvrir et disposer :

| Fenêtre | Contenu |
|---|---|
| Navigateur, onglet 1 | `presentation/index.html` (plein écran : F ; vue présentateur : S) |
| Navigateur, onglet 2 | http://localhost:8000 — l'interface « Vector Search Arena » |
| Navigateur, onglet 3 | http://localhost:8123/play — ClickHouse Play (utilisateur `demo`, mot de passe `demo`) |
| Terminal | `docker compose exec postgres psql -U demo -d demo` puis `\x auto` |
| Éditeur | `demo/sql/1_postgres_pgvector.sql` et `demo/sql/2_clickhouse.sql` ouverts, police agrandie |

Zoom navigateur à 125 %, police du terminal à 18 pt minimum. Couper les notifications.

---

## Démo 1 — PostgreSQL + pgvector (Emilio, 6 min)

| Temps | Action | Ce qu'on dit |
|---|---|---|
| 0:00 | Onglet **Recherche**, la requête « un robot qui tombe amoureux » est déjà là → *Rechercher* | « Trois moteurs, la même question. À gauche, le plein texte classique de PostgreSQL. » |
| 0:30 | Montrer la colonne de gauche (vide ou hors sujet) puis la colonne pgvector (WALL-E, Her) | « Aucun mot en commun entre la requête et le synopsis de WALL-E, et pourtant c'est le premier résultat. » |
| 1:00 | Déplier « SQL exécuté » de pgvector | « Une requête SQL ordinaire : `ORDER BY embedding <=> vecteur LIMIT 5`. » |
| 1:30 | Cliquer la puce « a heist movie with a clever crew » | « Requête en anglais, synopsis en français : Ocean's Eleven, Heat. Le modèle est multilingue. » |
| 2:00 | Puce « une équipe prépare un cambriolage », puis filtre **Pays = France** et **Depuis 2000** | « Recherche hybride : la similarité ET des filtres SQL classiques, dans une seule requête. » |
| 3:00 | Terminal psql : section 5 du script (`BEGIN; INSERT …; SELECT …; ROLLBACK;`) | « Le film et son vecteur arrivent ensemble ou pas du tout : c'est l'ACID de PostgreSQL. ClickHouse ne sait pas faire ça. » |
| 4:30 | Section 6 : `\gset` puis les deux `EXPLAIN` | « Sur 91 films, PostgreSQL choisit un parcours séquentiel, et il a raison. En forçant, on voit l'index HNSW : *Index Scan using films_embedding_hnsw*. » |
| 5:30 | (Option) Interface : déplier « Plan d'exécution » | Transition : « Marcus va maintenant vous montrer la même chose… dans une base qui n'a rien à voir. » |

**Si une requête donne un résultat inattendu** : c'est un argument, pas un échec. « Voilà exactement le point critique dont on parlera : proche ne veut pas dire pertinent. »

---

## Démo 2 — ClickHouse (Marcus, 6 min)

| Temps | Action | Ce qu'on dit |
|---|---|---|
| 0:00 | Interface, même requête : colonne ClickHouse | « Même vecteur, même distance cosinus, même famille d'index : les mêmes films (aux arrondis près, ClickHouse stocke son graphe en bfloat16). Les latences sur 91 lignes ne veulent rien dire : le vrai match, c'est le benchmark. » |
| 0:45 | Panneau du bas « analyse des 20 films les plus proches », requête « voyager dans le temps » | « Voici ce que ClickHouse fait de mieux : je cherche par le sens et j'agrège le résultat dans la même requête : genres, décennies, pays. » |
| 1:45 | ClickHouse Play : section 2 du script (recherche « robot ») | « En SQL ClickHouse : pas de type vector, un `Array(Float32)` et une fonction `cosineDistance`. » |
| 2:30 | Section 3 : requête des centroïdes par genre (`avgForEach`) | « Le vecteur moyen de chaque genre, calculé à la volée, et le genre le plus proche de chaque requête. Ce genre d'analyse est le terrain de ClickHouse. » |
| 3:30 | Section 4 : `EXPLAIN indexes = 1` | « La ligne *Skip … vector_similarity* prouve que l'index est utilisé. Attention à la forme imposée : LIMIT ≤ 100, même fonction que l'index. » |
| 4:15 | Section 5 bonus : `CREATE TABLE random_1m`, `INSERT … FROM numbers(1000000)`, puis la recherche | « Un million de vecteurs de 384 dimensions générés dans la base, puis une recherche exacte, sans index : regardez *Elapsed* et le débit en lignes par seconde. » |
| 5:30 | Onglet **Benchmark** de l'interface | Transition vers la slide 23 : « Et sur 100 000 vecteurs, avec les mêmes réglages des deux côtés, voici ce que nous avons mesuré. » |

Penser au `DROP TABLE random_1m;` à la fin (1,5 Go).

---

## Questions probables et réponses courtes

- **« Pourquoi pas Pinecone ou Qdrant ? »** — Le sujet est l'intégration du vecteur dans une base existante ; on les positionne sur la carte et dans l'arbre de décision. Ils se justifient au-delà de quelques centaines de millions de vecteurs ou pour du multi-tenant à forte charge.
- **« Vos latences sont-elles représentatives ? »** — Non : 1 client, données synthétiques, un portable. Le protocole est fourni (`bench.py`) pour refaire la mesure sur ses propres données.
- **« Comment choisir le modèle d'embedding ? »** — Langue, domaine, dimension (coût mémoire), licence, exécution locale ou API. On a pris un modèle multilingue local de 384 dimensions pour la démo ; le blueprint utilise OpenAI (1536 dimensions).
- **« Et la recherche hybride ? »** — Combiner BM25 (plein texte) et vecteur, puis fusionner les classements (Reciprocal Rank Fusion). PostgreSQL le permet en SQL ; c'est une amélioration que le blueprint JHipster n'implémente pas.
- **« Quelle limite pour pgvector ? »** — 2 000 dimensions indexables en `vector` (4 000 en `halfvec`) ; en pratique la RAM disponible pour l'index HNSW fixe l'échelle.

---

## En cas de problème

| Symptôme | Remède |
|---|---|
| L'interface affiche « Attente des bases de données… » | `docker compose ps` : attendre que postgres et clickhouse soient *healthy* |
| Port 5433, 8000 ou 8123 déjà pris | Modifier le port côté hôte dans `docker-compose.yml` |
| Corpus incohérent après modification de `films.json` | `curl -X POST http://localhost:8000/api/reload` |
| Tout repartir de zéro | `docker compose down -v` puis `docker compose up -d --build` |
| Rien ne marche | Basculer sur `demo1.mp4` / `demo2.mp4` |
