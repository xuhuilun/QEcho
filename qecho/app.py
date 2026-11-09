"""HTTP boundary: authentication, validation and durable event acceptance."""

import hashlib
import hmac
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .adapters import BridgeClient, BridgeError, ModelClient, ProviderError
from .engine import Engine
from .runtime import InstanceLock, protect_path
from .schemas import (
    AutomationStartInput,
    ContactCreate,
    ContactPatch,
    SendInput,
    SettingsPatch,
    SimulationInput,
)
from .simple import SimpleService
from .store import Store

LOGGER = logging.getLogger("qecho")
STATIC = Path(__file__).parent / "static"


def _token(directory: Path, name: str, env_name: str) -> str:
    configured = os.getenv(env_name, "").strip()
    if configured:
        if len(configured) < 16 or not configured.isascii():
            raise ValueError(f"{env_name} must contain at least 16 ASCII characters")
        return configured
    path = directory / name
    if not path.exists():
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "w") as handle:
                handle.write(secrets.token_urlsafe(32))
    protect_path(path, 0o600)
    value = path.read_text().strip()
    if len(value) < 16 or not value.isascii():
        raise ValueError(f"Invalid token file: {path}")
    return value


def create_app(
    data_dir: str | Path | None = None,
    model_client: ModelClient | None = None,
    bridge_client: BridgeClient | None = None,
) -> FastAPI:
    directory = Path(data_dir or os.getenv("QECHO_DATA_DIR", ".data")).resolve()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        protect_path(directory, 0o700)
        lock = InstanceLock(directory / "instance.lock")
        lock.acquire()
        store = None
        model = bridge = engine = None
        try:
            model = model_client or ModelClient()
            bridge = bridge_client or BridgeClient()
            application.state.admin_token = _token(directory, "admin-token", "QECHO_ADMIN_TOKEN")
            application.state.webhook_token = _token(
                directory, "webhook-token", "QECHO_WEBHOOK_TOKEN"
            )
            store = Store(directory / "qecho.sqlite3")
            protect_path(directory / "qecho.sqlite3", 0o600)
            application.state.store = store
            application.state.bridge = bridge
            engine = Engine(store, model, bridge)
            application.state.engine = engine
            application.state.simple = SimpleService(store, bridge)
            LOGGER.info("QEcho ready. Admin token file: %s", directory / "admin-token")
            yield
        finally:
            try:
                if engine and hasattr(engine, "shutdown"):
                    await engine.shutdown()
                for client in (model, bridge):
                    if client and hasattr(client, "close"):
                        await client.close()
            finally:
                try:
                    if store:
                        store.close()
                finally:
                    lock.release()

    application = FastAPI(
        title="QEcho",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    def authenticate(request: Request, kind: str) -> None:
        header = request.headers.get("authorization", "")
        scheme, _, supplied = header.partition(" ")
        expected = getattr(request.app.state, kind)
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            supplied.encode(), expected.encode()
        ):
            raise HTTPException(401, "访问令牌无效或缺失", headers={"WWW-Authenticate": "Bearer"})

    def admin(request: Request) -> None:
        authenticate(request, "admin_token")

    @application.middleware("http")
    async def response_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(KeyError)
    async def missing_resource(request: Request, exc: KeyError):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=404, content={"detail": "联系人或消息不存在"})

    @application.exception_handler(ValueError)
    async def invalid_operation(request: Request, exc: ValueError):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @application.exception_handler(ProviderError)
    @application.exception_handler(BridgeError)
    async def upstream_failure(request: Request, exc: Exception):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @application.get("/healthz")
    async def health():
        return {"status": "ok"}

    @application.get("/api/settings", dependencies=[Depends(admin)])
    async def settings():
        return application.state.store.get_settings()

    @application.patch("/api/settings", dependencies=[Depends(admin)])
    async def update_settings(body: SettingsPatch):
        return application.state.store.update_settings(body.model_dump(exclude_unset=True))

    @application.get("/api/setup", dependencies=[Depends(admin)])
    async def setup():
        # Only the authenticated owner can obtain the token needed to configure NapCat.
        return {
            "webhook_path": "/webhooks/onebot",
            "webhook_token": application.state.webhook_token,
        }

    @application.get("/api/automation", dependencies=[Depends(admin)])
    async def automation():
        return application.state.simple.status()

    @application.post("/api/qq/sync", dependencies=[Depends(admin)])
    async def sync_qq():
        return await application.state.simple.sync()

    @application.post("/api/automation/start", dependencies=[Depends(admin)])
    async def start_automation(body: AutomationStartInput):
        return await application.state.simple.start(
            body.qq_ids, body.prompt, body.expected_revision
        )

    @application.post("/api/automation/stop", dependencies=[Depends(admin)])
    async def stop_automation():
        return application.state.simple.stop()

    @application.get("/api/contacts", dependencies=[Depends(admin)])
    async def contacts():
        return application.state.store.list_contacts()

    @application.post("/api/contacts", status_code=201, dependencies=[Depends(admin)])
    async def create_contact(body: ContactCreate):
        return application.state.store.create_contact(body.model_dump())

    @application.patch("/api/contacts/{contact_id}", dependencies=[Depends(admin)])
    async def update_contact(contact_id: str, body: ContactPatch):
        return application.state.store.update_contact(
            contact_id, body.model_dump(exclude_unset=True)
        )

    @application.delete("/api/contacts/{contact_id}", dependencies=[Depends(admin)])
    async def delete_contact(contact_id: str):
        application.state.store.delete_contact(contact_id)
        return {"ok": True}

    @application.get("/api/contacts/{contact_id}/messages", dependencies=[Depends(admin)])
    async def messages(contact_id: str):
        return application.state.store.list_messages(contact_id)

    @application.delete("/api/contacts/{contact_id}/messages", dependencies=[Depends(admin)])
    async def clear_messages(contact_id: str):
        application.state.store.clear_messages(contact_id)
        return {"ok": True}

    @application.get("/api/contacts/{contact_id}/prompt", dependencies=[Depends(admin)])
    async def prompt(contact_id: str):
        return {"messages": application.state.engine.preview(contact_id)}

    @application.post("/api/contacts/{contact_id}/simulate", dependencies=[Depends(admin)])
    async def simulate(contact_id: str, body: SimulationInput):
        return await application.state.engine.simulate(contact_id, body.text)

    @application.post("/api/messages/{message_id}/send", dependencies=[Depends(admin)])
    async def send(message_id: str, body: SendInput):
        return await application.state.engine.send_draft(message_id, body.content)

    @application.post("/api/messages/{message_id}/cancel", dependencies=[Depends(admin)])
    async def cancel(message_id: str):
        return application.state.engine.cancel_draft(message_id)

    @application.post("/api/messages/{message_id}/regenerate", dependencies=[Depends(admin)])
    async def regenerate(message_id: str):
        return await application.state.engine.regenerate(message_id)

    @application.get("/api/bridge/friends", dependencies=[Depends(admin)])
    async def friends():
        store = application.state.store
        current = store.get_settings()
        if not current["qq_self_id"]:
            raise ValueError("请先在连接设置中填写本人 QQ 号")
        account = await application.state.bridge.login_info(
            current["bridge_base_url"], store.get_secret("bridge_token")
        )
        if account != current["qq_self_id"]:
            raise ValueError("桥接登录账号与配置的 QQ 号不一致，请先核对连接设置")
        if store.get_settings()["revision"] != current["revision"]:
            raise ValueError("连接设置已变化，请重新查询好友")
        result = await application.state.bridge.friends(
            current["bridge_base_url"], store.get_secret("bridge_token")
        )
        if store.get_settings()["revision"] != current["revision"]:
            raise ValueError("连接设置已变化，请重新查询好友")
        return result

    @application.get("/api/status", dependencies=[Depends(admin)])
    async def status():
        store = application.state.store
        current = store.get_settings()
        entries = store.list_contacts()
        return {
            "enabled": current["enabled"],
            "model_mode": current["model_mode"],
            "contacts": len(entries),
            "authorized_contacts": sum(1 for item in entries if item["enabled"]),
            "drafts": sum(
                1
                for contact in entries
                for item in store.list_messages(contact["id"])
                if item["status"] == "draft" and item["source"] == "qq"
            ),
            "model_configured": bool(current["model_base_url"] and current["model_name"]),
            "bridge_configured": bool(current["bridge_base_url"] and current["qq_self_id"]),
        }

    async def process(message_id: str) -> None:
        try:
            await application.state.engine.process_message(message_id)
        except Exception as exc:
            # Neither private message contents nor credentials belong in application logs.
            LOGGER.error("Background message processing failed (%s)", type(exc).__name__)

    @application.post("/webhooks/onebot")
    async def event(request: Request, background: BackgroundTasks):
        raw = await request.body()
        if len(raw) > 256_000:
            raise HTTPException(413, "事件过大")
        signature = request.headers.get("x-signature")
        if signature is not None:
            # NapCat HTTP Client signs the exact bytes using its configured token.
            expected = (
                "sha1="
                + hmac.new(application.state.webhook_token.encode(), raw, hashlib.sha1).hexdigest()
            )
            if not hmac.compare_digest(signature.encode(), expected.encode()):
                raise HTTPException(401, "事件签名无效")
        else:
            # Allow compatible bridges that use Bearer instead of HMAC.
            authenticate(request, "webhook_token")
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(400, "无效 JSON") from exc
        if not isinstance(payload, dict):
            raise HTTPException(400, "事件必须是 JSON 对象")
        result = application.state.engine.ingest_event(payload)
        if result.get("status") == "accepted" and result.get("message_id"):
            background.add_task(process, result["message_id"])
        return result

    application.mount("/static", StaticFiles(directory=STATIC, check_dir=False), name="static")

    @application.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC / "index.html")

    return application


app = create_app()
