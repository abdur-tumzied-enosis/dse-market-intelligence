# api/auth/router.py
from __future__ import annotations
import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from api.auth.jwt import create_access_token, create_refresh_token, decode_token
from api.auth.service import authenticate_user, create_user, get_user_by_id
from api.deps import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str | None = None


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: int
    email: str
    full_name: str | None
    tier: str


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/register", response_model=UserOut, status_code=201)
async def register(body: UserCreate, pool=Depends(get_db)):
    try:
        user = await create_user(pool, body.email, body.password, body.full_name)
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="Email already registered")
    return user


@router.post("/login", response_model=TokenPair)
async def login(body: UserLogin, pool=Depends(get_db)):
    user = await authenticate_user(pool, body.email, body.password)
    if not user or not user["is_active"]:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return TokenPair(
        access_token=create_access_token(user["id"], user["email"], user["tier"]),
        refresh_token=create_refresh_token(user["id"]),
    )


@router.post("/refresh", response_model=TokenPair)
async def refresh_tokens(body: RefreshRequest, pool=Depends(get_db)):
    try:
        payload = decode_token(body.refresh_token)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Not a refresh token")
    user = await get_user_by_id(pool, int(payload["sub"]))
    if not user or not user["is_active"]:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return TokenPair(
        access_token=create_access_token(user["id"], user["email"], user["tier"]),
        refresh_token=create_refresh_token(user["id"]),
    )
