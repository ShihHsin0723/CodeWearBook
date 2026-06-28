import faiss
import numpy as np
import requests
import copy
from functools import lru_cache
from typing import List, Tuple
from langchain_community.vectorstores import FAISS
from langchain.schema import Document
from langchain.embeddings.base import Embeddings
from langchain.text_splitter import CharacterTextSplitter
from sentence_transformers import SentenceTransformer
from config import EMBEDDING_MODEL_PATH, EMBEDDING_SOURCE, LlmSource
from get_llm_api_keys import get_api_keys


@lru_cache(maxsize=1)
def _load_api_keys():
    return get_api_keys()


def _get_api_key(key_name: str):
    return _load_api_keys().get(key_name)


def load_embedding_model(device="cuda"):
    if EMBEDDING_SOURCE == LlmSource.LOCAL:
        return SentenceTransformer(EMBEDDING_MODEL_PATH).to(device)
    else:
        return APIEmbeddings(EMBEDDING_SOURCE)


class LocalSentenceTransformerEmbeddings(Embeddings):
    def __init__(self, model):
        self.model = model

    def embed_documents(self, texts):
        return self.model.encode(texts, show_progress_bar=False).tolist()

    def embed_query(self, text):
        return self.model.encode([text])[0].tolist()


def build_knowledge_index(documents, embedding_model, chunk_size=400, chunk_overlap=50, k=12):
    enriched_documents = []
    for idx, doc in enumerate(documents):
        enriched_doc = copy.deepcopy(doc)
        metadata = dict(enriched_doc.metadata or {})
        metadata["_parent_doc_key"] = metadata.get("id") or metadata.get("table") or f"doc_{idx}"
        metadata["_full_page_content"] = enriched_doc.page_content
        enriched_doc.metadata = metadata
        enriched_documents.append(enriched_doc)

    splitter = CharacterTextSplitter(
        chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    chunks = splitter.split_documents(enriched_documents)

    # Wrap if model isn't already LangChain-compatible
    if not hasattr(embedding_model, "embed_documents"):
        embedding_model = LocalSentenceTransformerEmbeddings(embedding_model)

    vector_store = FAISS.from_documents(chunks, embedding_model)
    retriever = vector_store.as_retriever(
        search_type="similarity",
        search_kwargs={"k": k},
    )

    return retriever, vector_store


def _restore_full_documents(docs):
    restored_docs = []
    seen_keys = set()

    for doc in docs:
        metadata = dict(doc.metadata or {})
        parent_key = metadata.get("_parent_doc_key") or metadata.get("id") or metadata.get("table")
        full_page_content = metadata.get("_full_page_content", doc.page_content)

        if parent_key in seen_keys:
            continue
        seen_keys.add(parent_key)

        restored_metadata = {
            key: value
            for key, value in metadata.items()
            if key not in {"_full_page_content"}
        }
        restored_docs.append(Document(page_content=full_page_content, metadata=restored_metadata))

    return restored_docs


def get_relevant_chunks_by_type(query, retriever, type_filter=None, k=2, return_full_documents=False):
    # Over-fetch before type filtering so schema/rule retrieval is not capped by
    # the retriever's default top-k across all document types.
    search_k = max(k * 4, 12)

    if hasattr(retriever, "vectorstore"):
        retrieved_docs = retriever.vectorstore.similarity_search(query, k=search_k)
    else:
        original_search_kwargs = dict(getattr(retriever, "search_kwargs", {}) or {})
        retriever.search_kwargs = {**original_search_kwargs, "k": search_k}
        try:
            retrieved_docs = retriever.invoke(query)
        finally:
            retriever.search_kwargs = original_search_kwargs

    if type_filter:
        retrieved_docs = [
            doc for doc in retrieved_docs if doc.metadata.get("type") == type_filter]

    else:
        retrieved_docs = [
            doc for doc in retrieved_docs if doc.metadata.get("type") != "schema"]

    if return_full_documents:
        retrieved_docs = _restore_full_documents(retrieved_docs)

    return retrieved_docs[:k]


def _batch(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i+size]


class APIEmbeddings(Embeddings):
    """LangChain-compatible wrapper for public embedding APIs"""

    def __init__(self, embedding_source):
        self.embedding_source = embedding_source

    def embed_documents(self, texts):
        """Embed a list of documents with provider-friendly batching."""
        if self.embedding_source == LlmSource.GEMINI_API:
            batch_size = 100  # Gemini limit
        else:
            batch_size = 512  # safe default for OpenAI; API accepts large lists anyway

        out = []
        for chunk in _batch(texts, batch_size):
            out.extend(self._get_embeddings(chunk))
        return out

    def embed_query(self, text):
        """Embed a single query"""
        embeddings = self._get_embeddings([text])
        return embeddings[0] if embeddings else []

    def _get_embeddings(self, texts):
        """Route to appropriate API based on source"""
        if self.embedding_source == LlmSource.OPENAI_API:
            return self._call_openai_embeddings(texts)
        elif self.embedding_source == LlmSource.GEMINI_API:
            return self._call_gemini_embeddings(texts)
        else:
            raise ValueError(
                f"Unsupported embedding source: {self.embedding_source}")

    def _call_openai_embeddings(self, texts):
        openai_api_key = _get_api_key('openai_api_key')
        if not openai_api_key:
            raise ValueError("OPENAI_API_KEY is missing")

        url = "https://api.openai.com/v1/embeddings"
        headers = {
            "Authorization": f"Bearer {openai_api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": "text-embedding-3-large",
            "input": texts
        }

        response = requests.post(url, headers=headers, json=payload)

        try:
            response_json = response.json()
        except Exception as e:
            raise RuntimeError(
                f"Failed to parse JSON from OpenAI API: {e}\nRaw response: {response.text}")

        if "data" not in response_json:
            raise RuntimeError(
                f"Invalid response from OpenAI API:\n{response_json}")

        return [item["embedding"] for item in response_json["data"]]

    def _call_gemini_embeddings(self, texts):
        gemini_api_key = _get_api_key('gemini_api_key')
        if not gemini_api_key:
            raise ValueError("GEMINI_API_KEY is missing")

        # Gemini embedding API endpoint
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:batchEmbedContents?key={gemini_api_key}"

        headers = {
            "Content-Type": "application/json"
        }

        # Format requests for each text
        requests_data = []
        for text in texts:
            requests_data.append({
                "model": "models/gemini-embedding-001",
                "content": {
                    "parts": [{"text": text}]
                }
            })

        payload = {
            "requests": requests_data
        }

        response = requests.post(url, headers=headers, json=payload)

        try:
            response_json = response.json()
        except Exception as e:
            raise RuntimeError(
                f"Failed to parse JSON from Gemini API: {e}\nRaw response: {response.text}")

        if "embeddings" not in response_json:
            raise RuntimeError(
                f"Invalid response from Gemini API:\n{response_json}")

        return [embedding["values"] for embedding in response_json["embeddings"]]
