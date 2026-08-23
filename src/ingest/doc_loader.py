"""Document ingestion pipeline: chunks and embeds PDFs into a local Chroma vector store."""

import glob
import hashlib
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import chromadb
import pypdf
from src.config import CHROMA_DIR, DB_PATH, RAW_DATA_DIR
from src.db import get_db_connection
from src.models import AuthorityTier, DocMetadata, DocStatus


COLLECTION_NAME = "parcelpilot_docs"


def compute_file_hash(file_path: Path) -> str:
    """Compute SHA-256 hash of a file for change detection."""
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            sha.update(chunk)
    return sha.hexdigest()


def get_account_contract_mapping(db_path: Path = DB_PATH) -> Dict[str, str]:
    """Dynamically query accounts table to map contract filename to account_id."""
    mapping = {}
    if not db_path.exists():
        return mapping
    try:
        conn = get_db_connection(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT account_id, contract_file FROM accounts WHERE contract_file IS NOT NULL")
        for row in cursor.fetchall():
            if row["contract_file"]:
                mapping[row["contract_file"].strip()] = row["account_id"].strip()
        conn.close()
    except Exception:
        pass
    return mapping


def infer_doc_metadata(file_name: str, full_text: str, account_contract_map: Dict[str, str]) -> DocMetadata:
    """Infer document metadata, type, status, customer scope, and authority tier."""
    lower_name = file_name.lower()
    lower_text = full_text.lower()

    # Customer scope from contract mapping or text
    customer_scope = account_contract_map.get(file_name)
    if not customer_scope:
        # Check if text specifies an account ID pattern (e.g. Account: ACCT-001)
        acct_match = re.search(r"account:\s*(acct-\d+)", lower_text)
        if acct_match:
            customer_scope = acct_match.group(1).upper()

    # Determine status
    if "deprecated" in lower_name or "deprecated" in lower_text:
        status = DocStatus.DEPRECATED.value
    else:
        status = DocStatus.CURRENT.value

    # Determine doc type, title, and authority tier
    if customer_scope:
        doc_type = "customer_agreement"
        authority_tier = AuthorityTier.CUSTOMER_AGREEMENT.value
        title = file_name.replace(".pdf", "").replace("_", " ")
        version = "contract_2026"
    elif "deprecated" in lower_name or status == DocStatus.DEPRECATED.value:
        doc_type = "deprecated_policy"
        authority_tier = AuthorityTier.DEPRECATED_POLICY.value
        title = "Deprecated Support Policy"
        version = "v2"
    elif "sop" in lower_name or "cancellation" in lower_name:
        doc_type = "sop"
        authority_tier = AuthorityTier.CURRENT_SOP_POLICY.value
        title = "Cancellation & Service Credit SOP"
        version = "v4"
    elif "product_operations" in lower_name or "known_issues" in lower_name:
        doc_type = "product_operations"
        authority_tier = AuthorityTier.PRODUCT_OPS_GUIDE.value
        title = "Product Operations Guide & Known Issues"
        version = "current"
    elif "support_policy" in lower_name:
        doc_type = "support_policy"
        authority_tier = AuthorityTier.CURRENT_SOP_POLICY.value
        title = "Support Policy"
        version = "v3"
    else:
        doc_type = "general_policy"
        authority_tier = AuthorityTier.CURRENT_SOP_POLICY.value
        title = file_name.replace(".pdf", "").replace("_", " ")
        version = "current"

    return DocMetadata(
        source_file=file_name,
        doc_title=title,
        doc_type=doc_type,
        version=version,
        status=status,
        customer_scope=customer_scope,
        authority_tier=authority_tier,
    )


def extract_text_from_pdf(pdf_path: Path) -> str:
    """Extract clean text from a PDF file."""
    reader = pypdf.PdfReader(str(pdf_path))
    pages_text = []
    for page in reader.pages:
        txt = page.extract_text()
        if txt:
            pages_text.append(txt)
    return "\n\n".join(pages_text)


def chunk_document_text(text: str, max_chunk_chars: int = 1000, overlap_chars: int = 200) -> List[str]:
    """Chunk text recursively on section headers and paragraph breaks."""
    # Normalize excessive newlines/spaces
    text = re.sub(r"\n{3,}", "\n\n", text.strip())

    # Split into sections based on numbered headers or double newlines
    sections = re.split(r"\n(?=[0-9]+\.\s+[A-Z])", text)
    chunks: List[str] = []

    for section in sections:
        section = section.strip()
        if not section:
            continue
        if len(section) <= max_chunk_chars:
            chunks.append(section)
        else:
            # Sub-chunk by paragraphs or bullet points
            paragraphs = section.split("\n\n")
            current_chunk = ""
            for p in paragraphs:
                p = p.strip()
                if not p:
                    continue
                if len(current_chunk) + len(p) + 2 <= max_chunk_chars:
                    current_chunk = f"{current_chunk}\n\n{p}".strip()
                else:
                    if current_chunk:
                        chunks.append(current_chunk)
                    current_chunk = p
            if current_chunk:
                chunks.append(current_chunk)

    return chunks


def ingest_documents(
    raw_dir: Path = RAW_DATA_DIR,
    chroma_dir: Path = CHROMA_DIR,
    db_path: Path = DB_PATH,
    force: bool = False
) -> Dict[str, int]:
    """Extract, chunk, and embed all PDFs into ChromaDB."""
    pdf_files = sorted(raw_dir.glob("*.pdf"))
    if not pdf_files:
        raise FileNotFoundError(f"No PDF files found in {raw_dir}")

    account_map = get_account_contract_mapping(db_path)
    chroma_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(chroma_dir))

    # Reset or get collection
    if force:
        try:
            client.delete_collection(name=COLLECTION_NAME)
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"description": "ParcelPilot knowledge base documents"}
    )

    summary: Dict[str, int] = {}
    documents: List[str] = []
    ids: List[str] = []
    metadatas: List[Dict] = []

    for pdf_path in pdf_files:
        file_name = pdf_path.name
        raw_text = extract_text_from_pdf(pdf_path)
        meta = infer_doc_metadata(file_name, raw_text, account_map)
        chunks = chunk_document_text(raw_text)

        summary[file_name] = len(chunks)

        for idx, chunk in enumerate(chunks):
            chunk_id = f"{file_name}_chunk_{idx}"
            # Embed metadata with document for filtering
            meta_dict = {
                "source_file": meta.source_file,
                "doc_title": meta.doc_title,
                "doc_type": meta.doc_type,
                "version": meta.version,
                "status": meta.status,
                "customer_scope": meta.customer_scope if meta.customer_scope else "general",
                "authority_tier": meta.authority_tier,
                "chunk_index": idx,
            }
            documents.append(chunk)
            ids.append(chunk_id)
            metadatas.append(meta_dict)

    if documents:
        # Upsert into Chroma
        collection.upsert(
            documents=documents,
            ids=ids,
            metadatas=metadatas
        )

    return summary


if __name__ == "__main__":
    res = ingest_documents(force=True)
    print("Document ingestion complete:")
    total = sum(res.values())
    for f, count in res.items():
        print(f"  - {f}: {count} chunks")
    print(f"Total chunks stored: {total}")
