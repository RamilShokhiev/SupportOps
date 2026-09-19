"""Small-corpus hybrid retrieval. Callers must supply organization-scoped documents.

Demo vectors are reproducible feature hashes with a small, explicit EN/RU/TR
glossary. They are not a trained multilingual embedding model. In OpenAI mode
both indexing and querying use the configured embedding model and dimensions.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from typing import Any

import httpx

from .observability import record_embedding_usage


class EmbeddingError(RuntimeError):
    """A safe provider error; never embeds response bodies or credentials."""


_STOP = set("the a an and or in on at to from of for is are was were with after before please our we i it this that retailbridge version v ошибка код после для при это на до и в с из по sürüm sonra için ve bir ile lütfen".split())
_GLOSSARY = {
    "оплат": "payment", "платеж": "payment", "платёж": "payment", "ödeme": "payment",
    "касс": "checkout", "kasa": "checkout", "магазин": "store", "mağaza": "store",
    "обнов": "upgrade", "güncelle": "upgrade", "yükselt": "upgrade",
    "синхрон": "sync", "senkron": "sync", "остат": "inventory", "stok": "inventory",
    "токен": "token", "jeton": "token", "коннектор": "connector", "bağlayıcı": "connector",
    "истек": "expired", "истёк": "expired", "süresi": "expired", "чек": "receipt", "fiş": "receipt",
    "принтер": "printer", "yazıcı": "printer", "отчёт": "report", "отчет": "report", "rapor": "report",
    "выгруз": "export", "экспорт": "export", "dışa": "export", "csv": "export",
    "роль": "role", "рол": "role", "rol": "role", "доступ": "access", "erişim": "access",
    "периодич": "recurrent", "повтор": "recurrent", "aralıklı": "recurrent", "tekrar": "recurrent",
    "план": "plan", "plan": "plan", "разреш": "permission", "yetki": "permission",
}


def tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).lower().replace("\u0307", "")
    result = []
    for token in re.findall(r"[a-zа-яёçğıöşü0-9]+(?:-[0-9]+)?", normalized):
        if token in _STOP or token.isdigit() or len(token) < 2:
            continue
        concept = next((value for prefix, value in _GLOSSARY.items() if token.startswith(prefix)), token)
        if concept.endswith("s") and len(concept) > 4:
            concept = concept[:-1]
        result.append(concept)
    return result


def version_tuple(value: Any) -> tuple[int, ...] | None:
    if value is None:
        return None
    match = re.fullmatch(r"v?(\d+)(?:\.(\d+))(?:\.(\d+))?(?:\.(\d+))?", str(value).strip(), re.I)
    return tuple(int(part or 0) for part in match.groups()) if match else None


def embed_texts(texts: list[str], settings: Any) -> list[list[float]]:
    if not texts:
        return []
    dimensions = int(getattr(settings, "embedding_dimensions", 256))
    if dimensions < 8:
        raise EmbeddingError("Embedding dimensions must be at least 8.")
    provider = getattr(settings, "embedding_provider", "demo")
    if provider == "demo":
        vectors = []
        for text in texts:
            vector = [0.0] * dimensions
            for token in tokens(text):
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                index = int.from_bytes(digest[:4], "big") % dimensions
                vector[index] += 1.0 if digest[4] % 2 else -1.0
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            vectors.append([value / norm for value in vector])
        return vectors
    if provider != "openai":
        raise EmbeddingError("Unsupported embedding provider.")
    api_key = getattr(settings, "openai_api_key", None)
    if not api_key:
        raise EmbeddingError("OpenAI embedding mode requires OPENAI_API_KEY.")
    recorded = False
    try:
        response = httpx.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": getattr(settings, "embedding_model", "text-embedding-3-small"), "input": texts,
                  "dimensions": dimensions, "encoding_format": "float"},
            timeout=float(getattr(settings, "llm_timeout_seconds", 30.0)),
        )
        response.raise_for_status()
        payload = response.json()
        record_embedding_usage(payload.get("usage"), settings)
        recorded = True
        rows = sorted(payload["data"], key=lambda row: row["index"])
        vectors = [row["embedding"] for row in rows]
        if [row["index"] for row in rows] != list(range(len(texts))):
            raise ValueError("Missing or duplicated embedding indexes")
        if any(len(vector) != dimensions or any(not isinstance(x, (int, float)) or not math.isfinite(x) for x in vector) for vector in vectors):
            raise ValueError("Invalid embedding shape")
        return vectors
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        if not recorded:
            record_embedding_usage(None, settings)
        raise EmbeddingError(f"Embedding request failed ({type(exc).__name__}); indexing/query was not completed.") from None


def cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    denominator = math.sqrt(sum(x * x for x in left) * sum(x * x for x in right))
    return sum(a * b for a, b in zip(left, right)) / denominator if denominator else 0.0


def applicable(document: dict, fields: dict) -> bool:
    if document.get("status", "ready") != "ready":
        return False
    product = fields.get("product")
    if not product or str(document.get("product", "")).casefold() != str(product).casefold():
        return False
    requested = version_tuple(fields.get("version"))
    if requested is None:
        return False
    minimum, maximum = document.get("min_version"), document.get("max_version")
    if minimum and (version_tuple(minimum) is None or requested < version_tuple(minimum)):
        return False
    if maximum and (version_tuple(maximum) is None or requested > version_tuple(maximum)):
        return False
    return True


def retrieve(query: str, documents: list[dict], fields: dict, settings: Any, limit: int = 5) -> list[dict]:
    """Filter applicability before lexical/cosine reciprocal-rank fusion.

The prototype scores the scoped collection in Python. PostgreSQL stores pgvector
embeddings; a larger installation should push scoring into SQL without changing
the authorization and applicability filters. A score is an RRF score, not a
probability or a calibrated confidence estimate.
    """
    latest = {}
    for document in documents:
        if not applicable(document, fields):
            continue
        key = document.get("document_key", str(document["id"]))
        current = latest.get(key)
        if current is None or int(document.get("version", 1)) > int(current.get("version", 1)):
            latest[key] = document
    eligible = list(latest.values())
    code = str(fields.get("error_code") or "").upper()
    if code:
        code_pattern = re.compile(r"(?<![A-Z0-9])" + re.escape(code) + r"(?![A-Z0-9])", re.I)
        eligible = [doc for doc in eligible if code_pattern.search(doc.get("title", "") + " " + doc.get("content", ""))]
    if not eligible or limit < 1:
        return []
    query_tokens = set(tokens(query))
    query_vector = embed_texts([query], settings)[0]
    missing_embeddings = [doc for doc in eligible if not doc.get("embedding")]
    if missing_embeddings:
        embedded = embed_texts([doc.get("title", "") + "\n" + doc.get("content", "") for doc in missing_embeddings], settings)
        generated = {str(doc["id"]): vector for doc, vector in zip(missing_embeddings, embedded)}
    else:
        generated = {}
    lexical, semantic, candidates = {}, {}, {}
    for document in eligible:
        identifier = str(document["id"])
        content = document.get("content", "")
        document_tokens = set(tokens(document.get("title", "") + " " + content))
        overlap = len(query_tokens & document_tokens)
        vector = document.get("embedding") or generated[identifier]
        similarity = cosine(query_vector, vector)
        # A collision in feature hashing alone must not return unrelated text.
        relevant = bool(code) or overlap > 0 or (getattr(settings, "embedding_provider", "demo") == "openai" and similarity >= 0.3)
        if not relevant:
            continue
        lexical[identifier] = (10.0 if code else 0.0) + overlap / max(1, len(query_tokens))
        semantic[identifier] = similarity
        candidates[identifier] = document
    lexical_order = sorted(lexical, key=lambda key: (-lexical[key], key))
    semantic_order = sorted(semantic, key=lambda key: (-semantic[key], key))
    fused = {identifier: 0.0 for identifier in candidates}
    for ordered in (lexical_order, semantic_order):
        for rank, identifier in enumerate(ordered, start=1):
            fused[identifier] += 1.0 / (60 + rank)
    results = []
    for identifier in sorted(fused, key=lambda key: (-fused[key], key))[:limit]:
        document = candidates[identifier]
        content = document.get("content", "")
        # Keep a contiguous, exact source excerpt; no generated paraphrase is evidence.
        start = max(0, content.upper().find(code) - 120) if code else 0
        excerpt = content[start:start + 1400]
        results.append({
            "id": document["id"], "document_key": document.get("document_key", identifier),
            "title": document.get("title", "Untitled"), "version": document.get("version", 1),
            "product": document.get("product"), "min_version": document.get("min_version"),
            "max_version": document.get("max_version"), "excerpt": excerpt,
            "score": round(fused[identifier], 8), "lexical_score": round(lexical[identifier], 5),
            "semantic_score": round(semantic[identifier], 5), "conflict_group": document.get("conflict_group"),
            "retrieval_method": "lexical+cosine RRF", "url": f"/api/documents/{document['id']}",
        })
    return results
