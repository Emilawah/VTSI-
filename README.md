# Vector Databases — double démonstration PostgreSQL + pgvector / ClickHouse

Veille technologique et stratégique — Polytech Grenoble INFO5 — Marcus Sabon, Emilio Stien.

| Dossier / fichier | Contenu |
|---|---|
| `presentation/` | La présentation (≈ 35 min, 28 slides, notes d'orateur) |
| `presentation/slides.md` | **Source unique** des slides (Markdown) |
| `presentation/index.html` | Version **reveal.js** — ouvrir dans le navigateur |
| `presentation/remark.html` | Version **remark** — même contenu |
| `demo/` | La démo : `docker compose up` lance PostgreSQL + pgvector, ClickHouse et une interface web |
| `demo/sql/` | Scripts SQL à dérouler en direct (psql et ClickHouse Play) |
| `demo/app/bench.py` | Benchmark équitable pgvector vs ClickHouse (latence, recall, construction d'index) |
| `DEROULE-DEMO.md` | Minutage des deux démos, préparation, questions probables, plan B |
| `fiche-synthese.mediawiki` | Fiche de synthèse au format MediaWiki (à envoyer avant la séance) |

## Présentation

- Ouvrir `presentation/index.html` (fonctionne hors ligne : reveal.js et remark sont dans `presentation/lib/`).
- **S** : vue présentateur avec notes et minuteur · **F** : plein écran · **Échap** : vue d'ensemble.
- Version remark : `presentation/remark.html` — **P** : mode présentateur, **C** : cloner la fenêtre.
- Modifier `slides.md`, puis régénérer les deux versions (Git Bash) : `cd presentation && ./build.sh`.
- **PDF < 2 Mo** : ouvrir `presentation/index.html?print-pdf` dans Chrome ou Edge → Imprimer → Enregistrer au format PDF (paysage, marges : aucune, graphiques d'arrière-plan cochés).

Conventions de `slides.md` comprises par les deux moteurs : `---` sépare les slides, `???` ouvre les notes, `class: title` / `class: demo` en première ligne donne le style de la slide.

## Démo

```bash
cd demo
docker compose up -d --build        # premier lancement : ~10 min (PyTorch CPU + modèle d'embeddings)
```

- http://localhost:8000 — interface « Vector Search Arena » : plein texte vs pgvector vs ClickHouse, espace vectoriel 2D, résultats du benchmark
- http://localhost:8123/play — ClickHouse Play (utilisateur `demo`, mot de passe `demo`)
- `docker compose exec postgres psql -U demo -d demo` — PostgreSQL (aussi sur le port 5433 de l'hôte)
- `docker compose exec app python bench.py` — benchmark (résultats dans `demo/results/`)

Corpus : 91 films (titre, année, réalisateur, genre, pays, synopsis rédigé pour la démo). Seuls le titre et le synopsis sont vectorisés, avec `paraphrase-multilingual-MiniLM-L12-v2` (384 dimensions, local, sans clé d'API).

Versions : PostgreSQL 17 + pgvector (image `pgvector/pgvector:pg17`), ClickHouse 25.8 (index `vector_similarity` disponible à partir de cette version).
