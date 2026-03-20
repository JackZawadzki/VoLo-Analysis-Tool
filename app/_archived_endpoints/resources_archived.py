"""
ARCHIVED: Unused resource CRUD endpoints.
- PUT /{rid} — update resource
- DELETE /{rid} — delete resource

These endpoints are no longer used by the frontend.
Only GET (list) and POST (create) are used.
Kept for reference if they need to be revived.

Date archived: 2026-03-17
"""

import sqlite3
from fastapi import APIRouter, Depends, HTTPException
from ..auth import CurrentUser, get_current_user, require_admin
from ..database import get_db

router = APIRouter(prefix="/api/resources", tags=["resources-archived"])


@router.put("/{rid}")
def update_resource(rid: int, data: dict, user: CurrentUser = Depends(require_admin)):
    """ARCHIVED: Update a resource."""
    db = get_db()
    try:
        row = db.execute("SELECT * FROM displaced_resources WHERE id=?", (rid,)).fetchone()
        if not row:
            raise HTTPException(404, "Not found")
        if row["is_builtin"]:
            raise HTTPException(403, "Built-in resources cannot be edited")

        db.execute(
            """UPDATE displaced_resources
               SET name=?, units=?, base_ci=?, base_year=?, ci_type=?, annual_decline=?, description=?
               WHERE id=?""",
            (
                (data.get("name") or row["name"]).strip(),
                data.get("units", row["units"] or ""),
                float(data.get("base_ci", row["base_ci"])),
                int(data.get("base_year", row["base_year"])),
                data.get("ci_type", row["ci_type"]),
                float(data.get("annual_decline", row["annual_decline"])),
                data.get("description", row["description"] or ""),
                rid,
            ),
        )
        db.commit()
        updated = db.execute("SELECT * FROM displaced_resources WHERE id=?", (rid,)).fetchone()
        return dict(updated)
    finally:
        db.close()


@router.delete("/{rid}")
def delete_resource(rid: int, user: CurrentUser = Depends(require_admin)):
    """ARCHIVED: Delete a resource."""
    db = get_db()
    try:
        row = db.execute("SELECT is_builtin FROM displaced_resources WHERE id=?", (rid,)).fetchone()
        if not row:
            raise HTTPException(404, "Not found")
        if row["is_builtin"]:
            raise HTTPException(403, "Built-in resources cannot be deleted")
        db.execute("DELETE FROM displaced_resources WHERE id=?", (rid,))
        db.commit()
        return {"ok": True}
    finally:
        db.close()
