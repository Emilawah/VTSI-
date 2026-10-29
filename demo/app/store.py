"""Connexions, schémas et chargement du corpus de films dans les deux bases.

Les deux tables `films` sont strictement identiques côté données ; seul le moteur change.
La table `requetes` contient quelques requêtes en langage naturel déjà vectorisées,
pour pouvoir faire de la recherche sémantique en SQL pur pendant la démo (psql / ClickHouse Play).
"""
import json
import os
import pathlib
import time

import clickhouse_connect
import psycopg
from pgvector.psycopg import register_vector

from embed import DIM, embed

PG_DSN = os.getenv("PG_DSN", "postgresql://demo:demo@localhost:5433/demo")
CH_HOST = os.getenv("CH_HOST", "localhost")
CH_USER = os.getenv("CH_USER", "demo")
CH_PASSWORD = os.getenv("CH_PASSWORD", "demo")

DATA = pathlib.Path(__file__).parent / "data" / "films.json"

# Requêtes « pré-vectorisées » utilisables directement en SQL pendant la démo
PRESET_QUERIES = {
    "robot": "un robot qui tombe amoureux",
    "temps": "voyager dans le temps",
    "maison_hantee": "avoir peur dans une maison hantée",
    "espace": "survivre seul dans l'espace",
    "casse": "une équipe prépare un cambriolage",
    "reves": "des rêves à l'intérieur de rêves",
    "english": "a heist movie with a clever crew",
}

COLUMNS = ["id", "titre", "annee", "realisateur", "genre", "pays", "synopsis"]


def pg_connect() -> psycopg.Connection:
    conn = psycopg.connect(PG_DSN, autocommit=True)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    register_vector(conn)
    return conn


def ch_connect():
    return clickhouse_connect.get_client(host=CH_HOST, port=8123, username=CH_USER, password=CH_PASSWORD)


def wait_for_dbs(timeout: float = 120) -> None:
    deadline = time.time() + timeout
    while True:
        try:
            pg_connect().close()
            ch_connect().command("SELECT 1")
            return
        except Exception as exc:  # noqa: BLE001 - on attend simplement que les conteneurs soient prêts
            if time.time() > deadline:
                raise
            print(f"[store] bases pas encore prêtes ({exc.__class__.__name__}), nouvel essai…", flush=True)
            time.sleep(2)


PG_SCHEMA = f"""
DROP TABLE IF EXISTS films;
DROP TABLE IF EXISTS requetes;
CREATE TABLE films (
    id          int PRIMARY KEY,
    titre       text NOT NULL,
    annee       int  NOT NULL,
    realisateur text NOT NULL,
    genre       text NOT NULL,
    pays        text NOT NULL,
    synopsis    text NOT NULL,
    embedding   vector({DIM}) NOT NULL
);
-- Index ANN : graphe HNSW, distance cosinus
CREATE INDEX films_embedding_hnsw ON films USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
-- Index plein texte classique, pour la comparaison « mots-clés »
CREATE INDEX films_fts ON films USING gin (to_tsvector('french', titre || ' ' || synopsis));
CREATE TABLE requetes (label text PRIMARY KEY, texte text NOT NULL, embedding vector({DIM}) NOT NULL);
"""

CH_SCHEMA = [
    "DROP TABLE IF EXISTS films",
    "DROP TABLE IF EXISTS requetes",
    f"""
    CREATE TABLE films (
        id          UInt32,
        titre       String,
        annee       UInt16,
        realisateur String,
        genre       LowCardinality(String),
        pays        LowCardinality(String),
        synopsis    String,
        embedding   Array(Float32) CODEC(NONE),
        INDEX idx_embedding embedding TYPE vector_similarity('hnsw', 'cosineDistance', {DIM}) GRANULARITY 100000000,
        CONSTRAINT dim_ok CHECK length(embedding) = {DIM}
    ) ENGINE = MergeTree ORDER BY id
    """,
    """
    CREATE TABLE requetes (label String, texte String, embedding Array(Float32))
    ENGINE = MergeTree ORDER BY label
    """,
]


def document_text(film: dict) -> str:
    """Le texte vectorisé : titre + synopsis (ni le genre ni le réalisateur, pour ne pas « tricher »)."""
    return f"{film['titre']}. {film['synopsis']}"


def load_corpus() -> dict:
    """(Re)crée les tables dans les deux bases et y charge le même corpus vectorisé."""
    films = json.loads(DATA.read_text(encoding="utf-8"))
    t0 = time.perf_counter()
    vecs = embed([document_text(f) for f in films])
    qvecs = embed(list(PRESET_QUERIES.values()))
    t_embed = time.perf_counter() - t0

    t0 = time.perf_counter()
    with pg_connect() as pg, pg.cursor() as cur:
        cur.execute(PG_SCHEMA)
        cur.executemany(
            "INSERT INTO films VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            [tuple(f[c] for c in COLUMNS) + (vecs[i],) for i, f in enumerate(films)],
        )
        cur.executemany(
            "INSERT INTO requetes VALUES (%s, %s, %s)",
            [(label, texte, qvecs[i]) for i, (label, texte) in enumerate(PRESET_QUERIES.items())],
        )
        cur.execute("ANALYZE films")
    t_pg = time.perf_counter() - t0

    t0 = time.perf_counter()
    ch = ch_connect()
    for stmt in CH_SCHEMA:
        ch.command(stmt)
    ch.insert(
        "films",
        [[f[c] for c in COLUMNS] + [vecs[i].tolist()] for i, f in enumerate(films)],
        column_names=COLUMNS + ["embedding"],
    )
    ch.insert(
        "requetes",
        [[label, texte, qvecs[i].tolist()] for i, (label, texte) in enumerate(PRESET_QUERIES.items())],
        column_names=["label", "texte", "embedding"],
    )
    t_ch = time.perf_counter() - t0

    return {
        "documents": len(films),
        "dimensions": DIM,
        "embedding_s": round(t_embed, 2),
        "postgres_s": round(t_pg, 2),
        "clickhouse_s": round(t_ch, 2),
    }


def corpus_loaded() -> bool:
    try:
        with pg_connect() as pg:
            n_pg = pg.execute("SELECT count(*) FROM films").fetchone()[0]
        n_ch = ch_connect().command("SELECT count() FROM films")
        return n_pg > 0 and int(n_ch) > 0
    except Exception:  # noqa: BLE001 - table absente = corpus non chargé
        return False


if __name__ == "__main__":
    wait_for_dbs()
    print(load_corpus())
