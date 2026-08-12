from __future__ import annotations

import hashlib
import hmac


def create_proof(token: str, role: str, challenge: str) -> str:
    if role not in {"client", "server"}:
        raise ValueError("invalid HMAC role")
    payload = f"{role}:{challenge}".encode()
    return hmac.new(token.encode(), payload, hashlib.sha256).hexdigest()


def verify_proof(token: str, role: str, challenge: str, proof: str) -> bool:
    return hmac.compare_digest(create_proof(token, role, challenge), proof)
