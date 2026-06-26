"""Vercel serverless entry point — WSGI adapter for glm2api.

Exposes a WSGI `app` callable that Vercel's Python runtime discovers
automatically.  Delegates to the same core business logic (config /
GLMWebClient / translator) but without ThreadingHTTPServer.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from http import HTTPStatus
from io import BytesIO
from logging import Logger
from urllib.parse import parse_qs, urlparse

# Ensure the src directory is on sys.path so imports work on Vercel.
_src = os.path.join(os.path.dirname(__file__), "..", "src")
if _src not in sys.path:
    sys.path.insert(0, os.path.abspath(_src))

from glm2api.config import AppConfig, load_config
from glm2api.logging_utils import get_logger, setup_logging
from glm2api.services.glm_client import GLMWebClient, QueueTimeoutError, UpstreamAPIError

# One-time init (reused across cold starts).
_config: AppConfig | None = None
_client: GLMWebClient | None = None
_logger: Logger | None = None


def _init_once() -> tuple[AppConfig, GLMWebClient, Logger]:
    global _config, _client, _logger
    if _config is None:
        # Force guest mode on Vercel (no persistent token storage).
        os.environ.setdefault("GLM_USE_GUEST_REFRESH_TOKEN", "true")
        os.environ.setdefault("GLM_MAX_CONCURRENCY", "3")
        setup_logging("WARNING")
        _config = load_config()
        _logger = get_logger("glm2api.vercel")
        _client = GLMWebClient(config=_config, logger=_logger)
    return _config, _client, _logger


def _json_response(start_response, status: str, payload: dict) -> list[bytes]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
        ("Access-Control-Allow-Origin", "*"),
        ("Access-Control-Allow-Headers", "Authorization, Content-Type, x-api-key"),
        ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
    ]
    start_response(status, headers)
    return [body]


def _sse_response(start_response, status: str, chunks) -> list[bytes]:
    """Return SSE stream — note: Vercel buffers, so true streaming is limited."""
    headers = [
        ("Content-Type", "text/event-stream; charset=utf-8"),
        ("Cache-Control", "no-cache"),
        ("Connection", "close"),
        ("Access-Control-Allow-Origin", "*"),
    ]
    start_response(status, headers)
    parts: list[bytes] = []
    for chunk in chunks:
        if chunk:
            parts.append(chunk if isinstance(chunk, bytes) else chunk.encode("utf-8"))
    if not parts or b"data: [DONE]" not in parts[-1]:
        parts.append(b"data: [DONE]\n\n")
    return parts


def _path_without_query(environ: dict) -> str:
    return urlparse(environ.get("PATH_INFO", "/")).path


def _read_body(environ: dict) -> bytes:
    try:
        length = int(environ.get("CONTENT_LENGTH", "0"))
    except ValueError:
        length = 0
    return environ["wsgi.input"].read(length) if length else b"{}"


# ── WSGI application ─────────────────────────────────────────────────────────

def app(environ: dict, start_response) -> list[bytes]:
    config, client, logger = _init_once()
    method = environ.get("REQUEST_METHOD", "GET")
    path = _path_without_query(environ)

    # OPTIONS — CORS preflight
    if method == "OPTIONS":
        start_response("204 No Content", [
            ("Access-Control-Allow-Origin", "*"),
            ("Access-Control-Allow-Headers", "Authorization, Content-Type, x-api-key"),
            ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
        ])
        return [b""]

    try:
        # GET routes
        if method == "GET":
            if path == "/health":
                return _json_response(start_response, "200 OK", {"status": "ok"})

            if path == f"{config.api_prefix}/models":
                return _json_response(
                    start_response, "200 OK",
                    {
                        "object": "list",
                        "data": [
                            {"id": m, "object": "model", "owned_by": "glm2api"}
                            for m in config.exposed_models
                        ],
                    },
                )

        # POST routes
        if method == "POST":
            raw_body = _read_body(environ)
            try:
                payload = json.loads(raw_body.decode("utf-8"))
            except Exception:
                return _json_response(
                    start_response, "400 Bad Request",
                    {"error": {"message": "请求体不是合法 JSON"}},
                )

            if not isinstance(payload, dict):
                return _json_response(
                    start_response, "400 Bad Request",
                    {"error": {"message": "请求体必须是 JSON 对象"}},
                )

            _route = path

            # Image generation
            if _route == f"{config.api_prefix}/images/generations":
                if not payload.get("prompt"):
                    return _json_response(
                        start_response, "400 Bad Request",
                        {"error": {"message": "图片生成请求必须包含 prompt"}},
                    )
                result = client.generate_images(payload)
                return _json_response(start_response, "200 OK", result)

            # Chat completions
            if _route == f"{config.api_prefix}/chat/completions":
                if not isinstance(payload.get("messages"), list) or not payload.get("model"):
                    return _json_response(
                        start_response, "400 Bad Request",
                        {"error": {"message": "请求体必须包含 model 和 messages"}},
                    )

                if payload.get("stream"):
                    stream_iter = client.stream_chat_completion(payload)
                    return _sse_response(start_response, "200 OK", stream_iter)

                result, _ = client.chat_completion(payload)
                return _json_response(start_response, "200 OK", result)

        # Not found
        return _json_response(
            start_response, "404 Not Found",
            {"error": {"message": "Not Found"}},
        )

    except QueueTimeoutError as exc:
        return _json_response(
            start_response, "503 Service Unavailable",
            {"error": {"message": str(exc), "type": "queue_timeout"}},
        )
    except UpstreamAPIError as exc:
        return _json_response(
            start_response, "502 Bad Gateway",
            {"error": {"message": str(exc), "type": "upstream_error"}},
        )
    except Exception as exc:
        logger.error("未处理异常 path=%s error=%s\n%s", path, exc, traceback.format_exc())
        return _json_response(
            start_response, "502 Bad Gateway",
            {"error": {"message": "服务内部错误", "type": type(exc).__name__}},
        )
