from enum import Enum
from pathlib import Path


class LlmSource(Enum):
    ANTROPIC_API = 1
    GEMINI_API = 2
    OPENAI_API = 3
    TOGETHER_API = 4
    LOCAL = 5


LLM_SOURCE = LlmSource.ANTROPIC_API
EMBEDDING_SOURCE = LlmSource.GEMINI_API

# Public API Models
ANTROPIC_API_MODEL = "claude-opus-4-1-20250805"
GEMINI_API_MODEL = "gemini-3-pro-preview"
OPENAI_API_MODEL = "gpt-5.2"
TOGETHER_API_MODEL = "moonshotai/Kimi-K2.5"

# Local LLM Model
MODEL_PATH = "./model-weights/Mistral-7B-Instruct-v0.1"
CACHE_DIR = "./cache"
OUTPUT_DIR = "output"

# Local Embedding Model
EMBEDDING_MODEL_PATH = "/model-weights/bge-m3"

GROUND_TRUTH_PATH = "./ground_truth/ground_truths.json"

PROJECT_ROOT = Path(__file__).resolve().parent
SHARED_SQLITE_PATH = PROJECT_ROOT / "wearable_shared.sqlite"

DB_CONNECTION_STRING = f"sqlite:///{SHARED_SQLITE_PATH}"

# Max number of re-prompt retries on sql-related errors
MAX_REPROMPT_RETRY = 1
