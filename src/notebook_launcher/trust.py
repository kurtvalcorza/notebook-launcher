from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from .models import ResolvedSource, TrustScope
from .state import StateStore


@dataclass(frozen=True, slots=True)
class TrustMatch:
    trusted: bool
    trust_id: UUID | None = None
    scope: TrustScope | None = None


class TrustStore:
    def __init__(self, state: StateStore):
        self.state = state

    def find(self, source: ResolvedSource) -> TrustMatch:
        with self.state.connect() as conn:
            row = conn.execute(
                """
                SELECT id, scope
                FROM trust_records
                WHERE repository_id = ?
                  AND revoked_at IS NULL
                  AND (
                      scope = 'repository'
                      OR (scope = 'exact_commit' AND commit_sha = ?)
                  )
                ORDER BY CASE scope WHEN 'exact_commit' THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (source.repository_id, source.commit_sha),
            ).fetchone()
        if row is None:
            return TrustMatch(False)
        return TrustMatch(
            trusted=True,
            trust_id=UUID(row["id"]),
            scope=TrustScope(row["scope"]),
        )

    def grant(self, source: ResolvedSource, scope: TrustScope) -> UUID:
        return self.grant_identity(
            repository_id=source.repository_id,
            repository_node_id=source.repository_node_id,
            owner=source.owner,
            repository=source.repository,
            commit_sha=source.commit_sha,
            scope=scope,
        )

    def grant_identity(
        self,
        *,
        repository_id: int,
        repository_node_id: str | None,
        owner: str,
        repository: str,
        commit_sha: str,
        scope: TrustScope,
    ) -> UUID:
        trust_id = uuid4()
        scoped_commit = commit_sha if scope is TrustScope.EXACT_COMMIT else None
        with self.state.transaction() as conn:
            conn.execute(
                """
                INSERT INTO trust_records (
                    id, repository_id, repository_node_id,
                    grant_owner, grant_repository, scope,
                    commit_sha, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(trust_id),
                    repository_id,
                    repository_node_id,
                    owner,
                    repository,
                    scope.value,
                    scoped_commit,
                    datetime.now(UTC).isoformat(),
                ),
            )
        return trust_id

    def revoke(self, trust_id: UUID) -> bool:
        with self.state.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE trust_records
                SET revoked_at = ?
                WHERE id = ? AND revoked_at IS NULL
                """,
                (datetime.now(UTC).isoformat(), str(trust_id)),
            )
        return cursor.rowcount == 1
