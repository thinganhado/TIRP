from app import db
from sqlalchemy import text

# no app_context wrapper needed here – the view already runs in a request context
def fetch_all_students():
    sql = text("""
        SELECT
            p.student_id   AS id,
            p.first_name,
            p.last_name,
            p.email,
            p.house,
            a.class_id     AS class_
        FROM participants p
        LEFT JOIN allocations a
              ON p.student_id = a.student_id
        WHERE p.student_id <> 'student_id'
    """)

    rows = db.session.execute(sql).fetchall()

    return [
        {
            "id":       r.id,
            "first_name": r.first_name,
            "last_name": r.last_name,
            "email":    r.email,
            "house":    r.house,
            "class":    r.class_
        }
        for r in rows
    ]

from typing import Optional

def fetch_student_details(student_id: int) -> Optional[dict]:
    """Return detail row for a single student-id or None."""
    q = text("""
        SELECT
            p.perc_effort,
            p.attendance,
            p.perc_academic,
            p.complete_years,
            r.status
        FROM participants  p
        LEFT JOIN responses r ON r.student_id = p.student_id
        WHERE p.student_id = :sid
        LIMIT 1
    """)

    row = db.session.execute(q, {"sid": student_id}).fetchone()
    # row is None if the id doesn't exist
    return dict(row._mapping) if row else {}

def fetch_students():
    """
    Return list of {"id","name","class_id"} for dropdowns.
    """
    sql = text("""
      SELECT
        p.student_id AS id,
        p.first_name,
        p.last_name,
        COALESCE(a.class_id, 'Unassigned') AS class_id
      FROM participants p
      LEFT JOIN allocations a
        ON p.student_id = a.student_id
      WHERE p.student_id <> 'student_id'
      ORDER BY p.last_name, p.first_name
    """)
    rows = db.session.execute(sql).fetchall()
    return [
      {
        "id": r.id,
        "name": f"{r.first_name} {r.last_name}",
        "class_id": r.class_id
      }
      for r in rows
    ]