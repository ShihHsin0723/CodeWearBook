"""Read-only SQL and dataset-scope isolation checks."""

import re

from dataset_registry import SCOPE_CROSS, get_dataset_config, get_raw_table_prefixes


SAFE_TABLE_VALUED_FUNCTIONS = {"json_each", "json_tree"}
MUTATING_SQL_KEYWORDS = {
    "alter", "attach", "create", "delete", "detach", "drop", "insert",
    "pragma", "reindex", "replace", "update", "vacuum",
}
IDENTIFIER = r'(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_]*)'


def _strip_literals_and_comments(sql: str) -> str:
    without_block_comments = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    without_line_comments = re.sub(r"--[^\n]*", " ", without_block_comments)
    return re.sub(r"'(?:''|[^'])*'", "''", without_line_comments)


def _normalize_identifier(identifier: str) -> str:
    value = identifier.strip()
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("`") and value.endswith("`")
    ) or (value.startswith("[") and value.endswith("]")):
        value = value[1:-1]
    return value.lower()


def extract_table_references(sql: str):
    sanitized = _strip_literals_and_comments(sql)
    reference_pattern = re.compile(
        rf"\b(?:from|join)\s+({IDENTIFIER}(?:\s*\.\s*{IDENTIFIER})?)",
        re.IGNORECASE,
    )
    references = set()
    for match in reference_pattern.finditer(sanitized):
        raw_name = match.group(1)
        last_component = re.split(r"\s*\.\s*", raw_name)[-1]
        references.add(_normalize_identifier(last_component))
    return references


def _extract_cte_names(sql: str):
    sanitized = _strip_literals_and_comments(sql)
    pattern = re.compile(
        rf"(?:\bwith\b|,)\s*({IDENTIFIER})\s+as\s*\(",
        re.IGNORECASE,
    )
    return {_normalize_identifier(match.group(1)) for match in pattern.finditer(sanitized)}


def validate_sql_for_scope(sql_query, session_scope=None) -> None:
    if not sql_query:
        return

    sanitized = _strip_literals_and_comments(sql_query).strip()
    if not re.match(r"^(?:select|with)\b", sanitized, re.IGNORECASE):
        raise ValueError("Only read-only SELECT queries are allowed.")

    keyword_pattern = r"\b(?:" + "|".join(sorted(MUTATING_SQL_KEYWORDS)) + r")\b"
    mutation = re.search(keyword_pattern, sanitized, re.IGNORECASE)
    if mutation:
        raise ValueError(f"Read-only queries cannot use {mutation.group(0).upper()}.")

    statements = [part.strip() for part in sanitized.split(";") if part.strip()]
    if len(statements) != 1:
        raise ValueError("Exactly one SQL statement is allowed.")

    # Implicit comma joins are deliberately unsupported because they make table
    # extraction and scope enforcement less reliable. Explicit JOIN is clearer.
    if re.search(
        rf"\bfrom\s+{IDENTIFIER}(?:\s*\.\s*{IDENTIFIER})?"
        rf"(?:\s+(?:as\s+)?{IDENTIFIER})?\s*,",
        sanitized,
        re.IGNORECASE,
    ):
        raise ValueError("Use explicit JOIN syntax instead of comma-separated tables.")

    if not session_scope:
        return

    plugin = get_dataset_config(session_scope)
    allowed_prefix = "wearable_" if plugin.name == SCOPE_CROSS else plugin.table_prefix
    cte_names = _extract_cte_names(sanitized)
    references = extract_table_references(sanitized)
    physical_references = references - cte_names - SAFE_TABLE_VALUED_FUNCTIONS
    disallowed = sorted(
        table for table in physical_references if not table.startswith(allowed_prefix)
    )
    if disallowed:
        raise ValueError(
            f"Scope {plugin.name} only allows tables beginning with "
            f"{allowed_prefix!r}. Disallowed tables: {', '.join(disallowed)}."
        )

    if plugin.name == SCOPE_CROSS and not any(
        table.startswith("wearable_") for table in physical_references
    ):
        raise ValueError("Cross-dataset queries must reference a wearable_* view.")
