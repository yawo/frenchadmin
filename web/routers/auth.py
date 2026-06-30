from fastapi import APIRouter, Depends, HTTPException

from database.database_manage import get_connection
from web.auth.dependencies import require_auth
from web.auth.schemas import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from web.auth.security import create_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse)
def register(req: RegisterRequest):
    pw_hash = hash_password(req.password)
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE username = %s", (req.username,))
        if cur.fetchone():
            raise HTTPException(status_code=409, detail="Username already exists")
        cur.execute(
            "INSERT INTO users (username, password_hash) VALUES (%s, %s) RETURNING id",
            (req.username, pw_hash),
        )
        user_id = str(cur.fetchone()[0])
        conn.commit()

    token = create_token(user_id, req.username)
    return TokenResponse(token=token, username=req.username)


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, password_hash FROM users WHERE username = %s", (req.username,))
        row = cur.fetchone()

    if not row or not verify_password(req.password, row[1]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_token(str(row[0]), req.username)
    return TokenResponse(token=token, username=req.username)


@router.get("/me", response_model=UserResponse)
def me(user: dict = Depends(require_auth)):
    return UserResponse(id=user["id"], username=user["username"])
