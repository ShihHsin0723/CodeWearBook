"""Explicit retrieval controls; defaults preserve the existing query pipeline."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievalSettings:
    chunk_size: int = 400
    chunk_overlap: int = 50
    schema_keep: int = 8
    schema_search: int = 32
    kb_keep: int = 5
    kb_search: int = 40

    def __post_init__(self):
        for name in ("chunk_size", "schema_keep", "schema_search", "kb_keep", "kb_search"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if type(self.chunk_overlap) is not int or not 0 <= self.chunk_overlap < self.chunk_size:
            raise ValueError("chunk_overlap must be nonnegative and smaller than chunk_size")
        if self.schema_keep > self.schema_search or self.kb_keep > self.kb_search:
            raise ValueError("Retained counts cannot exceed candidate search counts")
