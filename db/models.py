"""
Database models and geo queries for NearByClub bot.
Uses SQLAlchemy 2 async + PostGIS for proximity searches.
"""
import os
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, String, Text, select, delete
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from geoalchemy2 import Geography
from geoalchemy2.functions import ST_DWithin, ST_AsGeoJSON, ST_MakePoint, ST_Distance
from sqlalchemy import func
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost/nearbyclub")

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tg_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(64), nullable=True)
    first_name = Column(String(128), nullable=True)
    animal_emoji = Column(String(8), default="🦊")
    animal_name = Column(String(32), default="Fox")
    animal_color = Column(String(16), default="#FF6B35")
    animal_assigned_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    is_banned = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow)
    message_count_today = Column(Integer, default=0)
    message_count_reset_at = Column(DateTime, default=datetime.utcnow)


class Location(Base):
    __tablename__ = "locations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    # PostGIS Geography point (lon, lat, SRID=4326)
    point = Column(Geography(geometry_type="POINT", srid=4326), nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime)  # Location TTL


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sender_id = Column(Integer, nullable=False, index=True)
    text = Column(Text, nullable=False)
    sent_at = Column(DateTime, default=datetime.utcnow)
    recipient_count = Column(Integer, default=0)
    radius_m = Column(Integer, default=500)


# ─── DB initialization ────────────────────────────────────────────────────────

async def init_db():
    async with engine.begin() as conn:
        # Ensure PostGIS extension is loaded
        await conn.execute(func.text("CREATE EXTENSION IF NOT EXISTS postgis"))
        await Base.metadata.create_all(conn)


# ─── Animal persona assignment ─────────────────────────────────────────────────

ANIMAL_PERSONAS = [
    ("🦊", "Fox", "#FF6B35"),
    ("🐺", "Wolf", "#6B7A8D"),
    ("🌊", "Shark", "#4A90D9"),
    ("🐆", "Leopard", "#F5A623"),
    ("🦅", "Eagle", "#7B4F2E"),
    ("🦋", "Butterfly", "#B05CB1"),
    ("🐍", "Viper", "#417505"),
    ("🦁", "Lion", "#D4A017"),
    ("🐻", "Bear", "#8B4513"),
    ("🦜", "Parrot", "#E91E63"),
    ("🦑", "Squid", "#9C27B0"),
    ("🐬", "Dolphin", "#0097A7"),
]

import random

def assign_animal() -> tuple[str, str, str]:
    """Returns (emoji, name, hex_color). Resets every 24h."""
    return random.choice(ANIMAL_PERSONAS)


# ─── Location queries ──────────────────────────────────────────────────────────

async def upsert_location(
    session: AsyncSession,
    user_id: int,
    lat: float,
    lon: float,
    ttl_hours: int = 2
) -> None:
    """Store or update a user's location. Location expires after ttl_hours."""
    expires = datetime.utcnow() + timedelta(hours=ttl_hours)
    point_wkt = f"POINT({lon} {lat})"

    existing = await session.execute(
        select(Location).where(Location.user_id == user_id)
    )
    loc = existing.scalar_one_or_none()

    if loc:
        loc.point = point_wkt
        loc.updated_at = datetime.utcnow()
        loc.expires_at = expires
    else:
        loc = Location(
            user_id=user_id,
            point=point_wkt,
            expires_at=expires
        )
        session.add(loc)

    await session.commit()


async def find_nearby_user_ids(
    session: AsyncSession,
    lat: float,
    lon: float,
    radius_m: int = 500,
    exclude_user_id: Optional[int] = None,
    limit: int = 100
) -> list[int]:
    """Return user_ids within radius_m meters, excluding expired locations."""
    ref_point = f"SRID=4326;POINT({lon} {lat})"
    now = datetime.utcnow()

    stmt = (
        select(Location.user_id)
        .where(
            ST_DWithin(Location.point, ref_point, radius_m / 111320.0),  # degrees approx
            Location.expires_at > now
        )
        .limit(limit)
    )

    if exclude_user_id is not None:
        stmt = stmt.where(Location.user_id != exclude_user_id)

    result = await session.execute(stmt)
    return [row[0] for row in result.fetchall()]


async def get_heatmap_points(
    session: AsyncSession,
    lat: float,
    lon: float,
    radius_m: int = 1000
) -> list[dict]:
    """
    Return fuzzed (±100m) location points for heatmap display.
    Never exposes exact coordinates.
    """
    ref_point = f"SRID=4326;POINT({lon} {lat})"
    now = datetime.utcnow()

    stmt = select(
        ST_AsGeoJSON(Location.point).label("geojson"),
        Location.updated_at
    ).where(
        ST_DWithin(Location.point, ref_point, radius_m / 111320.0),
        Location.expires_at > now
    )

    result = await session.execute(stmt)
    rows = result.fetchall()

    points = []
    for row in rows:
        import json
        coords = json.loads(row.geojson)["coordinates"]
        # Fuzz coordinates by ±0.001 degrees (~100m) for privacy
        fuzz_lat = coords[1] + random.uniform(-0.001, 0.001)
        fuzz_lon = coords[0] + random.uniform(-0.001, 0.001)
        points.append({"lat": round(fuzz_lat, 5), "lon": round(fuzz_lon, 5)})

    return points


async def count_nearby(
    session: AsyncSession,
    lat: float,
    lon: float,
    radius_m: int = 500,
    exclude_user_id: Optional[int] = None
) -> int:
    ref_point = f"SRID=4326;POINT({lon} {lat})"
    now = datetime.utcnow()

    stmt = select(func.count()).select_from(Location).where(
        ST_DWithin(Location.point, ref_point, radius_m / 111320.0),
        Location.expires_at > now
    )
    if exclude_user_id:
        stmt = stmt.where(Location.user_id != exclude_user_id)

    result = await session.execute(stmt)
    return result.scalar() or 0


async def cleanup_expired_locations(session: AsyncSession) -> int:
    result = await session.execute(
        delete(Location).where(Location.expires_at < datetime.utcnow())
    )
    await session.commit()
    return result.rowcount


# ─── User queries ──────────────────────────────────────────────────────────────

async def get_or_create_user(
    session: AsyncSession,
    tg_id: int,
    username: Optional[str],
    first_name: Optional[str]
) -> User:
    result = await session.execute(select(User).where(User.tg_id == tg_id))
    user = result.scalar_one_or_none()

    if not user:
        emoji, name, color = assign_animal()
        user = User(
            tg_id=tg_id,
            username=username,
            first_name=first_name,
            animal_emoji=emoji,
            animal_name=name,
            animal_color=color,
            animal_assigned_at=datetime.utcnow()
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    else:
        # Refresh animal every 24h
        if (datetime.utcnow() - user.animal_assigned_at).total_seconds() > 86400:
            emoji, name, color = assign_animal()
            user.animal_emoji = emoji
            user.animal_name = name
            user.animal_color = color
            user.animal_assigned_at = datetime.utcnow()
        user.last_seen = datetime.utcnow()
        user.username = username
        await session.commit()

    return user


async def get_all_active_user_tg_ids(session: AsyncSession) -> list[int]:
    result = await session.execute(
        select(User.tg_id).where(User.is_active == True, User.is_banned == False)
    )
    return [row[0] for row in result.fetchall()]
