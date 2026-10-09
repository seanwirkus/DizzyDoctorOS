"""Local DizzyDoctor users, groups and permissions.

Credentials live in a separate SQLite database in the app data directory.  That database is
never part of the clinic-data publisher: accounts are security state for the installed app, not
patient data.  Passwords are salted and hashed with scrypt; the plain text password is never
stored.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
from datetime import datetime


ALL_PERMISSIONS = (
    "schedule.view",
    "patients.view",
    "notes.view",
    "notes.create",
    "notes.file",
    "billing.view",
    "settings.manage",
    "users.manage",
)

DEFAULT_GROUPS = {
    "Administrators": ("*",),
    "Providers": ("schedule.view", "patients.view", "notes.view", "notes.create", "notes.file"),
    "Clinical Staff": ("schedule.view", "patients.view", "notes.view", "notes.create"),
    "Front Desk": ("schedule.view", "patients.view", "notes.view"),
    "Billing": ("patients.view", "notes.view", "billing.view"),
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _password(password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return salt, digest


class UserStore:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY, username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                display_name TEXT NOT NULL DEFAULT '', salt BLOB NOT NULL, password_hash BLOB NOT NULL,
                active INTEGER NOT NULL DEFAULT 1, must_change INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS groups(
                id INTEGER PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                description TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS group_permissions(
                group_id INTEGER NOT NULL, permission TEXT NOT NULL,
                PRIMARY KEY(group_id, permission), FOREIGN KEY(group_id) REFERENCES groups(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS user_groups(
                user_id INTEGER NOT NULL, group_id INTEGER NOT NULL,
                PRIMARY KEY(user_id, group_id), FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(group_id) REFERENCES groups(id) ON DELETE CASCADE);
        """)
        self.db.execute("PRAGMA foreign_keys=ON")
        self._bootstrap()

    def close(self) -> None:
        self.db.close()

    def _bootstrap(self) -> None:
        with self._lock:
            for name, permissions in DEFAULT_GROUPS.items():
                self.db.execute("INSERT OR IGNORE INTO groups(name, description) VALUES(?, ?)",
                                (name, "Full access" if name == "Administrators" else "DizzyDoctor built-in group"))
                gid = self.db.execute("SELECT id FROM groups WHERE name=?", (name,)).fetchone()[0]
                self.db.executemany("INSERT OR IGNORE INTO group_permissions(group_id, permission) VALUES(?, ?)",
                                    [(gid, p) for p in permissions])
            if not self.db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                uid = self._insert_user("admin", "Administrator", "change-me-now", True)
                gid = self.db.execute("SELECT id FROM groups WHERE name='Administrators'").fetchone()[0]
                self.db.execute("INSERT INTO user_groups(user_id, group_id) VALUES(?, ?)", (uid, gid))
            self.db.commit()

    def _insert_user(self, username: str, display_name: str, password: str, must_change: bool) -> int:
        salt, digest = _password(password)
        cur = self.db.execute(
            "INSERT INTO users(username, display_name, salt, password_hash, active, must_change, created_at, updated_at) "
            "VALUES(?,?,?,?,1,?,?,?)",
            (username, display_name, salt, digest, int(must_change), _now(), _now()),
        )
        return int(cur.lastrowid)

    def authenticate(self, username: str, password: str) -> dict | None:
        with self._lock:
            row = self.db.execute("SELECT * FROM users WHERE username=? AND active=1", (username.strip(),)).fetchone()
            if not row:
                # Do the expensive work even for an unknown name, reducing username timing leaks.
                _password(password, b"DizzyDoctorFail!")
                return None
            _salt, digest = _password(password, bytes(row["salt"]))
            if not hmac.compare_digest(digest, bytes(row["password_hash"])):
                return None
            return self.user(int(row["id"]))

    def user(self, user_id: int) -> dict | None:
        with self._lock:
            row = self.db.execute("SELECT id, username, display_name, active, must_change FROM users WHERE id=?", (user_id,)).fetchone()
            if not row:
                return None
            groups = [r[0] for r in self.db.execute(
                "SELECT g.name FROM groups g JOIN user_groups ug ON ug.group_id=g.id WHERE ug.user_id=? ORDER BY g.name", (user_id,))]
            perms = {r[0] for r in self.db.execute(
                "SELECT gp.permission FROM group_permissions gp JOIN user_groups ug ON ug.group_id=gp.group_id WHERE ug.user_id=?", (user_id,))}
        return {"id": row["id"], "username": row["username"], "display_name": row["display_name"],
                "active": bool(row["active"]), "must_change": bool(row["must_change"]),
                "groups": groups, "permissions": sorted(perms)}

    def allowed(self, user: dict | None, permission: str) -> bool:
        return bool(user and user.get("active") and ("*" in user.get("permissions", ()) or permission in user.get("permissions", ())))

    def list_state(self) -> dict:
        with self._lock:
            users = []
            for row in self.db.execute("SELECT id FROM users ORDER BY username"):
                users.append(self.user(int(row[0])))
            groups = []
            for row in self.db.execute("SELECT id, name, description FROM groups ORDER BY name"):
                perms = [r[0] for r in self.db.execute(
                    "SELECT permission FROM group_permissions WHERE group_id=? ORDER BY permission", (row["id"],))]
                groups.append({"id": row["id"], "name": row["name"], "description": row["description"], "permissions": perms})
        return {"users": users, "groups": groups, "permissions": list(ALL_PERMISSIONS)}

    def create_user(self, username: str, display_name: str, password: str, groups: list[str] | None = None,
                    active: bool = True, must_change: bool = True) -> dict:
        username = username.strip().lower()
        if not username or not username.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Username may contain letters, numbers, hyphens and underscores.")
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters.")
        with self._lock:
            try:
                uid = self._insert_user(username, display_name.strip() or username, password, must_change)
            except sqlite3.IntegrityError as ex:
                raise ValueError("That username already exists.") from ex
            self.db.execute("UPDATE users SET active=? WHERE id=?", (int(active), uid))
            self._set_groups(uid, groups or ["Front Desk"])
            self.db.commit()
        return self.user(uid) or {}

    def update_user(self, user_id: int, *, display_name: str | None = None, active: bool | None = None,
                    groups: list[str] | None = None, password: str | None = None, must_change: bool | None = None) -> dict:
        with self._lock:
            if not self.db.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
                raise ValueError("User not found.")
            if display_name is not None:
                self.db.execute("UPDATE users SET display_name=?, updated_at=? WHERE id=?", (display_name.strip(), _now(), user_id))
            if active is not None:
                self.db.execute("UPDATE users SET active=?, updated_at=? WHERE id=?", (int(active), _now(), user_id))
            if password is not None:
                if len(password) < 8:
                    raise ValueError("Password must be at least 8 characters.")
                salt, digest = _password(password)
                self.db.execute("UPDATE users SET salt=?, password_hash=?, must_change=?, updated_at=? WHERE id=?",
                                (salt, digest, int(True if must_change is None else must_change), _now(), user_id))
            elif must_change is not None:
                self.db.execute("UPDATE users SET must_change=?, updated_at=? WHERE id=?", (int(must_change), _now(), user_id))
            if groups is not None:
                self._set_groups(user_id, groups)
            self.db.commit()
        return self.user(user_id) or {}

    def _set_groups(self, user_id: int, names: list[str]) -> None:
        rows = self.db.execute(
            "SELECT id, name FROM groups WHERE lower(name) IN (%s)" % ",".join("?" for _ in names),
            [n.lower() for n in names],
        ).fetchall() if names else []
        if len(rows) != len(set(n.lower() for n in names)):
            raise ValueError("One or more groups do not exist.")
        self.db.execute("DELETE FROM user_groups WHERE user_id=?", (user_id,))
        self.db.executemany("INSERT INTO user_groups(user_id, group_id) VALUES(?, ?)", [(user_id, r["id"]) for r in rows])

    def create_group(self, name: str, description: str, permissions: list[str]) -> dict:
        name = name.strip()
        invalid = [p for p in permissions if p not in ALL_PERMISSIONS and p != "*"]
        if not name:
            raise ValueError("Group name is required.")
        if invalid:
            raise ValueError("Unknown permission: " + invalid[0])
        with self._lock:
            try:
                cur = self.db.execute("INSERT INTO groups(name, description) VALUES(?, ?)", (name, description.strip()))
            except sqlite3.IntegrityError as ex:
                raise ValueError("That group already exists.") from ex
            gid = int(cur.lastrowid)
            self.db.executemany("INSERT INTO group_permissions(group_id, permission) VALUES(?, ?)", [(gid, p) for p in permissions])
            self.db.commit()
        return next(g for g in self.list_state()["groups"] if g["id"] == gid)

    def update_group(self, group_id: int, description: str, permissions: list[str]) -> dict:
        invalid = [p for p in permissions if p not in ALL_PERMISSIONS and p != "*"]
        if invalid:
            raise ValueError("Unknown permission: " + invalid[0])
        with self._lock:
            row = self.db.execute("SELECT name FROM groups WHERE id=?", (group_id,)).fetchone()
            if not row:
                raise ValueError("Group not found.")
            if row["name"] == "Administrators" and "*" not in permissions:
                raise ValueError("The Administrators group must keep full access.")
            self.db.execute("UPDATE groups SET description=? WHERE id=?", (description.strip(), group_id))
            self.db.execute("DELETE FROM group_permissions WHERE group_id=?", (group_id,))
            self.db.executemany("INSERT INTO group_permissions(group_id, permission) VALUES(?, ?)", [(group_id, p) for p in permissions])
            self.db.commit()
        return next(g for g in self.list_state()["groups"] if g["id"] == group_id)
