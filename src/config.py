"""Configuration constants and path definitions for ParcelPilot AI."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
PROCESSED_DATA_DIR = DATA_DIR / "processed"
DB_PATH = PROCESSED_DATA_DIR / "parcelpilot.db"
CHROMA_DIR = PROCESSED_DATA_DIR / "chroma_db"

# Ensure directories exist
PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)
RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)

# Default models
DEFAULT_ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-7-sonnet-20250219")
FAST_ANTHROPIC_MODEL = os.getenv("ANTHROPIC_FAST_MODEL", "claude-3-5-haiku-20241022")
DEFAULT_GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

