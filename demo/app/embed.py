"""Calcul des embeddings, en local, sans clé d'API.

Modèle par défaut : paraphrase-multilingual-MiniLM-L12-v2 (384 dimensions, ~50 langues dont le français).
Les vecteurs sont normalisés (norme 1) : distance cosinus = 1 - produit scalaire.
"""
import os
import threading

import numpy as np

MODEL_NAME = os.getenv("EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
DIM = 384

_model = None
_lock = threading.Lock()


def _get_model():
    global _model
    with _lock:
        if _model is None:
            from sentence_transformers import SentenceTransformer

            _model = SentenceTransformer(MODEL_NAME, device="cpu")
        return _model


def embed(texts: list[str]) -> np.ndarray:
    vecs = _get_model().encode(texts, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
    return vecs.astype(np.float32)


def embed_one(text: str) -> np.ndarray:
    return embed([text])[0]


def ch_literal(vec: np.ndarray) -> str:
    """Vecteur -> littéral ClickHouse typé, utilisable comme constante dans un WITH."""
    return "CAST([" + ",".join(f"{x:.7g}" for x in vec) + "] AS Array(Float32))"


def short_literal(vec: np.ndarray, n: int = 3) -> str:
    """Version abrégée pour l'affichage du SQL dans l'interface."""
    head = ", ".join(f"{x:.4f}" for x in vec[:n])
    return f"[{head}, … {len(vec) - n} autres]"
