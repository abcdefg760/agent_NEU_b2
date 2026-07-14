from __future__ import annotations

import os
import re

from pathlib import Path
from typing import Any, Literal

from skills import resolve_data_path
from skills.errors import SkillError


SearchMode = Literal["keyword", "semantic", "hybrid"]
_SEMANTIC_MODELS: dict[str, Any] = {}


def _snippet(text: str, terms: list[str], radius: int = 60) -> str:
    lowered = text.casefold()
    positions = [lowered.find(term.casefold()) for term in terms]
    positions = [position for position in positions if position >= 0]
    start = max(0, (min(positions) if positions else 0) - radius)
    end = min(len(text), start + radius * 2)
    prefix = "..." if start else ""
    suffix = "..." if end < len(text) else ""
    return prefix + text[start:end].replace("\n", " ").strip() + suffix


def _load_semantic_model(model_path: str | None) -> Any:
    if not isinstance(model_path, str) or not model_path.strip():
        raise SkillError(
            "SEMANTIC_MODEL_UNAVAILABLE",
            "semantic search requires settings.semantic_search.model_path",
            category="configuration",
        )
    resolved = Path(model_path).expanduser().resolve()
    if not resolved.is_dir():
        raise SkillError(
            "SEMANTIC_MODEL_UNAVAILABLE",
            f"local semantic model directory does not exist: {resolved}",
            category="configuration",
        )
    cache_key = str(resolved)
    if cache_key in _SEMANTIC_MODELS:
        return _SEMANTIC_MODELS[cache_key]
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise SkillError(
            "SEMANTIC_DEPENDENCY_UNAVAILABLE",
            "sentence-transformers is not installed",
            category="dependency",
        ) from exc

    previous_hf = os.environ.get("HF_HUB_OFFLINE")
    previous_transformers = os.environ.get("TRANSFORMERS_OFFLINE")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    try:
        model = SentenceTransformer(str(resolved), local_files_only=True)
    except Exception as exc:
        raise SkillError(
            "SEMANTIC_MODEL_LOAD_FAILED",
            f"failed to load local semantic model: {exc}",
            category="dependency",
        ) from exc
    finally:
        if previous_hf is None:
            os.environ.pop("HF_HUB_OFFLINE", None)
        else:
            os.environ["HF_HUB_OFFLINE"] = previous_hf
        if previous_transformers is None:
            os.environ.pop("TRANSFORMERS_OFFLINE", None)
        else:
            os.environ["TRANSFORMERS_OFFLINE"] = previous_transformers
    _SEMANTIC_MODELS[cache_key] = model
    return model


def _semantic_scores(query: str, texts: list[str], model_path: str | None) -> list[float]:
    model = _load_semantic_model(model_path)
    try:
        encode_query = getattr(model, "encode_query", None)
        encode_document = getattr(model, "encode_document", None)
        if callable(encode_query) and callable(encode_document):
            query_embedding = encode_query(
                query,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            document_embeddings = encode_document(
                texts,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        else:
            embeddings = model.encode(
                [query, *texts],
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            query_embedding = embeddings[0]
            document_embeddings = embeddings[1:]
        return [
            max(-1.0, min(1.0, float(vector @ query_embedding)))
            for vector in document_embeddings
        ]
    except Exception as exc:
        if isinstance(exc, SkillError):
            raise
        raise SkillError(
            "SEMANTIC_INFERENCE_FAILED",
            f"semantic embedding inference failed: {exc}",
            category="execution",
        ) from exc


def _normalize_scores(scores: list[float]) -> list[float]:
    if not scores:
        return []
    minimum = min(scores)
    maximum = max(scores)
    if maximum == minimum:
        return [1.0 if maximum > 0.0 else 0.0 for _ in scores]
    scale = maximum - minimum
    return [(score - minimum) / scale for score in scores]


def local_file_search(
    query: str,
    root_dir: str = "docs",
    file_types: list[str] | None = None,
    top_k: int = 5,
    mode: SearchMode = "keyword",
    semantic_weight: float = 0.65,
    *,
    data_root: str | None = None,
    embedding_model_path: str | None = None,
    max_files: int = 500,
    max_file_bytes: int = 1_000_000,
) -> dict[str, Any]:
    """Search bounded local files with keyword, semantic, or hybrid ranking.

    Args:
        query: Non-empty text used to rank matching files.
        root_dir: Search directory relative to the configured data root.
        file_types: Optional list containing txt or md extensions.
        top_k: Maximum number of ranked results returned.
        mode: Ranking mode: keyword, semantic, or hybrid.
        semantic_weight: Semantic score weight used only by hybrid ranking.
        data_root: Framework-injected root that contains searchable files.
        embedding_model_path: Framework-injected local SentenceTransformer path.
        max_files: Framework-injected maximum number of candidate files.
        max_file_bytes: Framework-injected maximum size of each candidate file.

    Returns:
        A mapping containing the selected mode and ranked score components.

    Raises:
        SkillError: If arguments, paths, file types, or resource limits are invalid.
    """
    if not isinstance(query, str) or not query.strip():
        raise SkillError("INVALID_ARGUMENT", "query must be a non-empty string", category="validation")
    if len(query) > 500:
        raise SkillError(
            "INPUT_LIMIT_EXCEEDED",
            "query must not exceed 500 characters",
            category="limit",
        )
    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k <= 0:
        raise SkillError("INVALID_ARGUMENT", "top_k must be a positive integer", category="validation")
    if top_k > 100:
        raise SkillError("INPUT_LIMIT_EXCEEDED", "top_k must not exceed 100", category="limit")
    if mode not in {"keyword", "semantic", "hybrid"}:
        raise SkillError(
            "INVALID_SEARCH_MODE",
            "mode must be keyword, semantic, or hybrid",
            category="validation",
        )
    if isinstance(semantic_weight, bool) or not isinstance(semantic_weight, (int, float)):
        raise SkillError(
            "INVALID_ARGUMENT",
            "semantic_weight must be a number",
            category="validation",
        )
    semantic_weight = float(semantic_weight)
    if not 0.0 <= semantic_weight <= 1.0:
        raise SkillError(
            "INVALID_ARGUMENT",
            "semantic_weight must be between 0 and 1",
            category="validation",
        )
    if not isinstance(max_files, int) or isinstance(max_files, bool) or max_files <= 0:
        raise SkillError("CONFIGURATION_ERROR", "max_files must be positive", category="configuration")
    if not isinstance(max_file_bytes, int) or isinstance(max_file_bytes, bool) or max_file_bytes <= 0:
        raise SkillError("CONFIGURATION_ERROR", "max_file_bytes must be positive", category="configuration")
    search_root, data_root_path = resolve_data_path(root_dir, data_root)
    if not search_root.is_dir():
        raise SkillError(
            "DIRECTORY_NOT_FOUND",
            f"search directory not found: {root_dir}",
            category="not_found",
        )
    extensions = file_types or ["txt", "md"]
    if not isinstance(extensions, list) or not all(isinstance(item, str) and item for item in extensions):
        raise SkillError(
            "INVALID_ARGUMENT",
            "file_types must be a list of non-empty strings",
            category="validation",
        )
    normalized_extensions = {f".{item.lower().lstrip('.')}" for item in extensions}
    if not normalized_extensions.issubset({".txt", ".md"}):
        raise SkillError(
            "UNSUPPORTED_FORMAT",
            "local_file_search only supports txt and md",
            category="validation",
        )
    terms = [term for term in re.split(r"\s+", query.strip()) if term]
    candidates: list[dict[str, Any]] = []
    candidate_count = 0
    for path in sorted(search_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in normalized_extensions:
            continue
        candidate_count += 1
        if candidate_count > max_files:
            raise SkillError(
                "SEARCH_LIMIT_EXCEEDED",
                f"search contains more than {max_files} candidate files",
                category="limit",
            )
        if path.stat().st_size > max_file_bytes:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        lowered = text.casefold()
        keyword_count = sum(lowered.count(term.casefold()) for term in terms)
        candidates.append(
            {
                "path": path.relative_to(data_root_path).as_posix(),
                "text": text,
                "keyword_count": keyword_count,
            }
        )

    max_keyword = max((item["keyword_count"] for item in candidates), default=0)
    semantic_scores = (
        _semantic_scores(query, [item["text"] for item in candidates], embedding_model_path)
        if mode in {"semantic", "hybrid"} and candidates
        else [None] * len(candidates)
    )
    normalized_semantic_scores = (
        _normalize_scores([float(score) for score in semantic_scores])
        if mode in {"semantic", "hybrid"}
        else [None] * len(candidates)
    )
    results = []
    for candidate, semantic_score, normalized_semantic_score in zip(
        candidates, semantic_scores, normalized_semantic_scores
    ):
        keyword_score = candidate["keyword_count"] / max_keyword if max_keyword else 0.0
        if mode == "keyword":
            if not candidate["keyword_count"]:
                continue
            score: int | float = candidate["keyword_count"]
        elif mode == "semantic":
            score = float(semantic_score)
        else:
            score = (
                (1.0 - semantic_weight) * keyword_score
                + semantic_weight * float(normalized_semantic_score)
            )
        results.append(
            {
                "path": candidate["path"],
                "score": round(score, 6) if isinstance(score, float) else score,
                "keyword_score": round(keyword_score, 6),
                "semantic_score": round(float(semantic_score), 6) if semantic_score is not None else None,
                "normalized_semantic_score": round(float(normalized_semantic_score), 6)
                if normalized_semantic_score is not None
                else None,
                "keyword_count": candidate["keyword_count"],
                "snippet": _snippet(candidate["text"], terms),
            }
        )
    results.sort(key=lambda item: (-item["score"], item["path"]))
    return {"mode": mode, "results": results[:top_k]}
