"""FastAPI 入口：路由装配、请求上下文（request_id）中间件、统一异常处理。"""

import asyncio
import logging
import sqlite3
import time
from contextlib import asynccontextmanager, suppress
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.attachments import router as attachments_router
from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.api.kb import router as kb_router
from app.api.models import router as models_router
from app.api.sessions import router as sessions_router
from app.api.v1.errors import error_meta
from app.api.v1.router import router as v1_router
from app.attachments import storage as attachment_storage
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.runtime_overrides import clear_overrides
from app.indexing.lock import acquire_server_lock, release_server_lock
from app.packs.legacy_adapter import enable_legacy_pack
from app.session import store as session_store

logger = logging.getLogger("indocqa")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def _maintenance(settings) -> int:
    """先清理过期会话的附件文件，再删除会话；最后回收 staging/孤儿文件。"""
    expired = session_store.expired_session_ids(settings.session_db, settings.session_ttl_days)
    for session_id in expired:
        attachment_storage.delete_session_files(settings, session_id)
    removed = session_store.cleanup(settings.session_db, settings.session_ttl_days)
    attachment_storage.cleanup_staging(settings)
    attachment_storage.cleanup_orphans(settings)
    return removed


async def _session_gc(settings) -> None:
    """服务长期运行时每小时清理一次超过 TTL 的会话与附件。"""
    while True:
        await asyncio.sleep(3600)
        await asyncio.to_thread(_maintenance, settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    acquire_server_lock(settings)
    if settings.legacy_pack:
        try:
            enabled = enable_legacy_pack(
                settings.legacy_pack, settings.packs_dir, settings.version
            )
            logger.info("问答链路已加载 Domain Pack：%s", enabled)
        except Exception as e:  # noqa: BLE001 Pack 失败不阻塞问答链路启动
            clear_overrides()
            logger.warning("问答链路 Domain Pack 加载失败，使用默认配置：%s", e)
    else:
        clear_overrides()
    removed = await asyncio.to_thread(_maintenance, settings)
    if removed:
        logger.info("会话清理：删除 %s 个超过 %s 天的会话", removed, settings.session_ttl_days)
    try:
        from app.indexing.folders import migrate_folders, normalize_display_names

        if settings.folder_migration_enabled:
            stats = migrate_folders(settings)
            if stats.get("migrated"):
                logger.info(
                    "目录迁移：%s 份文档补分类，目录 %s 个", stats["migrated"], stats["folders"]
                )
        else:
            logger.info("目录迁移默认关闭（INDOCQA_FOLDER_MIGRATION_ENABLED=false）")
        renamed = normalize_display_names(settings)
        if renamed:
            logger.info("文档名修正：%s 份上传文档去除临时前缀", renamed)
    except Exception as e:  # noqa: BLE001 迁移失败不应阻塞服务启动
        logger.warning("目录迁移跳过：%s", e)
    task = asyncio.create_task(_session_gc(settings))
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        # 进程退出时再清一次并压缩文件，避免 WAL/空洞长期堆积
        await asyncio.to_thread(_maintenance, settings)
        try:
            session_store.compact(settings.session_db)
        except sqlite3.Error as e:
            logger.warning("会话库压缩失败：%s", e)
        release_server_lock()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(models_router)
    app.include_router(chat_router)
    app.include_router(kb_router)
    app.include_router(attachments_router)
    app.include_router(sessions_router)
    # 控制面与构建编排链路：只挂 /api/v1，不改动问答与知识运营链路
    app.include_router(v1_router)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-Id") or uuid4().hex[:12]
        request.state.request_id = request_id
        # /api/v1 静态 Bearer 鉴权：健康检查与 loopback 例外
        if (
            request.url.path.startswith("/api/v1")
            and request.url.path != "/api/v1/health"
            and settings.auth_token
        ):
            client_host = request.client.host if request.client else ""
            unauthorized = (
                client_host not in {"127.0.0.1", "::1"}
                and request.headers.get("Authorization") != f"Bearer {settings.auth_token}"
            )
            if unauthorized:
                name, retryable = error_meta(40101)
                return JSONResponse(
                    status_code=401,
                    content={
                        "error": {
                            "code": 40101,
                            "name": name,
                            "message": "未授权访问",
                            "detail": None,
                            "retryable": retryable,
                            "requestId": request_id,
                        }
                    },
                    headers={"X-Request-Id": request_id},
                )
        start = time.perf_counter()
        response = await call_next(request)
        if request.url.path.startswith("/api/v1"):
            response.headers["X-API-Version"] = "1"
        response.headers["X-Request-Id"] = request_id
        logger.info(
            "%s %s -> %s (%.0f ms) [%s]",
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - start) * 1000,
            request_id,
        )
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        name, retryable = error_meta(exc.code)
        request_id = getattr(request.state, "request_id", None) or request.headers.get(
            "X-Request-Id"
        )
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "error": {
                    "code": exc.code,
                    "name": name,
                    "message": exc.message,
                    "detail": exc.detail,
                    "retryable": retryable,
                    "requestId": request_id,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        """pydantic 校验失败也走统一错误包，避免 422 默认结构泄漏到前端。"""
        name, retryable = error_meta(40001)
        request_id = getattr(request.state, "request_id", None) or request.headers.get(
            "X-Request-Id"
        )
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": 40001,
                    "name": name,
                    "message": "参数校验失败",
                    "detail": jsonable_encoder(exc.errors()),
                    "retryable": retryable,
                    "requestId": request_id,
                }
            },
        )

    return app


app = create_app()
