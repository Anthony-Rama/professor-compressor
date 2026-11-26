"""Multipart upload admission, validation, and queue handoff."""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from aiohttp import BodyPartReader, web

from .domain import (
    BrowserResult,
    CompressionDiagnostic,
    DeliveryRequest,
    JobState,
    UploadJob,
)
from .media_validation import valid_mp4_signature
from .metrics import RuntimeMetrics
from .notifications import compression_progress_alert
from .observability_contract import valid_id
from .relay_diagnostics import parse_compression_diagnostics
from .web_responses import json_error, pending_response

logger = logging.getLogger(__name__)

DELIVERY_RESPONSE_WAIT_SECONDS = 2
UPLOAD_DEADLINE_SECONDS = 300
# Longest wait for the next multipart field or chunk before the upload stalls.
CHUNK_TIMEOUT_SECONDS = 30
METADATA_CHUNK_BYTES = 1024
CLIP_CHUNK_BYTES = 1024 * 1024
MAX_CAPTION_BYTES = 4000
MAX_CAPTION_CHARACTERS = 1000
MAX_DIAGNOSTICS_BYTES = 16384


class UploadLimits(Protocol):
    @property
    def max_concurrent_uploads(self) -> int: ...
    @property
    def max_clips(self) -> int: ...
    @property
    def max_delivery_buffer_bytes(self) -> int: ...
    @property
    def max_result_total_bytes(self) -> int: ...


@dataclass(slots=True)
class RelayState:
    """Event-loop-owned queue and accounting shared by the relay and workers."""

    queue: asyncio.Queue[DeliveryRequest] | None = None
    active_uploads: int = 0
    buffered_bytes: int = 0

    def try_reserve(self, size: int, limit: int) -> bool:
        if self.buffered_bytes + size > limit:
            return False
        self.buffered_bytes += size
        return True

    def release(self, size: int) -> None:
        self.buffered_bytes = max(0, self.buffered_bytes - size)


def safe_result_name(name: str | None, number: int) -> str:
    raw = (name or f"clip-{number}.mp4").replace("\\", "/").split("/")[-1]
    cleaned = "".join(
        character for character in raw if character.isalnum() or character in " ._-"
    ).strip(" .")
    stem = cleaned[:-4] if cleaned.lower().endswith(".mp4") else cleaned
    stem = stem[:120].rstrip(" .")
    return f"{stem or f'clip-{number}'}.mp4"


class RelayBufferFull(Exception):
    """Reserving another chunk would exceed the shared delivery buffer."""


@dataclass(slots=True)
class UploadProgress:
    """Byte and timing facts for one upload attempt, reported if it fails."""

    started: float
    last_chunk_at: float
    deadline: float = 0.0
    received_bytes: int = 0
    # Clip bytes reserved in RelayState; released unless the batch is queued.
    reserved_bytes: int = 0

    def chunk_timeout(self) -> float:
        return max(0, min(CHUNK_TIMEOUT_SECONDS, self.deadline - time.monotonic()))

    def record_chunk(self, size: int) -> None:
        self.received_bytes += size
        self.last_chunk_at = time.monotonic()

    def failure_facts(self, code: str, attempts: int) -> dict:
        now = time.monotonic()
        return {
            "error_code": code,
            "stage": "upload",
            "upload_attempts": attempts,
            "received_bytes": self.received_bytes,
            "relay_receive_seconds": max(0, now - self.started),
            "receive_idle_seconds": max(0, now - self.last_chunk_at),
        }


@dataclass(slots=True)
class UploadedBatch:
    results: list[BrowserResult]
    caption: str
    diagnostics: list[CompressionDiagnostic]


def admission_error(
    job: UploadJob,
    request: web.Request,
    *,
    state: RelayState,
    limits: UploadLimits,
    rate_limit: Callable[[web.Request, str], int | None],
) -> web.Response | None:
    """Refuse uploads that cannot start now; None means the upload may begin."""
    if job.state in {JobState.UPLOADING, JobState.QUEUED}:
        return pending_response()
    if job.state is not JobState.CLAIMED:
        return json_error(
            "This one-use session is no longer available.",
            410,
            "session_used",
        )
    retry_after = rate_limit(request, "upload")
    if retry_after is not None:
        return json_error(
            f"Too many upload attempts. Try again in {retry_after} seconds.",
            429,
            "rate_limited",
            retry_after=retry_after,
        )
    if state.active_uploads >= limits.max_concurrent_uploads:
        return json_error(
            "The relay is receiving its maximum number of uploads. "
            "It will retry automatically.",
            503,
            "relay_busy",
            retry_after=3,
        )
    queue = state.queue
    if queue is None or queue.full():
        return json_error(
            "The Discord delivery queue is full. It will retry automatically.",
            503,
            "queue_full",
            retry_after=5,
        )
    return None


async def read_bounded_field(
    field: BodyPartReader, progress: UploadProgress, max_bytes: int, too_long: str
) -> bytes:
    """Read a small text field, failing as soon as it passes max_bytes."""
    data = bytearray()
    while chunk := await asyncio.wait_for(
        field.read_chunk(METADATA_CHUNK_BYTES), timeout=progress.chunk_timeout()
    ):
        data.extend(chunk)
        progress.record_chunk(len(chunk))
        if len(data) > max_bytes:
            raise ValueError(too_long)
    return bytes(data)


async def read_clip(
    field: BodyPartReader,
    name: str,
    *,
    progress: UploadProgress,
    state: RelayState,
    limits: UploadLimits,
    discord_limit: int,
) -> bytes:
    """Read one MP4 into the shared buffer, reserving its bytes as they arrive."""
    file_data = bytearray()
    while chunk := await asyncio.wait_for(
        field.read_chunk(CLIP_CHUNK_BYTES), timeout=progress.chunk_timeout()
    ):
        if not state.try_reserve(len(chunk), limits.max_delivery_buffer_bytes):
            raise RelayBufferFull
        progress.record_chunk(len(chunk))
        progress.reserved_bytes += len(chunk)
        file_data.extend(chunk)
        if len(file_data) > discord_limit:
            raise ValueError(f"{name} exceeds Discord's current file-size limit.")
        if progress.reserved_bytes > limits.max_result_total_bytes:
            raise ValueError("The combined compressed results exceed the relay limit.")
    if not file_data:
        raise ValueError(f"{name} is empty.")
    result_bytes = bytes(file_data)
    del file_data
    if not valid_mp4_signature(result_bytes):
        raise ValueError(
            f"{name} did not contain a complete MP4 video. "
            "Try the original again or convert it to MP4 first."
        )
    return result_bytes


async def read_batch(
    request: web.Request,
    *,
    progress: UploadProgress,
    state: RelayState,
    limits: UploadLimits,
    discord_limit: int,
) -> UploadedBatch:
    """Read the optional caption and diagnostics, then every clip, in that order."""
    if not request.content_type.startswith("multipart/"):
        raise ValueError("The upload format was invalid. Please try again.")
    reader = await request.multipart()
    progress.deadline = time.monotonic() + UPLOAD_DEADLINE_SECONDS
    batch = UploadedBatch(results=[], caption="", diagnostics=[])
    caption_received = False
    diagnostics_received = False
    while True:
        field = await asyncio.wait_for(reader.next(), timeout=progress.chunk_timeout())
        if field is None:
            break
        if field.name == "caption":
            if caption_received or batch.results:
                raise ValueError("The message field was repeated or out of order.")
            caption_received = True
            caption_data = await read_bounded_field(
                field, progress, MAX_CAPTION_BYTES, "The message is too long."
            )
            try:
                batch.caption = " ".join(caption_data.decode("utf-8").split())
            except UnicodeDecodeError as error:
                raise ValueError("The message contains invalid text.") from error
            if len(batch.caption) > MAX_CAPTION_CHARACTERS:
                raise ValueError("The message must be 1,000 characters or fewer.")
            continue
        if field.name == "diagnostics":
            if diagnostics_received or batch.results:
                raise ValueError("The diagnostics field was repeated or out of order.")
            diagnostics_received = True
            raw_diagnostics = await read_bounded_field(
                field,
                progress,
                MAX_DIAGNOSTICS_BYTES,
                "The diagnostics field is too long.",
            )
            batch.diagnostics = parse_compression_diagnostics(
                raw_diagnostics, max_clips=limits.max_clips
            )
            continue
        if field.name != "clips" or not field.filename:
            raise ValueError("Only video file fields are accepted.")
        if len(batch.results) >= limits.max_clips:
            raise ValueError(f"You can send at most {limits.max_clips} results.")
        name = safe_result_name(field.filename, len(batch.results) + 1)
        data = await read_clip(
            field,
            name,
            progress=progress,
            state=state,
            limits=limits,
            discord_limit=discord_limit,
        )
        batch.results.append(BrowserResult(name=name, data=data))
    if not batch.results:
        raise ValueError("No compressed MP4 results were received.")
    if len(batch.diagnostics) != len(batch.results):
        batch.diagnostics = []
    return batch


def enqueue(
    queue: asyncio.Queue[DeliveryRequest],
    job: UploadJob,
    batch: UploadedBatch,
    *,
    progress: UploadProgress,
    run_id: str,
) -> DeliveryRequest:
    """Queue the batch for a worker; raises QueueFull without side effects."""
    loop = asyncio.get_running_loop()
    completion: asyncio.Future[str] = loop.create_future()
    # A disconnected request may never await this future again.
    completion.add_done_callback(
        lambda future: future.exception() if not future.cancelled() else None
    )
    delivery = DeliveryRequest(
        job=job,
        results=batch.results,
        completed=completion,
        caption=batch.caption,
        diagnostics=batch.diagnostics,
        received_seconds=time.monotonic() - progress.started,
        run_id=run_id,
    )
    queue.put_nowait(delivery)
    return delivery


def track_received(
    job: UploadJob,
    delivery: DeliveryRequest,
    *,
    track: Callable[..., None],
    total_size: int,
    run_id: str,
) -> None:
    track(
        job,
        "received",
        run_id=run_id,
        data={
            "files": len(delivery.results),
            "output_bytes": total_size,
            "relay_receive_seconds": delivery.received_seconds,
        },
    )
    for number, (result, diagnostic) in enumerate(
        zip(delivery.results, delivery.diagnostics, strict=False), 1
    ):
        track(
            job,
            "file_received",
            run_id=run_id,
            file_index=number,
            data={
                **diagnostic.telemetry,
                "input_bytes": diagnostic.input_bytes,
                "output_bytes": len(result.data),
                "duration_seconds": diagnostic.duration_seconds,
                "encoding_seconds": diagnostic.encoding_seconds,
                "input_format": diagnostic.input_format,
            },
        )


async def receive_results(
    request: web.Request,
    *,
    state: RelayState,
    limits: UploadLimits,
    authorize: Callable[[web.Request], tuple[UploadJob | None, web.Response | None]],
    rate_limit: Callable[[web.Request, str], int | None],
    track: Callable[..., None],
    notify: Callable[[str], None],
    metrics: RuntimeMetrics,
) -> web.Response:
    """Receive one completed browser batch and transfer it to the delivery queue."""
    receive_started = time.monotonic()
    job, response = authorize(request)
    if response is not None:
        return response
    assert job is not None
    refusal = admission_error(
        job, request, state=state, limits=limits, rate_limit=rate_limit
    )
    if refusal is not None:
        return refusal
    queue = state.queue
    assert queue is not None

    progress = UploadProgress(started=receive_started, last_chunk_at=receive_started)
    job.relay_upload_attempts += 1
    upload_slot_held = True
    job.state = JobState.UPLOADING
    upload_run = valid_id(request.headers.get("X-Diagnostic-Run")) or job.active_run_id
    track(job, "upload_started", run_id=upload_run)
    state.active_uploads += 1
    if not job.relay_upload_started:
        job.relay_upload_started = True
        notify(
            compression_progress_alert(
                job,
                "📨 **Relay upload started**",
                "Stage: browser began sending finished files; "
                "receipt is not yet confirmed.",
            )
        )
    try:
        batch = await read_batch(
            request,
            progress=progress,
            state=state,
            limits=limits,
            discord_limit=job.discord_limit,
        )
        result_count = len(batch.results)
        try:
            delivery = enqueue(queue, job, batch, progress=progress, run_id=upload_run)
        except asyncio.QueueFull:
            job.state = JobState.CLAIMED
            return json_error(
                "The Discord delivery queue filled up. It will retry automatically.",
                503,
                "queue_full",
                retry_after=5,
            )
        # No await between enqueue and handoff. From here the worker, not the
        # HTTP request, releases the batch's reserved bytes.
        state.active_uploads -= 1
        upload_slot_held = False
        job.state = JobState.QUEUED
        metrics.increment("batches_queued")
        metrics.increment("files_queued", result_count)
        metrics.increment("bytes_queued", progress.reserved_bytes)
        track_received(
            job,
            delivery,
            track=track,
            total_size=progress.reserved_bytes,
            run_id=upload_run,
        )
        notify(
            compression_progress_alert(
                job,
                "📤 **Finished files reached relay**",
                f"Files: `{result_count}`; queued for Discord delivery.",
            )
        )
        try:
            message = await asyncio.wait_for(
                asyncio.shield(delivery.completed),
                timeout=DELIVERY_RESPONSE_WAIT_SECONDS,
            )
        except TimeoutError:
            return pending_response()
        except RuntimeError as error:
            _, response = authorize(request)
            return (
                response
                if response is not None
                else json_error(str(error), 502, "discord_delivery_failed")
            )
        job.state = JobState.DONE
        return web.json_response(
            {
                "ok": True,
                "message": message,
                "count": result_count,
            },
            headers={"Cache-Control": "no-store"},
        )
    except RelayBufferFull:
        return json_error(
            "The relay memory buffer is full. Try again shortly.",
            503,
            "delivery_buffer_full",
            retry_after=5,
        )
    except TimeoutError:
        track(
            job,
            "upload_failed",
            run_id=upload_run,
            data=progress.failure_facts("UPLOAD_TIMEOUT", job.relay_upload_attempts),
        )
        return json_error(
            "The upload stalled. Please try again.", 408, "upload_timeout"
        )
    except ConnectionResetError:
        job.state = JobState.CLAIMED
        track(
            job,
            "upload_failed",
            run_id=upload_run,
            data=progress.failure_facts(
                "UPLOAD_CONNECTION_RESET", job.relay_upload_attempts
            ),
        )
        logger.warning(
            "Upload connection reset (reference=%s)", job.diagnostic_reference
        )
        return json_error(
            "The upload connection was interrupted. Keep this page open and retry "
            "sending; your prepared files do not need recompression.",
            503,
            "upload_connection_reset",
            retry_after=3,
        )
    except (ValueError, web.HTTPException) as error:
        track(
            job,
            "upload_failed",
            run_id=upload_run,
            data={"error_code": "INVALID_UPLOAD", "stage": "upload"},
        )
        job.state = JobState.CLAIMED
        return json_error(str(error), 400, "invalid_upload")
    except Exception as error:
        job.state = JobState.CLAIMED
        track(
            job,
            "upload_failed",
            run_id=upload_run,
            data=progress.failure_facts("RELAY_ERROR", job.relay_upload_attempts),
        )
        logger.warning("Upload relay failed (%s)", type(error).__name__)
        return json_error(
            "The relay could not read the compressed files. "
            "Check your connection and try again.",
            500,
            "relay_error",
        )
    finally:
        if upload_slot_held:
            state.active_uploads -= 1
            state.release(progress.reserved_bytes)
            job.state = JobState.CLAIMED
