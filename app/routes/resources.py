"""
Displaced-resource CRUD routes (carbon intensity database).
"""

import sqlite3
from fastapi import APIRouter, Depends, HTTPException
from ..auth import CurrentUser, get_current_user, require_admin
from ..database import get_db

router = APIRouter(prefix="/api/resources", tags=["resources"])


@router.get("")
def list_resources(user: CurrentUser = Depends(get_current_user)):
    db = get_db()
    try:
        rows = db.execute(
            "SELECT * FROM displaced_resources ORDER BY is_builtin DESC, name"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


@router.post("")
def create_resource(data: dict, user: CurrentUser = Depends(require_admin)):
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    try:
        base_ci = float(data["base_ci"])
        base_year = int(data.get("base_year", 2022))
        ci_type = data.get("ci_type", "linear")
        annual_decline = float(
            data.get("annual_decline", base_ci / 30 if ci_type == "linear" else 0)
        )
    except (KeyError, ValueError) as e:
        raise HTTPException(400, f"Invalid field: {e}")

    db = get_db()
    try:
        cur = db.execute(
            """INSERT INTO displaced_resources
               (name, units, base_ci, base_year, ci_type, annual_decline, description, created_by)
               VALUES (?,?,?,?,?,?,?,?)""",
            (name, data.get("units", ""), base_ci, base_year, ci_type, annual_decline,
             data.get("description", ""), user.id),
        )
        db.commit()
        row = db.execute(
            "SELECT * FROM displaced_resources WHERE id=?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Resource name already exists")
    finally:
        db.close()


