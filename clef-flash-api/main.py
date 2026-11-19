"""Public documentation and compatibility facade for the Clef-Flash model."""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.base import BaseHTTPMiddleware

try:
    import api_server as _legacy_api_server
    _DEFAULT_INFLUX_URL = getattr(
        _legacy_api_server,
        "INFLUX_URL",
        "http://10.9.0.9:8008/api/v2/write?org=main&bucket=telegraf&precision=s",
    )
    _DEFAULT_INFLUX_TOKEN = getattr(_legacy_api_server, "INFLUX_TOKEN", "")
    _DEFAULT_HOST_NAME = getattr(_legacy_api_server, "HOST_NAME", "10.9.0.32")
except ImportError:
    _DEFAULT_INFLUX_URL = "http://10.9.0.9:8008/api/v2/write?org=main&bucket=telegraf&precision=s"
    _DEFAULT_INFLUX_TOKEN = ""
    _DEFAULT_HOST_NAME = "10.9.0.32"


UPSTREAM_URL = os.getenv("LLAMA_SERVER_URL", "http://127.0.0.1:18080").rstrip("/")
REQUEST_TIMEOUT_SECONDS = max(1.0, float(os.getenv("REQUEST_TIMEOUT_SECONDS", "300")))
MAX_CONCURRENT_INFERENCE = max(1, int(os.getenv("MAX_CONCURRENT_INFERENCE", "64")))
INFERENCE_QUEUE_TIMEOUT = max(0.1, float(os.getenv("INFERENCE_QUEUE_TIMEOUT", "10.0")))
INFLUX_URL = os.getenv("INFLUX_URL", _DEFAULT_INFLUX_URL)
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", _DEFAULT_INFLUX_TOKEN)
HOST_NAME = os.getenv("HOST_NAME", _DEFAULT_HOST_NAME)


def _escape_influx_tag(value: str, max_len: int = 64) -> str:
    """Normalize tag values for InfluxDB line protocol safety."""
    return (
        value.replace("\\", "_")
        .replace(" ", "_")
        .replace(",", "_")
        .replace("=", "_")
        .replace("\n", "_")[:max_len]
    )


async def record_telemetry(
    telemetry_client: httpx.AsyncClient | None,
    status_code: int,
    latency: float,
    input_tokens: int = 0,
    questions_count: int = 0,
    choice_count: int = 0,
    noul_count: int = 0,
    score_count: int = 0,
    error_type: str | None = None,
) -> None:
    """Push inference metrics asynchronously using the shared client."""
    if not INFLUX_TOKEN or telemetry_client is None:
        return
    try:
        status_type = (
            "success"
            if status_code < 400
            else ("client_error" if status_code < 500 else "server_error")
        )
        safe_host = _escape_influx_tag(HOST_NAME)
        tags = f"host={safe_host},model=clef-flash,status={status_code},status_type={status_type}"
        if error_type:
            safe_err = _escape_influx_tag(error_type, max_len=32)
            tags += f",error_type={safe_err}"
        fields = (
            f"latency_seconds={latency},"
            f"input_tokens={input_tokens}i,"
            f"questions_count={questions_count}i,"
            f"choice_count={choice_count}i,"
            f"noul_count={noul_count}i,"
            f"score_count={score_count}i"
        )
        line = f"clef_inference,{tags} {fields}"
        await telemetry_client.post(
            INFLUX_URL,
            headers={
                "Authorization": f"Token {INFLUX_TOKEN}",
                "Content-Type": "text/plain; charset=utf-8",
            },
            content=line.encode("utf-8"),
        )
    except Exception as exc:
        print(f"Telemetry error: {exc}", file=sys.stderr)
ACME_CHALLENGE_DIR = Path(
    os.getenv("ACME_CHALLENGE_DIR", "/acme-root/.well-known/acme-challenge")
)
PUBLIC_HOSTNAME = "clef.create360.ai"
FlexibleValue = str | dict[str, Any] | list[Any]
StateValue = str | dict[str, Any] | list[Any] | None


class PublicModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class NoulQuestion(PublicModel):
    type: Literal["noul"] = "noul"
    instructions: FlexibleValue | None = Field(
        default=None, description="Yes/no question. If omitted, the question ID is used."
    )
    criteria: dict[str, FlexibleValue] | None = Field(
        default=None, description="Optional descriptions for true and false."
    )


class ChoiceQuestion(PublicModel):
    type: Literal["choice"] = "choice"
    instructions: FlexibleValue | None = None
    criteria: dict[str, Any] = Field(
        min_length=1,
        max_length=255,
        description="Map of option IDs to descriptions; use null when no description is needed.",
    )


class ScoreQuestion(PublicModel):
    type: Literal["score"] = "score"
    instructions: FlexibleValue | None = None
    criteria: list[FlexibleValue] = Field(
        min_length=2,
        max_length=10,
        description="Ordered score levels from lowest to highest.",
    )


Question = Annotated[
    NoulQuestion | ChoiceQuestion | ScoreQuestion,
    Field(discriminator="type"),
]


class SystemOneRequest(PublicModel):
    model: Literal["clef-flash"] = Field(
        default="clef-flash",
        description="Model identifier. The service has Clef-Flash loaded.",
    )
    state: StateValue = Field(
        description="State to evaluate: string, object, array, or null."
    )
    questions: dict[str, Question] = Field(
        min_length=1,
        description=(
            "Questions keyed by caller-chosen IDs. Supports noul, choice, and score. "
            "Clef evaluates all questions jointly in one physical batch; the combined "
            "rendered request must stay within the 4096-token limit. The API does not "
            "split questions automatically."
        ),
    )
    images: list[str] | None = Field(
        default=None,
        description="Optional image data URLs, for example data:image/jpeg;base64,...",
    )

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "model": "clef-flash",
                    "state": {
                        "invoice": {
                            "vendor": "Acme",
                            "total": 1250.0,
                            "currency": "USD",
                            "status": "overdue",
                        }
                    },
                    "questions": {
                        "status": {
                            "type": "choice",
                            "instructions": "What is the invoice status?",
                            "criteria": {
                                "paid": "Invoice is paid.",
                                "overdue": "Invoice is past due.",
                                "draft": "Invoice was not sent.",
                            },
                        },
                        "large": {
                            "type": "noul",
                            "instructions": "Is the total above 1000 USD?",
                        },
                    },
                }
            ]
        },
    )


class NoulAnswer(PublicModel):
    type: Literal["noul"]
    noul: float = Field(ge=0, le=1, description="Probability that the answer is true.")


class ChoiceAnswer(PublicModel):
    type: Literal["choice"]
    choice: str
    probabilities: dict[str, float]
    confidence: float = Field(ge=0, le=1)


class ScoreAnswer(PublicModel):
    type: Literal["score"]
    score: float
    legend: dict[str, FlexibleValue] = Field(
        description="Each score level mapped to its original string, object, or array description."
    )
    probabilities: dict[str, float]
    confidence: float = Field(ge=0, le=1)


class Usage(PublicModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class SystemOneResponse(PublicModel):
    model: str
    answers: dict[str, NoulAnswer | ChoiceAnswer | ScoreAnswer]
    usage: Usage


class HealthResponse(BaseModel):
    status: Literal["ok"]


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(
        base_url=UPSTREAM_URL,
        timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=5.0, pool=10.0),
        limits=httpx.Limits(max_keepalive_connections=64, max_connections=128),
        follow_redirects=False,
    )
    app.state.health_http = httpx.AsyncClient(
        base_url=UPSTREAM_URL,
        timeout=httpx.Timeout(2.0, connect=1.0),
        limits=httpx.Limits(max_keepalive_connections=2, max_connections=10),
        follow_redirects=False,
    )
    app.state.telemetry_http = httpx.AsyncClient(
        timeout=httpx.Timeout(1.5, connect=1.0),
        limits=httpx.Limits(max_keepalive_connections=10, max_connections=50),
        follow_redirects=False,
    )
    app.state.inference_semaphore = asyncio.Semaphore(MAX_CONCURRENT_INFERENCE)
    try:
        yield
    finally:
        await asyncio.gather(
            app.state.http.aclose(),
            app.state.health_http.aclose(),
            app.state.telemetry_http.aclose(),
            return_exceptions=True,
        )


app = FastAPI(
    title="Clef-Flash Decision API",
    summary="Typed decision API for the Cloudflare Clef-Flash model.",
    description=(
        "Evaluate a text or structured state against typed questions and receive "
        "probabilities for each answer. This service exposes the System One API; "
        "it does not provide chat-completion behavior."
    ),
    version="1.0.2",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    servers=[{"url": "/", "description": "Current host"}],
    lifespan=lifespan,
)
app.add_middleware(SecurityHeadersMiddleware)


@app.middleware("http")
async def public_http_and_acme(request: Request, call_next):
    hostname = request.headers.get("host", "").split(":", 1)[0].lower()
    if hostname == PUBLIC_HOSTNAME:
        acme_prefix = "/.well-known/acme-challenge/"
        if request.url.path.startswith(acme_prefix):
            token = request.url.path[len(acme_prefix) :]
            if not re.fullmatch(r"[A-Za-z0-9_-]+", token):
                return PlainTextResponse("Not Found", status_code=404)
            try:
                challenge = (ACME_CHALLENGE_DIR / token).read_text(encoding="utf-8")
            except OSError:
                return PlainTextResponse("Not Found", status_code=404)
            return PlainTextResponse(challenge)

        forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
        if forwarded_proto != "https":
            target = f"https://{PUBLIC_HOSTNAME}{request.url.path}"
            if request.url.query:
                target += f"?{request.url.query}"
            return RedirectResponse(target, status_code=308)

    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    max_age=600,
)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "Request body failed validation.",
                "details": exc.errors(),
            }
        },
    )


@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["Operations"],
    summary="Check API and model readiness",
    responses={503: {"description": "Model backend is unavailable."}},
)
async def health(request: Request) -> HealthResponse:
    health_client = getattr(request.app.state, "health_http", request.app.state.http)
    try:
        upstream = await health_client.get("/health", timeout=2.0)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail={"status": "unavailable", "component": "model"},
        ) from exc
    if upstream.status_code != 200:
        raise HTTPException(
            status_code=503,
            detail={"status": "unavailable", "component": "model"},
        )
    return HealthResponse(status="ok")


@app.post(
    "/v1/systemone",
    tags=["Decision"],
    summary="Evaluate typed questions against a state",
    description=(
        "Returns one typed answer for each question ID. Choice questions return "
        "probabilities and a selected option; score questions return a weighted "
        "score; noul questions return the probability of true. Image input uses "
        "data URLs. Streaming is not supported.\n\n"
        "**Prompt limit:** The complete rendered prompt for `state`, all question "
        "instructions and criteria, and image tokens must fit in one 4096-token "
        "physical batch. Clef evaluates questions jointly and this API does not "
        "automatically split oversized requests. Keep requests below the limit to "
        "leave room for model-template and multimodal overhead."
    ),
    response_model=SystemOneResponse,
    responses={
        400: {"description": "The model rejected the request."},
        422: {"description": "The request body failed validation."},
        501: {"description": "The model does not support the requested input."},
        502: {"description": "The model backend returned an invalid response."},
        500: {"description": "Inference failed; an input beyond the physical batch limit can trigger this response."},
        503: {"description": "The model backend is unavailable."},
        504: {"description": "The model backend timed out."},
    },
)
async def systemone(
    payload: SystemOneRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    started_at = time.monotonic()
    question_counts = {
        "choice_count": sum(q.type == "choice" for q in payload.questions.values()),
        "noul_count": sum(q.type == "noul" for q in payload.questions.values()),
        "score_count": sum(q.type == "score" for q in payload.questions.values()),
    }

    def queue_telemetry(status_code: int, input_tokens: int = 0, error_type: str | None = None):
        background_tasks.add_task(
            record_telemetry,
            telemetry_client=getattr(request.app.state, "telemetry_http", None),
            status_code=status_code,
            latency=round(time.monotonic() - started_at, 3),
            input_tokens=input_tokens,
            questions_count=len(payload.questions),
            choice_count=question_counts["choice_count"],
            noul_count=question_counts["noul_count"],
            score_count=question_counts["score_count"],
            error_type=error_type,
        )

    semaphore = getattr(request.app.state, "inference_semaphore", None)
    acquired = False
    try:
        if semaphore is not None:
            try:
                await asyncio.wait_for(semaphore.acquire(), timeout=INFERENCE_QUEUE_TIMEOUT)
                acquired = True
            except asyncio.TimeoutError:
                print(
                    f"Inference queue full after {round(time.monotonic() - started_at, 3)}s (max={MAX_CONCURRENT_INFERENCE})",
                    file=sys.stderr,
                )
                queue_telemetry(503, error_type="InferenceQueueFull")
                return JSONResponse(
                    content={"detail": {"code": "inference_queue_full", "message": "Inference queue is full; please retry shortly."}},
                    status_code=503,
                    headers={"Retry-After": "2"},
                    background=background_tasks,
                )

        try:
            upstream = await request.app.state.http.post(
                "/v1/systemone", json=payload.model_dump(exclude_unset=True)
            )
        except httpx.TimeoutException as exc:
            print(f"Upstream timeout: {exc}", file=sys.stderr)
            queue_telemetry(504, error_type="ModelTimeout")
            return JSONResponse(
                content={"detail": {"code": "model_timeout", "message": "Model request timed out."}},
                status_code=504,
                background=background_tasks,
            )
        except httpx.HTTPError as exc:
            print(f"Upstream HTTP error: {type(exc).__name__}: {exc}", file=sys.stderr)
            queue_telemetry(503, error_type="ModelUnavailable")
            return JSONResponse(
                content={"detail": {"code": "model_unavailable", "message": "Model backend is unavailable."}},
                status_code=503,
                background=background_tasks,
            )
    finally:
        if acquired and semaphore is not None:
            semaphore.release()

    try:
        result = upstream.json()
    except ValueError:
        print(f"Invalid upstream JSON (HTTP {upstream.status_code}): {upstream.text[:200]}", file=sys.stderr)
        queue_telemetry(502, error_type="InvalidModelJSON")
        return JSONResponse(
            content={"detail": {"code": "invalid_model_response", "message": "Model backend returned invalid JSON."}},
            status_code=502,
            background=background_tasks,
        )

    if upstream.is_success:
        if not isinstance(result, dict):
            queue_telemetry(502, error_type="InvalidModelResponse")
            return JSONResponse(
                content={"detail": {"code": "invalid_model_response", "message": "Model backend returned an invalid response."}},
                status_code=502,
                background=background_tasks,
            )
        result["model"] = "clef-flash"

    out_status = upstream.status_code
    err_text = upstream.text if not upstream.is_success else ""
    if out_status >= 500 and "is too large to process" in err_text and "physical batch size" in err_text:
        out_status = 400
    if not upstream.is_success and out_status >= 500:
        print(f"Upstream HTTP {upstream.status_code}: {err_text[:250]}", file=sys.stderr)

    input_tokens = result.get("usage", {}).get("input_tokens", 0) if isinstance(result, dict) else 0
    error_type = None if upstream.is_success else ("BatchSizeExceeded" if out_status == 400 and upstream.status_code >= 500 else f"BackendHTTP_{upstream.status_code}")
    queue_telemetry(out_status, input_tokens=input_tokens, error_type=error_type)
    return JSONResponse(content=result, status_code=out_status, background=background_tasks)
