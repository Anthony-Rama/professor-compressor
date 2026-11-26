"""Multipart upload admission, validation, and queue handoff."""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from aiohttp import web

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

    results: list[BrowserResult] = []
    caption = ""
    caption_received = False
    diagnostics: list[CompressionDiagnostic] = []
    diagnostics_received = False
    total_size = 0
    last_chunk_at = receive_started
    received_bytes = 0
    job.relay_upload_attempts += 1

    def upload_failure_facts(code: str) -> dict:
        now = time.monotonic()
        return {
            "error_code": code,
            "stage": "upload",
            "upload_attempts": job.relay_upload_attempts,
            "received_bytes": received_bytes,
            "relay_receive_seconds": max(0, now - receive_started),
            "receive_idle_seconds": max(0, now - last_chunk_at),
        }

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
        if not request.content_type.startswith("multipart/"):
            raise ValueError("The upload format was invalid. Please try again.")
        reader = await request.multipart()
        upload_deadline = time.monotonic() + 300
        while True:
            field = await asyncio.wait_for(
                reader.next(),
                timeout=max(0, min(30, upload_deadline - time.monotonic())),
            )
            if field is None:
                break
            if field.name == "caption":
                if caption_received or results:
                    raise ValueError("The message field was repeated or out of order.")
                caption_received = True
                caption_data = bytearray()
                while chunk := await asyncio.wait_for(
                    field.read_chunk(1024),
                    timeout=max(0, min(30, upload_deadline - time.monotonic())),
                ):
                    caption_data.extend(chunk)
                    received_bytes += len(chunk)
                    last_chunk_at = time.monotonic()
                    if len(caption_data) > 4000:
                        break
                if len(caption_data) > 4000:
                    raise ValueError("The message is too long.")
                try:
                    caption = " ".join(caption_data.decode("utf-8").split())
                except UnicodeDecodeError as error:
                    raise ValueError("The message contains invalid text.") from error
                if len(caption) > 1000:
                    raise ValueError("The message must be 1,000 characters or fewer.")
                continue
            if field.name == "diagnostics":
                if diagnostics_received or results:
                    raise ValueError(
                        "The diagnostics field was repeated or out of order."
                    )
                diagnostics_received = True
                raw_diagnostics = bytearray()
                while chunk := await asyncio.wait_for(
                    field.read_chunk(1024),
                    timeout=max(0, min(30, upload_deadline - time.monotonic())),
                ):
                    raw_diagnostics.extend(chunk)
                    received_bytes += len(chunk)
                    last_chunk_at = time.monotonic()
                    if len(raw_diagnostics) > 16384:
                        raise ValueError("The diagnostics field is too long.")
                diagnostics = parse_compression_diagnostics(
                    bytes(raw_diagnostics), max_clips=limits.max_clips
                )
                continue
            if field.name != "clips" or not field.filename:
                raise ValueError("Only video file fields are accepted.")
            if len(results) >= limits.max_clips:
                raise ValueError(f"You can send at most {limits.max_clips} results.")

            name = safe_result_name(field.filename, len(results) + 1)

            file_data = bytearray()
            while chunk := await asyncio.wait_for(
                field.read_chunk(1024 * 1024),
                timeout=max(0, min(30, upload_deadline - time.monotonic())),
            ):
                if not state.try_reserve(len(chunk), limits.max_delivery_buffer_bytes):
                    return json_error(
                        "The relay memory buffer is full. Try again shortly.",
                        503,
                        "delivery_buffer_full",
                        retry_after=5,
                    )
                received_bytes += len(chunk)
                last_chunk_at = time.monotonic()
                total_size += len(chunk)
                file_data.extend(chunk)
                if len(file_data) > job.discord_limit:
                    raise ValueError(
                        f"{name} exceeds Discord's current file-size limit."
                    )
                if total_size > limits.max_result_total_bytes:
                    raise ValueError(
                        "The combined compressed results exceed the relay limit."
                    )
            if not file_data:
                raise ValueError(f"{name} is empty.")
            result_bytes = bytes(file_data)
            del file_data
            if not valid_mp4_signature(result_bytes):
                raise ValueError(
                    f"{name} did not contain a complete MP4 video. "
                    "Try the original again or convert it to MP4 first."
                )
            results.append(BrowserResult(name=name, data=result_bytes))

        if not results:
            raise ValueError("No compressed MP4 results were received.")
        if len(diagnostics) != len(results):
            diagnostics = []
        result_count = len(results)
        loop = asyncio.get_running_loop()
        completion: asyncio.Future[str] = loop.create_future()
        # A disconnected request may never await this future again.
        completion.add_done_callback(
            lambda future: future.exception() if not future.cancelled() else None
        )
        delivery = DeliveryRequest(
            job=job,
            results=results,
            completed=completion,
            caption=caption,
            diagnostics=diagnostics,
            received_seconds=time.monotonic() - receive_started,
            run_id=upload_run,
        )
        try:
            queue.put_nowait(delivery)
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
        metrics.increment("files_queued", len(results))
        metrics.increment("bytes_queued", total_size)
        track(
            job,
            "received",
            run_id=upload_run,
            data={
                "files": len(results),
                "output_bytes": total_size,
                "relay_receive_seconds": delivery.received_seconds,
            },
        )
        for number, (result, diagnostic) in enumerate(
            zip(results, diagnostics, strict=False), 1
        ):
            track(
                job,
                "file_received",
                run_id=upload_run,
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
        notify(
            compression_progress_alert(
                job,
                "📤 **Finished files reached relay**",
                f"Files: `{result_count}`; queued for Discord delivery.",
            )
        )
        try:
            message = await asyncio.wait_for(
                asyncio.shield(completion), timeout=DELIVERY_RESPONSE_WAIT_SECONDS
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
    except TimeoutError:
        track(
            job,
            "upload_failed",
            run_id=upload_run,
            data=upload_failure_facts("UPLOAD_TIMEOUT"),
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
            data=upload_failure_facts("UPLOAD_CONNECTION_RESET"),
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
            data=upload_failure_facts("RELAY_ERROR"),
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
            state.release(total_size)
            job.state = JobState.CLAIMED
