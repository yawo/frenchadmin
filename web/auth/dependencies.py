from __future__ import annotations

import jwt as pyjwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from database.database_manage import get_connection
from web.auth.security import decode_token

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict | None:
    if credentials is None:
        return None
    try:
        payload = decode_token(credentials.credentials)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, username, llm_model, llm_base_url, llm_api_key_encrypted FROM users WHERE id = %s",
            (payload["sub"],),
        )
        row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=401, detail="User not found")

    return {
        "id": str(row[0]),
        "username": row[1],
        "llm_model": row[2],
        "llm_base_url": row[3],
        "llm_api_key_encrypted": row[4],
    }


def require_auth(user: dict | None = Depends(get_current_user)) -> dict:
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user
