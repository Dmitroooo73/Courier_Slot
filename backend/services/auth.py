from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from hashlib import sha256
from secrets import compare_digest, token_urlsafe
from uuid import UUID, uuid4

from pwdlib import PasswordHash

from backend.pipeline.results import PostgresResults


ACCESS_LIFETIME = timedelta(minutes=15)
SESSION_LIFETIME = timedelta(days=7)
PASSWORDS = PasswordHash.recommended()


@lru_cache(maxsize=1)
def dummy_password_hash() -> str:
    return PASSWORDS.hash(token_urlsafe(32))


def digest(token: str) -> str:
    return sha256(token.encode()).hexdigest()


def session_id(token: str) -> UUID:
    return UUID(hex=token.split(".", 1)[0])


@dataclass(frozen=True)
class Account:
    id: int
    login: str
    role: str
    created_at: datetime
    preferred_zone: str | None = None


@dataclass(frozen=True)
class Profile:
    account: Account
    display_name: str
    bio: str
    birthday: date | None
    avatar_id: str | None
    disabled_at: datetime | None
    active_sessions: int


@dataclass(frozen=True)
class DirectoryEntry:
    id: int
    login: str
    role: str
    created_at: datetime
    display_name: str
    disabled_at: datetime | None


@dataclass(frozen=True)
class DirectoryStats:
    accounts: int
    admins: int
    disabled: int
    active_sessions: int


@dataclass(frozen=True)
class Tokens:
    access: str
    refresh: str
    expires_at: datetime


class AuthStore:
    def __init__(self, database: PostgresResults):
        self.database = database

    def initialize(self) -> None:
        with self.database.connection() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS auth_users (
                    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    login text NOT NULL UNIQUE,
                    password_hash text NOT NULL,
                    role text NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
                    created_at timestamptz NOT NULL DEFAULT now()
                )
            """)
            db.execute("""
                ALTER TABLE auth_users
                    ADD COLUMN IF NOT EXISTS display_name text NOT NULL DEFAULT '',
                    ADD COLUMN IF NOT EXISTS bio text NOT NULL DEFAULT '',
                    ADD COLUMN IF NOT EXISTS birthday date,
                    ADD COLUMN IF NOT EXISTS preferred_zone text,
                    ADD COLUMN IF NOT EXISTS avatar_id text,
                    ADD COLUMN IF NOT EXISTS disabled_at timestamptz
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    id uuid PRIMARY KEY,
                    user_id bigint NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
                    refresh_hash text NOT NULL,
                    expires_at timestamptz NOT NULL
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS auth_sessions_user ON auth_sessions (user_id)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS auth_access_tokens (
                    token_hash text PRIMARY KEY,
                    session_id uuid NOT NULL REFERENCES auth_sessions(id) ON DELETE CASCADE,
                    expires_at timestamptz NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS auth_spent_tokens (
                    session_id uuid NOT NULL REFERENCES auth_sessions(id) ON DELETE CASCADE,
                    token_hash text NOT NULL,
                    PRIMARY KEY (session_id, token_hash)
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS auth_access_session ON auth_access_tokens (session_id)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS auth_attempts (
                    client text NOT NULL,
                    action text NOT NULL,
                    hits integer NOT NULL,
                    expires_at timestamptz NOT NULL,
                    PRIMARY KEY (client, action)
                )
            """)

    def allow_attempt(self, client: str, action: str, limit: int) -> bool:
        with self.database.connection() as db:
            db.execute("DELETE FROM auth_attempts WHERE expires_at <= now()")
            return db.execute("""
                INSERT INTO auth_attempts (client, action, hits, expires_at)
                VALUES (%s, %s, 1, now() + interval '1 minute')
                ON CONFLICT (client, action) DO UPDATE SET hits = auth_attempts.hits + 1
                WHERE auth_attempts.hits < %s
            """, (client, action, limit)).rowcount == 1

    def register(self, login: str, password: str, previous: str | None) -> tuple[Account, Tokens] | None:
        password_hash = PASSWORDS.hash(password)
        with self.database.connection() as db:
            row = db.execute("""
                INSERT INTO auth_users (login, password_hash) VALUES (%s, %s)
                ON CONFLICT (login) DO NOTHING RETURNING id, login, role, created_at
            """, (login, password_hash)).fetchone()
            if row is None:
                return None
            account = Account(*row)
            return account, self._start_session(db, account.id, previous)

    def login(self, login: str, password: str, previous: str | None) -> tuple[Account, Tokens] | None:
        with self.database.connection() as db:
            row = db.execute("""
                SELECT id, login, role, created_at, preferred_zone, password_hash FROM auth_users
                WHERE login = %s AND disabled_at IS NULL FOR UPDATE
            """, (login,)).fetchone()
            valid = PASSWORDS.verify(password, row[-1] if row else dummy_password_hash())
            if not row or not valid:
                return None
            account = Account(*row[:-1])
            return account, self._start_session(db, account.id, previous)

    def _start_session(self, db, user_id: int, previous: str | None) -> Tokens:
        key = uuid4()
        now = datetime.now(UTC)
        tokens = Tokens(token_urlsafe(32), f"{key.hex}.{token_urlsafe(32)}", now + SESSION_LIFETIME)
        db.execute("DELETE FROM auth_sessions WHERE expires_at <= now()")
        if previous:
            self._end_session(db, previous)
        db.execute("""
            INSERT INTO auth_sessions (id, user_id, refresh_hash, expires_at) VALUES (%s, %s, %s, %s)
        """, (key, user_id, digest(tokens.refresh), tokens.expires_at))
        self._save_access(db, key, tokens, now)
        return tokens

    def refresh_session(self, refresh: str) -> tuple[Account, Tokens] | None:
        key = session_id(refresh)
        refresh_hash = digest(refresh)
        with self.database.connection() as db:
            row = db.execute("""
                SELECT s.refresh_hash, s.expires_at, u.id, u.login, u.role, u.created_at, u.preferred_zone
                FROM auth_sessions s JOIN auth_users u ON u.id = s.user_id
                WHERE s.id = %s AND s.expires_at > now() AND u.disabled_at IS NULL FOR UPDATE OF s
            """, (key,)).fetchone()
            if row is None:
                return None
            current_hash, expires_at, *account = row
            if not compare_digest(refresh_hash, current_hash):
                self._end_session(db, refresh)
                return None
            tokens = Tokens(token_urlsafe(32), f"{key.hex}.{token_urlsafe(32)}", expires_at)
            db.execute("""
                INSERT INTO auth_spent_tokens (session_id, token_hash) VALUES (%s, %s)
            """, (key, refresh_hash))
            db.execute("UPDATE auth_sessions SET refresh_hash = %s WHERE id = %s", (digest(tokens.refresh), key))
            db.execute("DELETE FROM auth_access_tokens WHERE session_id = %s AND expires_at <= now()", (key,))
            self._save_access(db, key, tokens, datetime.now(UTC))
        return Account(*account), tokens

    def _save_access(self, db, key: UUID, tokens: Tokens, now: datetime) -> None:
        db.execute("""
            INSERT INTO auth_access_tokens (token_hash, session_id, expires_at) VALUES (%s, %s, %s)
        """, (digest(tokens.access), key, min(now + ACCESS_LIFETIME, tokens.expires_at)))

    def account_for(self, access: str) -> Account | None:
        with self.database.connection() as db:
            row = db.execute("""
                SELECT u.id, u.login, u.role, u.created_at, u.preferred_zone
                FROM auth_access_tokens a JOIN auth_sessions s ON a.session_id = s.id
                JOIN auth_users u ON u.id = s.user_id
                WHERE a.token_hash = %s AND a.expires_at > now() AND s.expires_at > now() AND u.disabled_at IS NULL
            """, (digest(access),)).fetchone()
        return Account(*row) if row else None

    def logout(self, refresh: str) -> None:
        with self.database.connection() as db:
            self._end_session(db, refresh)

    def _end_session(self, db, refresh: str) -> None:
        key = session_id(refresh)
        token_hash = digest(refresh)
        row = db.execute("SELECT refresh_hash FROM auth_sessions WHERE id = %s FOR UPDATE", (key,)).fetchone()
        if row is None:
            return
        if compare_digest(token_hash, row[0]) or db.execute("""
            SELECT 1 FROM auth_spent_tokens WHERE session_id = %s AND token_hash = %s
        """, (key, token_hash)).fetchone():
            db.execute("DELETE FROM auth_sessions WHERE id = %s", (key,))

    def change_password(self, user_id: int, current: str, replacement: str) -> bool:
        with self.database.connection() as db:
            row = db.execute("""
                SELECT password_hash FROM auth_users WHERE id = %s AND disabled_at IS NULL FOR UPDATE
            """, (user_id,)).fetchone()
            if row is None or not PASSWORDS.verify(current, row[0]):
                return False
            db.execute("UPDATE auth_users SET password_hash = %s WHERE id = %s", (PASSWORDS.hash(replacement), user_id))
            db.execute("DELETE FROM auth_sessions WHERE user_id = %s", (user_id,))
            return True

    def profile(self, user_id: int) -> Profile | None:
        with self.database.connection() as db:
            return self._profile(db, user_id)

    def _profile(self, db, user_id: int) -> Profile | None:
        row = db.execute("""
            SELECT u.id, u.login, u.role, u.created_at, u.preferred_zone,
                   u.display_name, u.bio, u.birthday, u.avatar_id, u.disabled_at,
                   (SELECT count(*) FROM auth_sessions s WHERE s.user_id = u.id AND s.expires_at > now())
            FROM auth_users u WHERE u.id = %s
        """, (user_id,)).fetchone()
        return Profile(Account(*row[:5]), *row[5:]) if row else None

    def update_profile(self, user_id: int, name: str, bio: str, birthday: date | None, zone: str | None) -> Profile | None:
        with self.database.connection() as db:
            result = db.execute("""
                UPDATE auth_users SET display_name = %s, bio = %s, birthday = %s, preferred_zone = %s
                WHERE id = %s AND disabled_at IS NULL
            """, (name, bio, birthday, zone, user_id))
            return self._profile(db, user_id) if result.rowcount else None

    def replace_avatar(self, user_id: int, avatar_id: str | None) -> tuple[Profile, str | None] | None:
        with self.database.connection() as db:
            row = db.execute("""
                SELECT avatar_id FROM auth_users WHERE id = %s AND disabled_at IS NULL FOR UPDATE
            """, (user_id,)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE auth_users SET avatar_id = %s WHERE id = %s", (avatar_id, user_id))
            return self._profile(db, user_id), row[0]

    def users(self, after: int, limit: int, query: str, role: str, status: str) -> tuple[list[DirectoryEntry], DirectoryStats]:
        with self.database.connection() as db:
            rows = db.execute("""
                SELECT id, login, role, created_at, display_name, disabled_at FROM auth_users
                WHERE id > %(after)s
                  AND (%(role)s = '' OR role = %(role)s)
                  AND (%(status)s = '' OR (disabled_at IS NOT NULL) = (%(status)s = 'disabled'))
                  AND (strpos(lower(login), lower(%(query)s)) > 0
                       OR strpos(lower(display_name), lower(%(query)s)) > 0 OR %(query)s = '')
                ORDER BY id LIMIT %(limit)s
            """, {"after": after, "limit": limit, "query": query, "role": role, "status": status}).fetchall()
            stats = db.execute("""
                SELECT count(*), count(*) FILTER (WHERE role = 'admin'), count(disabled_at),
                       (SELECT count(*) FROM auth_sessions WHERE expires_at > now())
                FROM auth_users
            """).fetchone()
        return [DirectoryEntry(*row) for row in rows], DirectoryStats(*stats)

    def moderate(self, user_id: int, action: str) -> Profile | None:
        with self.database.connection() as db:
            row = db.execute("SELECT role FROM auth_users WHERE id = %s FOR UPDATE", (user_id,)).fetchone()
            if row is None or row[0] != "user":
                return None
            if action == "block":
                db.execute("UPDATE auth_users SET disabled_at = COALESCE(disabled_at, now()) WHERE id = %s", (user_id,))
            if action == "unblock":
                db.execute("UPDATE auth_users SET disabled_at = NULL WHERE id = %s", (user_id,))
            if action in {"block", "revoke_sessions"}:
                db.execute("DELETE FROM auth_sessions WHERE user_id = %s", (user_id,))
            return self._profile(db, user_id)

    def promote(self, login: str) -> bool:
        with self.database.connection() as db:
            return db.execute("UPDATE auth_users SET role = 'admin' WHERE login = %s AND disabled_at IS NULL", (login,)).rowcount == 1
