"""Test scaffolding.

The crossreference package ultimately imports ``database.database_manage``
which pulls in ``utils`` (itself importing torch / sentence-transformers).
To keep unit tests lightweight we pre-register a stub ``utils`` module that
exposes just the symbols the database and crossreference modules need at
import time. Tests that exercise real SQL behaviour must override this via
``monkeypatch`` on ``database.database_manage.get_connection``.
"""

import sys
import types


def _install_utils_stub():
    if "utils" in sys.modules:
        return
    mod = types.ModuleType("utils")
    mod.format_model_name = lambda m: str(m).replace("/", "_")
    mod.format_to_table_name = lambda m: str(m).lower()
    mod.generate_embeddings_with_retry = lambda data, model: [[0.0]]
    mod._extract_distinct_data = lambda *args, **kwargs: []
    mod.make_chunks = lambda **kwargs: []
    mod.get_recommended_chunk_size = lambda *args, **kwargs: 512
    mod.get_recommended_chunk_overlap = lambda *args, **kwargs: 50
    mod.embed_texts_with_retry = lambda *args, **kwargs: []
    mod.format_text_for_embedding = lambda *args, **kwargs: ""
    mod.generate_embeddings = lambda *args, **kwargs: []
    mod.get_nonfinite_fallback_model = lambda *args, **kwargs: ""
    mod.correct_wrong_column_contents = lambda *args, **kwargs: None
    mod.doc_to_chunk = lambda *args, **kwargs: []
    mod.download_file = lambda *args, **kwargs: None
    mod.extract_and_remove_tar_file = lambda *args, **kwargs: None
    mod.extract_and_remove_tar_files = lambda *args, **kwargs: None
    mod.file_sha256 = lambda *args, **kwargs: ""
    mod.format_subtitles = lambda *args, **kwargs: ""
    mod.load_config = lambda *args, **kwargs: {}
    mod.load_data_history = lambda *args, **kwargs: {}
    mod.remove_file = lambda *args, **kwargs: None
    mod.remove_folder = lambda *args, **kwargs: None
    mod._make_schedule = lambda *args, **kwargs: None
    mod.CheckpointManager = object
    mod.HuggingFace = object
    mod.PerfTelemetry = object
    mod.upload_dataset_task = lambda *args, **kwargs: None
    sys.modules["utils"] = mod


_install_utils_stub()
