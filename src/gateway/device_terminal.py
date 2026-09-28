"""Public ticket-authenticated WS route; HTTP App routers require bearer Requests."""

from fastapi import APIRouter, WebSocket

router = APIRouter()


@router.websocket("/ws/v1/terminals/{session_id}")
async def terminal(socket: WebSocket, session_id: str):
    from src.apps.device_access.backend.terminal import connect

    await connect(socket, session_id)
