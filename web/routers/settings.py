from fastapi import APIRouter, Depends

from database.database_manage import get_connection
from web.auth.dependencies import require_auth
from web.auth.schemas import LLMSettingsRequest, LLMSettingsResponse
from web.auth.security import encrypt_api_key

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/llm", response_model=LLMSettingsResponse)
def get_llm_settings(user: dict = Depends(require_auth)):
    return LLMSettingsResponse(
        llm_model=user["llm_model"],
        llm_base_url=user["llm_base_url"],
        has_api_key=bool(user["llm_api_key_encrypted"]),
    )


@router.put("/llm", response_model=LLMSettingsResponse)
def update_llm_settings(req: LLMSettingsRequest, user: dict = Depends(require_auth)):
    all_null = req.llm_model is None and req.llm_base_url is None and req.llm_api_key is None

    with get_connection() as conn:
        cur = conn.cursor()
        if all_null:
            cur.execute(
                """UPDATE users
                   SET llm_model = NULL, llm_base_url = NULL, llm_api_key_encrypted = NULL, updated_at = NOW()
                   WHERE id = %s""",
                (user["id"],),
            )
        elif req.llm_api_key:
            encrypted_key = encrypt_api_key(req.llm_api_key)
            cur.execute(
                """UPDATE users
                   SET llm_model = %s, llm_base_url = %s, llm_api_key_encrypted = %s, updated_at = NOW()
                   WHERE id = %s""",
                (req.llm_model, req.llm_base_url, encrypted_key, user["id"]),
            )
        else:
            cur.execute(
                """UPDATE users
                   SET llm_model = %s, llm_base_url = %s, updated_at = NOW()
                   WHERE id = %s""",
                (req.llm_model, req.llm_base_url, user["id"]),
            )
        conn.commit()

    has_key = bool(req.llm_api_key) or (not all_null and bool(user["llm_api_key_encrypted"]))
    return LLMSettingsResponse(
        llm_model=req.llm_model,
        llm_base_url=req.llm_base_url,
        has_api_key=has_key,
    )
