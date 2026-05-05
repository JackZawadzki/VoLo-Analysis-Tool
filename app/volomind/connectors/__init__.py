from .base import SourceConnector, SyncResult  # noqa: F401
from .granola import GranolaConnector  # noqa: F401
from .drive_admin import DriveAdminConnector  # noqa: F401


def get_connector(source_id: str) -> type[SourceConnector]:
    """Resolve a source_id to its connector class."""
    registry: dict[str, type[SourceConnector]] = {
        "granola": GranolaConnector,
        "gdrive_admin": DriveAdminConnector,
    }
    if source_id not in registry:
        raise ValueError(f"unknown source_id: {source_id}")
    return registry[source_id]
