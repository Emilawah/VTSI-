"""Vector Search Arena — API de la démo.

Même requête, même vecteur, trois moteurs :
  * PostgreSQL plein texte (la recherche « par mots-clés » d'avant)
  * PostgreSQL + pgvector (opérateur <=>, index HNSW)
  * ClickHouse (cosineDistance, index vector_similarity)
"""
import json
import pathlib
import threading
import time

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import store
from embed import DIM, MODEL_NAME, ch_literal, embed_one, short_literal

app = FastAPI(title="Vector Search Arena")
ROOT = pathlib.Path(__file__).parent
RESULTS = ROOT / "results"

_pg_lock = threading.Lock()
_ch_lock = threading.Lock()  # le client HTTP ClickHouse partage une session : pas de requêtes concurrentes
_pg = None
_ch = None
_space = None  # base PCA mise en cache : (moyenne, composantes, points)
_status = {"ready": False, "message": "Démarrage…"}


def pg():
    global _pg
    if _pg is None or _pg.closed:
        _pg = store.pg_connect()
    return _pg


def ch():
    global _ch
    if _ch is None:
        _ch = store.ch_connect()
    return _ch


def _boot():
    try:
        _status["message"] = "Attente des bases de données…"
        store.wait_for_dbs()
        if not store.corpus_loaded():
            _status["message"] = "Chargement du corpus et calcul des embeddings…"
            print("[boot]", store.load_corpus(), flush=True)
        embed_one("préchauffage du modèle")
        _status.update(ready=True, message="Prêt")
    except Exception as exc:  # noqa: BLE001
        _status.update(ready=False, message=f"Erreur au démarrage : {exc}")
        raise


@app.on_event("startup")
def startup():
    threading.Thread(target=_boot, daemon=True).start()


def _timed(fn):
    t0 = time.perf_counter()
    out = fn()
    return out, round((time.perf_counter() - t0) * 1000, 2)


def _filters_pg(pays, annee_min):
    where, params = [], []
    if pays:
        where.append("pays = %s")
        params.append(pays)
    if annee_min:
        where.append("annee >= %s")
        params.append(annee_min)
    return ("WHERE " + " AND ".join(where) + "\n") if where else "", params


def _filters_ch(pays, annee_min):
    where, params = [], {}
    if pays:
        where.append("pays = {pays:String}")
        params["pays"] = pays
    if annee_min:
        where.append("annee >= {annee_min:UInt16}")
        params["annee_min"] = annee_min
    return ("WHERE " + " AND ".join(where) + "\n") if where else "", params


ROW_KEYS = ["id", "titre", "annee", "realisateur", "genre", "pays", "score"]
SELECT_COLS = "SELECT id, titre, annee, realisateur, genre, pays,\n"


def search_fulltext(q, k, pays, annee_min):
    where, params = _filters_pg(pays, annee_min)
    where = where.replace("WHERE ", "AND ") if where else ""
    # Requête « OU » sur les lexèmes : on laisse sa chance au plein texte
    sql = (
        SELECT_COLS
        + "       ts_rank(to_tsvector('french', titre || ' ' || synopsis), q) AS score\n"
        "FROM films,\n"
        "     replace(plainto_tsquery('french', %s)::text, '&', '|')::tsquery AS q\n"
        "WHERE to_tsvector('french', titre || ' ' || synopsis) @@ q\n"
        f"{where}"
        f"ORDER BY score DESC\nLIMIT {int(k)}"
    )
    with _pg_lock:
        rows, ms = _timed(lambda: pg().execute(sql, [q, *params]).fetchall())
    shown = sql.replace("%s", "'" + q.replace("'", "''") + "'", 1)
    return {"sql": shown, "ms": ms, "rows": [dict(zip(ROW_KEYS, r)) for r in rows]}


def search_pgvector(vec, k, pays, annee_min):
    where, params = _filters_pg(pays, annee_min)
    sql = (
        SELECT_COLS
        + "       embedding <=> %s AS distance\n"
        "FROM films\n"
        f"{where}"
        f"ORDER BY embedding <=> %s\nLIMIT {int(k)}"
    )
    with _pg_lock:
        rows, ms = _timed(lambda: pg().execute(sql, [vec, *params, vec]).fetchall())
    shown = sql.replace("%s", f"'{short_literal(vec)}'::vector")
    return {"sql": shown, "ms": ms, "rows": [dict(zip(ROW_KEYS, r)) for r in rows]}


def search_clickhouse(vec, k, pays, annee_min):
    where, params = _filters_ch(pays, annee_min)
    body = (
        SELECT_COLS
        + "       cosineDistance(embedding, ref) AS distance\n"
        "FROM films\n"
        f"{where}"
        f"ORDER BY cosineDistance(embedding, ref) ASC\nLIMIT {int(k)}"
    )
    sql = f"WITH {ch_literal(vec)} AS ref\n" + body
    with _ch_lock:
        res, ms = _timed(lambda: ch().query(sql, parameters=params))
    shown = f"WITH {short_literal(vec)} AS ref\n" + body
    return {"sql": shown, "ms": ms, "rows": [dict(zip(ROW_KEYS, r)) for r in res.result_rows]}


def analytics_clickhouse(vec, n=20):
    """Ce que ClickHouse fait mieux que tout le monde : agréger le résultat d'une recherche vectorielle."""
    body = (
        "SELECT genre,\n"
        "       count()                                         AS nb_films,\n"
        "       round(avg(annee))                               AS annee_moyenne,\n"
        "       round(min(distance), 3)                         AS meilleure_distance,\n"
        "       arraySort(groupUniqArray(intDiv(annee, 10) * 10)) AS decennies,\n"
        "       groupUniqArray(pays)                            AS liste_pays\n"
        "FROM (\n"
        "    WITH {ref} AS ref\n"
        "    SELECT genre, annee, pays, cosineDistance(embedding, ref) AS distance\n"
        "    FROM films\n"
        "    ORDER BY cosineDistance(embedding, ref) ASC\n"
        f"    LIMIT {int(n)}\n"
        ")\n"
        "GROUP BY genre\n"
        "ORDER BY nb_films DESC, meilleure_distance ASC"
    )
    with _ch_lock:
        res, ms = _timed(lambda: ch().query(body.replace("{ref}", ch_literal(vec))))
    keys = ["genre", "nb_films", "annee_moyenne", "meilleure_distance", "decennies", "liste_pays"]
    return {
        "sql": body.replace("{ref}", short_literal(vec)),
        "ms": ms,
        "rows": [dict(zip(keys, r)) for r in res.result_rows],
    }


def _require_ready():
    if not _status["ready"]:
        raise HTTPException(503, _status["message"])


@app.get("/api/health")
def health():
    info = {"ready": _status["ready"], "message": _status["message"], "model": MODEL_NAME, "dimensions": DIM}
    if _status["ready"]:
        with _pg_lock:
            info["postgres"] = pg().execute("SHOW server_version").fetchone()[0]
            info["pgvector"] = pg().execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()[0]
            info["documents"] = pg().execute("SELECT count(*) FROM films").fetchone()[0]
            info["pays"] = [r[0] for r in pg().execute("SELECT pays FROM films GROUP BY pays ORDER BY count(*) DESC, pays").fetchall()]
        with _ch_lock:
            info["clickhouse"] = ch().command("SELECT version()")
    return info


@app.get("/api/search")
def search(q: str, k: int = 5, pays: str = "", annee_min: int = 0):
    _require_ready()
    k = max(1, min(k, 50))
    vec, embed_ms = _timed(lambda: embed_one(q))
    return {
        "query": q,
        "embedding_ms": embed_ms,
        "embedding_preview": [round(float(x), 4) for x in vec[:8]],
        "fulltext": search_fulltext(q, k, pays, annee_min),
        "pgvector": search_pgvector(vec, k, pays, annee_min),
        "clickhouse": search_clickhouse(vec, k, pays, annee_min),
        "analytics": analytics_clickhouse(vec),
    }


@app.get("/api/explain")
def explain(q: str, engine: str, force_index: bool = True, k: int = 5):
    """Plan d'exécution : on prouve que l'index ANN est (ou n'est pas) utilisé."""
    _require_ready()
    vec = embed_one(q)
    if engine == "pgvector":
        sql = f"EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)\nSELECT id, titre FROM films ORDER BY embedding <=> %s LIMIT {int(k)}"
        with _pg_lock, pg().transaction():
            # Sur 91 lignes, le planificateur préfère (à raison) un parcours séquentiel : on le force à utiliser HNSW.
            if force_index:
                pg().execute("SET LOCAL enable_seqscan = off")
            plan = "\n".join(r[0] for r in pg().execute(sql, [vec]).fetchall())
        prefix = "SET enable_seqscan = off;\n" if force_index else ""
        return {"sql": prefix + sql.replace("%s", f"'{short_literal(vec)}'::vector"), "plan": plan}
    if engine == "clickhouse":
        body = "EXPLAIN indexes = 1\nWITH {ref} AS ref\nSELECT id, titre FROM films\nORDER BY cosineDistance(embedding, ref) ASC\nLIMIT " + str(int(k))
        with _ch_lock:
            res = ch().query(body.replace("{ref}", ch_literal(vec)))
        return {"sql": body.replace("{ref}", short_literal(vec)), "plan": "\n".join(r[0] for r in res.result_rows)}
    raise HTTPException(400, "engine doit valoir pgvector ou clickhouse")


@app.get("/api/space")
def space(q: str = ""):
    """Projection 2D (ACP) de tous les films, et de la requête si fournie."""
    global _space
    _require_ready()
    if _space is None:
        with _pg_lock:
            rows = pg().execute("SELECT id, titre, genre, embedding FROM films ORDER BY id").fetchall()
        X = np.stack([np.asarray(r[3], dtype=np.float32) for r in rows])
        mean = X.mean(axis=0)
        _, _, vt = np.linalg.svd(X - mean, full_matrices=False)
        comps = vt[:2]
        pts = (X - mean) @ comps.T
        _space = (mean, comps, [{"id": r[0], "titre": r[1], "genre": r[2], "x": float(p[0]), "y": float(p[1])} for r, p in zip(rows, pts)])
    mean, comps, points = _space
    out = {"points": points}
    if q:
        vec = embed_one(q)
        p = (vec - mean) @ comps.T
        out["query"] = {"x": float(p[0]), "y": float(p[1])}
        out["neighbours"] = [r["id"] for r in search_pgvector(vec, 5, "", 0)["rows"]]
    return out


@app.post("/api/reload")
def reload():
    global _space
    _space = None
    return store.load_corpus()


@app.get("/api/bench")
def bench():
    f = RESULTS / "bench.json"
    if not f.exists():
        return {"available": False}
    return {"available": True, **json.loads(f.read_text(encoding="utf-8"))}


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
