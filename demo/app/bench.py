"""Benchmark équitable pgvector vs ClickHouse.

Protocole (identique pour les deux moteurs) :
  1. N vecteurs synthétiques « en grappes » (plus réalistes qu'un bruit uniforme), normalisés, dimension D.
  2. Vérité terrain : les k plus proches voisins EXACTS calculés avec numpy.
  3. Ingestion, recherche exacte (sans index), construction de l'index HNSW, recherche approchée.
  4. Mêmes paramètres HNSW des deux côtés : M = 16, ef_construction = 64, vecteurs float32 (pas de quantification).
  5. Mesures : temps d'ingestion, de construction d'index, latence p50/p95 (client, mono-thread), recall@k, taille disque.

Usage (dans le conteneur) :
  docker compose exec app python bench.py                 # 100 000 x 384
  docker compose exec app python bench.py --n 1000000     # 1 M x 384 (prévoir ~8 Go de RAM pour Docker)
"""
import argparse
import datetime as dt
import json
import pathlib
import statistics
import time

import numpy as np

import store

RESULTS = pathlib.Path(__file__).parent / "results"
M, EF_CONSTRUCTION = 16, 64


def generate(n, dim, clusters, seed):
    rng = np.random.default_rng(seed)
    centers = rng.standard_normal((clusters, dim)).astype(np.float32)
    X = np.empty((n, dim), dtype=np.float32)
    for a in range(0, n, 100_000):
        b = min(n, a + 100_000)
        labels = rng.integers(0, clusters, b - a)
        X[a:b] = centers[labels] + 0.8 * rng.standard_normal((b - a, dim)).astype(np.float32)
    X /= np.linalg.norm(X, axis=1, keepdims=True)
    return X, centers, rng


def make_queries(centers, nq, rng):
    labels = rng.integers(0, len(centers), nq)
    Q = centers[labels] + 0.8 * rng.standard_normal((nq, centers.shape[1])).astype(np.float32)
    return Q / np.linalg.norm(Q, axis=1, keepdims=True)


def ground_truth(X, Q, k):
    """k plus proches voisins exacts (cosinus = produit scalaire sur vecteurs normalisés), par blocs."""
    best_s = np.full((len(Q), k), -np.inf, dtype=np.float32)
    best_i = np.zeros((len(Q), k), dtype=np.int64)
    for a in range(0, len(X), 200_000):
        s = Q @ X[a:a + 200_000].T
        idx = np.argpartition(-s, k, axis=1)[:, :k] if s.shape[1] > k else np.tile(np.arange(s.shape[1]), (len(Q), 1))
        cand_s = np.concatenate([best_s, np.take_along_axis(s, idx, 1)], axis=1)
        cand_i = np.concatenate([best_i, idx + a], axis=1)
        order = np.argsort(-cand_s, axis=1)[:, :k]
        best_s, best_i = np.take_along_axis(cand_s, order, 1), np.take_along_axis(cand_i, order, 1)
    return [set(row.tolist()) for row in best_i]


def measure(run_query, Q, truth, k, warmup=5):
    for q in Q[:warmup]:
        run_query(q)
    lat, recall = [], []
    for q, t in zip(Q, truth):
        t0 = time.perf_counter()
        ids = run_query(q)
        lat.append((time.perf_counter() - t0) * 1000)
        recall.append(len(set(ids) & t) / k)
    lat.sort()
    return {
        "p50_ms": round(statistics.median(lat), 2),
        "p95_ms": round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 2),
        "qps_1_thread": round(1000 / statistics.mean(lat), 1),
        "recall": round(statistics.mean(recall), 4),
    }


def timed(fn):
    t0 = time.perf_counter()
    fn()
    return round(time.perf_counter() - t0, 2)


# --------------------------------------------------------------------------- PostgreSQL + pgvector
def bench_postgres(X, Q, truth, k, ef_values, exact_queries):
    out = {"engine": "PostgreSQL + pgvector"}
    conn = store.pg_connect()
    out["version"] = conn.execute("SELECT 'PostgreSQL ' || current_setting('server_version') || ' / pgvector ' || extversion FROM pg_extension WHERE extname = 'vector'").fetchone()[0]
    dim = X.shape[1]
    conn.execute("DROP TABLE IF EXISTS bench")
    conn.execute(f"CREATE TABLE bench (id int4 PRIMARY KEY, v vector({dim}) NOT NULL)")

    def ingest():
        with conn.cursor() as cur, cur.copy("COPY bench (id, v) FROM STDIN WITH (FORMAT BINARY)") as copy:
            copy.set_types(["int4", "vector"])
            for i in range(len(X)):
                copy.write_row((i, X[i]))
        conn.execute("ANALYZE bench")

    out["ingest_s"] = timed(ingest)
    print(f"  ingestion : {out['ingest_s']} s", flush=True)

    # LIMIT en dur : un LIMIT paramétré peut pousser le planificateur (plan générique) vers un seq scan
    def q_ids(q):
        return [r[0] for r in conn.execute(f"SELECT id FROM bench ORDER BY v <=> %s LIMIT {k}", (q,)).fetchall()]

    out["exact"] = measure(q_ids, Q[:exact_queries], truth[:exact_queries], k, warmup=2)
    print(f"  recherche exacte (seq scan) : {out['exact']}", flush=True)

    conn.execute("SET maintenance_work_mem = '1GB'")
    conn.execute("SET max_parallel_maintenance_workers = 4")
    out["index_build_s"] = timed(lambda: conn.execute(
        f"CREATE INDEX bench_v_hnsw ON bench USING hnsw (v vector_cosine_ops) WITH (m = {M}, ef_construction = {EF_CONSTRUCTION})"))
    print(f"  construction HNSW : {out['index_build_s']} s", flush=True)

    out["ann"] = {}
    for ef in ef_values:
        conn.execute(f"SET hnsw.ef_search = {max(ef, k)}")
        out["ann"][str(ef)] = measure(q_ids, Q, truth, k)
        print(f"  HNSW ef_search={ef} : {out['ann'][str(ef)]}", flush=True)

    plan = conn.execute(f"EXPLAIN SELECT id FROM bench ORDER BY v <=> %s LIMIT {k}", (Q[0],)).fetchall()
    out["plan"] = "\n".join(r[0] for r in plan)
    out["table_mb"] = round(conn.execute("SELECT pg_table_size('bench')").fetchone()[0] / 2**20, 1)
    out["index_mb"] = round(conn.execute("SELECT pg_relation_size('bench_v_hnsw')").fetchone()[0] / 2**20, 1)
    conn.close()
    return out


# --------------------------------------------------------------------------- ClickHouse
def bench_clickhouse(X, Q, truth, k, ef_values, exact_queries):
    out = {"engine": "ClickHouse"}
    ch = store.ch_connect()
    out["version"] = "ClickHouse " + ch.command("SELECT version()")
    dim = X.shape[1]
    ch.command("DROP TABLE IF EXISTS bench")
    ch.command(f"CREATE TABLE bench (id UInt32, v Array(Float32) CODEC(NONE)) ENGINE = MergeTree ORDER BY id")

    def ingest():
        for a in range(0, len(X), 50_000):
            b = min(len(X), a + 50_000)
            ch.insert("bench", [list(range(a, b)), X[a:b].tolist()], column_names=["id", "v"], column_oriented=True)
        # Une seule « part » => un seul graphe HNSW à interroger (sinon un graphe par part)
        ch.command("OPTIMIZE TABLE bench FINAL")

    out["ingest_s"] = timed(ingest)
    print(f"  ingestion (+ OPTIMIZE FINAL) : {out['ingest_s']} s", flush=True)

    def literal(q):
        return "CAST([" + ",".join(f"{x:.7g}" for x in q) + "] AS Array(Float32))"

    def q_ids(q, settings=""):
        sql = f"WITH {literal(q)} AS ref SELECT id FROM bench ORDER BY cosineDistance(v, ref) ASC LIMIT {k} {settings}"
        return [r[0] for r in ch.query(sql).result_rows]

    out["exact"] = measure(q_ids, Q[:exact_queries], truth[:exact_queries], k, warmup=2)
    print(f"  recherche exacte (scan colonne) : {out['exact']}", flush=True)

    ch.command(f"ALTER TABLE bench ADD INDEX idx_v v TYPE vector_similarity('hnsw', 'cosineDistance', {dim}, 'f32', {M}, {EF_CONSTRUCTION})")
    out["index_build_s"] = timed(lambda: ch.command("ALTER TABLE bench MATERIALIZE INDEX idx_v SETTINGS mutations_sync = 2"))
    print(f"  construction HNSW : {out['index_build_s']} s", flush=True)

    out["ann"] = {}
    for ef in ef_values:
        s = f"SETTINGS hnsw_candidate_list_size_for_search = {max(ef, k)}"
        out["ann"][str(ef)] = measure(lambda q: q_ids(q, s), Q, truth, k)
        print(f"  HNSW ef_search={ef} : {out['ann'][str(ef)]}", flush=True)

    plan = ch.query(f"EXPLAIN indexes = 1 WITH {literal(Q[0])} AS ref SELECT id FROM bench ORDER BY cosineDistance(v, ref) ASC LIMIT {k}")
    out["plan"] = "\n".join(r[0] for r in plan.result_rows)
    sizes = ch.query("SELECT sum(data_compressed_bytes), sum(secondary_indices_compressed_bytes) FROM system.parts WHERE database = currentDatabase() AND table = 'bench' AND active").result_rows[0]
    out["table_mb"] = round(sizes[0] / 2**20, 1)
    out["index_mb"] = round(sizes[1] / 2**20, 1)
    return out


def to_markdown(r):
    lines = [
        f"**Jeu de données** : {r['n']:,} vecteurs × {r['dim']} dimensions, {r['queries']} requêtes, k = {r['k']} — HNSW M = {M}, ef_construction = {EF_CONSTRUCTION}, float32".replace(",", " "),
        "",
        "| Mesure | PostgreSQL + pgvector | ClickHouse |",
        "|---|---:|---:|",
    ]
    pg, chh = r["postgres"], r["clickhouse"]
    lines.append(f"| Ingestion | {pg['ingest_s']} s | {chh['ingest_s']} s |")
    lines.append(f"| Recherche exacte p50 | {pg['exact']['p50_ms']} ms | {chh['exact']['p50_ms']} ms |")
    lines.append(f"| Construction index HNSW | {pg['index_build_s']} s | {chh['index_build_s']} s |")
    for ef in r["ef_values"]:
        a, b = pg["ann"][str(ef)], chh["ann"][str(ef)]
        lines.append(f"| HNSW ef={ef} : p50 / p95 | {a['p50_ms']} / {a['p95_ms']} ms | {b['p50_ms']} / {b['p95_ms']} ms |")
        lines.append(f"| HNSW ef={ef} : recall@{r['k']} | {a['recall']:.3f} | {b['recall']:.3f} |")
    lines.append(f"| Taille table / index | {pg['table_mb']} / {pg['index_mb']} Mo | {chh['table_mb']} / {chh['index_mb']} Mo |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=100_000)
    ap.add_argument("--dim", type=int, default=384)
    ap.add_argument("--queries", type=int, default=200)
    ap.add_argument("--exact-queries", type=int, default=20, help="la recherche exacte est lente : moins de requêtes")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--clusters", type=int, default=200)
    ap.add_argument("--ef", type=int, nargs="+", default=[40, 100, 200])
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--only", choices=["postgres", "clickhouse"])
    args = ap.parse_args()

    store.wait_for_dbs()
    print(f"Génération de {args.n:,} vecteurs × {args.dim}…".replace(",", " "), flush=True)
    X, centers, rng = generate(args.n, args.dim, args.clusters, args.seed)
    Q = make_queries(centers, args.queries, rng)
    print("Calcul de la vérité terrain (k-NN exact numpy)…", flush=True)
    truth = ground_truth(X, Q, args.k)

    result = {
        "date": dt.datetime.now().isoformat(timespec="seconds"),
        "n": args.n, "dim": args.dim, "queries": args.queries, "k": args.k, "ef_values": args.ef,
        "hnsw": {"m": M, "ef_construction": EF_CONSTRUCTION},
    }
    if args.only != "clickhouse":
        print("\n== PostgreSQL + pgvector ==", flush=True)
        result["postgres"] = bench_postgres(X, Q, truth, args.k, args.ef, args.exact_queries)
    if args.only != "postgres":
        print("\n== ClickHouse ==", flush=True)
        result["clickhouse"] = bench_clickhouse(X, Q, truth, args.k, args.ef, args.exact_queries)

    RESULTS.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    for name in (f"bench-{stamp}.json", "bench.json"):
        (RESULTS / name).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    if "postgres" in result and "clickhouse" in result:
        md = to_markdown(result)
        (RESULTS / "bench.md").write_text(md + "\n", encoding="utf-8")
        print("\n" + md)
    print(f"\nRésultats écrits dans results/ (visibles dans l'onglet Benchmark de http://localhost:8000)")


if __name__ == "__main__":
    main()
