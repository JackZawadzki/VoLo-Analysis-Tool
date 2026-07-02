"""VoLo data-room reorganizer -- a catalog-driven, reversible pipeline.

Public API:
    from dataroom_organizer import pipeline
    pipeline.inventory(source, out)   # Phase 1, read-only
    pipeline.run(source, out)         # Phases 1-3
"""

from . import config, pipeline  # noqa: F401

__version__ = "0.1.0"
