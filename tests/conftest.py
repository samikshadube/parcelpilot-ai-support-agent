"""Global pytest fixtures and path configuration."""

import sys
from pathlib import Path
import pytest

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.db import init_db
from src.ingest.data_loader import ingest_structured_data
from src.ingest.doc_loader import ingest_documents


@pytest.fixture(scope="session", autouse=True)
def initialize_system():
    """Initialize database and vector embeddings before running tests."""
    init_db()
    ingest_structured_data()
    ingest_documents()
