from contextlib import asynccontextmanager
from hypurrquant.db import init_db, close_db
from hypurrquant.logging_config import (
    configure_logging,
    coroutine_logging,
    set_coroutine_id,
)
from hypurrquant.server.exception_handler import (
    base_order_exception_handler,
    hypuerliquid_client_error_handler,
    hypuerliquid_server_error_handler,
    request_validaiton_exception_handler,
    global_exception_handler,
)
from hypurrquant.server.health import health_router
from hypurrquant.exception import BaseOrderException
from hypurrquant.messaging.dependencies import get_producer

from hypurrquant.evm.utils.rpc import use_chain
from router.router import router
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError

from hyperliquid.utils.error import ServerError, ClientError
from concurrent.futures import ThreadPoolExecutor
import asyncio
from dotenv import load_dotenv


_logger = configure_logging(__name__)

load_dotenv()


PREFIX = "/api"
DEFAULT_THREAD_WORKERS = 5


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_db()

        # 2. producer 시작
        producer = get_producer()
        await producer.start()
        try:
            yield
        finally:
            await producer.stop()
            await close_db()  # 안전 종료 (항상 호출)
    except Exception as e:
        _logger.exception(f"Error during startup: {e}")
        raise e


def create_app() -> FastAPI:
    """
    FastAPI 앱 생성 함수.
    추후 확장성을 고려하여 함수로 분리.
    """

    app = FastAPI(lifespan=lifespan)

    # 라우트 등록
    app.include_router(health_router)
    app.include_router(router, prefix=PREFIX)

    # 예외 핸들러 등록
    app.add_exception_handler(BaseOrderException, base_order_exception_handler)
    app.add_exception_handler(ClientError, hypuerliquid_client_error_handler)
    app.add_exception_handler(ServerError, hypuerliquid_server_error_handler)
    app.add_exception_handler(Exception, global_exception_handler)
    app.add_exception_handler(
        RequestValidationError, request_validaiton_exception_handler
    )

    loop = asyncio.get_event_loop()
    executor = ThreadPoolExecutor(max_workers=DEFAULT_THREAD_WORKERS)
    loop.set_default_executor(executor)

    @app.middleware("http")
    @coroutine_logging
    async def logging_middleware(request, call_next):
        """
        요청 로그 미들웨어
        """
        _logger.info(f"Request: {request.method} {request.url}")

        # 시간 체크
        start = asyncio.get_running_loop().time()
        response = await call_next(request)
        end = asyncio.get_running_loop().time()
        duration = end - start
        # 응답 로그
        _logger.info(f"Response: {response.status_code}")
        _logger.debug(f"Duration: {duration:.2f} seconds")

        return response

    @app.middleware("http")
    async def set_coroutine_id_middleware(request: Request, call_next):
        # 1) 헤더에서 ID 가져오기
        cid = request.headers.get("X-Coroutine-ID")
        if cid:
            _logger.debug(f"Set Coroutine ID: {cid}")
            set_coroutine_id(cid, force=True)

        # 2) 다음 미들웨어 / 라우터 처리
        response = await call_next(request)

        return response

    return app


app = create_app()
