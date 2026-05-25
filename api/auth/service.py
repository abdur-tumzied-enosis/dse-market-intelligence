# api/auth/service.py
from __future__ import annotations
import bcrypt
import asyncpg


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode(), salt).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


async def create_user(
    pool: asyncpg.Pool,
    email: str,
    password: str,
    full_name: str | None = None,
) -> dict:
    hashed = hash_password(password)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO users (email, hashed_password, full_name)
            VALUES ($1, $2, $3)
            RETURNING id, email, full_name, tier, is_active, created_at
            """,
            email,
            hashed,
            full_name,
        )
    return dict(row)


async def authenticate_user(
    pool: asyncpg.Pool,
    email: str,
    password: str,
) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, email, hashed_password, full_name, tier, is_active
            FROM users WHERE email = $1
            """,
            email,
        )
    if not row:
        return None
    if not verify_password(password, row["hashed_password"]):
        return None
    return dict(row)


async def get_user_by_id(pool: asyncpg.Pool, user_id: int) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, email, full_name, tier, is_active FROM users WHERE id = $1",
            user_id,
        )
    return dict(row) if row else None
