from visualization import visualize_data, auto_visualize_data
import argparse
import os
import gc
import re
import torch
import faiss
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime
import code
import time
import random
import builtins
import ast
import pathlib
import re
import yaml
import json

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from langchain.schema import Document

from config import CACHE_DIR, MODEL_PATH, OUTPUT_DIR, EMBEDDING_MODEL_PATH, DB_CONNECTION_STRING, LLM_SOURCE, LlmSource
from dataset_registry import (
    SCOPE_BUMP,
    SCOPE_CROSS,
    SCOPE_SINC,
    create_tables_for_scope,
    get_dataset_config,
    get_dataset_scopes,
    load_data_for_scope,
    normalize_scope_name,
)
from embedding import load_embedding_model, build_knowledge_index, get_relevant_chunks_by_type, _restore_full_documents
from kb_registry import build_scoped_kb_documents, get_scope_sql_guideline_doc
from llm import call_llm, verify_llm_api_available
from generate_schema import generate_schema_description
from knowledge_base_context import sql_guideline
from sql_scope_validation import validate_sql_for_scope as _validate_sql_for_scope

# Number of independent SQL generations used for majority voting
MAJORITY_VOTE_RUNS = 1
SQL_GENERATION_RETRIES = 1
AUTHORITATIVE_SINGLE_TABLE_CONFIDENCE = 0.65
AUTHORITATIVE_MULTI_TABLE_CONFIDENCE = 0.6
AUTHORITATIVE_SCORE_GAP = 1.75
AMBIGUITY_TRACE_ENV_VAR = "SINC_AMBIGUITY_TRACE_PATH"
ABLATION_ENV_VAR = "SINC_ABLATION"
ABLATION_NO_SCHEMA_KEYWORDS = "no_schema_keywords"
ABLATION_NO_ALL_SCHEMA_KEYWORDS = "no_all_schema_keywords"
ABLATION_NO_KB = "no_kb"
ABLATION_NO_AMBIGUITY_RESOLVER = "no_ambiguity_resolver"
ABLATION_NO_MAJORITY_VOTING = "no_majority_voting"


def get_active_ablations():
    raw_value = os.environ.get(ABLATION_ENV_VAR, "")
    return {
        item.strip().lower().replace("-", "_")
        for item in raw_value.split(",")
        if item.strip() and item.strip().lower() not in {"baseline", "none"}
    }


def is_ablation_enabled(name):
    return name in get_active_ablations()

# If we use local llm instead of public APIs
if LLM_SOURCE == LlmSource.LOCAL:
    # Check GPU & FAISS availability
    print("CUDA available:", torch.cuda.is_available())
    device_map = "auto" if torch.cuda.is_available() else "cpu"
    torch_dtype = torch.float16 if torch.cuda.is_available() else torch.bfloat16

    if faiss.get_num_gpus() > 0:
        print("c GPU is available!")
    else:
        print("FAISS GPU is NOT available.")

    # Free up CUDA memory
    torch.cuda.empty_cache()
    gc.collect()

    # Load local LLM pipeline
    pipeline_llm = call_llm()
    print("Model loaded successfully on",
          "GPU" if torch.cuda.is_available() else "CPU")
else:
    verify_llm_api_available()

# Load embedding model
embedding_model = load_embedding_model()

ALL_KNOWLEDGE_DOCS = []


def prompt_for_session_scope():
    """Prompt once for the default dataset scope used in the session."""
    print("\nSelect dataset scope for this session:")
    scopes = get_dataset_scopes()
    for index, scope in enumerate(scopes, 1):
        print(f"{index}. {scope}")

    while True:
        raw_choice = input(f"Choose 1-{len(scopes)} (press Enter for 1): ").strip() or "1"
        try:
            selected_scope = normalize_scope_name(raw_choice)
        except ValueError:
            print(f"Invalid scope selection. Please choose 1-{len(scopes)}.")
            continue

        return selected_scope


def _doc_matches_scope(doc, scope):
    """Return True when a schema/KB document belongs to the active scope."""
    metadata = getattr(doc, "metadata", {}) or {}
    table_name = str(metadata.get("table", "")).strip().lower()
    dataset_scope = str(metadata.get("dataset_scope", "")).strip()

    if scope == SCOPE_BUMP:
        return table_name.startswith("bump_") or dataset_scope in {"global", SCOPE_BUMP}
    if scope == SCOPE_SINC:
        return table_name.startswith("sinc_") or dataset_scope in {"global", SCOPE_SINC}
    if scope == SCOPE_CROSS:
        return table_name.startswith("wearable_") or dataset_scope in {"global", SCOPE_CROSS}
    try:
        plugin = get_dataset_config(scope)
    except ValueError:
        return False
    return table_name.startswith(plugin.table_prefix) or dataset_scope in {"global", plugin.name}


def filter_schema_docs_for_scope(schema_docs, scope):
    """Filter generated schema docs down to the active dataset scope."""
    filtered_docs = [doc for doc in schema_docs if _doc_matches_scope(doc, scope)]
    return filtered_docs or schema_docs


def filter_kb_docs_for_scope(kb_docs, scope):
    """Filter KB docs down to the active dataset scope."""
    filtered_docs = [doc for doc in kb_docs if _doc_matches_scope(doc, scope)]
    return filtered_docs or kb_docs


def initialize_retrieval_context(engine, session_scope):
    """Build scoped schema/KB documents and retrievers for the active dataset."""
    global ALL_SCHEMA_DOCS, ALL_KNOWLEDGE_DOCS

    all_schema_docs = generate_schema_description(engine)
    scoped_schema_docs = filter_schema_docs_for_scope(all_schema_docs, session_scope)
    all_kb_docs = build_scoped_kb_documents(session_scope)
    scoped_kb_docs = filter_kb_docs_for_scope(all_kb_docs, session_scope)

    ALL_SCHEMA_DOCS = scoped_schema_docs
    ALL_KNOWLEDGE_DOCS = scoped_kb_docs

    schema_retriever, schema_vector_store = build_knowledge_index(
        ALL_SCHEMA_DOCS, embedding_model)
    kb_retriever, kb_vector_store = build_knowledge_index(
        ALL_KNOWLEDGE_DOCS, embedding_model)

    return {
        "schema_docs": ALL_SCHEMA_DOCS,
        "kb_docs": ALL_KNOWLEDGE_DOCS,
        "schema_retriever": schema_retriever,
        "schema_vector_store": schema_vector_store,
        "kb_retriever": kb_retriever,
        "kb_vector_store": kb_vector_store,
    }


def _scope_specific_join_rule(scope):
    if scope == SCOPE_BUMP:
        return "Join rule: when combining BUMP participant-level tables, join on record_id unless the schema explicitly requires otherwise."
    if scope == SCOPE_SINC:
        return "Join rule: when combining SinC participant-level tables, join on user_id unless the schema explicitly requires otherwise."
    if scope == SCOPE_CROSS:
        return "Join rule: in cross-dataset mode, use the harmonized wearable_* views and compare by dataset or participant_key."
    try:
        plugin = get_dataset_config(scope)
    except ValueError:
        return "Join rule: use the participant identifier defined by the active schema."
    return (
        f"Join rule: when combining {plugin.name} participant-level tables, "
        f"join on {plugin.entity_key} unless the schema explicitly requires otherwise."
    )


def validate_sql_for_scope(sql_query, session_scope=None):
    """Reject mutating SQL and table references outside the selected scope."""
    return _validate_sql_for_scope(sql_query, session_scope=session_scope)


def _extract_json_array(text):
    """
    Extract and parse the first JSON array found in text.
    Returns None if parsing fails.
    """
    if not text:
        return None

    fenced_match = re.search(
        r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", text, re.IGNORECASE)
    if fenced_match:
        try:
            return json.loads(fenced_match.group(1))
        except Exception:
            pass

    array_match = re.search(r"(\[[\s\S]*\])", text)
    if array_match:
        try:
            return json.loads(array_match.group(1))
        except Exception:
            return None
    return None


def _extract_table_name_from_schema_doc(doc):
    """Return the table name declared inside a schema document."""
    for line in doc.page_content.splitlines():
        line = line.strip()
        if line.lower().startswith("table:"):
            return line.split(":", 1)[1].strip()
    return doc.metadata.get("table", "")


def _get_schema_doc_for_table(table_name):
    """Return the scoped schema document for an exact or scope-normalized table name."""
    if not table_name:
        return None

    normalized_table_name = table_name.strip().lower()
    normalized_base_name = _base_table_name(normalized_table_name)
    base_match = None

    for doc in ALL_SCHEMA_DOCS:
        candidate_name = doc.metadata.get("table") or _extract_table_name_from_schema_doc(doc)
        candidate_name = candidate_name.strip()
        if not candidate_name:
            continue

        normalized_candidate_name = candidate_name.lower()
        if normalized_candidate_name == normalized_table_name:
            return doc

        if _base_table_name(normalized_candidate_name) == normalized_base_name and base_match is None:
            base_match = doc

    return base_match


def _base_table_name(table_name):
    if not table_name:
        return ""
    if table_name.startswith("bump_"):
        return table_name[len("bump_"):]
    if table_name.startswith("sinc_"):
        return table_name[len("sinc_"):]
    return table_name


def _extract_schema_description(doc):
    """Return a readable description for a schema document."""
    for line in doc.page_content.splitlines():
        line = line.strip()
        if line.lower().startswith("description:"):
            return line.split(":", 1)[1].strip()
    table_name = _extract_table_name_from_schema_doc(doc)
    return f"Contains data related to {table_name.replace('_', ' ')}."


def _score_schema_doc_for_query(query_lower, doc):
    """Return weighted schema keyword evidence for a query."""
    keywords = [kw.lower() for kw in doc.metadata.get(
        "keywords", []) if isinstance(kw, str)]
    table_name = (doc.metadata.get("table")
                  or _extract_table_name_from_schema_doc(doc)).lower()
    keyword_hits = 0
    keyword_score = 0.0

    normalized_query = f" {query_lower} "
    normalized_table_name = table_name.replace("_", " ")

    if table_name and (table_name in query_lower or normalized_table_name in query_lower):
        keyword_hits += 1
        keyword_score += 6.0

    for kw in keywords:
        if not kw:
            continue
        normalized_kw = kw.strip().lower()
        if not normalized_kw:
            continue
        if normalized_kw in normalized_query:
            keyword_hits += 1
            # Longer phrases are treated as more specific and get more weight.
            if " " in normalized_kw:
                keyword_score += 3.0
            elif len(normalized_kw) >= 8:
                keyword_score += 2.25
            else:
                keyword_score += 1.5

    return keyword_hits, keyword_score


def _normalize_text_for_match(text):
    """Normalize text into a whitespace-delimited form for substring checks."""
    normalized = re.sub(r"[^a-z0-9]+", " ", str(text or "").lower())
    return f" {normalized.strip()} "


def _query_explicitly_mentions_table(natural_query, table_name):
    """Return True when the query explicitly names the table or its normalized alias."""
    if not natural_query or not table_name:
        return False

    normalized_query = _normalize_text_for_match(natural_query)
    candidate_names = {
        table_name.lower(),
        _base_table_name(table_name).lower(),
        table_name.lower().replace("_", " "),
        _base_table_name(table_name).lower().replace("_", " "),
    }
    for candidate_name in candidate_names:
        normalized_candidate = _normalize_text_for_match(candidate_name)
        if normalized_candidate.strip() and normalized_candidate in normalized_query:
            return True
    return False


def _top_score_gap(candidate_tables):
    """Return the score gap between the top two ranked candidates."""
    if len(candidate_tables) < 2:
        return float("inf")
    return float(candidate_tables[0]["score"] - candidate_tables[1]["score"])


def _clamp_confidence(value):
    """Clamp a numeric confidence score to the 0-1 interval."""
    return max(0.0, min(1.0, float(value or 0.0)))


def _candidate_score_map(candidate_tables):
    """Return table -> candidate score for ambiguity candidate tables."""
    return {
        candidate["table"]: float(candidate.get("score", 0.0) or 0.0)
        for candidate in candidate_tables
    }


def _compute_authoritative_confidence(llm_scope, candidate_tables, natural_query):
    """
    Compute a deterministic confidence score from retrieval evidence and LLM/table agreement.

    The LLM still chooses the resolution type and tables; this score replaces the
    LLM's self-reported confidence for threshold gating.
    """
    if not llm_scope:
        return 0.0

    selected_tables = llm_scope.get("selected_tables", [])
    if not selected_tables or not candidate_tables:
        return 0.0

    score_by_table = _candidate_score_map(candidate_tables)
    top_candidate = candidate_tables[0]
    top_score = float(top_candidate.get("score", 0.0) or 0.0)
    top_score_confidence = _clamp_confidence(top_score / 10.0)
    gap_confidence = _clamp_confidence(max(0.0, _top_score_gap(candidate_tables)) / 4.0)

    if llm_scope.get("resolution_type") == "single_table":
        selected_table = selected_tables[0]
        if _query_explicitly_mentions_table(natural_query, selected_table):
            return 1.0

        llm_retriever_agreement = 1.0 if top_candidate["table"] == selected_table else 0.0
        return _clamp_confidence(
            (0.45 * top_score_confidence)
            + (0.35 * gap_confidence)
            + (0.20 * llm_retriever_agreement)
        )

    if llm_scope.get("resolution_type") == "multi_table":
        selected_scores = [
            score_by_table.get(table_name, 0.0)
            for table_name in selected_tables
        ]
        selected_score_confidence = _clamp_confidence(
            (sum(selected_scores) / len(selected_scores)) / 10.0
        )
        candidate_names = {candidate["table"] for candidate in candidate_tables[:6]}
        candidate_coverage = (
            sum(1 for table_name in selected_tables if table_name in candidate_names)
            / len(selected_tables)
        )
        multi_table_intent = 1.0 if _query_explicitly_requests_multi_table(natural_query) else 0.0

        return _clamp_confidence(
            (0.45 * selected_score_confidence)
            + (0.30 * candidate_coverage)
            + (0.25 * multi_table_intent)
        )

    return 0.0


def _attach_computed_confidence(llm_scope, candidate_tables, natural_query):
    """Return an LLM scope copy whose confidence is the computed confidence."""
    if not llm_scope:
        return None

    computed_confidence = _compute_authoritative_confidence(
        llm_scope,
        candidate_tables,
        natural_query,
    )
    scoped = dict(llm_scope)
    scoped["llm_reported_confidence"] = float(scoped.get("confidence", 0.0) or 0.0)
    scoped["computed_confidence"] = computed_confidence
    scoped["confidence"] = computed_confidence
    return scoped


def _print_ambiguity_confidence(llm_scope, candidate_tables):
    """Print a concise ambiguity resolver confidence summary."""
    if not llm_scope:
        print("Ambiguity confidence: unavailable (no LLM scope)")
        return

    selected_tables = llm_scope.get("selected_tables", [])
    selected = ", ".join(selected_tables) if selected_tables else "none"
    retriever_top = candidate_tables[0]["table"] if candidate_tables else "none"
    print(
        "Ambiguity confidence: "
        f"{float(llm_scope.get('confidence', 0.0) or 0.0):.2f} computed "
        f"(LLM reported {float(llm_scope.get('llm_reported_confidence', 0.0) or 0.0):.2f}; "
        f"type={llm_scope.get('resolution_type', 'unknown')}; "
        f"llm_selected={selected}; "
        f"retriever_top={retriever_top}; "
        f"gap={_top_score_gap(candidate_tables):.2f})"
    )


def _write_ambiguity_trace(natural_query, session_scope, llm_scope, candidate_tables, decision):
    """Optionally append ambiguity resolver diagnostics to a JSONL trace file."""
    trace_path = os.environ.get(AMBIGUITY_TRACE_ENV_VAR, "").strip()
    if not trace_path:
        return

    top_candidates = []
    for candidate in candidate_tables[:6]:
        top_candidates.append({
            "table": candidate.get("table"),
            "score": float(candidate.get("score", 0.0) or 0.0),
            "keyword_hits": int(candidate.get("keyword_hits", 0) or 0),
        })

    record = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "query": str(natural_query or ""),
        "session_scope": session_scope,
        "decision": decision,
        "confidence_source": "computed_from_candidate_scores",
        "llm_scope": llm_scope,
        "top_score_gap": _top_score_gap(candidate_tables),
        "thresholds": {
            "single_table_confidence": AUTHORITATIVE_SINGLE_TABLE_CONFIDENCE,
            "multi_table_confidence": AUTHORITATIVE_MULTI_TABLE_CONFIDENCE,
            "score_gap": AUTHORITATIVE_SCORE_GAP,
        },
        "top_candidates": top_candidates,
    }

    path = pathlib.Path(trace_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _should_accept_authoritative_single_table(llm_scope, candidate_tables, natural_query):
    """Return True when a single-table choice is strong enough to skip clarification."""
    if not llm_scope or llm_scope.get("resolution_type") != "single_table":
        return False

    selected_tables = llm_scope.get("selected_tables", [])
    if len(selected_tables) != 1:
        return False

    selected_table = selected_tables[0]
    if _query_explicitly_mentions_table(natural_query, selected_table):
        return True

    confidence = float(llm_scope.get("confidence", 0.0) or 0.0)
    if confidence < AUTHORITATIVE_SINGLE_TABLE_CONFIDENCE:
        return False

    top_candidate = candidate_tables[0] if candidate_tables else None
    if top_candidate and top_candidate["table"] != selected_table:
        return False

    return _top_score_gap(candidate_tables) >= AUTHORITATIVE_SCORE_GAP


def _query_explicitly_requests_multi_table(natural_query):
    """Return True when the wording clearly asks to combine or compare datasets."""
    query = str(natural_query or "").lower()
    return any(
        phrase in query
        for phrase in (
            "compare",
            "correlate",
            "relationship between",
            "join",
            "across tables",
            "combine",
            "along with",
            "versus",
            "vs ",
        )
    )


def _should_accept_authoritative_multi_table(llm_scope, candidate_tables, natural_query):
    """Return True when a multi-table resolution is strong enough to skip clarification."""
    if not llm_scope or llm_scope.get("resolution_type") != "multi_table":
        return False

    selected_tables = llm_scope.get("selected_tables", [])
    if len(selected_tables) < 2:
        return False

    confidence = float(llm_scope.get("confidence", 0.0) or 0.0)
    if confidence >= AUTHORITATIVE_MULTI_TABLE_CONFIDENCE:
        return True

    if not _query_explicitly_requests_multi_table(natural_query):
        return False

    candidate_names = {candidate["table"] for candidate in candidate_tables[:6]}
    return all(table_name in candidate_names for table_name in selected_tables)


def _dedupe_docs(docs):
    """Deduplicate documents by id when available, otherwise by content."""
    deduped = []
    seen = set()

    for doc in docs:
        if isinstance(doc, str):
            key = ("text", doc.strip())
            normalized_doc = Document(page_content=doc, metadata={})
        else:
            doc_id = doc.metadata.get("id")
            key = ("id", doc_id) if doc_id else (
                "text", doc.page_content.strip())
            normalized_doc = doc

        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized_doc)

    return deduped


def _kb_doc_matches_selected_tables(doc, selected_tables):
    """Return True when a KB doc appears to describe one of the selected tables."""
    if not selected_tables:
        return False

    metadata = getattr(doc, "metadata", {}) or {}
    haystacks = [
        str(metadata.get("table", "")).lower(),
        str(metadata.get("id", "")).lower(),
        doc.page_content.lower(),
    ]

    for table_name in selected_tables:
        normalized_candidates = {
            table_name.lower(),
            _base_table_name(table_name).lower(),
        }
        if any(candidate and candidate in haystack for candidate in normalized_candidates for haystack in haystacks):
            return True
    return False


def _get_schema_matched_kb_docs(selected_tables, limit=2):
    """Find KB docs that directly mention the selected schema tables."""
    if not selected_tables:
        return []

    matched_docs = []
    for doc in ALL_KNOWLEDGE_DOCS:
        if _kb_doc_matches_selected_tables(doc, selected_tables):
            matched_docs.append(doc)

    return matched_docs[:limit]


def _get_semantic_schema_matched_kb_docs(query, kb_retriever, selected_tables, limit=5):
    """Retrieve schema-linked KB docs by semantic relevance within the selected table scope."""
    if not selected_tables:
        return []

    search_k = max(limit * 8, 24)
    if hasattr(kb_retriever, "vectorstore"):
        retrieved_docs = kb_retriever.vectorstore.similarity_search(
            query, k=search_k)
    else:
        original_search_kwargs = dict(
            getattr(kb_retriever, "search_kwargs", {}) or {})
        kb_retriever.search_kwargs = {**original_search_kwargs, "k": search_k}
        try:
            retrieved_docs = kb_retriever.invoke(query)
        finally:
            kb_retriever.search_kwargs = original_search_kwargs

    retrieved_docs = [
        doc for doc in retrieved_docs
        if doc.metadata.get("type") != "schema"
    ]
    retrieved_docs = _restore_full_documents(retrieved_docs)

    matched_docs = []
    seen_keys = set()
    for doc in retrieved_docs:
        if not _kb_doc_matches_selected_tables(doc, selected_tables):
            continue
        doc_id = doc.metadata.get("id")
        key = ("id", doc_id) if doc_id else ("text", doc.page_content.strip())
        if key in seen_keys:
            continue
        seen_keys.add(key)
        matched_docs.append(doc)
        if len(matched_docs) >= limit:
            break

    return matched_docs


def get_relevant_kb_chunks(query, kb_retriever, selected_tables=None):
    """Retrieve KB chunks with schema-linked priority plus semantic supplements."""
    schema_matched_docs = _get_semantic_schema_matched_kb_docs(
        query, kb_retriever, selected_tables or [], limit=5)

    kb_docs = list(schema_matched_docs)
    boost_counts = {}

    if schema_matched_docs:
        boost_counts["schema_matched"] = len(schema_matched_docs)

    deduped_docs = _dedupe_docs(kb_docs)
    return deduped_docs, {
        "base_count": len(schema_matched_docs),
        "boost_counts": boost_counts,
        "pre_dedupe_total": len(kb_docs),
        "deduped_count": len(deduped_docs),
    }


def is_frequency_availability_query(natural_query):
    """Return True for metadata-discovery questions about cadence availability."""
    query = str(natural_query or "").strip().lower()
    if not query:
        return False

    asks_availability = any(
        phrase in query
        for phrase in (
            "what data",
            "which data",
            "what tables",
            "which tables",
            "what higher-frequency",
            "which higher-frequency",
            "what high-frequency",
            "which high-frequency",
            "what is available",
            "which is available",
            "do we have",
            "available",
        )
    ) or (
        query.startswith(("what ", "which "))
        and any(
            term in query
            for term in ("frequency", "cadence", "interval", "resolution", "granularity")
        )
    )
    mentions_cadence = any(
        phrase in query
        for phrase in (
            "frequency",
            "cadence",
            "interval",
            "resolution",
            "granularity",
            "minute-level",
            "sub-daily",
            "higher-frequency",
            "higher frequency",
            "high-frequency",
            "high frequency",
        )
    ) or bool(re.search(r"\b\d+\s*-\s*minute\b", query)) or bool(re.search(r"\b\d+\s*minutes?\b", query))

    return asks_availability and mentions_cadence


def _query_mentions_survey_domain(query):
    """Return True when the user is asking about survey-like instruments."""
    query = str(query or "").strip().lower()
    return any(
        term in query
        for term in (
            "survey",
            "surveys",
            "questionnaire",
            "questionnaires",
            "ema",
            "ecological momentary assessment",
        )
    )


def _extract_frequency_threshold_minutes(natural_query):
    """Infer a maximum cadence threshold from the user query when present."""
    query = str(natural_query or "").strip().lower()
    if not query:
        return None

    if any(
        phrase in query
        for phrase in (
            "higher-frequency",
            "higher frequency",
            "high-frequency",
            "high frequency",
            "sub-daily",
        )
    ):
        return 1440 if _query_mentions_survey_domain(query) else 60

    if "hourly" in query:
        return 60

    patterns = [
        r"\bevery\s+(\d+)\s*minutes?\b",
        r"\b(\d+)\s*-\s*minute\b",
        r"\b(\d+)\s*minutes?\b",
        r"\b(\d+)\s*min\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, query)
        if match:
            try:
                return int(match.group(1))
            except (TypeError, ValueError):
                continue

    return None


def _get_frequency_catalog_docs(session_scope=None):
    """Return structured frequency metadata docs for the active scope."""
    docs = []
    for doc in ALL_KNOWLEDGE_DOCS:
        metadata = getattr(doc, "metadata", {}) or {}
        if metadata.get("type") != "frequency_catalog":
            continue
        if session_scope and not _doc_matches_scope(doc, session_scope):
            continue
        docs.append(doc)
    return docs


def _filter_frequency_docs_by_query_hints(docs, natural_query):
    """Narrow frequency docs when the query names a source or measure family."""
    query = str(natural_query or "").strip().lower()
    if not query:
        return docs

    if _query_mentions_survey_domain(query):
        return [
            doc for doc in docs
            if (getattr(doc, "metadata", {}) or {}).get("data_domain") == "survey"
        ]

    hint_terms = []
    for term in (
        "garmin",
        "oura",
        "bodyport",
        "reaction time",
        "ema",
        "stress",
        "body battery",
        "sleep",
        "respiration",
        "pulse ox",
        "spo2",
        "heart rate",
        "activity",
    ):
        if term in query:
            hint_terms.append(term)

    if not hint_terms:
        return docs

    filtered_docs = []
    for doc in docs:
        metadata = getattr(doc, "metadata", {}) or {}
        haystack = " ".join(
            str(value)
            for value in (
                metadata.get("table", ""),
                metadata.get("measure_column", ""),
                metadata.get("data_domain", ""),
                metadata.get("notes", ""),
                doc.page_content,
            )
        ).lower()
        if any(term in haystack for term in hint_terms):
            filtered_docs.append(doc)

    return filtered_docs or docs


def _filter_frequency_docs_by_threshold(docs, threshold_minutes):
    """Keep entries that meet the requested cadence threshold."""
    if threshold_minutes is None:
        return docs

    filtered_docs = []
    for doc in docs:
        metadata = getattr(doc, "metadata", {}) or {}
        frequency_minutes = metadata.get("frequency_minutes")

        if frequency_minutes is None:
            if threshold_minutes >= 60 and metadata.get("frequency_level") == "array":
                filtered_docs.append(doc)
            continue

        try:
            numeric_frequency = float(frequency_minutes)
        except (TypeError, ValueError):
            continue

        if numeric_frequency <= threshold_minutes:
            filtered_docs.append(doc)

    return filtered_docs


def _build_frequency_availability_result(natural_query, session_scope=None):
    """Answer cadence availability questions from structured frequency metadata."""
    docs = _get_frequency_catalog_docs(session_scope=session_scope)
    docs = _filter_frequency_docs_by_query_hints(docs, natural_query)
    threshold_minutes = _extract_frequency_threshold_minutes(natural_query)
    matched_docs = _filter_frequency_docs_by_threshold(docs, threshold_minutes)
    if not matched_docs and threshold_minutes is None:
        matched_docs = docs

    rows = []
    for doc in matched_docs:
        metadata = getattr(doc, "metadata", {}) or {}
        rows.append(
            {
                "table": metadata.get("table", ""),
                "measure_or_column": metadata.get("measure_column", "") or "row-level records",
                "storage_level": metadata.get("frequency_level", ""),
                "frequency": metadata.get("frequency_label", ""),
                "frequency_minutes": metadata.get("frequency_minutes"),
                "confidence": metadata.get("confidence", ""),
                "notes": metadata.get("notes", ""),
            }
        )

    result_df = pd.DataFrame(rows)
    if result_df.empty:
        empty_df = pd.DataFrame(
            columns=[
                "table",
                "measure_or_column",
                "storage_level",
                "frequency",
                "frequency_minutes",
                "confidence",
                "notes",
            ]
        )
        explanation = (
            f"Answered from the structured frequency catalog for {_scope_display_name(session_scope)}. "
            "No higher-frequency entries matched this request."
        )
        return empty_df, explanation

    result_df["frequency_minutes_sort"] = pd.to_numeric(
        result_df["frequency_minutes"], errors="coerce"
    )
    result_df["frequency_minutes_sort"] = result_df["frequency_minutes_sort"].fillna(
        np.inf
    )
    result_df = result_df.sort_values(
        by=["frequency_minutes_sort", "table", "measure_or_column"]
    ).drop(columns=["frequency_minutes_sort"])
    result_df["frequency_minutes"] = result_df["frequency_minutes"].where(
        result_df["frequency_minutes"].notna(), ""
    )
    result_df = result_df.reset_index(drop=True)

    explanation_lines = [
        f"Answered from the structured frequency catalog for {_scope_display_name(session_scope)}. No SQL was run.",
    ]
    for row in result_df.head(8).itertuples(index=False):
        if row.measure_or_column == "row-level records":
            item = f"{row.table}: {row.frequency} row-level data"
        else:
            item = f"{row.table}.{row.measure_or_column}: {row.frequency}"
        if row.confidence == "inferred":
            item += " [inferred]"
        explanation_lines.append(f"- {item}")
    if len(result_df) > 8:
        explanation_lines.append(
            f"- {len(result_df) - 8} additional matching entries are listed in the table."
        )

    return result_df, "\n".join(explanation_lines)


def _scope_display_name(session_scope):
    """Return a short user-facing name for the current query scope."""
    if not session_scope:
        return "current catalog"
    return str(session_scope)


def _build_ambiguity_candidates(natural_query, schema_retriever):
    """
    Build candidate schema tables using keyword overlap + retriever relevance.
    """
    query_lower = natural_query.lower()
    candidate_map = {}

    if not is_ablation_enabled(ABLATION_NO_ALL_SCHEMA_KEYWORDS):
        for doc in ALL_SCHEMA_DOCS:
            table_name = doc.metadata.get(
                "table") or _extract_table_name_from_schema_doc(doc)
            keyword_hits, keyword_score = _score_schema_doc_for_query(
                query_lower, doc)
            if keyword_hits > 0:
                candidate_map[table_name] = {
                    "table": table_name,
                    "doc": doc,
                    "keyword_hits": keyword_hits,
                    "score": float(keyword_score),
                }

    retrieved_schema_docs = get_relevant_chunks_by_type(
        natural_query, schema_retriever, type_filter="schema", k=8)
    for rank, doc in enumerate(retrieved_schema_docs):
        table_name = doc.metadata.get(
            "table") or _extract_table_name_from_schema_doc(doc)
        if not table_name:
            continue
        retriever_score = max(0.0, 3.0 - (rank * 0.35))
        if table_name not in candidate_map:
            candidate_map[table_name] = {
                "table": table_name,
                "doc": doc,
                "keyword_hits": 0,
                "score": 0.0,
            }
        candidate_map[table_name]["score"] += retriever_score

    return sorted(candidate_map.values(), key=lambda x: x["score"], reverse=True)


def _rank_ambiguity_options_with_llm(natural_query, candidate_tables, session_scope=None):
    """Use the LLM to order ambiguous table candidates by likelihood."""
    if not candidate_tables:
        return []

    candidate_lines = []
    for idx, candidate in enumerate(candidate_tables, 1):
        doc = candidate["doc"]
        table_name = candidate["table"]
        description = _extract_schema_description(doc)
        keywords = ", ".join(doc.metadata.get("keywords", [])[:12])
        candidate_lines.append(
            f"{idx}. table={table_name}; description={description}; keywords={keywords}"
        )

    ranking_prompt = f"""
    You are ranking which table in the active dataset scope is most likely intended by a user question.
    Return ONLY a JSON array with objects in descending likelihood.
    Each object must include keys:
    - table (exact table name from candidates)
    - likelihood (float between 0 and 1)
    - option_label (very short user-friendly title, max 6 words)
    - option_description (concise, easy to understand, max 14 words)

    User question:
    {natural_query}

    Active dataset scope:
    {_scope_display_name(session_scope)}

    Candidate tables:
    {chr(10).join(candidate_lines)}
    """
    response = call_llm(ranking_prompt)
    parsed = _extract_json_array(response)
    if not isinstance(parsed, list):
        return []

    valid_table_names = {c["table"] for c in candidate_tables}
    cleaned = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        table_name = item.get("table")
        if table_name not in valid_table_names:
            continue
        try:
            likelihood = float(item.get("likelihood", 0))
        except Exception:
            likelihood = 0.0
        cleaned.append({
            "table": table_name,
            "likelihood": max(0.0, min(1.0, likelihood)),
            "option_label": str(item.get("option_label", table_name)).strip()[:60] or table_name,
            "option_description": str(item.get("option_description", "")).strip()[:120],
        })

    return sorted(cleaned, key=lambda x: x["likelihood"], reverse=True)


def _fallback_ranked_options(candidate_tables, max_options=3):
    """Heuristically rank candidate tables when LLM ranking fails."""
    ranked = sorted(candidate_tables, key=lambda x: x["score"], reverse=True)[
        :max_options]
    options = []
    for item in ranked:
        table_name = item["table"]
        description = _extract_schema_description(item["doc"])
        options.append({
            "table": table_name,
            "likelihood": round(min(0.99, max(0.05, item["score"] / 10.0)), 2),
            "option_label": table_name.replace("_", " ").title(),
            "option_description": description[:120],
        })
    return options


def _prompt_user_for_ambiguity_selection(options, retry_count=0, session_scope=None):
    """Prompt the user to select one or more options or declare no match."""
    scope_name = _scope_display_name(session_scope)
    print(
        f"\nYour question may map to multiple tables in the {scope_name} scope. "
        "Please choose one or more options:"
    )
    for idx, option in enumerate(options, 1):
        print(
            f"{idx}. {option['option_label']} - {option['option_description']}")

    none_index = len(options) + 1
    print(f"{none_index}. None of the above - I need different table options in this scope.")
    prompt = (
        f"Select one or more options 1-{len(options)} separated by commas "
        f"or choose {none_index} for none of the above"
    )
    if retry_count == 0:
        prompt += " (press Enter for 1): "
    else:
        prompt += ": "

    user_choice = input(prompt).strip()
    if user_choice == "" and retry_count == 0:
        return {"selection_type": "options", "selected_indices": [0]}

    parsed_indices = []
    none_selected = False
    for part in user_choice.split(","):
        part = part.strip()
        if not part.isdigit():
            continue
        idx = int(part)
        if idx == none_index:
            none_selected = True
            continue
        if 1 <= idx <= len(options):
            normalized_idx = idx - 1
            if normalized_idx not in parsed_indices:
                parsed_indices.append(normalized_idx)

    if none_selected:
        return {"selection_type": "none_of_above", "selected_indices": []}

    if parsed_indices:
        return {"selection_type": "options", "selected_indices": parsed_indices}

    if retry_count == 0:
        print("Invalid choice. Defaulting to option 1.")
        return {"selection_type": "options", "selected_indices": [0]}

    print("Invalid choice. Treating this as no matching table in the current scope.")
    return {"selection_type": "no_match", "selected_indices": []}


def _build_no_match_resolution(user_message, reason):
    """Build a terminal ambiguity result when no table appears to match."""
    return {
        "resolution_type": "no_match",
        "selected_tables": [],
        "option_label": "No matching table",
        "option_description": user_message,
        "reason": reason,
    }


def _resolve_table_scope_with_llm(natural_query, candidate_tables, session_scope=None):
    """
    Decide whether the question should use one table, multiple tables, or ask for clarification.
    """
    if not candidate_tables:
        return None

    candidate_lines = []
    for idx, candidate in enumerate(candidate_tables, 1):
        doc = candidate["doc"]
        table_name = candidate["table"]
        description = _extract_schema_description(doc)
        keywords = ", ".join(doc.metadata.get("keywords", [])[:12])
        candidate_lines.append(
            f"{idx}. table={table_name}; description={description}; keywords={keywords}"
        )

    resolution_prompt = f"""
    You are deciding which table scope is needed for a natural-language SQL request.

    Return ONLY one JSON object with this schema:
    {{
      "resolution_type": "single_table" | "multi_table" | "clarification_needed",
      "selected_tables": ["exact_table_name"],
      "confidence": 0.0,
      "user_message": "short sentence for the user",
      "reason": "brief internal reason"
    }}

    Rules:
    - Use "multi_table" when the user clearly asks to combine, compare, correlate, join, or compute across fields that belong to different tables.
    - Use "single_table" when one table is sufficient.
    - Use "clarification_needed" only when the intent is still genuinely unclear after reviewing the candidates.
    - "selected_tables" must contain exact table names from the candidate list.
    - For "single_table", include exactly one table.
    - For "multi_table", include at least two tables and keep only the necessary ones.
    - Keep "user_message" under 18 words.

    User question:
    {natural_query}

    Active dataset scope:
    {_scope_display_name(session_scope)}

    Candidate tables:
    {chr(10).join(candidate_lines)}
    """

    response = call_llm(resolution_prompt)
    try:
        parsed = json.loads(response)
    except Exception:
        fenced_match = re.search(
            r"```(?:json)?\s*({[\s\S]*?})\s*```", response, re.IGNORECASE)
        if fenced_match:
            try:
                parsed = json.loads(fenced_match.group(1))
            except Exception:
                parsed = None
        else:
            parsed = None

    if not isinstance(parsed, dict):
        return None

    valid_table_names = {c["table"] for c in candidate_tables}
    selected_tables = [
        table_name for table_name in parsed.get("selected_tables", [])
        if table_name in valid_table_names
    ]
    resolution_type = str(parsed.get("resolution_type", "")).strip()

    if resolution_type == "single_table" and len(selected_tables) == 1:
        return {
            "resolution_type": "single_table",
            "selected_tables": selected_tables,
            "confidence": float(parsed.get("confidence", 0.0) or 0.0),
            "user_message": str(parsed.get("user_message", "")).strip(),
            "reason": str(parsed.get("reason", "")).strip(),
        }

    if resolution_type == "multi_table" and len(selected_tables) >= 2:
        return {
            "resolution_type": "multi_table",
            "selected_tables": selected_tables,
            "confidence": float(parsed.get("confidence", 0.0) or 0.0),
            "user_message": str(parsed.get("user_message", "")).strip(),
            "reason": str(parsed.get("reason", "")).strip(),
        }

    if resolution_type == "clarification_needed":
        return {
            "resolution_type": "clarification_needed",
            "selected_tables": selected_tables,
            "confidence": float(parsed.get("confidence", 0.0) or 0.0),
            "user_message": str(parsed.get("user_message", "")).strip(),
            "reason": str(parsed.get("reason", "")).strip(),
        }

    return None


def detect_and_resolve_ambiguity(natural_query, schema_retriever, max_options=3, max_retries=1, session_scope=None):
    """
    Detect table scope and resolve to either a single table, a multi-table set,
    or a user clarification when intent remains ambiguous.
    """
    current_query = natural_query
    user_comments = ""

    for retry_count in range(max_retries + 1):
        candidates = _build_ambiguity_candidates(
            current_query, schema_retriever)
        if len(candidates) < 2:
            if retry_count < max_retries:
                comments = input(
                    f"\nI could not find enough candidate tables in the {_scope_display_name(session_scope)} scope. "
                    "Describe the data you want: "
                ).strip()
                if comments:
                    user_comments = comments
                    current_query = f"{natural_query}\nAdditional table guidance: {comments}"
                    continue
            return _build_no_match_resolution(
                f"The requested data likely does not exist in the {_scope_display_name(session_scope)} scope.",
                "Too few candidate tables were found during ambiguity resolution.",
            )

        scope_candidates = candidates[:max(max_options, 6)]
        top_candidates = candidates[:max_options]
        llm_scope = _resolve_table_scope_with_llm(
            current_query, scope_candidates, session_scope=session_scope)
        llm_scope = _attach_computed_confidence(
            llm_scope, scope_candidates, current_query)
        _print_ambiguity_confidence(llm_scope, scope_candidates)
        if _should_accept_authoritative_multi_table(llm_scope, scope_candidates, current_query):
            selected_tables = llm_scope["selected_tables"]
            _write_ambiguity_trace(
                current_query,
                session_scope,
                llm_scope,
                scope_candidates,
                "auto_accept_multi_table",
            )
            print(
                "\nDetected that this question needs multiple tables: "
                + ", ".join(selected_tables)
            )
            return {
                "resolution_type": "multi_table",
                "selected_tables": selected_tables,
                "option_label": " + ".join(table.replace("_", " ").title() for table in selected_tables),
                "option_description": llm_scope.get("user_message", "Use multiple related datasets."),
            }

        if _should_accept_authoritative_single_table(llm_scope, scope_candidates, current_query):
            selected_table = llm_scope["selected_tables"][0]
            _write_ambiguity_trace(
                current_query,
                session_scope,
                llm_scope,
                scope_candidates,
                "auto_accept_single_table",
            )
            selected_doc = next(
                (
                    candidate["doc"]
                    for candidate in scope_candidates
                    if candidate["table"] == selected_table
                ),
                None,
            )
            return {
                "resolution_type": "single_table",
                "selected_tables": [selected_table],
                "table": selected_table,
                "option_label": selected_table.replace("_", " ").title(),
                "option_description": (
                    llm_scope.get("user_message")
                    or (_extract_schema_description(selected_doc) if selected_doc else "")
                ),
            }

        if llm_scope and llm_scope["resolution_type"] in {"single_table", "multi_table"}:
            _write_ambiguity_trace(
                current_query,
                session_scope,
                llm_scope,
                scope_candidates,
                "request_clarification_candidates_too_close",
            )
            print(
                "\nCandidate tables remain too close to auto-select confidently; requesting clarification."
            )
        elif llm_scope:
            _write_ambiguity_trace(
                current_query,
                session_scope,
                llm_scope,
                scope_candidates,
                "request_clarification_llm_unclear",
            )
        else:
            _write_ambiguity_trace(
                current_query,
                session_scope,
                llm_scope,
                scope_candidates,
                "request_clarification_no_llm_scope",
            )

        llm_ranked = _rank_ambiguity_options_with_llm(
            current_query, top_candidates, session_scope=session_scope)
        if not llm_ranked:
            llm_ranked = _fallback_ranked_options(
                top_candidates, max_options=max_options)

        options = llm_ranked[:max_options]
        if len(options) < 2:
            return _build_no_match_resolution(
                f"The requested data likely does not exist in the {_scope_display_name(session_scope)} scope.",
                "The ranked clarification list did not contain enough credible options.",
            )

        selection = _prompt_user_for_ambiguity_selection(
            options, retry_count=retry_count, session_scope=session_scope)
        if selection["selection_type"] == "none_of_above":
            if retry_count >= max_retries:
                return _build_no_match_resolution(
                    f"The requested data likely does not exist in the {_scope_display_name(session_scope)} scope.",
                    "The user rejected the clarification options after the final retry.",
                )

            comments = input(
                "\nDescribe the table or fields you expected so I can regenerate the options: "
            ).strip()
            if not comments:
                return _build_no_match_resolution(
                    f"The requested data likely does not exist in the {_scope_display_name(session_scope)} scope.",
                    "No additional clarification was provided after the user rejected the options.",
                )
            user_comments = comments
            current_query = f"{natural_query}\nAdditional table guidance: {comments}"
            continue

        if selection["selection_type"] == "no_match":
            return _build_no_match_resolution(
                f"The requested data likely does not exist in the {_scope_display_name(session_scope)} scope.",
                "The user did not select a valid clarification option.",
            )

        selected_options = [options[idx]
                            for idx in selection["selected_indices"]]
        selected_tables = [option["table"] for option in selected_options]

        if len(selected_options) == 1:
            selected_option = selected_options[0]
            print(
                f"Using: {selected_option['option_label']} ({selected_option['table']})")
            result = {
                "resolution_type": "single_table",
                "selected_tables": [selected_option["table"]],
                "table": selected_option["table"],
                "option_label": selected_option["option_label"],
                "option_description": selected_option["option_description"],
            }
            if user_comments:
                result["clarification_comments"] = user_comments
            return result

        combined_label = " + ".join(option["option_label"]
                                    for option in selected_options)
        combined_description = "User selected multiple tables during clarification."
        print(f"Using multiple tables: {', '.join(selected_tables)}")
        result = {
            "resolution_type": "multi_table",
            "selected_tables": selected_tables,
            "option_label": combined_label,
            "option_description": combined_description,
        }
        if user_comments:
            result["clarification_comments"] = user_comments
        return result

    return _build_no_match_resolution(
        f"The requested data likely does not exist in the {_scope_display_name(session_scope)} scope.",
        "Ambiguity resolution exhausted all retries.",
    )


def contains_missing_keyword(user_question: str) -> bool:
    """
    Return True if the question references missingness (e.g., "missing", "missingness", "misses", "miss").
    """
    if not user_question:
        return False
    pattern = re.compile(r"\bmiss(?:ing(?:ness(?:es)?)?|es)?\b", re.IGNORECASE)
    return bool(pattern.search(user_question))


def missingness_count_options():
    """
    Collects user preference for how missingness should be computed and returns
    a prompt fragment to steer the LLM's SQL generation accordingly.

    Option 1: User supplies expected collection frequency per entity.
    Option 2: System counts NULL/NA values in existing data (default).
    """
    print("Missingness options:")
    print("1) Enter expected collection frequency (per entity).")
    print("2) Count observed NA/null values (default).")
    choice = (input("Select option 1 or 2 (press Enter for 2): ").strip() or "2")

    if choice == "1":
        freq_input = input(
            "Enter expected collection frequency (e.g., daily, weekly, monthly): "
        ).strip()
        freq_word = freq_input.lower()
        prompt_hint = (
            "Compute missingness by comparing expected collection cadence "
            f"('{freq_word}') per entity. Choose the entity/group column from the schema/KB "
            "and compare expected counts to actual counts; return entities with deficits "
            "and summarize NULL/NA counts by column."
        )
        return {
            "mode": "expected_frequency",
            "expected_frequency": freq_word,
            "prompt_hint": prompt_hint,
        }

    prompt_hint = (
        "Compute missingness by counting NULL/NA values per column and reporting totals "
        "and percentages."
    )
    return {"mode": "observed_nulls", "prompt_hint": prompt_hint}


def build_missingness_prompt(formatted_schema, formatted_kb, formatted_natural_query, missingness_hint=""):
    """Build the SQL prompt used specifically for missingness questions."""
    hint_block = f"Missingness preference:\n{missingness_hint}\n\n" if missingness_hint else ""
    return f"""
    You are a helpful assistant that translates natural-language questions into SQL, focused on missingness analysis.

    Respond with only a single SQL query wrapped in triple backticks like: ```SQL ...the query... ```
    Do NOT include any other text, comments, explanations, or extra SQL examples.
    Use the provided schema and knowledge base only; avoid hallucinations.

    {hint_block}
    Include ALL individuals, even the ones without missingness.
    If the user asks about missingness for an array-backed or JSON-backed measure (for example Garmin fields such as time_offset_stress_level_values), detect missingness within the array values for that measure and not only at the row level.

    Here is the database schema:
    {formatted_schema}

    Here is the knowledge base:
    {formatted_kb}

    Translate the following query into SQL:
    {formatted_natural_query}

    Here are a few examples:

    [Example 1]
    User Query: What are the missingness for all individuals for oura deep sleep?
    Missingness option: Count observed NA/null values
    SQL:
    ```SQL
    SELECT record_id,
    COUNT(*) AS total_days,
    SUM(CASE WHEN deep IS NULL THEN 1 ELSE 0 END) AS missing_days,
    ROUND(100.0 * SUM(CASE WHEN deep IS NULL THEN 1 ELSE 0 END) / COUNT(*), 2) AS missingness_percentage
    FROM bump_oura_sleep
    GROUP BY record_id
    HAVING SUM(CASE WHEN deep IS NULL THEN 1 ELSE 0 END) > 0
    ORDER BY missingness_percentage DESC;
    ```

    [Example 2]
    User Query: What are the missingness for all individuals for oura deep sleep?
    Missingness option: Enter expected collection frequency (daily)
    SQL:
    ```SQL
    SQL:
    WITH date_range AS (
    SELECT 
        record_id,
        MIN(event_date) as first_date,
        MAX(event_date) as last_date,
        julianday(MAX(event_date)) - julianday(MIN(event_date)) + 1 as expected_days
    FROM bump_oura_sleep
    GROUP BY record_id
    ),
    actual_counts AS (
    SELECT 
        record_id,
        COUNT(*) as actual_days,
        SUM(CASE WHEN deep IS NULL THEN 1 ELSE 0 END) as deep_null_count
    FROM bump_oura_sleep
    GROUP BY record_id
    )
    SELECT 
    dr.record_id,
    dr.expected_days,
    ac.actual_days,
    dr.expected_days - ac.actual_days as missing_days,
    ac.deep_null_count,
    ROUND(100.0 * (dr.expected_days - ac.actual_days) / dr.expected_days, 2) as collection_missingness_pct
    FROM date_range dr
    JOIN actual_counts ac ON dr.record_id = ac.record_id
    WHERE dr.expected_days > ac.actual_days OR ac.deep_null_count > 0
    ORDER BY collection_missingness_pct DESC, deep_null_count DESC;
    ```
    """


def _build_scope_sql_examples(session_scope=None):
    """Return a compact example block tailored to the active dataset scope."""
    bump_examples = """
    [Example 1]
    User Query: What are the average oura activity score and oura sleep score for user sema4_4?
    SQL:
    ```SQL
    SELECT
        AVG(oa.score) AS avg_activity_score,
        AVG(os.score) AS avg_sleep_score
    FROM bump_oura_activity oa
    JOIN bump_oura_sleep os ON oa.record_id = os.record_id
    WHERE oa.record_id = 'sema4_4';
    ```

    [Example 2]
    User Query: What is the date of birth of user sema4_4?
    SQL:
    ```SQL
    SELECT answer_text AS date_of_birth
    FROM bump_surveys
    WHERE record_id = 'sema4_4'
      AND question_text LIKE '%Select your date of birth%';
    ```

    [Example 3]
    User Query: What are the ages of all patients?
    SQL:
    ```SQL
    SELECT
        record_id,
        CAST(strftime('%Y', event_date) AS INTEGER) -
        CAST(MIN(substr(answer_text, 1, 4)) AS INTEGER) AS age
    FROM bump_surveys
    WHERE question_text LIKE '%Select your date of birth%'
    GROUP BY record_id, event_date;
    ```

    [Example 4]
    User Query: What is the emotional support score for patient sema4_37?
    SQL:
    ```SQL
    SELECT
        record_id,
        event_date,
        SUM(
            CASE
                WHEN answer_text = 'Never' THEN 1
                WHEN answer_text = 'Rarely' THEN 2
                WHEN answer_text = 'Sometimes' THEN 3
                WHEN answer_text = 'Usually' THEN 4
                WHEN answer_text = 'Always' THEN 5
                ELSE 0
            END
        ) AS emotional_support_score
    FROM bump_surveys
    WHERE record_id = 'sema4_37'
      AND (
        question_text LIKE '%someone who will listen to me when I need to talk%'
        OR question_text LIKE '%someone to confide in or talk to about myself or my problems%'
        OR question_text LIKE '%someone who makes me feel appreciated%'
        OR question_text LIKE '%someone to talk with when I have a bad day%'
      )
    GROUP BY record_id, event_date
    ORDER BY event_date;
    ```
    """

    sinc_examples = """
    [Example 1]
    User Query: How many patients are overweight?
    SQL:
    ```SQL
    SELECT COUNT(DISTINCT user_id) AS num_patients
    FROM sinc_bodyport
    WHERE bmi_kg_m2 > 25.0;
    ```

    [Example 2]
    User Query: What is the PHQ-8 score for user X on each date?
    SQL:
    ```SQL
    SELECT
        user_id,
        event_date,
        SUM(
            CASE
                WHEN answer LIKE '%(0)%' THEN 0
                WHEN answer LIKE '%(1)%' THEN 1
                WHEN answer LIKE '%(2)%' THEN 2
                WHEN answer LIKE '%(3)%' THEN 3
                ELSE 0
            END
        ) AS phq8_score
    FROM sinc_phq_8
    WHERE question NOT LIKE '%how difficult%'
      AND user_id = 'X'
    GROUP BY user_id, event_date
    HAVING COUNT(*) = 8
    ORDER BY event_date;
    ```

    [Example 3]
    User Query: Classify each user X's PHQ-8 score.
    SQL:
    ```SQL
    WITH scored AS (
        SELECT
            user_id,
            event_date,
            SUM(
                CASE
                    WHEN answer LIKE '%(0)%' THEN 0
                    WHEN answer LIKE '%(1)%' THEN 1
                    WHEN answer LIKE '%(2)%' THEN 2
                    WHEN answer LIKE '%(3)%' THEN 3
                    ELSE 0
                END
            ) AS phq8_score
        FROM sinc_phq_8
        WHERE question NOT LIKE '%how difficult%'
          AND user_id = 'X'
        GROUP BY user_id, event_date
        HAVING COUNT(*) = 8
    )
    SELECT
        user_id,
        event_date,
        phq8_score,
        CASE
            WHEN phq8_score < 5 THEN 'Minimal depression'
            WHEN phq8_score < 10 THEN 'Mild depression'
            WHEN phq8_score < 15 THEN 'Moderate depression'
            WHEN phq8_score < 20 THEN 'Moderately severe depression'
            ELSE 'Severe depression'
        END AS depression_level
    FROM scored
    ORDER BY event_date;
    ```

    [Example 4]
    User Query: What are the top 5 most commonly reported EIMs?
    SQL:
    ```SQL
    SELECT answer AS eim, COUNT(*) AS num_reports
    FROM sinc_extraintestinal_manifestations
    WHERE answer IS NOT NULL AND answer != ''
    GROUP BY answer
    ORDER BY num_reports DESC
    LIMIT 5;
    ```
    """

    cross_examples = """
    [Example 1]
    User Query: What is the average sleep score by dataset?
    SQL:
    ```SQL
    SELECT dataset, AVG(sleep_score) AS avg_sleep_score
    FROM wearable_sleep_daily
    WHERE sleep_score IS NOT NULL
    GROUP BY dataset
    ORDER BY dataset;
    ```

    [Example 2]
    User Query: Compare the average activity score and average steps by dataset.
    SQL:
    ```SQL
    SELECT
        dataset,
        AVG(activity_score) AS avg_activity_score,
        AVG(steps) AS avg_steps
    FROM wearable_activity_daily
    WHERE activity_score IS NOT NULL OR steps IS NOT NULL
    GROUP BY dataset
    ORDER BY dataset;
    ```

    [Example 3]
    User Query: What is the average readiness score by dataset?
    SQL:
    ```SQL
    SELECT dataset, AVG(readiness_score) AS avg_readiness_score
    FROM wearable_readiness_daily
    WHERE readiness_score IS NOT NULL
    GROUP BY dataset
    ORDER BY dataset;
    ```

    [Example 4]
    User Query: How many participants have at least 30 days of sleep data in each dataset?
    SQL:
    ```SQL
    SELECT dataset, COUNT(*) AS participants_with_30_days
    FROM (
        SELECT dataset, participant_key
        FROM wearable_sleep_daily
        GROUP BY dataset, participant_key
        HAVING COUNT(DISTINCT event_date) >= 30
    ) qualified_participants
    GROUP BY dataset
    ORDER BY dataset;
    ```

    [Example 5]
    User Query: Compare average BMI and weight by dataset from Bodyport.
    SQL:
    ```SQL
    SELECT
        dataset,
        AVG(bmi_kg_m2) AS avg_bmi,
        AVG(weight_kg) AS avg_weight_kg
    FROM wearable_bodyport_daily
    WHERE bmi_kg_m2 IS NOT NULL OR weight_kg IS NOT NULL
    GROUP BY dataset
    ORDER BY dataset;
    ```

    [Example 6]
    User Query: Compare average stress EMA score by dataset.
    SQL:
    ```SQL
    SELECT
        dataset,
        AVG(item_score) AS avg_stress_score
    FROM wearable_ema_daily
    WHERE measure = 'stress' AND item_score IS NOT NULL
    GROUP BY dataset
    ORDER BY dataset;
    ```
    """

    if session_scope == SCOPE_BUMP:
        return bump_examples
    if session_scope == SCOPE_SINC:
        return sinc_examples
    if session_scope == SCOPE_CROSS:
        return cross_examples
    try:
        plugin = get_dataset_config(session_scope)
    except ValueError:
        return ""
    return (
        f"No static SQL examples are registered for {plugin.name}. "
        "Use only tables and columns present in the retrieved schema and follow "
        "the dataset SQL guideline."
    )


def _execute_sql_candidate(response_text, engine, output_msg=True, session_scope=None):
    """
    Execute one LLM response and normalize the outcome into a comparable candidate record.
    Errors remain valid candidates for majority voting.
    """
    sql_query = ""

    try:
        final_result, sql_query = try_execute_sql(
            response_text, engine, output_msg, session_scope=session_scope)
        if isinstance(final_result, pd.DataFrame) and len(final_result) == 0:
            error_message = "The query returned no results."
        else:
            error_message = ""
        return {
            "response_text": response_text,
            "result": final_result,
            "sql_query": sql_query,
            "explanation": "",
            "error_message": error_message,
        }
    except Exception as e:
        try:
            sql_query = extract_sql(response_text) or ""
        except Exception:
            sql_query = ""
        return {
            "response_text": response_text,
            "result": None,
            "sql_query": sql_query,
            "explanation": "",
            "error_message": str(e),
        }


def _generate_sql_candidate_with_retry(
    initial_prompt,
    retry_prompt_builder,
    engine,
    output_msg=True,
    session_scope=None,
    max_retries=SQL_GENERATION_RETRIES,
):
    """Generate and execute SQL, retrying only generation or execution failures."""
    prompt = initial_prompt
    candidate = None

    for attempt_idx in range(max_retries + 1):
        response_text = call_llm(prompt)
        candidate = _execute_sql_candidate(
            response_text,
            engine,
            output_msg,
            session_scope=session_scope,
        )

        # A zero-row DataFrame is still a successful SQL execution.
        if isinstance(candidate["result"], pd.DataFrame):
            return candidate

        if attempt_idx < max_retries:
            print(
                "SQL generation/execution failed; "
                f"automatically retrying ({attempt_idx + 1}/{max_retries})..."
            )
            prompt = retry_prompt_builder(candidate["error_message"])

    return candidate


def _split_top_level(expr, sep=","):
    """Split a SQL expression on top-level separators only."""
    parts = []
    start = 0
    depth = 0
    in_single = False
    in_double = False
    for i, ch in enumerate(expr):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == sep and depth == 0:
                parts.append(expr[start:i].strip())
                start = i + 1
    tail = expr[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _normalize_simple_expression(expr):
    """Normalize whitespace/casing in a SQL expression for comparison."""
    expr = re.sub(r"\s+", " ", expr.strip().lower())
    expr = re.sub(r"\s*([=<>!+\-*/(),])\s*", r"\1", expr)
    return expr


def _strip_expression_alias(expr):
    """Remove explicit or implicit trailing aliases from an expression."""
    expr = expr.strip()
    expr = re.sub(r"\s+as\s+[a-zA-Z_][a-zA-Z0-9_]*$",
                  "", expr, flags=re.IGNORECASE)
    expr = re.sub(
        r"^(.*[\]\)`\"'\w])\s+[a-zA-Z_][a-zA-Z0-9_]*$",
        r"\1",
        expr,
        flags=re.IGNORECASE,
    )
    return expr


def _extract_table_alias_map(sql):
    """Map SQL aliases back to table names for comparison."""
    alias_map = {}
    pattern = re.compile(
        r"\b(from|join)\s+([a-zA-Z_][\w\.]*)(?:\s+(?:as\s+)?([a-zA-Z_][\w]*))?",
        flags=re.IGNORECASE,
    )
    for _kw, table_name, alias in pattern.findall(sql):
        if alias and alias.lower() != table_name.lower():
            alias_map[alias.lower()] = table_name.lower()
    return alias_map


def _remove_alias_references(expr, alias_map):
    """Drop table alias prefixes from an expression."""
    out = expr
    for alias in sorted(alias_map.keys(), key=len, reverse=True):
        out = re.sub(rf"\b{re.escape(alias)}\.", "", out, flags=re.IGNORECASE)
    return out


def _normalize_where_clause(where_clause):
    """Normalize top-level AND conditions to reduce order sensitivity."""
    terms = re.split(r"\band\b", where_clause, flags=re.IGNORECASE)
    normalized_terms = [_normalize_simple_expression(t)
                        for t in terms if t.strip()]
    normalized_terms.sort()
    return " and ".join(normalized_terms)


def _extract_clause(sql, start_keyword, end_keywords):
    """Extract a top-level clause body from SQL."""
    pattern = re.compile(
        rf"\b{re.escape(start_keyword)}\b(.*?)(?=\b(?:{'|'.join(end_keywords)})\b|$)",
        flags=re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(sql)
    return match.group(1).strip() if match else ""


def _canonicalize_sql_for_voting(sql):
    """
    Build a best-effort canonical SQL form for majority voting.
    This intentionally ignores formatting and most aliases.
    """
    if not sql or not sql.strip():
        return ""

    normalized = sql.replace("\\n", " ").replace(
        "\\t", " ").replace("\\r", " ")
    normalized = normalized.strip().strip(";")
    normalized = re.sub(r"\s+", " ", normalized)
    normalized_lower = normalized.lower()
    alias_map = _extract_table_alias_map(normalized_lower)
    without_alias_refs = _remove_alias_references(normalized_lower, alias_map)
    without_alias_defs = re.sub(
        r"\b(from|join)\s+([a-zA-Z_][\w\.]*)(?:\s+(?:as\s+)?[a-zA-Z_][\w]*)",
        r"\1 \2",
        without_alias_refs,
        flags=re.IGNORECASE,
    )
    return without_alias_defs.strip()


def _majority_vote_sql_candidate(candidates):
    """
    Select the most frequent SQL candidate. If all candidates differ, keep the first one.
    Execution failures are included as candidates.
    """
    if not candidates:
        raise ValueError("No SQL candidates were generated.")

    counts = {}
    best_key = None
    best_count = 0

    for candidate in candidates:
        canonical_sql = _canonicalize_sql_for_voting(candidate["sql_query"])
        candidate_key = canonical_sql or f"ERROR::{candidate['error_message'].strip()}"
        counts[candidate_key] = counts.get(candidate_key, 0) + 1
        if counts[candidate_key] > best_count:
            best_key = candidate_key
            best_count = counts[candidate_key]

    if best_count == 1:
        return candidates[0]

    for candidate in candidates:
        canonical_sql = _canonicalize_sql_for_voting(candidate["sql_query"])
        candidate_key = canonical_sql or f"ERROR::{candidate['error_message'].strip()}"
        if candidate_key == best_key:
            return candidate

    return candidates[0]


def _describe_majority_vote_result(candidates, selected_candidate):
    """Return a one-line summary of the majority voting outcome."""
    if len(candidates) == 1:
        outcome = "SQL" if selected_candidate["sql_query"].strip() else "ERROR"
        return f"Majority vote: single generation run; using generated {outcome} candidate."

    selected_key = _canonicalize_sql_for_voting(
        selected_candidate["sql_query"]
    ) or f"ERROR::{selected_candidate['error_message'].strip()}"
    selected_count = 0

    for candidate in candidates:
        candidate_key = _canonicalize_sql_for_voting(
            candidate["sql_query"]
        ) or f"ERROR::{candidate['error_message'].strip()}"
        if candidate_key == selected_key:
            selected_count += 1

    outcome = "SQL" if selected_candidate["sql_query"].strip() else "ERROR"
    if selected_count == 1:
        return f"Majority vote: no agreement across {len(candidates)} runs; using first {outcome} candidate."
    return f"Majority vote: selected {outcome} candidate with {selected_count}/{len(candidates)} matching runs."


def process_natural_query(natural_query, engine, schema_retriever, kb_retriever, impute="no", session_scope=None):
    """
    Process a natural language query and return results from the database.
    """
    query_start_time = time.perf_counter()
    user_query = natural_query.strip()

    if is_frequency_availability_query(user_query):
        result_df, explanation = _build_frequency_availability_result(
            user_query,
            session_scope=session_scope,
        )
        total_elapsed_seconds = time.perf_counter() - query_start_time
        print(
            f"Answered frequency availability query from metadata in {total_elapsed_seconds:.2f} seconds."
        )
        return result_df, "", explanation

    if session_scope:
        natural_query = (
            f"{user_query}\n\n"
            f"Requested dataset scope: {session_scope}."
        )
    else:
        natural_query = user_query

    active_ablations = get_active_ablations()

    # Ambiguity detection on candidate tables within the active scope.
    if is_ablation_enabled(ABLATION_NO_AMBIGUITY_RESOLVER):
        selected_option = None
    else:
        selected_option = detect_and_resolve_ambiguity(
            natural_query, schema_retriever, max_options=3, session_scope=session_scope)
    selected_tables = selected_option.get(
        "selected_tables", []) if selected_option else []
    resolution_type = selected_option.get(
        "resolution_type") if selected_option else None
    if resolution_type == "no_match":
        message = selected_option.get(
            "option_description",
            f"The requested data likely does not exist in the {_scope_display_name(session_scope)} scope.",
        )
        print(message)
        total_elapsed_seconds = time.perf_counter() - query_start_time
        print(f"\nTotal query time: {total_elapsed_seconds:.2f} seconds")
        return None, "", message

    # Get relevant schema
    schema_chunks, schema_stats = get_schema_chunks_by_keyword_match(
        natural_query,
        schema_retriever,
        boosted_tables=selected_tables,
        restrict_to_tables=selected_tables,
    )
    schema_selection_mode = "hybrid_ranked"
    if schema_stats.get("restricted_count", 0) > 0:
        schema_selection_mode = "authoritative_scope"
    elif schema_stats.get("ambiguity_boost_count", 0) > 0:
        schema_selection_mode = "ambiguity_boosted"
    formatted_schema = format_schema_chunks(schema_chunks)

    # Get relevant knowledge base info
    if is_ablation_enabled(ABLATION_NO_KB):
        kb_stats = {
            "base_count": 0,
            "boost_counts": {},
            "pre_dedupe_total": 0,
            "deduped_count": 0,
        }
        kb_docs = []
        kb_expanded = []
        kb_reference_expansion_count = 0
    else:
        kb_chunks = [sql_guideline]  # include sql_guideline all the time
        scoped_sql_guideline_doc = get_scope_sql_guideline_doc(session_scope)
        if scoped_sql_guideline_doc is not None:
            kb_chunks.append(scoped_sql_guideline_doc)
        kb_retrieved_docs, kb_stats = get_relevant_kb_chunks(
            natural_query,
            kb_retriever,
            selected_tables=selected_tables,
        )
        kb_chunks += kb_retrieved_docs

        # Expand kb_chunks with linked references
        doc_by_id = {
            doc.metadata["id"]: doc for doc in ALL_KNOWLEDGE_DOCS if "id" in doc.metadata}
        kb_docs = _dedupe_docs(kb_chunks)
        kb_expanded = expand_with_references(kb_docs, doc_by_id)
        kb_reference_expansion_count = len(kb_expanded) - len(kb_docs)
    formatted_kb = format_knowledge_chunks(
        [doc.page_content for doc in kb_expanded])

    formatted_natural_query = natural_query.strip()
    scope_join_rule = _scope_specific_join_rule(session_scope)
    if selected_option:
        if resolution_type == "multi_table":
            formatted_natural_query += (
                "\n\nSelected dataset scope:\n"
                f"- Tables: {', '.join(selected_tables)}\n"
                f"- User intent: {selected_option['option_label']}\n"
                f"- Description: {selected_option['option_description']}\n"
                "- SQL generation rule: use all listed tables if needed by the request.\n"
                f"- {scope_join_rule}\n"
                "- Do not collapse this request to a single table."
            )
        else:
            formatted_natural_query += (
                "\n\nSelected dataset scope:\n"
                f"- Table: {selected_option['table']}\n"
                f"- User intent: {selected_option['option_label']}\n"
                f"- Description: {selected_option['option_description']}\n"
                "- SQL generation rule: prioritize this table as the primary source unless the question explicitly requests combining datasets.\n"
                f"- {scope_join_rule}"
            )

    # Check if user asks for missingness plotting
    missingness_mode = contains_missing_keyword(formatted_natural_query)
    missingness_hint = ""
    if missingness_mode:
        # Collect user preference to steer missingness SQL generation
        missingness_choice = missingness_count_options()
        missingness_hint = missingness_choice.get("prompt_hint", "")

    previous_error_message = ""
    final_result = None
    sql_query = ""
    explanation = ""
    response_text = ""

    print(f"Processing query: {formatted_natural_query}")
    if active_ablations:
        print(f"Active ablation(s): {', '.join(sorted(active_ablations))}")
    print(
        "Schema retrieval stats: "
        f"keyword_matches={schema_stats['keyword_match_count']}, "
        f"semantic_hits={schema_stats['semantic_count']}, "
        f"ambiguity_boosted={schema_stats.get('ambiguity_boost_count', 0)}, "
        f"restricted={schema_stats.get('restricted_count', 0)}, "
        f"ranked_candidates={schema_stats['ranked_count']}, "
        f"final_schema_chunks={len(schema_chunks)}, "
        f"selection_mode={schema_selection_mode}"
    )
    print(
        "KB retrieval stats: "
        f"base={kb_stats['base_count']}, "
        f"schema_boost={kb_stats['boost_counts'].get('schema_matched', 0)}, "
        f"retrieved_pre_dedupe={kb_stats['pre_dedupe_total']}, "
        f"retrieved_post_dedupe={kb_stats['deduped_count']}, "
        f"with_sql_guideline={len(kb_docs)}, "
        f"reference_expansion_added={kb_reference_expansion_count}, "
        f"final_kb_chunks={len(kb_expanded)}"
    )
    print("\nFormatted Schema:\n", formatted_schema)
    print("\nFormatted KB:\n", formatted_kb)

    # --- PART 1: SQL GENERATION VOTE ---
    vote_candidates = []
    generation_runs = 1 if is_ablation_enabled(
        ABLATION_NO_MAJORITY_VOTING) else MAJORITY_VOTE_RUNS
    for run_idx in range(generation_runs):
        print(
            f"\nRunning SQL generation candidate {run_idx + 1}/{generation_runs}...")
        if missingness_mode:
            prompt = build_missingness_prompt(
                formatted_schema,
                formatted_kb,
                formatted_natural_query,
                missingness_hint,
            )
            retry_prompt_builder = lambda error_message, base_prompt=prompt: (
                f"{base_prompt}\n\n"
                "The previous SQL generation or execution failed with this error:\n"
                f"{error_message}\n\n"
                "Generate a corrected SQL query that avoids this error."
            )
        else:
            prompt = build_prompt(formatted_schema, formatted_kb, formatted_natural_query,
                                  previous_error_message=previous_error_message,
                                  session_scope=session_scope)
            retry_prompt_builder = lambda error_message: build_prompt(
                formatted_schema,
                formatted_kb,
                formatted_natural_query,
                previous_error_message=error_message,
                session_scope=session_scope,
            )
        vote_candidates.append(
            _generate_sql_candidate_with_retry(
                prompt,
                retry_prompt_builder,
                engine,
                True,
                session_scope=session_scope,
            )
        )

    selected_candidate = _majority_vote_sql_candidate(vote_candidates)
    response_text = selected_candidate["response_text"]
    final_result = selected_candidate["result"]
    sql_query = selected_candidate["sql_query"]
    explanation = ""
    previous_error_message = selected_candidate["error_message"]
    print(_describe_majority_vote_result(vote_candidates, selected_candidate))

    if sql_query:
        explanation = generate_explanation(
            formatted_schema,
            formatted_kb,
            formatted_natural_query,
            sql_query,
        )

    # --- PART 2: CONTINUOUS REFINEMENT LOOP ---
    while True:
        # 1. Determine System Status
        if isinstance(final_result, pd.DataFrame):
            system_status = "Success" if len(
                final_result) > 0 else "No data found."
        else:
            system_status = f"Execution failed: {previous_error_message}"

        # 2. Display Current State to User
        print(f"\n" + "="*60)
        print(f"CURRENT SQL & DATA (Attempt)")
        print("="*60)
        print(f"SQL:\n{sql_query}\n")

        if isinstance(final_result, pd.DataFrame):
            print("Data Preview:")
            print(final_result.head(15).to_string())
            print(f"\n({len(final_result)} rows total)")
        else:
            print(f"Status: {system_status}")

        print(f"\nExplanation:\n{explanation}")

        # 3. Get User Feedback
        user_feedback = input(
            "\n[Feedback] Type changes to refine, or press Enter if satisfied: ").strip()

        # 4. Exit loop if user is satisfied
        if not user_feedback:
            print("Finalizing results...")
            break

        # 5. Regenerate based on feedback
        print("\nRegenerating based on your feedback...")

        refinement_prompt = f"""
        Refine the SQL based on user feedback.
        
        Original Question: {formatted_natural_query}
        Previous SQL: {sql_query}
        System Outcome: {system_status}
        User Feedback: {user_feedback}
        
        Respond with only corrected SQL wrapped in triple backticks: ```SQL ... ```
        Do not include explanations or any other text.

        Contextual Schema:
        {formatted_schema}
        
        Contextual Knowledge:
        {formatted_kb}
        """

        retry_prompt_builder = lambda error_message, base_prompt=refinement_prompt: (
            f"{base_prompt}\n\n"
            "The previous refinement failed with this error:\n"
            f"{error_message}\n\n"
            "Generate corrected SQL that avoids this error."
        )
        refined_candidate = _generate_sql_candidate_with_retry(
            refinement_prompt,
            retry_prompt_builder,
            engine,
            True,
            session_scope=session_scope,
        )
        response_text = refined_candidate["response_text"]
        final_result = refined_candidate["result"]
        sql_query = refined_candidate["sql_query"]
        previous_error_message = refined_candidate["error_message"]

        if isinstance(final_result, pd.DataFrame):
            explanation = generate_explanation(
                formatted_schema,
                formatted_kb,
                formatted_natural_query,
                sql_query,
            )
        else:
            print(
                "Execution Error during refinement: "
                f"{previous_error_message}"
            )

    total_elapsed_seconds = time.perf_counter() - query_start_time
    print(f"\nTotal query time: {total_elapsed_seconds:.2f} seconds")
    return final_result, sql_query, explanation


def try_execute_sql(response_text, engine, output_msg=True, session_scope=None):
    """
    Processes 'response_text' into sql query and attempts to execute it against the database and returns the results.
    Raises an exception when the 'response_text' is invalid or already raised by the engine.
    """
    if not response_text:
        raise ValueError("No response")

    # Extract SQL query from the response
    sql_query = extract_sql(response_text)

    if not sql_query:
        print("Failed to generate a valid SQL query.")
        print(response_text)
        raise ValueError("Error: Could not generate a valid SQL query.")

    if output_msg:
        print(f"Generated SQL Query:\n{sql_query}\n\n")

    # Prevent SQL execution if the extracted query is empty or invalid
    if not sql_query.strip():
        raise ValueError("Error: Extracted SQL query is empty.")

    validate_sql_for_scope(sql_query, session_scope=session_scope)

    # Execute the SQL query and return results. Do not handle any exceptions
    with engine.connect() as connection:
        result = pd.read_sql(sql_query, connection)

    if output_msg:
        print(f"Query returned {len(result)} rows")
        if len(result) == 0:
            print("Empty result - query executed successfully but found no matching data")

    return result, sql_query


def extract_explanation(response_text):
    """
    Extracts the KB explanation block enclosed by <KB_EXPLANATIONS>...</KB_EXPLANATIONS> from an LLM response.
    """
    match = re.search(r"<KB_EXPLANATIONS>(.*?)</KB_EXPLANATIONS>",
                      response_text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def generate_explanation(formatted_schema, formatted_kb, formatted_natural_query, sql_query):
    """Generate a KB explanation for the finalized SQL query."""
    if not sql_query:
        return ""

    explanation_prompt = build_explanation_prompt(
        formatted_schema,
        formatted_kb,
        formatted_natural_query,
        sql_query,
    )
    response_text = call_llm(explanation_prompt)
    return extract_explanation(response_text)


def build_dataframe_extraction_prompt(sql_query):
    """Build a prompt that extracts per-table inspection SQL from a generated query."""
    return f"""
    Given the following SQL query:

    {sql_query}

    1. Identify all tables and columns referenced in the query.
    2. For each table, generate a SQL query of the form:
       SELECT column1, column2, column3 FROM table;
    3. Output only a valid Python list of strings, e.g. ["```SQL SELECT col1, col2 FROM t1;```", "```SQL SELECT col1 FROM t2;```"].
       - Must make sure to include the triple backticks annotation ```SQL ... ``` around the SQL query.
       - No explanations, comments, or additional text.

    The number of items in the list must exactly equal the number of unique tables identified.
    """


def build_imputation_prompt(user_prompt, all_cols, missing_cols):
    """Build a prompt for generating executable dataframe imputation code."""
    return f"""
    Write EXECUTABLE Python function impute_dataframe(df) that takes in a pd.DataFrame and imputes its missing values:

    Rules:
    - Use the provided column type map in "Dataframe columns" (name->type in {{numeric,categorical,datetime}}). Do not infer types.
    - Group key: ['user_id', date_col] if any column name contains 'date' (case-insensitive, pick the first); else ['user_id'].
    - For each column in "Columns with missing values":
    * numeric: fill group mean via groupby.transform('mean'); fallback to global median; if all NaN, fill 0.0.
    * categorical: fill group mode; on ties pick lexicographically smallest; fallback global mode; else 'Unknown'.
    * datetime: within each user_id, sort by this datetime and ffill then bfill.
    * if a numeric column is time-indexed and ANY datetime column exists: within each user_id sorted by the first datetime col, interpolate (method='time' if possible else 'linear'), then ffill then bfill.
    - Do not modify input in place; return a new DataFrame; preserve row order.
    - If the user question requests only a summary (keywords: mean, average, median, mode, sum, count, std, variance, correlation, aggregate), skip imputation and simply drop NaNs in required columns.

    User question: {user_prompt}
    Dataframe columns: {all_cols}
    Columns with missing values: {missing_cols}

    ONLY output Python code. No comments or explanations. DO NOT wrap in markdown code blocks.
    """


def build_prompt(
    formatted_schema,
    formatted_kb,
    formatted_natural_query,
    previous_error_message="",
    session_scope=None,
) -> str:
    """Build the main natural-language-to-SQL prompt."""
    example_block = _build_scope_sql_examples(session_scope)
    prompt = f"""
    You are a helpful assistant that translates natural-language questions into SQL.

    Respond with only a single SQL query wrapped in triple backticks like: ```SQL ...the query... ```
    Do NOT include any other text, comments, explanations, or extra SQL examples.
    Use the provided schema and knowledge base only; avoid hallucinations.

    Here is the database schema:\n\n{formatted_schema}\n\n
    Here is the knowledge base:\n\n{formatted_kb}\n\n
    Translate the following query into SQL:\n\n{formatted_natural_query}
        
    Here are a few examples:
    {example_block}
    """
    if previous_error_message:
        prompt += f"""
        The previous failed error message:\n\n{previous_error_message}\n\n
        """
    return prompt


def build_explanation_prompt(formatted_schema, formatted_kb, formatted_natural_query, sql_query):
    """Build a post-SQL explanation prompt grounded in the final SQL."""
    return f"""
    You are explaining a SQL query using the provided schema and knowledge base.

    Respond with only a KB explanation block wrapped in tags:
    <KB_EXPLANATIONS> ... </KB_EXPLANATIONS>

    Rules:
    - Keep the explanation concise and useful.
    - Use short bullet points.
    - Explain relevant terms, score meanings, assumptions, and why the SQL answers the question.
    - If no KB entries are relevant, write: No relevant KB entries.
    - Do not include any text outside the tags.

    User question:
    {formatted_natural_query}

    Final SQL:
    {sql_query}

    Database schema:
    {formatted_schema}

    Knowledge base:
    {formatted_kb}
    """


def extract_sql(response_text):
    """
    Extract the SQL query from a llm response.
    """
    # Use regex to find the SQL code block

    # Try to extract from triple-backtick SQL code block
    pattern = r"```(?:sql|SQL)\s*\n(.*?)\n```"
    match = re.search(pattern, response_text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()

    # Fallback: extract SQL-like statement ending in semicolon
    fallback_pattern = r"(SELECT|INSERT|UPDATE|DELETE|WITH|CREATE|DROP|ALTER)(.*?;)"
    fallback_match = re.search(
        fallback_pattern, response_text, re.DOTALL | re.IGNORECASE)
    if fallback_match:
        return (fallback_match.group(1) + fallback_match.group(2)).strip()

    return None


def execute_sql_query(sql_query, engine, session_scope=None):
    """
    Execute the SQL query against the database and return the results.
    """
    try:
        validate_sql_for_scope(sql_query, session_scope=session_scope)
        with engine.connect() as connection:
            # Execute the query and fetch results
            result = pd.read_sql(sql_query, connection)
        return result
    except Exception as e:
        return f"Error executing query: {e}"


def get_schema_chunks_by_keyword_match(query, schema_retriever, boosted_tables=None, restrict_to_tables=None):
    """Fetch schema chunks using merged keyword, semantic retrieval, and ambiguity boosts."""
    query_lower = query.lower()
    candidate_map = {}
    keyword_match_count = 0
    ambiguity_boost_count = 0

    if not (
        is_ablation_enabled(ABLATION_NO_SCHEMA_KEYWORDS)
        or is_ablation_enabled(ABLATION_NO_ALL_SCHEMA_KEYWORDS)
    ):
        for chunk in ALL_SCHEMA_DOCS:
            table_name = chunk.metadata.get(
                "table") or _extract_table_name_from_schema_doc(chunk)
            if not table_name:
                continue

            keyword_hits, keyword_score = _score_schema_doc_for_query(
                query_lower, chunk)
            if keyword_hits > 0:
                keyword_match_count += 1
                candidate_map[table_name] = {
                    "doc": chunk,
                    "score": float(keyword_score),
                    "keyword_hits": keyword_hits,
                }

    semantic_docs = get_relevant_chunks_by_type(
        query, schema_retriever, type_filter="schema", k=8)
    for rank, doc in enumerate(semantic_docs):
        table_name = doc.metadata.get(
            "table") or _extract_table_name_from_schema_doc(doc)
        if not table_name:
            continue

        semantic_score = max(0.0, 4.0 - (rank * 0.35))
        if table_name not in candidate_map:
            candidate_map[table_name] = {
                "doc": doc,
                "score": 0.0,
                "keyword_hits": 0,
            }
        candidate_map[table_name]["score"] += semantic_score

    ambiguity_bonus = 7.0
    for table_name in dict.fromkeys(boosted_tables or []):
        schema_doc = _get_schema_doc_for_table(table_name)
        if schema_doc is None:
            continue

        canonical_table_name = schema_doc.metadata.get(
            "table") or _extract_table_name_from_schema_doc(schema_doc)
        if not canonical_table_name:
            continue

        if canonical_table_name not in candidate_map:
            candidate_map[canonical_table_name] = {
                "doc": schema_doc,
                "score": 0.0,
                "keyword_hits": 0,
            }
        candidate_map[canonical_table_name]["score"] += ambiguity_bonus
        candidate_map[canonical_table_name]["keyword_hits"] += 1
        ambiguity_boost_count += 1

    if not candidate_map:
        return [], {
            "keyword_match_count": keyword_match_count,
            "semantic_count": len(semantic_docs),
            "ambiguity_boost_count": ambiguity_boost_count,
            "ranked_count": 0,
            "final_count": 0,
            "restricted_count": 0,
        }

    ranked_docs = sorted(
        candidate_map.values(),
        key=lambda item: (item["score"], item["keyword_hits"]),
        reverse=True,
    )

    allowed_tables = dict.fromkeys(restrict_to_tables or [])
    if allowed_tables:
        restricted_ranked_docs = []
        for table_name in allowed_tables:
            schema_doc = _get_schema_doc_for_table(table_name)
            if schema_doc is None:
                continue
            canonical_table_name = schema_doc.metadata.get(
                "table") or _extract_table_name_from_schema_doc(schema_doc)
            if not canonical_table_name:
                continue
            ranked_item = next(
                (item for item in ranked_docs if (
                    item["doc"].metadata.get("table")
                    or _extract_table_name_from_schema_doc(item["doc"])
                ) == canonical_table_name),
                None,
            )
            if ranked_item is not None:
                restricted_ranked_docs.append(ranked_item)
            else:
                restricted_ranked_docs.append({
                    "doc": schema_doc,
                    "score": float(ambiguity_bonus),
                    "keyword_hits": 1,
                })
        top_docs = restricted_ranked_docs
    else:
        top_docs = ranked_docs[:6]
    return [item["doc"].page_content for item in top_docs], {
        "keyword_match_count": keyword_match_count,
        "semantic_count": len(semantic_docs),
        "ambiguity_boost_count": ambiguity_boost_count,
        "ranked_count": len(ranked_docs),
        "final_count": len(top_docs),
        "restricted_count": len(top_docs) if allowed_tables else 0,
    }


def expand_with_references(docs, all_docs_by_id):
    """Append directly referenced KB documents and reverse-linked companions."""
    expanded = docs.copy()
    queue = docs.copy()
    seen_ids = {doc.metadata.get("id") for doc in docs if "id" in doc.metadata}

    reverse_ref_map = {}
    for doc_id, candidate_doc in all_docs_by_id.items():
        for ref_id in candidate_doc.metadata.get("refers_to", []):
            reverse_ref_map.setdefault(ref_id, []).append(doc_id)

    while queue:
        doc = queue.pop(0)
        doc_id = doc.metadata.get("id")
        related_ids = list(doc.metadata.get("refers_to", []))
        if doc_id:
            related_ids.extend(reverse_ref_map.get(doc_id, []))

        for ref_id in related_ids:
            if ref_id not in seen_ids and ref_id in all_docs_by_id:
                referenced_doc = all_docs_by_id[ref_id]
                expanded.append(referenced_doc)
                queue.append(referenced_doc)
                seen_ids.add(ref_id)

    return expanded


def format_schema_chunks(schema_chunks):
    """
    Format schema-related chunks (e.g., table definitions and columns) for inclusion in prompts.
    """
    formatted_schema = ""

    for chunk in schema_chunks:
        lines = chunk.strip().split("\n")
        if not lines:
            continue

        table_name = lines[0].strip()
        column_data = " ".join(lines[1:]).strip()

        formatted_schema += f"- {table_name}\n"

        if column_data:
            columns = column_data.split(
                " - Column: ") if " - Column: " in column_data else column_data.split(",")
            simplified_columns = []

            for column in columns:
                parts = column.strip().split(", ")
                if len(parts) >= 3:
                    column_name = parts[1].strip("'")
                    data_type = parts[2].strip("'")
                    simplified_columns.append(f"{column_name} ({data_type})")
                elif len(parts) == 1 and "(" in parts[0]:
                    simplified_columns.append(parts[0])

            if simplified_columns:
                formatted_schema += "  Columns: " + \
                    ", ".join(simplified_columns) + "\n"

    return formatted_schema.strip()


def format_knowledge_chunks(kb_chunks):
    """
    Format knowledge base chunks (non-schema content) for inclusion in prompts.
    """
    formatted_kb = ""

    for i, chunk in enumerate(kb_chunks, 1):
        formatted_kb += f"\n[Knowledge #{i}]\n{chunk.strip()}\n"

    return formatted_kb.strip()


def save_ground_truth(natural_query, result_df):
    """
    Save the user-approved (query, result) pair to a ground truth file.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.join("./ground_truth", f"ground_truth_{timestamp}.csv")

    # Save query as metadata at the top and then append the result
    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"# Query: {natural_query}\n")
        result_df.to_csv(f, index=False)

    print(f"Ground truth saved to {filename}")


def save_results_to_file(dataframe):
    """Save a dataframe result to a timestamped CSV in the output directory."""
    # Note: we use a timestamp here, so that we don't accidentally override
    # any previously saved files that were given the same filename prefix.
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename_prefix = input("Enter the file name prefix (e.g., results): ")
    filename = os.path.join(OUTPUT_DIR, f"{filename_prefix}_{timestamp}.csv")
    dataframe.to_csv(filename, index=False)
    print(f"Results saved to {filename}")


def debug_mode(dataframe):
    """
    Open an interactive shell with access to the DataFrame.
    """
    print("\nEntering debug mode. You can interact with the DataFrame directly via variable name 'dataframe'.")
    print("Type 'exit()' or Ctrl+D to leave debug mode.\n")
    shell = code.InteractiveConsole(locals={"dataframe": dataframe})
    shell.interact()


def interactive_session_with_llm(engine, session_scope=None):
    """
    Start an interactive session for natural language queries.
    """
    _interactive_session(engine, sql_only=False, session_scope=session_scope)


def interactive_session_sql_only(engine, session_scope=None):
    """
    Start an interactive session for SQL queries.
    """
    _interactive_session(engine, sql_only=True, session_scope=session_scope)


def _interactive_session(engine, sql_only=True, session_scope=None):
    """Run the command-line interaction loop for SQL or natural-language queries."""
    retrieval_context = initialize_retrieval_context(engine, session_scope)
    schema_retriever = retrieval_context["schema_retriever"]
    kb_retriever = retrieval_context["kb_retriever"]

    print("\n\nWelcome to the interactive query session! Type 'exit' to quit.")
    if session_scope:
        print(f"Active dataset scope: {session_scope}")
    print(
        "Scoped retrieval context: "
        f"schema_docs={len(ALL_SCHEMA_DOCS)}, "
        f"kb_docs={len(ALL_KNOWLEDGE_DOCS)}"
    )
    while True:

        results = None
        if sql_only:
            print("For your reference, here is the schema description:")
            print(schema_description)
            sql_query = input("\nEnter a SQL query: ")
            if sql_query.lower() == "exit":
                print("Goodbye!")
                break

            # Process the SQL query
            results = execute_sql_query(sql_query, engine, session_scope=session_scope)

        else:
            natural_query = input("\nAsk a question: ")
            if natural_query.lower() == "exit":
                print("Goodbye!")
                break

            # Whether to impute N/A values if any
            # impute = input(
            #     "\nDo you want to impute N/A values if there's any? (yes/no): ").strip().lower()
            impute = "no"

            results, sql_query, explanation = process_natural_query(
                natural_query, engine, schema_retriever, kb_retriever, impute, session_scope=session_scope)

        # Display results
        if isinstance(results, pd.DataFrame):
            print("Query Results:")
            print(results)
            print()
            print("Explanation:")
            print(explanation)

            # Ask for user feedback
            feedback = input(
                "\nIs this result correct? (yes/no): ").strip().lower()
            if feedback == "yes" and not sql_only:
                save_ground_truth(natural_query, results)

            action_choice = input(
                "\nWould you like to visualize the data (enter 'v'), enter debug mode to interact with the results ('d'), save to a local file ('s') or 'c' to continue?").lower()
            if action_choice == "v":
                # visualize_data(results)
                auto_visualize_data(natural_query.lower(), sql_query, results)
            elif action_choice == "d":
                debug_mode(results)
            elif action_choice == "s":
                save_results_to_file(results)

        else:
            print(results)


def create_shared_db_engine():
    """Create an engine pointing at the configured shared database."""
    # Connect to an existing shared DB (set DB configs in beginning of file accordingly)
    return create_engine(DB_CONNECTION_STRING)


def create_in_memory_db_engine(session_scope=SCOPE_BUMP):
    """Create and populate an in-memory SQLite database for local querying."""
    # Use an in-memory SQLite database
    engine = create_engine("sqlite:///:memory:", echo=False)

    # Create all the tables
    create_tables_for_scope(engine, session_scope)

    # Load the selected dataset
    load_data_for_scope(engine, session_scope)

    # Load all the stress & recovery data
    # load_all_stressrecov_data(engine)

    return engine


def parse_args():
    """Parse command-line arguments for database selection."""
    parser = argparse.ArgumentParser(
        description="Run the updated querying system interactively."
    )
    parser.add_argument(
        "--shared-db",
        action="store_true",
        help="Use the shared DB connection instead of the in-memory SQLite database.",
    )
    parser.add_argument(
        "--dataset-scope",
        help="Registered dataset name/alias or cross-dataset.",
    )
    return parser.parse_args()


def main():
    """Initialize the database engine and launch the interactive session."""
    args = parse_args()
    session_scope = normalize_scope_name(
        args.dataset_scope) if args.dataset_scope else prompt_for_session_scope()

    # Create database engine either shared DB or in-memory SQLite.
    if args.shared_db:
        engine = create_shared_db_engine()
    else:
        engine = create_in_memory_db_engine(session_scope)

    # start interactive shell
    # interactive_session_sql_only(engine)
    interactive_session_with_llm(engine, session_scope=session_scope)


if __name__ == "__main__":
    main()
