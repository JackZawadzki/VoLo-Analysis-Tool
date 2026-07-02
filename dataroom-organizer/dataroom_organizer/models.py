"""Shared record types for the catalog (one row per file)."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class FileRecord:
    """One row of the master catalog -- everything we know about a single file.

    The catalog is the single source of truth (plan, Guiding Principles); the
    clean folders, navigation guides and validation reports are all generated
    from these rows.
    """

    # --- identity / provenance (Phase 1, read-only) ---
    file_id: str = ""                  # short stable id
    source_path: str = ""              # absolute path in the ORIGINAL drive
    source_rel: str = ""               # path relative to the source root
    filename: str = ""
    ext: str = ""
    size_bytes: int = 0
    mtime_iso: str = ""                # filesystem modified time
    sha256: str = ""                   # content hash (exact-duplicate detection)

    # --- inference ---
    company: str = ""                  # inferred company ("" / "UNKNOWN" if not resolved)
    company_evidence: str = ""         # how the company was determined
    origin_folder: str = ""            # top-level legacy folder it lived in
    origin_kind: str = ""              # "company" | "theme" | "root"

    bucket_code: str = ""              # one of the 16 folder codes
    bucket_name: str = ""
    confidence: float = 0.0            # 0..1
    needs_review: bool = False
    evidence: list[str] = field(default_factory=list)   # signals that fired

    # --- dates / versions ---
    doc_date: str = ""                 # best date (filename date else mtime)
    doc_date_source: str = ""          # "filename" | "mtime"
    confidentiality: str = ""          # internal/external/redacted/... ("" if none)
    version_family: str = ""           # key grouping versions of the same doc
    stage: str = ""                    # draft/final/executed/... ("" if none)
    disposition: str = ""              # PLACED | SUPERSEDED | DUPLICATE | PENDING
    current: bool = False              # is this the current version of its family?
    superseded_by: str = ""            # file_id of the current version, if superseded
    duplicate_of: str = ""             # file_id of the kept copy, if an exact duplicate

    # --- extraction ---
    text_chars: int = 0                # amount of text we could read (0 => opaque/image)
    gnative_url: str = ""              # for Google-native pointers: link to the online doc
    extracted_numbers: list[dict] = field(default_factory=list)  # code-read figures w/ cell refs
    summary: str = ""                  # one-line, human/AI readable description
    flags: list[str] = field(default_factory=list)   # e.g. "image-only", "not-owned", "macro-enabled"

    # --- placement (Phase 3) ---
    dest_rel: str = ""                 # path in the reorganized copy
    also_relevant_to: list[str] = field(default_factory=list)  # cross-reference bucket codes

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        # Flatten list/dict columns for tabular output.
        d["evidence"] = " | ".join(self.evidence)
        d["flags"] = " | ".join(self.flags)
        d["also_relevant_to"] = ",".join(self.also_relevant_to)
        d["extracted_numbers"] = "; ".join(
            f"{n.get('label','?')}={n.get('value','?')}@{n.get('ref','?')}"
            for n in self.extracted_numbers
        )
        return d
