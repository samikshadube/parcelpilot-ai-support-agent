---
description: Build/refresh the document vector store from data/raw PDFs
---

Run (or, if it doesn't exist yet, build) the document ingestion pipeline described in specifications.md §3:

1. Confirm `data/raw/` contains the 6 source PDFs (`01_Support_Policy_v3_CURRENT.pdf` … `06_LumenWorks_Service_Agreement.pdf`). If missing, stop and tell me — don't fabricate placeholder documents.
2. Extract → chunk → embed each PDF, tagging each chunk with `source_file`, `doc_type`, `version`, `status` (current/deprecated), and `customer_scope` (null for general docs, the account id for `05_Northstar...`/`06_LumenWorks...`).
3. Persist to the Chroma store under `data/processed/`. Make re-runs idempotent (content-hash source files, skip unchanged).
4. Print a short summary: files processed, chunk counts, any metadata that had to be inferred vs. read directly from filenames/content.

If the ingestion module (`src/ingest/`) doesn't exist yet, build it per specifications.md §3 and the tool-contract skill's conventions, then run it.

$ARGUMENTS
