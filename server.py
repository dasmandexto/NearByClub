"""
FastAPI server — serves the WebApp, heatmap API, and handles WebSocket for real-time updates.
"""
import json
import os
from datetime import datetime
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

from db.models import (
    async_session, init_db, get_or_create_user,
    upsert_location, find_nearby_user_ids, get_heatmap_points, count_nearby
)

load_dotenv()

app = FastAPI(title="NearByClub Server", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), "webapp")
app.mount("/static", StaticFiles(directory=WEBAPP_DIR), name="static")

# WebSocket connections for real-time heatmap pushes
active_connections: dict[str, WebSocket] = {}


@app.on_event("startup")
async def on_startup():
    await init_db()


@app.get("/", response_class=HTMLResponse)
async def serve_webapp():
    with open(os.path.join(WEBAPP_DIR, "index.html"), encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# ─── API Models ───────────────────────────────────────────────────────────────

class LocationUpdate(BaseModel):
    tg_id: int
    lat: float
    lon: float
    radius_m: int = 500  # 500m default, max 1000m


class HeatmapRequest(BaseModel):
    lat: float
    lon: float
    radius_m: int = 1000


# ─── REST Endpoints ───────────────────────────────────────────────────────────

@app.post("/api/location")
async def update_location(payload: LocationUpdate):
    """Store/update user location and return nearby count."""
    if payload.radius_m > 1000:
        payload.radius_m = 1000  # hard cap at 1km

    async with async_session() as session:
        await upsert_location(session, payload.tg_id, payload.lat, payload.lon)
        count = await count_nearby(
            session, payload.lat, payload.lon,
            radius_m=payload.radius_m,
            exclude_user_id=payload.tg_id
        )

    return {"nearby_count": count, "radius_m": payload.radius_m}


@app.post("/api/heatmap")
async def get_heatmap(payload: HeatmapRequest):
    """Return fuzzed heatmap points (privacy-safe) for Leaflet heatmap layer."""
    async with async_session() as session:
        points = await get_heatmap_points(
            session, payload.lat, payload.lon, radius_m=min(payload.radius_m, 1000)
        )
    return {"points": points, "count": len(points)}


@app.get("/api/status")
async def status():
    return {
        "status": "online",
        "active_ws": len(active_connections),
        "time": datetime.utcnow().isoformat()
    }


# ─── WebSocket for real-time heatmap push ────────────────────────────────────

@app.websocket("/ws/{tg_id}")
async def websocket_endpoint(websocket: WebSocket, tg_id: str):
    await websocket.accept()
    active_connections[tg_id] = websocket
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "location":
                    lat = msg["lat"]
                    lon = msg["lon"]
                    radius_m = min(int(msg.get("radius_m", 500)), 1000)

                    async with async_session() as session:
                        await upsert_location(session, int(tg_id), lat, lon)
                        points = await get_heatmap_points(session, lat, lon, radius_m=1000)
                        count = await count_nearby(session, lat, lon, radius_m=radius_m, exclude_user_id=int(tg_id))

                    await websocket.send_text(json.dumps({
                        "type": "heatmap_update",
                        "points": points,
                        "nearby_count": count,
                        "radius_m": radius_m
                    }))
            except (json.JSONDecodeError, KeyError, ValueError):
                pass
    except WebSocketDisconnect:
        active_connections.pop(tg_id, None)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
