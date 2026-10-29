-- =====================================================================
--  DÉMO 1 — PostgreSQL + pgvector, en SQL pur
--  docker compose exec postgres psql -U demo -d demo
--  Dans psql : \x auto   puis copier-coller étape par étape.
-- =====================================================================

-- ---------------------------------------------------------------- 1. Un vecteur est un type comme un autre
SELECT extversion FROM pg_extension WHERE extname = 'vector';

SELECT '[1, 0, 0]'::vector <-> '[0, 1, 0]'::vector AS distance_l2,           -- √2
       '[1, 0, 0]'::vector <=> '[0, 1, 0]'::vector AS distance_cosinus,      -- 1 : orthogonaux
       '[1, 1, 0]'::vector <=> '[2, 2, 0]'::vector AS meme_direction,        -- 0 : la norme ne compte pas
       '[1, 0, 0]'::vector <#> '[1, 0, 0]'::vector AS produit_scalaire_neg;  -- -1

-- ---------------------------------------------------------------- 2. La table : une colonne vector(384) à côté des colonnes classiques
\d films

SELECT id, titre, annee, left(embedding::text, 50) || '…' AS embedding
FROM films ORDER BY id LIMIT 3;

-- ---------------------------------------------------------------- 3. Le problème : la recherche par mots-clés
SELECT texte FROM requetes WHERE label = 'robot';

SELECT titre FROM films
WHERE synopsis ILIKE '%robot%' AND synopsis ILIKE '%amoureu%';     -- 0 ligne : WALL-E n'est jamais décrit avec ces mots

-- ---------------------------------------------------------------- 4. La recherche sémantique : ORDER BY distance LIMIT k
SELECT f.titre, f.annee, round((f.embedding <=> r.embedding)::numeric, 3) AS distance
FROM films f, requetes r
WHERE r.label = 'robot'
ORDER BY f.embedding <=> r.embedding
LIMIT 5;

SELECT f.titre, round((f.embedding <=> r.embedding)::numeric, 3) AS distance
FROM films f, requetes r WHERE r.label = 'temps'
ORDER BY f.embedding <=> r.embedding LIMIT 5;

-- Multilingue : une requête en anglais retrouve des synopsis en français
SELECT f.titre, round((f.embedding <=> r.embedding)::numeric, 3) AS distance
FROM films f, requetes r WHERE r.label = 'english'
ORDER BY f.embedding <=> r.embedding LIMIT 3;

-- « Si vous avez aimé… » : le vecteur d'une ligne sert de requête (recommandation)
SELECT titre, round((embedding <=> (SELECT embedding FROM films WHERE titre = 'Interstellar'))::numeric, 3) AS distance
FROM films WHERE titre <> 'Interstellar'
ORDER BY embedding <=> (SELECT embedding FROM films WHERE titre = 'Interstellar')
LIMIT 5;

-- ---------------------------------------------------------------- 5. La force de PostgreSQL : SQL hybride (filtres + jointures + ACID)
SELECT f.titre, f.annee, f.pays, round((f.embedding <=> r.embedding)::numeric, 3) AS distance
FROM films f JOIN requetes r ON r.label = 'casse'
WHERE f.annee >= 2000
ORDER BY f.embedding <=> r.embedding
LIMIT 5;

-- Transactionnel : le film et son vecteur arrivent ensemble, ou pas du tout
BEGIN;
INSERT INTO films
SELECT 999, 'Le film de la démo', 2026, 'Sabon & Stien', 'Science-fiction', 'France',
       'Inséré en direct pendant la présentation', embedding
FROM requetes WHERE label = 'espace';
SELECT titre FROM films ORDER BY embedding <=> (SELECT embedding FROM requetes WHERE label = 'espace') LIMIT 3;
ROLLBACK;  -- tout disparaît, index HNSW compris

-- ---------------------------------------------------------------- 6. L'index HNSW : le prouver avec EXPLAIN
\di films*

-- Sur 91 lignes, le planificateur choisit (à raison) un parcours séquentiel + tri
SELECT embedding AS q FROM requetes WHERE label = 'robot' \gset
EXPLAIN (ANALYZE, COSTS OFF) SELECT titre FROM films ORDER BY embedding <=> :'q' LIMIT 5;

-- On le force à utiliser l'index pour voir apparaître « Index Scan using films_embedding_hnsw »
SET enable_seqscan = off;
EXPLAIN (ANALYZE, COSTS OFF) SELECT titre FROM films ORDER BY embedding <=> :'q' LIMIT 5;
RESET enable_seqscan;

-- Réglages : le compromis rappel / vitesse
SHOW hnsw.ef_search;                       -- 40 par défaut : taille de la liste de candidats explorés
SET hnsw.ef_search = 100;                  -- meilleur rappel, plus lent
SET hnsw.iterative_scan = strict_order;    -- pgvector ≥ 0.8 : poursuit le parcours si le WHERE élimine trop de candidats

-- ---------------------------------------------------------------- 7. Après le benchmark (python bench.py) : passage à l'échelle
SELECT count(*) FROM bench;
SELECT pg_size_pretty(pg_table_size('bench')) AS table, pg_size_pretty(pg_relation_size('bench_v_hnsw')) AS index_hnsw;

\timing on
SELECT v AS qb FROM bench WHERE id = 42 \gset
SET hnsw.ef_search = 40;
SELECT id, v <=> :'qb' AS d FROM bench ORDER BY v <=> :'qb' LIMIT 5;    -- index HNSW : quelques ms
SET enable_indexscan = off;
SELECT id, v <=> :'qb' AS d FROM bench ORDER BY v <=> :'qb' LIMIT 5;    -- exact : seq scan complet
RESET enable_indexscan;
\timing off
