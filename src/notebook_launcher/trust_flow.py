from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from .models import ResolvedSource, TrustScope
from .state import StateStore


@dataclass(frozen=True, slots=True)
class LaunchSourceIdentity:
    launch_id: str
    repository_id: int
    repository_node_id: str | None
    owner: str
    repository: str
    commit_sha: str
    notebook_path: str


class TrustFlowStore:
    def __init__(self, state: StateStore) -> None:
        self.state = state
        with state.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS launch_sources (
                    launch_id TEXT PRIMARY KEY REFERENCES launches(id) ON DELETE CASCADE,
                    repository_id INTEGER NOT NULL,
                    repository_node_id TEXT,
                    owner TEXT NOT NULL,
                    repository TEXT NOT NULL,
                    commit_sha TEXT NOT NULL,
                    notebook_path TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS trust_challenges (
                    launch_id TEXT PRIMARY KEY REFERENCES launches(id) ON DELETE CASCADE,
                    nonce_hash TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used_at TEXT
                );
                """
            )

    @staticmethod
    def _hash_nonce(nonce: str) -> str:
        return hashlib.sha256(nonce.encode()).hexdigest()

    def create_challenge(
        self,
        launch_id: str,
        source: ResolvedSource,
        *,
        ttl_seconds: int,
    ) -> tuple[str, datetime]:
        nonce = secrets.token_urlsafe(32)
        expires = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        with self.state.transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO launch_sources (
                    launch_id, repository_id, repository_node_id,
                    owner, repository, commit_sha, notebook_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    launch_id,
                    source.repository_id,
                    source.repository_node_id,
                    source.owner,
                    source.repository,
                    source.commit_sha,
                    source.notebook_path,
                ),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO trust_challenges (
                    launch_id, nonce_hash, expires_at, used_at
                ) VALUES (?, ?, ?, NULL)
                """,
                (launch_id, self._hash_nonce(nonce), expires.isoformat()),
            )
        return nonce, expires

    def source_for_launch(self, launch_id: str) -> LaunchSourceIdentity | None:
        with self.state.connect() as conn:
            row = conn.execute(
                "SELECT * FROM launch_sources WHERE launch_id = ?",
                (launch_id,),
            ).fetchone()
        if row is None:
            return None
        return LaunchSourceIdentity(
            launch_id=launch_id,
            repository_id=int(row["repository_id"]),
            repository_node_id=row["repository_node_id"],
            owner=row["owner"],
            repository=row["repository"],
            commit_sha=row["commit_sha"],
            notebook_path=row["notebook_path"],
        )

    def validate_challenge(self, launch_id: str, nonce: str) -> bool:
        with self.state.connect() as conn:
            row = conn.execute(
                "SELECT nonce_hash, expires_at, used_at FROM trust_challenges WHERE launch_id = ?",
                (launch_id,),
            ).fetchone()
        if row is None or row["used_at"] is not None:
            return False
        if datetime.fromisoformat(row["expires_at"]) < datetime.now(UTC):
            return False
        return hmac.compare_digest(row["nonce_hash"], self._hash_nonce(nonce))

    def complete_trust(
        self,
        launch_id: str,
        nonce: str,
        scope: TrustScope,
    ) -> UUID:
        now = datetime.now(UTC)
        with self.state.transaction() as conn:
            challenge = conn.execute(
                "SELECT nonce_hash, expires_at, used_at FROM trust_challenges WHERE launch_id = ?",
                (launch_id,),
            ).fetchone()
            if challenge is None:
                raise ValueError("trust challenge not found")
            if challenge["used_at"] is not None:
                raise ValueError("trust challenge already used")
            if datetime.fromisoformat(challenge["expires_at"]) < now:
                raise ValueError("trust challenge expired")
            if not hmac.compare_digest(challenge["nonce_hash"], self._hash_nonce(nonce)):
                raise ValueError("invalid trust challenge")

            source = conn.execute(
                "SELECT * FROM launch_sources WHERE launch_id = ?",
                (launch_id,),
            ).fetchone()
            if source is None:
                raise ValueError("launch source identity missing")

            scoped_commit = source["commit_sha"] if scope is TrustScope.EXACT_COMMIT else None
            if scope is TrustScope.EXACT_COMMIT:
                existing = conn.execute(
                    """
                    SELECT id FROM trust_records
                    WHERE repository_id = ? AND scope = 'exact_commit'
                      AND commit_sha = ? AND revoked_at IS NULL
                    LIMIT 1
                    """,
                    (source["repository_id"], scoped_commit),
                ).fetchone()
            else:
                existing = conn.execute(
                    """
                    SELECT id FROM trust_records
                    WHERE repository_id = ? AND scope = 'repository'
                      AND revoked_at IS NULL
                    LIMIT 1
                    """,
                    (source["repository_id"],),
                ).fetchone()

            if existing is not None:
                trust_id = UUID(existing["id"])
            else:
                trust_id = uuid4()
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
                        source["repository_id"],
                        source["repository_node_id"],
                        source["owner"],
                        source["repository"],
                        scope.value,
                        scoped_commit,
                        now.isoformat(),
                    ),
                )

            conn.execute(
                "UPDATE trust_challenges SET used_at = ? WHERE launch_id = ?",
                (now.isoformat(), launch_id),
            )
            cursor = conn.execute(
                """
                UPDATE launches
                SET state = 'authorized', trust_covered = 1, updated_at = ?
                WHERE id = ? AND state = 'pending_trust'
                """,
                (now.isoformat(), launch_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("launch is not pending trust")
            return trust_id
