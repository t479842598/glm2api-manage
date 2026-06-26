"""Admin panel — cookie-HMAC auth, config/token/logging/chat-test API routes.

Integrates into the existing ThreadingHTTPServer handler class (stdlib only).
"""
from __future__ import annotations

import hmac
import json
import time
import traceback
from hashlib import sha256
from http import HTTPStatus
from pathlib import Path
from typing import Any, Callable

from .config import AppConfig
from .logging_utils import get_buffered_logs

COOKIE_NAME = "glm2api_admin_session"
COOKIE_MAX_AGE = 60 * 60 * 12  # 12 hours
NO_STORE_HEADERS = {"Cache-Control": "no-store"}


# ── API Key record & store ──────────────────────────────────────────────────

class ApiKeyRecord:
    __slots__ = ("name", "key", "enabled", "created_at")

    def __init__(self, name: str, key: str, enabled: bool = True, created_at: str = "") -> None:
        self.name = name
        self.key = key
        self.enabled = enabled
        self.created_at = created_at or time.strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self, mask: bool = False) -> dict[str, object]:
        return {
            "name": self.name,
            "key": _mask(self.key, keep=6) if mask else self.key,
            "enabled": self.enabled,
            "created_at": self.created_at,
        }


class ApiKeyStore:
    def __init__(self) -> None:
        self._keys: dict[str, ApiKeyRecord] = {}

    @property
    def count(self) -> int:
        return len(self._keys)

    @property
    def active_count(self) -> int:
        return sum(1 for k in self._keys.values() if k.enabled)

    def get(self, name: str) -> ApiKeyRecord | None:
        return self._keys.get(name)

    def validate(self, raw_key: str) -> bool:
        if not raw_key:
            return False
        for rec in self._keys.values():
            if rec.enabled and rec.key == raw_key:
                return True
        return False

    def list_all(self) -> list[dict[str, object]]:
        return [r.to_dict(mask=True) for r in self._keys.values()]

    def add(self, rec: ApiKeyRecord) -> None:
        self._keys[rec.name] = rec

    def delete(self, name: str) -> bool:
        if name not in self._keys:
            return False
        del self._keys[name]
        return True

    def update(self, name: str, **fields: object) -> bool:
        rec = self._keys.get(name)
        if not rec:
            return False
        if "key" in fields:
            rec.key = str(fields["key"])
        if "enabled" in fields:
            rec.enabled = bool(fields["enabled"])
        return True

    def to_json(self) -> str:
        return json.dumps([r.to_dict() for r in self._keys.values()], ensure_ascii=False)

    def load_json(self, raw: str) -> None:
        if not raw or not raw.strip():
            return
        items = json.loads(raw)
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            self._keys[item["name"]] = ApiKeyRecord(
                name=str(item["name"]),
                key=str(item.get("key", "")),
                enabled=bool(item.get("enabled", True)),
                created_at=str(item.get("created_at", "")),
            )


def _persist_api_keys(handler) -> None:
    """Persist api keys into os.environ (server.py uses env to re-read)."""
    import os
    store: ApiKeyStore = handler._admin_api_key_store
    json_val = store.to_json() if store.count else ""
    os.environ["GLM2API_API_KEYS"] = json_val
    # Also try to write back to .env file
    try:
        _write_env_file("GLM2API_API_KEYS", json_val)
    except Exception:
        pass


def _write_env_file(key: str, value: str | None) -> None:
    """Rewrite a single key in the .env file."""
    env_path = Path(".env")
    if not env_path.exists():
        return
    content = env_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    updated = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value or ''}"
            updated = True
            break
    if not updated:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"{key}={value or ''}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── HMAC helpers ────────────────────────────────────────────────────────────

def _sign(secret: str, issued_at: str) -> str:
    return hmac.new(secret.encode(), issued_at.encode(), sha256).hexdigest()


def _cookie_value(secret: str) -> str:
    issued_at = str(int(time.time()))
    return f"{issued_at}.{_sign(secret, issued_at)}"


def _parse_cookie(cookie_header: str | None, name: str) -> str:
    if not cookie_header:
        return ""
    for part in cookie_header.split(";"):
        part = part.strip()
        if part.startswith(f"{name}="):
            return part[len(name) + 1 :]
    return ""


# ── API response helper ─────────────────────────────────────────────────────

def _api_ok(data: object = None, msg: str = "ok") -> dict[str, object]:
    return {"code": 0, "msg": msg, "data": data or {}}


def _api_err(msg: str, code: int = 1) -> dict[str, object]:
    return {"code": code, "msg": msg, "data": {}}


# ── Mask helper ─────────────────────────────────────────────────────────────

def _mask(value: str, keep: int = 6) -> str:
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}...{value[-keep:]}"


# ── Admin auth check ────────────────────────────────────────────────────────

def _admin_secret(config: AppConfig) -> str:
    return config.admin_key


def _check_admin_auth(headers: dict[str, str], config: AppConfig) -> bool:
    secret = _admin_secret(config)
    if not secret:
        return False
    cookie_header = headers.get("Cookie", "")
    raw = _parse_cookie(cookie_header, COOKIE_NAME)
    try:
        issued_at, signature = raw.split(".", 1)
        issued_ts = int(issued_at)
    except ValueError:
        return False
    if int(time.time()) - issued_ts > COOKIE_MAX_AGE:
        return False
    return hmac.compare_digest(signature, _sign(secret, issued_at))


# ── Config payload ──────────────────────────────────────────────────────────

def _config_payload(config: AppConfig) -> dict[str, object]:
    """Build config overview data for the admin panel."""
    prefixed_tokens: list[str] = []
    masked_tokens: list[str] = []
    for i, tok in enumerate(config.glm_refresh_tokens):
        if tok == "__glm_guest__":
            prefixed_tokens.append(f"🧑‍💻 游客-{i+1}")
        else:
            prefixed_tokens.append(tok[:12] if len(tok) > 12 else tok)
        masked_tokens.append(_mask(tok, keep=8) if tok != "__glm_guest__" else "游客账号")

    token_source = "游客模式" if config.glm_use_guest_refresh_token else (
        "token.txt" if config.token_file_path.exists() else ".env GLM_REFRESH_TOKEN"
    )

    return {
        "host": config.host,
        "port": config.port,
        "api_prefix": config.api_prefix,
        "log_level": config.log_level,
        "debug_dump_all": config.debug_dump_all,
        "request_timeout": config.request_timeout,
        "glm_base_url": config.glm_base_url,
        "glm_use_guest_refresh_token": config.glm_use_guest_refresh_token,
        "token_source": token_source,
        "token_count": len(config.glm_refresh_tokens),
        "tokens": [
            {"index": i + 1, "masked": m, "prefix": p}
            for i, (m, p) in enumerate(zip(masked_tokens, prefixed_tokens))
        ],
        "glm_assistant_id": config.glm_assistant_id,
        "glm_image_assistant_id": config.glm_image_assistant_id,
        "glm_max_concurrency": config.glm_max_concurrency,
        "glm_delete_conversation": config.glm_delete_conversation,
        "glm_busy_max_retries": config.glm_busy_max_retries,
        "glm_guest_max_retries": config.glm_guest_max_retries,
        "model_count": len(config.exposed_models),
        "models": config.exposed_models[:20],
        "server_api_keys_configured": bool(config.server_api_keys),
        "server_api_keys_count": len(config.server_api_keys),
        "admin_key_configured": bool(config.admin_key),
        "admin_key_masked": _mask(config.admin_key),
        "api_key_count": 0,
        "api_keys": [],
        "auth_enabled": bool(config.server_api_keys),
        "blocked_tool_names": config.blocked_tool_names,
        "cors_allow_origin": config.cors_allow_origin,
    }


# ── Request record store ────────────────────────────────────────────────────

class RequestRecord:
    __slots__ = ("id", "time", "method", "path", "model", "status", "duration_ms", "error")
    _counter = 0

    def __init__(
        self,
        method: str,
        path: str,
        model: str = "",
        status: int = 0,
        duration_ms: float = 0,
        error: str = "",
    ) -> None:
        RequestRecord._counter += 1
        self.id = RequestRecord._counter
        self.time = time.strftime("%H:%M:%S")
        self.method = method
        self.path = path
        self.model = model
        self.status = status
        self.duration_ms = round(duration_ms, 1)
        self.error = error

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id, "time": self.time, "method": self.method,
            "path": self.path, "model": self.model, "status": self.status,
            "duration_ms": self.duration_ms, "error": self.error,
        }


class RequestLogStore:
    def __init__(self, max_size: int = 500) -> None:
        self._records: list[RequestRecord] = []
        self._max = max_size

    def add(self, rec: RequestRecord) -> None:
        self._records.append(rec)
        while len(self._records) > self._max:
            self._records.pop(0)

    def list(self, since_id: int = 0, limit: int = 100) -> list[dict[str, object]]:
        items = self._records[:]
        if since_id:
            items = [r for r in items if r.id > since_id]
        return [r.to_dict() for r in items[-limit:]]

    def stats(self) -> dict[str, object]:
        total = len(self._records)
        if not total:
            return {"total": 0, "success": 0, "error": 0, "avg_ms": 0}
        ok = sum(1 for r in self._records if 200 <= r.status < 300)
        err = sum(1 for r in self._records if r.status >= 400)
        avg_ms = round(sum(r.duration_ms for r in self._records) / total, 1) if total else 0
        return {"total": total, "success": ok, "error": err, "avg_ms": avg_ms}

    def clear(self) -> None:
        self._records.clear()


# ── Admin handler mixin ─────────────────────────────────────────────────────
# These methods are designed to be mixed into the RequestHandler class
# created inside GLM2APIServer._build_handler().


def _write_html(handler, status: HTTPStatus, html: str, extra_headers: dict[str, str] | None = None) -> None:
    body = html.encode("utf-8")
    handler.send_response(status)
    handler._send_common_headers()
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    if extra_headers:
        for k, v in extra_headers.items():
            handler.send_header(k, v)
    handler.end_headers()
    handler.wfile.write(body)


def _write_admin_json(handler, data: dict[str, object], status: HTTPStatus = HTTPStatus.OK) -> None:
    handler._write_json(status, data)


def _read_admin_body(handler) -> dict[str, object]:
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length) if length else b"{}"
    return json.loads(raw.decode("utf-8")) if raw else {}


def _check_admin(handler) -> bool:
    config: AppConfig = handler._admin_config
    headers = {k: v for k, v in handler.headers.items()}
    return _check_admin_auth(headers, config)


def handle_admin_login(handler, config: AppConfig) -> None:
    body = _read_admin_body(handler)
    key = str(body.get("key", ""))
    if not config.admin_key:
        _write_admin_json(handler, _api_err("ADMIN_KEY not configured"), HTTPStatus.SERVICE_UNAVAILABLE)
        return
    if not hmac.compare_digest(key, config.admin_key):
        _write_admin_json(handler, _api_err("Invalid admin key"), HTTPStatus.UNAUTHORIZED)
        return
    resp_data = dict(_config_payload(config))
    api_store: ApiKeyStore = handler._admin_api_key_store
    resp_data["api_key_count"] = api_store.count
    resp_data["api_keys"] = api_store.list_all()
    resp_data["auth_enabled"] = bool(config.server_api_keys) or api_store.active_count > 0
    resp_json = json.dumps(_api_ok(resp_data), ensure_ascii=False).encode("utf-8")
    handler.send_response(HTTPStatus.OK)
    handler._send_common_headers()
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(resp_json)))
    handler.send_header(
        "Set-Cookie",
        f"{COOKIE_NAME}={_cookie_value(config.admin_key)}; "
        f"Max-Age={COOKIE_MAX_AGE}; HttpOnly; SameSite=Lax; Path=/",
    )
    handler.end_headers()
    handler.wfile.write(resp_json)


def handle_admin_logout(handler) -> None:
    resp_json = json.dumps(_api_ok(), ensure_ascii=False).encode("utf-8")
    handler.send_response(HTTPStatus.OK)
    handler._send_common_headers()
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(resp_json)))
    handler.send_header(
        "Set-Cookie",
        f"{COOKIE_NAME}=; Max-Age=0; HttpOnly; SameSite=Lax; Path=/",
    )
    handler.end_headers()
    handler.wfile.write(resp_json)


def handle_admin_session(handler) -> None:
    config: AppConfig = handler._admin_config
    _write_admin_json(handler, _api_ok({
        "authenticated": _check_admin(handler),
        "admin_key_configured": bool(config.admin_key),
    }))


def handle_admin_overview(handler) -> None:
    if not _check_admin(handler):
        _write_admin_json(handler, _api_err("Login required"), HTTPStatus.UNAUTHORIZED)
        return
    config: AppConfig = handler._admin_config
    store: RequestLogStore = handler._admin_request_store
    api_store: ApiKeyStore = handler._admin_api_key_store
    stats = store.stats()
    _write_admin_json(handler, _api_ok({
        "status": "ok",
        "token_count": len(config.glm_refresh_tokens),
        "guest_mode": config.glm_use_guest_refresh_token,
        "model_count": len(config.exposed_models),
        "concurrency": config.glm_max_concurrency,
        "api_key_count": api_store.count,
        "api_key_active": api_store.active_count,
        "request_stats": stats,
    }))


def handle_admin_config(handler) -> None:
    if not _check_admin(handler):
        _write_admin_json(handler, _api_err("Login required"), HTTPStatus.UNAUTHORIZED)
        return
    config: AppConfig = handler._admin_config
    store: ApiKeyStore = handler._admin_api_key_store
    payload = dict(_config_payload(config))
    payload["api_key_count"] = store.count
    payload["api_keys"] = store.list_all()
    payload["auth_enabled"] = bool(config.server_api_keys) or store.active_count > 0
    _write_admin_json(handler, _api_ok(payload))


def handle_admin_logs(handler) -> None:
    if not _check_admin(handler):
        _write_admin_json(handler, _api_err("Login required"), HTTPStatus.UNAUTHORIZED)
        return
    qs = handler.path.split("?", 1)[1] if "?" in handler.path else ""
    params: dict[str, str] = {}
    for pair in qs.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            params[k] = v
    since_id = int(params.get("since_id", "0"))
    limit = min(int(params.get("limit", "200")), 500)
    level = params.get("level")
    _write_admin_json(handler, _api_ok({
        "items": get_buffered_logs(since_id=since_id, limit=limit, level=level),
        "limit": limit,
    }))


def handle_admin_requests(handler) -> None:
    if not _check_admin(handler):
        _write_admin_json(handler, _api_err("Login required"), HTTPStatus.UNAUTHORIZED)
        return
    qs = handler.path.split("?", 1)[1] if "?" in handler.path else ""
    params: dict[str, str] = {}
    for pair in qs.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            params[k] = v
    since_id = int(params.get("since_id", "0"))
    limit = min(int(params.get("limit", "100")), 200)
    store: RequestLogStore = handler._admin_request_store
    _write_admin_json(handler, _api_ok({"items": store.list(since_id=since_id, limit=limit), "limit": limit}))


def handle_admin_requests_clear(handler) -> None:
    if not _check_admin(handler):
        _write_admin_json(handler, _api_err("Login required"), HTTPStatus.UNAUTHORIZED)
        return
    store: RequestLogStore = handler._admin_request_store
    store.clear()
    _write_admin_json(handler, _api_ok({}, "cleared"))


def handle_admin_chat_test(handler) -> None:
    if not _check_admin(handler):
        _write_admin_json(handler, _api_err("Login required"), HTTPStatus.UNAUTHORIZED)
        return
    body = _read_admin_body(handler)
    model = str(body.get("model", "glm-4-flash") or "glm-4-flash").strip()
    prompt = str(body.get("prompt", "ping") or "ping").strip()
    config: AppConfig = handler._admin_config
    client = handler._admin_glm_client
    try:
        chat_payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        result, _ = client.chat_completion(chat_payload)
        choice = (result.get("choices") or [{}])[0] if isinstance(result, dict) else {}
        content = (choice.get("message") or {}).get("content", "") if isinstance(choice, dict) else ""
        _write_admin_json(handler, _api_ok({
            "ok": True,
            "model": result.get("model") if isinstance(result, dict) else model,
            "reply": content,
            "finish_reason": choice.get("finish_reason") if isinstance(choice, dict) else "",
        }))
    except Exception as exc:
        _write_admin_json(handler, _api_ok({"ok": False, "info": str(exc)}))


# ── API Key management handlers ─────────────────────────────────────────────


def handle_admin_api_keys_list(handler) -> None:
    if not _check_admin(handler):
        _write_admin_json(handler, _api_err("Login required"), HTTPStatus.UNAUTHORIZED)
        return
    store: ApiKeyStore = handler._admin_api_key_store
    _write_admin_json(handler, _api_ok({
        "items": store.list_all(),
        "count": store.count,
        "active_count": store.active_count,
    }))


def handle_admin_api_key_create(handler) -> None:
    if not _check_admin(handler):
        _write_admin_json(handler, _api_err("Login required"), HTTPStatus.UNAUTHORIZED)
        return
    body = _read_admin_body(handler)
    name = str(body.get("name") or "").strip()
    key = str(body.get("key") or "").strip()
    if not name:
        _write_admin_json(handler, _api_err("name 不能为空"), HTTPStatus.BAD_REQUEST)
        return
    if len(key) < 4:
        _write_admin_json(handler, _api_err("key 至少需要 4 个字符"), HTTPStatus.BAD_REQUEST)
        return
    store: ApiKeyStore = handler._admin_api_key_store
    if store.get(name):
        _write_admin_json(handler, _api_err(f"API Key '{name}' 已存在"), HTTPStatus.CONFLICT)
        return
    rec = ApiKeyRecord(name=name, key=key)
    store.add(rec)
    _persist_api_keys(handler)
    _write_admin_json(handler, _api_ok(rec.to_dict(mask=True), "已创建"))


def handle_admin_api_key_update(handler, name: str) -> None:
    if not _check_admin(handler):
        _write_admin_json(handler, _api_err("Login required"), HTTPStatus.UNAUTHORIZED)
        return
    body = _read_admin_body(handler)
    store: ApiKeyStore = handler._admin_api_key_store
    fields: dict[str, object] = {}
    if "key" in body:
        k = str(body["key"]).strip()
        if k and len(k) < 4:
            _write_admin_json(handler, _api_err("key 至少需要 4 个字符"), HTTPStatus.BAD_REQUEST)
            return
        fields["key"] = k
    if "enabled" in body:
        fields["enabled"] = bool(body["enabled"])
    if not store.update(name, **fields):
        _write_admin_json(handler, _api_err(f"API Key '{name}' 不存在"), HTTPStatus.NOT_FOUND)
        return
    _persist_api_keys(handler)
    updated = store.get(name)
    _write_admin_json(handler, _api_ok(updated.to_dict(mask=True) if updated else {}, "已更新"))


def handle_admin_api_key_delete(handler, name: str) -> None:
    if not _check_admin(handler):
        _write_admin_json(handler, _api_err("Login required"), HTTPStatus.UNAUTHORIZED)
        return
    store: ApiKeyStore = handler._admin_api_key_store
    if not store.delete(name):
        _write_admin_json(handler, _api_err(f"API Key '{name}' 不存在"), HTTPStatus.NOT_FOUND)
        return
    _persist_api_keys(handler)
    _write_admin_json(handler, _api_ok({}, "已删除"))


def handle_admin_api_key_toggle(handler, name: str) -> None:
    if not _check_admin(handler):
        _write_admin_json(handler, _api_err("Login required"), HTTPStatus.UNAUTHORIZED)
        return
    store: ApiKeyStore = handler._admin_api_key_store
    rec = store.get(name)
    if not rec:
        _write_admin_json(handler, _api_err(f"API Key '{name}' 不存在"), HTTPStatus.NOT_FOUND)
        return
    store.update(name, enabled=not rec.enabled)
    _persist_api_keys(handler)
    updated = store.get(name)
    _write_admin_json(handler, _api_ok(updated.to_dict(mask=True) if updated else {}, "已切换"))


def handle_admin_page(handler) -> None:
    html_path = Path(__file__).parent / "admin_static" / "index.html"
    if not html_path.exists():
        _write_html(handler, HTTPStatus.NOT_FOUND, "<h1>admin panel not found</h1>")
        return
    _write_html(handler, HTTPStatus.OK, html_path.read_text(encoding="utf-8"), extra_headers=NO_STORE_HEADERS)
