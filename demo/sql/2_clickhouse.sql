-- =====================================================================
--  DÉMO 2 — ClickHouse, en SQL pur
--  Navigateur : http://localhost:8123/play   (utilisateur demo / mot de passe demo)
--  ou terminal : docker compose exec clickhouse clickhouse-client --user demo --password demo
--  Exécuter une requête à la fois.
-- =====================================================================

-- ---------------------------------------------------------------- 1. Pas de type « vector » : un simple Array(Float32)
SELECT version();

SELECT L2Distance([1, 0, 0], [0, 1, 0])     AS distance_l2,
       cosineDistance([1, 0, 0], [0, 1, 0]) AS distance_cosinus,
       cosineDistance([1, 1, 0], [2, 2, 0]) AS meme_direction,
       dotProduct([1, 0, 0], [1, 0, 0])     AS produit_scalaire;

SHOW CREATE TABLE films;      -- INDEX ... TYPE vector_similarity('hnsw', 'cosineDistance', 384)

SELECT id, titre, length(embedding) AS dim, arraySlice(embedding, 1, 4) AS debut
FROM films ORDER BY id LIMIT 3;

-- ---------------------------------------------------------------- 2. La recherche sémantique : la même requête qu'en PostgreSQL
WITH (SELECT embedding FROM requetes WHERE label = 'robot') AS ref
SELECT titre, annee, round(cosineDistance(embedding, ref), 3) AS distance
FROM films
ORDER BY cosineDistance(embedding, ref) ASC
LIMIT 5;

WITH (SELECT embedding FROM requetes WHERE label = 'maison_hantee') AS ref
SELECT titre, round(cosineDistance(embedding, ref), 3) AS distance
FROM films ORDER BY cosineDistance(embedding, ref) LIMIT 5;

-- « Si vous avez aimé Inception… »
WITH (SELECT embedding FROM films WHERE titre = 'Inception') AS ref
SELECT titre, round(cosineDistance(embedding, ref), 3) AS distance
FROM films WHERE titre != 'Inception'
ORDER BY cosineDistance(embedding, ref) LIMIT 5;

-- ---------------------------------------------------------------- 3. Le point fort de ClickHouse : de l'analytique SUR le résultat vectoriel
-- « Parmi les 20 films les plus proches de 'voyager dans le temps', quels genres et quelles décennies ? »
SELECT genre,
       count()                                           AS nb_films,
       round(avg(annee))                                 AS annee_moyenne,
       arraySort(groupUniqArray(intDiv(annee, 10) * 10)) AS decennies
FROM (
    WITH (SELECT embedding FROM requetes WHERE label = 'temps') AS ref
    SELECT genre, annee
    FROM films
    ORDER BY cosineDistance(embedding, ref) ASC
    LIMIT 20
)
GROUP BY genre
ORDER BY nb_films DESC;

-- Matrice de similarité entre toutes les requêtes et tous les genres (centroïdes calculés à la volée)
SELECT r.label,
       argMin(g.genre, cosineDistance(r.embedding, g.centroide)) AS genre_le_plus_proche,
       round(min(cosineDistance(r.embedding, g.centroide)), 3)   AS distance
FROM requetes AS r
CROSS JOIN (
    SELECT genre, CAST(avgForEach(embedding) AS Array(Float32)) AS centroide   -- moyenne composante par composante
    FROM films GROUP BY genre
) AS g
GROUP BY r.label
ORDER BY distance;

-- ---------------------------------------------------------------- 4. L'index : le prouver avec EXPLAIN
-- Forme imposée : référence constante (WITH), ORDER BY <même fonction que l'index> ASC, LIMIT ≤ 100
EXPLAIN indexes = 1
WITH (SELECT embedding FROM requetes WHERE label = 'robot') AS ref
SELECT titre FROM films ORDER BY cosineDistance(embedding, ref) LIMIT 5;
-- chercher la ligne « Skip … Name: idx_embedding … Description: vector_similarity GRANULARITY … »
-- (si votre version ne l'affiche pas avec un sous-select, le panneau « Plan d'exécution » de l'interface
--  fait la même requête avec le vecteur écrit en littéral)

-- Réglages de recherche (équivalent de hnsw.ef_search)
WITH (SELECT embedding FROM requetes WHERE label = 'robot') AS ref
SELECT titre FROM films ORDER BY cosineDistance(embedding, ref) LIMIT 5
SETTINGS hnsw_candidate_list_size_for_search = 64;

-- ---------------------------------------------------------------- 5. Après le benchmark : la force brute vectorisée
SELECT count() FROM bench;
SELECT formatReadableSize(sum(data_compressed_bytes))             AS donnees,
       formatReadableSize(sum(secondary_indices_compressed_bytes)) AS index_hnsw,
       count()                                                     AS parts
FROM system.parts WHERE database = currentDatabase() AND table = 'bench' AND active;

-- Avec l'index HNSW
WITH (SELECT v FROM bench WHERE id = 42) AS ref
SELECT id, cosineDistance(v, ref) AS d FROM bench ORDER BY cosineDistance(v, ref) LIMIT 5;

-- Sans index : scan complet en colonnes + SIMD (regarder « Elapsed » et « rows/s »)
WITH (SELECT v FROM bench WHERE id = 42) AS ref
SELECT id, cosineDistance(v, ref) AS d FROM bench ORDER BY cosineDistance(v, ref) LIMIT 5
SETTINGS use_skip_indexes = 0;

-- Bonus : générer 1 million de vecteurs aléatoires DANS la base, en une requête
CREATE TABLE IF NOT EXISTS random_1m (id UInt32, v Array(Float32) CODEC(NONE)) ENGINE = MergeTree ORDER BY id;
-- (le 3e argument de randNormal est ignoré : il empêche que la valeur soit calculée une seule fois)
INSERT INTO random_1m SELECT number, arrayMap(i -> randNormal(0, 1, number + i), range(384)) FROM numbers(1000000);
WITH (SELECT v FROM random_1m WHERE id = 7) AS ref
SELECT id, cosineDistance(v, ref) AS d FROM random_1m ORDER BY d LIMIT 5;     -- 1 M × 384 floats, force brute
DROP TABLE random_1m;
