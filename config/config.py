import os

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Detect the environment (Docker or local)
if os.getenv("RUNNING_IN_DOCKER", "false").lower() == "true":
    # In Docker, using absolute paths
    base_path = "/tmp/mediatech"
    postgres_host = "postgres"
    postgres_port = "5432"
    falkordb_host = "falkordb"
    falkordb_port = "6379"
else:
    # Locally, using relative paths
    base_path = "."
    postgres_host = os.getenv("POSTGRES_HOST", "localhost")
    postgres_port = os.getenv("POSTGRES_PORT", "5433")
    falkordb_host = os.getenv("FALKORDB_HOST", "localhost")
    falkordb_port = os.getenv("FALKORDB_PORT", "6379")


# PostgreSQL configuration
POSTGRES_DB = os.getenv("POSTGRES_DB", "mediatech")
POSTGRES_USER = os.getenv("POSTGRES_USER", "user")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "password")
POSTGRES_HOST = postgres_host
POSTGRES_PORT = postgres_port

# FalkorDB configuration
FALKORDB_HOST = falkordb_host
FALKORDB_PORT = int(falkordb_port)
FALKORDB_PASSWORD = os.getenv("FALKORDB_PASSWORD", None)
FALKORDB_GRAPH_NAME = os.getenv("FALKORDB_GRAPH_NAME", "frenchadmin")

BASE_PATH = base_path

# Paths for configurations and data history
config_file_path = os.path.join(BASE_PATH, "config", "data_config.json")
data_history_path = os.path.join(BASE_PATH, "config", "data_history.json")

# Export folders
parquet_files_folder = os.path.join(BASE_PATH, "data", "parquet")


def get_env_variable_path(var_name: str, default_value: str = None):
    """
    Get an environment variable with a default value and construct its absolute path.

    Args:
        var_name (str): The name of the environment variable.
        default_value: The value to return if the variable is not set.

    Returns:
        str: The value of the environment variable or the default value.
    """
    path = os.getenv(var_name, default_value)
    return os.path.join(BASE_PATH, path)


# Data source mapping
SOURCE_MAP = {
    "legi": ["legi"],
    "jade": ["jade"],
    "bofip": ["bofip"],
}

# Data folders
JADE_DATA_FOLDER = get_env_variable_path("JADE_DATA_FOLDER", "data/unprocessed/jade")
BOFIP_DATA_FOLDER = get_env_variable_path("BOFIP_DATA_FOLDER", "data/unprocessed/bofip")
LEGI_DATA_FOLDER = get_env_variable_path("LEGI_DATA_FOLDER", "data/unprocessed/legi")

# LLM API configuration (OpenRouter)
API_URL = os.getenv("API_URL", "https://openrouter.ai/api/v1")
API_KEY = os.getenv("API_KEY", "your_api_key_here")
LLM_MODEL = os.getenv("LLM_MODEL", "openrouter/hunter-alpha")

# Embedding model configuration (downloaded and run locally via HuggingFace)
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "louisbrulenaudet/lemone-embed-pro")

# Hugging Face configuration
HF_TOKEN = os.getenv("HF_TOKEN", "your_hugging_face_token_here")


def _get_env_bool(var_name: str, default: bool) -> bool:
    """Read a boolean environment variable with permissive parsing."""
    value = os.getenv(var_name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# Optimization feature flags
ENABLE_BATCH_EMBEDDING = _get_env_bool("ENABLE_BATCH_EMBEDDING", True)
ENABLE_FAST_DB_INSERT = _get_env_bool("ENABLE_FAST_DB_INSERT", True)
ENABLE_BATCH_GRAPH_UPSERT = _get_env_bool("ENABLE_BATCH_GRAPH_UPSERT", True)
ENABLE_PARALLEL_PROCESSING = _get_env_bool("ENABLE_PARALLEL_PROCESSING", False)
ENABLE_PERF_TELEMETRY = _get_env_bool("ENABLE_PERF_TELEMETRY", True)

# Embedding tuning
EMBEDDING_BATCH_MAX_SIZE = int(os.getenv("EMBEDDING_BATCH_MAX_SIZE", "64"))
EMBEDDING_RETRY_ATTEMPTS = int(os.getenv("EMBEDDING_RETRY_ATTEMPTS", "5"))
EMBEDDING_ENCODE_BATCH_SIZE = int(os.getenv("EMBEDDING_ENCODE_BATCH_SIZE", "8"))
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "7500"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "500"))

# PostgreSQL insert tuning
FAST_DB_INSERT_PAGE_SIZE = int(os.getenv("FAST_DB_INSERT_PAGE_SIZE", "1000"))

# Parallel processing tuning
MAX_WORKERS = int(os.getenv("MAX_WORKERS", str(max(1, (os.cpu_count() or 2) // 2))))
BATCH_SIZE_DOCS = int(os.getenv("BATCH_SIZE_DOCS", "32"))
WRITE_CONCURRENCY = int(os.getenv("WRITE_CONCURRENCY", "1"))

# Telemetry and profiling
ENABLE_CPROFILE = _get_env_bool("ENABLE_CPROFILE", False)
ENABLE_TRACEMALLOC = _get_env_bool("ENABLE_TRACEMALLOC", False)
PERF_REPORTS_DIR = os.getenv("PERF_REPORTS_DIR", os.path.join(BASE_PATH, "data", "perf_reports"))
