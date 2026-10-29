FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/models \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# PyTorch en version CPU uniquement (~200 Mo au lieu de plusieurs Go avec CUDA)
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch==2.5.1

COPY requirements.txt .
RUN pip install -r requirements.txt

# Le modèle d'embeddings est téléchargé PENDANT LE BUILD : le jour J, la démo tourne sans internet.
ARG EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
ENV EMBEDDING_MODEL=${EMBEDDING_MODEL}
RUN python -c "import os; from sentence_transformers import SentenceTransformer; SentenceTransformer(os.environ['EMBEDDING_MODEL'])"
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1

COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
