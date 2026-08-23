---
description: Load ParcelPilot_Assessment_Data.xlsx into SQLite, including the snapshot-time meta row
---

Run (or build, if it doesn't exist yet) the structured-data ingestion pipeline described in specifications.md §3:

1. Confirm `data/raw/ParcelPilot_Assessment_Data.xlsx` exists. If missing, stop and tell me.
2. Read the workbook's README sheet first and extract the stated dataset snapshot time — store it in a `meta` table in SQLite. This is the fixed "now" for all time-based logic; flag clearly if the README sheet doesn't exist or the snapshot time isn't obviously stated.
3. Load every other sheet into a matching SQLite table under `data/processed/`, preserving column names/types sensibly.
4. Print a summary: tables created, row counts, the snapshot time found.

If a decision has to be made about how to model a sheet (e.g. normalizing a column, inferring a foreign key relationship), log it to memory.md under Decisions with rationale.

$ARGUMENTS
