"""SQLite side of the index: the ``index_entries`` manifest and the ``chunk_fts`` keyword index.
All statements are parameterized; callers own the transaction."""
from collections.abc import Iterable

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.db.models import IndexEntry, SearchIndex


def fts_project_token(project_id: str) -> str:
    # A single \w+ token, so FTS queries can be restricted to one project.
    return "p" + project_id.replace("-", "").lower()


def load_entries(session: Session, project_id: str) -> list[IndexEntry]:
    return list(
        session.scalars(select(IndexEntry).where(IndexEntry.project_id == project_id)).all()
    )


def add_fts(
    session: Session, entry_id: int, project_id: str, path: str, symbol: str, body: str
) -> None:
    session.execute(
        text(
            "INSERT INTO chunk_fts(rowid, project, path, symbol, body) "
            "VALUES (:rowid, :project, :path, :symbol, :body)"
        ),
        {
            "rowid": entry_id,
            "project": fts_project_token(project_id),
            "path": path,
            "symbol": symbol,
            "body": body,
        },
    )


def delete_entries(session: Session, entry_ids: Iterable[int]) -> None:
    ids = list(entry_ids)
    for start in range(0, len(ids), 500):
        batch = ids[start : start + 500]
        params = {f"id{i}": value for i, value in enumerate(batch)}
        placeholders = ", ".join(f":id{i}" for i in range(len(batch)))
        session.execute(text(f"DELETE FROM chunk_fts WHERE rowid IN ({placeholders})"), params)
        session.execute(delete(IndexEntry).where(IndexEntry.id.in_(batch)))


def delete_project_index(session: Session, project_id: str) -> None:
    """Remove a project's manifest, keyword-index rows and index state (caller commits).
    Vectors are removed separately after the commit (indexer.drop_project_vectors)."""
    ids = session.scalars(select(IndexEntry.id).where(IndexEntry.project_id == project_id)).all()
    delete_entries(session, ids)
    session.execute(delete(SearchIndex).where(SearchIndex.project_id == project_id))
