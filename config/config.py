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
    qdrant_host = "qdrant"
    qdrant_port = "6333"
    falkordb_host = "falkordb"
    falkordb_port = "6379"
else:
    # Locally, using relative paths
    base_path = "."
    postgres_host = os.getenv("POSTGRES_HOST", "localhost")
    postgres_port = os.getenv("POSTGRES_PORT", "5433")
    qdrant_host = os.getenv("QDRANT_HOST", "localhost")
    qdrant_port = os.getenv("QDRANT_PORT", "6333")
    falkordb_host = os.getenv("FALKORDB_HOST", "localhost")
    falkordb_port = os.getenv("FALKORDB_PORT", "6379")


# PostgreSQL configuration
POSTGRES_DB = os.getenv("POSTGRES_DB", "mediatech")
POSTGRES_USER = os.getenv("POSTGRES_USER", "user")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "password")
POSTGRES_HOST = postgres_host
POSTGRES_PORT = postgres_port

# Qdrant configuration
QDRANT_HOST = qdrant_host
QDRANT_PORT = int(qdrant_port)
QDRANT_URL = f"http://{qdrant_host}:{qdrant_port}"
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", None)

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
    "service_public": [
        "service_public_part",
        "service_public_pro",
    ],
    "travail_emploi": ["travail_emploi"],
    "legi": ["legi"],
    "jade": ["jade"],
    "bofip": ["bofip"],
    "cnil": ["cnil"],
    "state_administrations_directory": ["state_administrations_directory"],
    "local_administrations_directory": ["local_administrations_directory"],
    "constit": ["constit"],
    "dole": ["dole"],
    "data_gouv_datasets_catalog": ["data_gouv_datasets_catalog"],
}

# Data folders
CNIL_DATA_FOLDER = get_env_variable_path("CNIL_DATA_FOLDER", "data/unprocessed/cnil")
CONSTIT_DATA_FOLDER = get_env_variable_path(
    "CONSTIT_DATA_FOLDER", "data/unprocessed/constit"
)
LOCAL_ADMINISTRATIONS_DIRECTORY_FOLDER = get_env_variable_path(
    "LOCAL_ADMINISTRATIONS_DIRECTORY_FOLDER",
    "data/unprocessed/local_administrations_directory",
)
STATE_ADMINISTRATIONS_DIRECTORY_FOLDER = get_env_variable_path(
    "STATE_ADMINISTRATIONS_DIRECTORY_FOLDER",
    "data/unprocessed/state_administrations_directory",
)
DOLE_DATA_FOLDER = get_env_variable_path("DOLE_DATA_FOLDER", "data/unprocessed/dole")
JADE_DATA_FOLDER = get_env_variable_path("JADE_DATA_FOLDER", "data/unprocessed/jade")
BOFIP_DATA_FOLDER = get_env_variable_path("BOFIP_DATA_FOLDER", "data/unprocessed/bofip")
LEGI_DATA_FOLDER = get_env_variable_path("LEGI_DATA_FOLDER", "data/unprocessed/legi")
TRAVAIL_EMPLOI_DATA_FOLDER = get_env_variable_path(
    "TRAVAIL_EMPLOI_DATA_FOLDER", "data/unprocessed/travail_emploi"
)
SERVICE_PUBLIC_PRO_DATA_FOLDER = get_env_variable_path(
    "SERVICE_PUBLIC_PRO_DATA_FOLDER",
    "data/unprocessed/service_public_pro",
)
SERVICE_PUBLIC_PART_DATA_FOLDER = get_env_variable_path(
    "SERVICE_PUBLIC_PART_DATA_FOLDER",
    "data/unprocessed/service_public_part",
)
DATA_GOUV_DATASETS_CATALOG_DATA_FOLDER = get_env_variable_path(
    "DATA_GOUV_DATASETS_CATALOG_DATA_FOLDER",
    "data/unprocessed/data_gouv_datasets_catalog",
)

# LLM API configuration (OpenRouter)
API_URL = os.getenv("API_URL", "https://openrouter.ai/api/v1")
API_KEY = os.getenv("API_KEY", "your_api_key_here")
LLM_MODEL = os.getenv("LLM_MODEL", "openrouter/hunter-alpha")

# Embedding model configuration (downloaded and run locally via HuggingFace)
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "louisbrulenaudet/lemone-embed-pro")

# Hugging Face configuration
HF_TOKEN = os.getenv("HF_TOKEN", "your_hugging_face_token_here")
