"""Password hashing (PBKDF2-SHA256), JWT issue/verify, role and tenant checks."""

import base64
import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from typing import Optional

import jwt

JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_TTL_SECONDS = int(os.getenv("JWT_TTL_SECONDS", "3600"))
JWT_ISSUER = "fleetpulse-api"
PBKDF2_ROUNDS = 200_000


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        _, rounds, salt_b64, dk_b64 = encoded.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt_b64), int(rounds))
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except Exception:
        return False


@dataclass(frozen=True)
class Principal:
    user_id: str
    email: str
    role: str
    tenant_id: Optional[str]

    @property
    def is_admin(self) -> bool:
        return self.role == "ADMIN"


def issue_token(p: Principal) -> str:
    if not JWT_SECRET:
        raise RuntimeError("JWT_SECRET is not configured")
    now = int(time.time())
    claims = {"sub": p.user_id, "email": p.email, "role": p.role, "tenant_id": p.tenant_id,
              "iss": JWT_ISSUER, "iat": now, "exp": now + JWT_TTL_SECONDS}
    return jwt.encode(claims, JWT_SECRET, algorithm="HS256")


def decode_token(token: str) -> Principal:
    c = jwt.decode(token, JWT_SECRET, algorithms=["HS256"], issuer=JWT_ISSUER, options={"require": ["exp", "sub", "iss"]})
    return Principal(c["sub"], c["email"], c["role"], c.get("tenant_id"))
