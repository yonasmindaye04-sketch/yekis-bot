"""Vercel Serverless Function entrypoint with automatic path normalization and root redirect."""

from __future__ import annotations

from fastapi.responses import RedirectResponse

from app.main import app as fastapi_app


async def app(scope, receive, send):
    if scope.get("type") == "http":
        path = scope.get("path", "")
        # Normalize trailing /api/index.py or bare /api rewrites from Vercel
        if path in ("", "/", "/api", "/api/", "/api/index.py"):
            response = RedirectResponse(url="/admin/", status_code=307)
            await response(scope, receive, send)
            return

        if path.startswith("/api/index.py"):
            normalized = path[len("/api/index.py"):]
            scope["path"] = normalized if normalized else "/"

    await fastapi_app(scope, receive, send)


__all__ = ["app"]
