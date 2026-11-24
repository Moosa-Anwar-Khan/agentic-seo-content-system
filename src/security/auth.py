"""Database-backed accounts, authenticator codes, and opaque sessions."""

import base64
import binascii
import hashlib
import hmac
import secrets
import re
import struct
import time
import threading
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from uuid import uuid4

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(password: str) -> str:
    if not 12 <= len(password) <= 128:
        raise ValueError("Password must contain 12 to 128 characters")
    salt = secrets.token_bytes(16)
    value = Argon2id(salt=salt, length=32, iterations=3, lanes=4, memory_cost=64 * 1024).derive(password.encode())
    return "argon2id$" + base64.urlsafe_b64encode(salt + value).decode()


def verify_password(password: str, encoded: str) -> bool:
    try:
        raw = base64.urlsafe_b64decode(encoded.removeprefix("argon2id$").encode())
        value = Argon2id(salt=raw[:16], length=32, iterations=3, lanes=4, memory_cost=64 * 1024).derive(password.encode())
        return encoded.startswith("argon2id$") and hmac.compare_digest(value, raw[16:])
    except (ValueError, TypeError, binascii.Error):
        return False


_DUMMY_PASSWORD_HASH = hash_password("invalid-password-placeholder")


class BootstrapAccountConflict(ValueError):
    """A valid local setup code still needs compatible account credentials."""
    def __init__(self, reason):
        super().__init__('Administrator account setup conflict')
        self.reason = reason


def totp_code(secret: str, step: int | None = None) -> str:
    key = base64.b32decode(secret, casefold=True)
    counter = int(time.time() // 30 if step is None else step)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 15
    return str((struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7fffffff) % 1000000).zfill(6)


def valid_totp(secret: str, code: str, last_step: int = -1) -> int | None:
    if not (len(code) == 6 and code.isascii() and code.isdigit()):
        return None
    current = int(time.time() // 30)
    for step in (current - 1, current, current + 1):
        if step > last_step and hmac.compare_digest(totp_code(secret, step), code):
            return step
    return None


class AccountStore:
    def __init__(self, storage):
        self.storage = storage
        self.pg = storage.__class__.__name__ == "PostgresStorage"
        self.prefix = "seo_" if self.pg else ""
        self.param = "%s" if self.pg else "?"
        with storage.connect() as db:
            p = self.prefix
            db.execute(f"CREATE TABLE IF NOT EXISTS {p}users (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, name TEXT NOT NULL, role TEXT NOT NULL, password_hash TEXT NOT NULL, totp_secret TEXT, last_totp_step BIGINT NOT NULL DEFAULT -1, must_change_password INTEGER NOT NULL DEFAULT 1, enabled INTEGER NOT NULL DEFAULT 1)")
            db.execute(f"CREATE TABLE IF NOT EXISTS {p}auth_sessions (digest TEXT PRIMARY KEY, user_id TEXT NOT NULL, csrf_digest TEXT NOT NULL, expires_at TEXT NOT NULL)")
            db.execute(f"CREATE TABLE IF NOT EXISTS {p}auth_challenges (digest TEXT PRIMARY KEY, user_id TEXT NOT NULL, stage TEXT NOT NULL, expires_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0)")
            db.execute(f"CREATE TABLE IF NOT EXISTS {p}recovery_codes (user_id TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(user_id,digest))")
            db.execute(f"CREATE TABLE IF NOT EXISTS {p}login_attempts (key TEXT PRIMARY KEY, count INTEGER NOT NULL, window_until TEXT NOT NULL)")
            if self.pg:
                db.execute(f"ALTER TABLE {p}users ADD COLUMN IF NOT EXISTS registration_status TEXT NOT NULL DEFAULT 'active'")
            elif 'registration_status' not in {row[1] for row in db.execute(f'PRAGMA table_info({p}users)').fetchall()}:
                db.execute(f"ALTER TABLE {p}users ADD COLUMN registration_status TEXT NOT NULL DEFAULT 'active'")
            db.execute(f"CREATE TABLE IF NOT EXISTS {p}run_owners (run_id TEXT PRIMARY KEY, user_id TEXT NOT NULL)")
            db.execute(f"CREATE TABLE IF NOT EXISTS {p}run_shares (run_id TEXT NOT NULL,user_id TEXT NOT NULL,granted_by TEXT NOT NULL,PRIMARY KEY(run_id,user_id))")
            db.execute(f"CREATE TABLE IF NOT EXISTS {p}access_audit (id TEXT PRIMARY KEY,actor_id TEXT NOT NULL,action TEXT NOT NULL,target_id TEXT NOT NULL,created_at TEXT NOT NULL)")
            db.execute(f"DELETE FROM {p}reviewer_sessions")

    def execute(self, sql: str, params=(), *, one=False, all_rows=False):
        with self.storage.connect() as db:
            cursor = db.execute(sql.replace("?", self.param), params)
            if one:
                return cursor.fetchone()
            if all_rows:
                return cursor.fetchall()
        return None

    def user(self, field: str, value: str):
        if field not in {"id", "email"}:
            raise ValueError("Invalid user lookup")
        row = self.execute(f"SELECT id,email,name,role,password_hash,totp_secret,last_totp_step,must_change_password,enabled,registration_status FROM {self.prefix}users WHERE {field}=?", (value,), one=True)
        return dict(zip(("id", "email", "name", "role", "password_hash", "totp_secret", "last_totp_step", "must_change_password", "enabled", "registration_status"), row)) if row else None

    def users(self):
        rows = self.execute(f"SELECT id,email,name,role,enabled,registration_status FROM {self.prefix}users ORDER BY name", all_rows=True)
        return [dict(zip(("id", "email", "name", "role", "enabled", "registration_status"), row)) for row in rows]

    def create_user(self, email: str, name: str, role: str, password: str, *, pending=False, first_admin=False, chosen_password=False, claim_pending=False):
        email = email.strip().lower()
        name = unicodedata.normalize('NFKC', name).strip()
        if role not in {'admin','reviewer','tax_adviser','member'} or not name or len(name)>100 or any(ord(ch)<32 for ch in name) or len(email)>254 or not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',email):
            raise ValueError('Invalid account')
        if pending and (role!='member' or name.casefold() in {'sam','alex'}):
            raise ValueError('Reserved account identity')
        if claim_pending and (not first_admin or role != 'admin' or pending):
            raise ValueError('Pending account claims require trusted first-administrator setup')
        user_id,encoded=str(uuid4()),hash_password(password)
        p=self.param
        with self.storage.connect() as db:
            if self.pg:db.execute('SELECT pg_advisory_xact_lock(648320062)')
            else:db.execute('BEGIN IMMEDIATE')
            if first_admin and db.execute(f"SELECT COUNT(*) FROM {self.prefix}users WHERE role='admin'").fetchone()[0]:
                raise ValueError('An administrator already exists')
            existing = None
            if claim_pending:
                existing = db.execute(f'SELECT id,password_hash,role,enabled,registration_status,totp_secret,last_totp_step FROM {self.prefix}users WHERE email={p}', (email,)).fetchone()
                if existing:
                    if existing[2] != 'member' or existing[3] or existing[4] != 'pending' or existing[5] or existing[6] >= 0:
                        raise BootstrapAccountConflict('account_unavailable')
                    if not verify_password(password, existing[1]):
                        raise BootstrapAccountConflict('pending_password')
            if name.casefold() in {'sam','alex'}:
                names=db.execute(f'SELECT name FROM {self.prefix}users').fetchall()
                if any(unicodedata.normalize('NFKC',row[0]).strip().casefold()==name.casefold() for row in names):
                    if claim_pending:
                        raise BootstrapAccountConflict('reserved_identity')
                    raise ValueError('Reserved identity already exists')
            if existing:
                user_id = existing[0]
                claimed = db.execute(f"UPDATE {self.prefix}users SET name={p},role='admin',enabled=1,registration_status='active',must_change_password=0 WHERE id={p} AND password_hash={p} AND role='member' AND registration_status='pending' AND enabled=0 AND totp_secret IS NULL AND last_totp_step<0 RETURNING id",
                                     (name, user_id, existing[1])).fetchone()
                if not claimed:
                    raise BootstrapAccountConflict('account_unavailable')
                # The existing password and stable identity survive; MFA enrollment is mandatory.
                db.execute(f'DELETE FROM {self.prefix}auth_sessions WHERE user_id={p}', (user_id,))
                db.execute(f'DELETE FROM {self.prefix}auth_challenges WHERE user_id={p}', (user_id,))
                db.execute(f'DELETE FROM {self.prefix}recovery_codes WHERE user_id={p}', (user_id,))
                db.execute(f'INSERT INTO {self.prefix}access_audit VALUES ({p},{p},{p},{p},{p})',
                           (str(uuid4()), user_id, 'bootstrap_pending_admin', user_id, datetime.now(timezone.utc).isoformat()))
            else:
                db.execute(f'INSERT INTO {self.prefix}users(id,email,name,role,password_hash,enabled,registration_status,must_change_password) VALUES ({p},{p},{p},{p},{p},{p},{p},{p})',
                    (user_id,email,name,role,encoded,0 if pending else 1,'pending' if pending else 'active',0 if pending or chosen_password else 1))
        return self.user('id',user_id)

    def decide_registration(self,user_id,approved,actor_id):
        p=self.param
        with self.storage.connect() as db:
            changed=db.execute(f"UPDATE {self.prefix}users SET registration_status={p},enabled={p} WHERE id={p} AND registration_status='pending' RETURNING id",('active' if approved else 'rejected',int(approved),user_id)).fetchone()
            if not changed:raise ValueError('Registration is not pending')
            db.execute(f'INSERT INTO {self.prefix}access_audit VALUES ({p},{p},{p},{p},{p})',(str(uuid4()),actor_id,'approve_registration' if approved else 'reject_registration',user_id,datetime.now(timezone.utc).isoformat()))

    def count_admins(self):
        return self.execute(f"SELECT COUNT(*) FROM {self.prefix}users WHERE role='admin'", one=True)[0]

    def claim_totp(self, user_id: str, step: int):
        row = self.execute(f"UPDATE {self.prefix}users SET last_totp_step=? WHERE id=? AND last_totp_step<? RETURNING id", (step,user_id,step), one=True)
        return bool(row)

    def complete_setup(self, user_id: str, challenge_secret: str, step: int, codes: list[str]):
        p = self.param
        session_secret, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        with self.storage.connect() as db:
            claimed = db.execute(f"UPDATE {self.prefix}users SET last_totp_step={p} WHERE id={p} AND last_totp_step<{p} RETURNING id", (step,user_id,step)).fetchone()
            consumed = db.execute(f"DELETE FROM {self.prefix}auth_challenges WHERE digest={p} AND user_id={p} AND stage='setup_mfa' AND attempts<5 AND expires_at>{p} RETURNING digest", (digest(challenge_secret),user_id,now.isoformat())).fetchone()
            if not claimed or not consumed:
                raise PermissionError("Invalid or reused authenticator setup")
            for code in codes:
                db.execute(f"INSERT INTO {self.prefix}recovery_codes VALUES ({p},{p})", (user_id,digest(code)))
            db.execute(f"INSERT INTO {self.prefix}auth_sessions VALUES ({p},{p},{p},{p})", (digest(session_secret),user_id,digest(csrf),(now+timedelta(hours=12)).isoformat()))
        return session_secret, csrf

    def update(self, user_id: str, **fields):
        allowed = {"password_hash", "totp_secret", "last_totp_step", "must_change_password", "enabled"}
        if not fields or not set(fields).issubset(allowed):
            raise ValueError("Invalid account update")
        assignments = ",".join(f"{key}=?" for key in fields)
        self.execute(f"UPDATE {self.prefix}users SET {assignments} WHERE id=?", (*fields.values(), user_id))

    def revoke_user(self, user_id: str):
        self.execute(f"DELETE FROM {self.prefix}auth_sessions WHERE user_id=?", (user_id,))
        self.execute(f"DELETE FROM {self.prefix}auth_challenges WHERE user_id=?", (user_id,))

    def reset_credentials(self, user_id: str, password_hash: str):
        p = self.param
        with self.storage.connect() as db:
            db.execute(f"UPDATE {self.prefix}users SET password_hash={p},totp_secret=NULL,last_totp_step=-1,must_change_password=1 WHERE id={p}", (password_hash,user_id))
            db.execute(f"DELETE FROM {self.prefix}recovery_codes WHERE user_id={p}", (user_id,))
            db.execute(f"DELETE FROM {self.prefix}auth_sessions WHERE user_id={p}", (user_id,))
            db.execute(f"DELETE FROM {self.prefix}auth_challenges WHERE user_id={p}", (user_id,))

    def change_initial_password(self, user_id: str, challenge_secret: str, password_hash: str):
        p = self.param
        challenge_digest = digest(challenge_secret)
        with self.storage.connect() as db:
            changed = db.execute(f"UPDATE {self.prefix}users SET password_hash={p},must_change_password=0 WHERE id={p} AND must_change_password=1 RETURNING id", (password_hash,user_id)).fetchone()
            advanced = db.execute(f"UPDATE {self.prefix}auth_challenges SET stage='setup_mfa' WHERE digest={p} AND user_id={p} AND stage='setup_password' AND attempts<5 AND expires_at>{p} RETURNING digest", (challenge_digest,user_id,datetime.now(timezone.utc).isoformat())).fetchone()
            if not changed or not advanced:
                raise PermissionError("Initial password already changed")
            db.execute(f"DELETE FROM {self.prefix}auth_challenges WHERE user_id={p} AND digest<>{p}", (user_id,challenge_digest))

    def set_enabled(self, user_id: str, enabled: bool):
        target=self.user('id',user_id)
        if not target or target['registration_status']!='active':raise ValueError('Only activated accounts may be enabled')
        p = self.param
        with self.storage.connect() as db:
            db.execute(f"UPDATE {self.prefix}users SET enabled={p} WHERE id={p}", (int(enabled),user_id))
            if not enabled:
                db.execute(f"DELETE FROM {self.prefix}auth_sessions WHERE user_id={p}", (user_id,))
                db.execute(f"DELETE FROM {self.prefix}auth_challenges WHERE user_id={p}", (user_id,))

    def throttle(self, key: str, failed: bool):
        now = datetime.now(timezone.utc)
        if not failed:
            self.execute(f"DELETE FROM {self.prefix}login_attempts WHERE key=?", (key,))
            return
        until = (now + timedelta(minutes=15)).isoformat()
        self.execute(f"INSERT INTO {self.prefix}login_attempts(key,count,window_until) VALUES (?,1,?) ON CONFLICT(key) DO UPDATE SET count=CASE WHEN {self.prefix}login_attempts.window_until>? THEN {self.prefix}login_attempts.count+1 ELSE 1 END,window_until=CASE WHEN {self.prefix}login_attempts.window_until>? THEN {self.prefix}login_attempts.window_until ELSE excluded.window_until END", (key, until, now.isoformat(), now.isoformat()))

    def throttled(self, key: str):
        row = self.execute(f"SELECT count,window_until FROM {self.prefix}login_attempts WHERE key=?", (key,), one=True)
        return bool(row and row[0] >= 5 and datetime.fromisoformat(row[1]) > datetime.now(timezone.utc))

    def prune_expired(self):
        now=datetime.now(timezone.utc).isoformat()
        with self.storage.connect() as db:
            p=self.param
            db.execute(f"DELETE FROM {self.prefix}auth_sessions WHERE expires_at<{p}",(now,))
            db.execute(f"DELETE FROM {self.prefix}auth_challenges WHERE expires_at<{p}",(now,))
            db.execute(f"DELETE FROM {self.prefix}login_attempts WHERE window_until<{p}",(now,))


class PendingAccount(PermissionError):
    pass


class AuthService:
    def __init__(self, storage, encryption_key: str):
        if not encryption_key:
            raise RuntimeError("AUTH_ENCRYPTION_KEY is required; run scripts/bootstrap.py")
        self.cipher = Fernet(encryption_key.encode())
        self.store = AccountStore(storage)
        self.store.prune_expired()
        storage.account_store = self.store
        self._bootstrap_digest = None
        self._bootstrap_expires = None
        self._bootstrap_lock = threading.Lock()

    def bootstrap_admin(self, email: str, password: str):
        return self.store.create_user(email, "Sam", "admin", password, first_admin=True)

    def prepare_bootstrap(self):
        if self.store.count_admins():return None
        code=secrets.token_urlsafe(32)
        self._bootstrap_digest=digest(code)
        self._bootstrap_expires=datetime.now(timezone.utc)+timedelta(minutes=15)
        return code

    def bootstrap_from_browser(self,code,email,name,password):
        with self._bootstrap_lock:
            key=digest('bootstrap-attempts')
            if self.store.throttled(key):raise PermissionError('Setup temporarily limited')
            valid=self._bootstrap_digest and self._bootstrap_expires>datetime.now(timezone.utc) and hmac.compare_digest(digest(code.strip()),self._bootstrap_digest)
            if not valid:
                self.store.throttle(key,True)
                raise PermissionError('Invalid or expired local setup code')
            try:
                self.store.create_user(email,name,'admin',password,first_admin=True,chosen_password=True,claim_pending=True)
            except BootstrapAccountConflict:
                self.store.throttle(key, True)
                raise
            self._bootstrap_digest=None
            return self.login(email,password)

    def register(self,email,name,password):
        key=digest('registration:'+email.strip().lower())
        if self.store.throttled(key):raise PermissionError('Registration temporarily limited')
        self.store.throttle(key,True)
        self.store.create_user(email,name,'member',password,pending=True)

    def login(self, email: str, password: str):
        email = email.strip().lower()
        account_key = digest("login:account:" + email)
        if self.store.throttled(account_key):
            raise PermissionError("Sign-in temporarily limited")
        user = self.store.user("email", email)
        valid_password=verify_password(password,user['password_hash']) if user else verify_password(password,_DUMMY_PASSWORD_HASH)
        if user and valid_password and user['registration_status']=='pending':
            raise PendingAccount('Administrator activation is required')
        if not user or not user["enabled"] or not valid_password:
            self.store.throttle(account_key, True)
            raise PermissionError("Invalid credentials")
        self.store.throttle(account_key, False)
        stage = "setup_password" if user["must_change_password"] else "setup_mfa" if user["last_totp_step"] < 0 else "mfa"
        secret = secrets.token_urlsafe(32)
        expiry = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        self.store.execute(f"INSERT INTO {self.store.prefix}auth_challenges VALUES (?,?,?,?,0)", (digest(secret), user["id"], stage, expiry))
        return {"challenge": secret, "stage": stage}

    def challenge(self, secret: str, stage: str):
        row = self.store.execute(f"SELECT user_id,stage,expires_at,attempts FROM {self.store.prefix}auth_challenges WHERE digest=?", (digest(secret),), one=True)
        if not row or row[1] != stage or datetime.fromisoformat(row[2]) <= datetime.now(timezone.utc) or row[3] >= 5:
            raise PermissionError("Invalid or expired sign-in challenge")
        user = self.store.user("id", row[0])
        if not user or not user["enabled"] or user["registration_status"] != "active":
            raise PermissionError("Invalid sign-in challenge")
        return user

    def fail_challenge(self, secret: str):
        self.store.execute(f"UPDATE {self.store.prefix}auth_challenges SET attempts=attempts+1 WHERE digest=?", (digest(secret),))

    def change_initial_password(self, secret: str, password: str):
        user = self.challenge(secret, "setup_password")
        if verify_password(password, user["password_hash"]):
            raise ValueError("Choose a different password")
        self.store.change_initial_password(user["id"],secret,hash_password(password))

    def start_mfa(self, secret: str):
        user = self.challenge(secret, "setup_mfa")
        if not user["totp_secret"]:
            raw = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
            self.store.update(user["id"], totp_secret=self.cipher.encrypt(raw.encode()).decode())
        else:
            raw = self.cipher.decrypt(user["totp_secret"].encode()).decode()
        uri = f"otpauth://totp/Muster:{quote(user['email'])}?secret={raw}&issuer=Muster&algorithm=SHA1&digits=6&period=30"
        return {"secret": raw, "uri": uri}

    def finish_mfa_setup(self, secret: str, code: str):
        user = self.challenge(secret, "setup_mfa")
        if not user["totp_secret"]:
            raise ValueError("Authenticator setup has not started")
        step = valid_totp(self.cipher.decrypt(user["totp_secret"].encode()).decode(), code)
        if step is None:
            self.fail_challenge(secret)
            raise PermissionError("Invalid authenticator code")
        codes = [secrets.token_hex(16) for _ in range(10)]
        return self.store.complete_setup(user["id"],secret,step,codes), codes

    def verify_mfa(self, secret: str, code: str):
        user = self.challenge(secret, "mfa")
        raw = self.cipher.decrypt(user["totp_secret"].encode()).decode()
        step = valid_totp(raw, code, user["last_totp_step"])
        if step is None:
            row = self.store.execute(f"DELETE FROM {self.store.prefix}recovery_codes WHERE user_id=? AND digest=? RETURNING digest", (user["id"], digest(code)), one=True)
            if not row:
                self.fail_challenge(secret)
                raise PermissionError("Invalid authenticator code")
        else:
            if not self.store.claim_totp(user["id"],step):
                self.fail_challenge(secret)
                raise PermissionError("Authenticator code already used")
        self.store.execute(f"DELETE FROM {self.store.prefix}auth_challenges WHERE digest=?", (digest(secret),))
        return self.session(user["id"])

    def session(self, user_id: str):
        self.store.prune_expired()
        secret, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        expiry = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
        self.store.execute(f"INSERT INTO {self.store.prefix}auth_sessions VALUES (?,?,?,?)", (digest(secret), user_id, digest(csrf), expiry))
        return secret, csrf

    def identify(self, secret: str | None, csrf: str | None = None):
        if not secret:
            return None
        row = self.store.execute(f"SELECT user_id,csrf_digest,expires_at FROM {self.store.prefix}auth_sessions WHERE digest=?", (digest(secret),), one=True)
        if not row:
            return None
        if datetime.fromisoformat(row[2]) <= datetime.now(timezone.utc):
            self.logout(secret)
            return None
        if csrf is not None and not hmac.compare_digest(digest(csrf), row[1]):
            return None
        user = self.store.user("id", row[0])
        return user if user and user["enabled"] and user["registration_status"] == "active" else None

    def logout(self, secret: str | None):
        if secret:
            self.store.execute(f"DELETE FROM {self.store.prefix}auth_sessions WHERE digest=?", (digest(secret),))

    def fresh_admin(self, user: dict, password: str, code: str):
        key = digest("admin:" + user["id"])
        if self.store.throttled(key) or user["role"] != "admin" or not user.get("totp_secret") or not verify_password(password, user["password_hash"]):
            self.store.throttle(key, True)
            raise PermissionError("Administrator reauthentication required")
        step = valid_totp(self.cipher.decrypt(user["totp_secret"].encode()).decode(), code)
        if step is None:
            self.store.throttle(key, True)
            raise PermissionError("Administrator reauthentication required")
        self.store.throttle(key, False)
        self.store.update(user["id"], last_totp_step=max(step,user["last_totp_step"]))
