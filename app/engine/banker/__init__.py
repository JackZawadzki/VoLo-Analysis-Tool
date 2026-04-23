"""Banker Agent — extracts structured data from Excel financial models.

Design principles:
  - General: no assumptions about industry, archetype, or company structure.
  - Flexible schema: categories are soft strings, labels are preserved verbatim.
  - Provenance-enforced: every extracted value carries (sheet, cell) back to the source.
  - Agent picks WHAT to extract. Deterministic code handles number transcription.
"""
__version__ = "0.1.0"
