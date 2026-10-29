**Jeu de données** : 100 000 vecteurs × 384 dimensions  200 requêtes  k = 10 — HNSW M = 16  ef_construction = 64  float32

| Mesure | PostgreSQL + pgvector | ClickHouse |
|---|---:|---:|
| Ingestion | 1.73 s | 5.35 s |
| Recherche exacte p50 | 27.49 ms | 78.87 ms |
| Construction index HNSW | 9.66 s | 5.89 s |
| HNSW ef=40 : p50 / p95 | 2.75 / 4.72 ms | 15.33 / 19.94 ms |
| HNSW ef=40 : recall@10 | 0.894 | 0.956 |
| HNSW ef=100 : p50 / p95 | 3.44 / 6.08 ms | 14.66 / 19.88 ms |
| HNSW ef=100 : recall@10 | 0.966 | 0.995 |
| HNSW ef=200 : p50 / p95 | 4.13 / 7.29 ms | 14.12 / 20.49 ms |
| HNSW ef=200 : recall@10 | 0.986 | 0.997 |
| Taille table / index | 156.6 / 195.3 Mo | 147.6 / 155.1 Mo |
