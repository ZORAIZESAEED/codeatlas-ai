from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.security import create_access_token
from app.auth.service import AuthService, InvalidCredentials
from app.db.session import get_session
from app.schemas import LoginRequest, RegisterRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, session=Depends(get_session)):
    """Exchange a username and password for an access token."""
    try:
        user = AuthService(session).authenticate(payload.username, payload.password)
    except InvalidCredentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenResponse(access_token=create_access_token(str(user.id)))


@router.post("/register", status_code=201)
def register(payload: RegisterRequest, session=Depends(get_session)):
    try:
        user = AuthService(session).register(payload.username, payload.email, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"id": user.id, "username": user.username}
