"""Queued Discord delivery, receipts, and delivery diagnostics."""

import asyncio
import json
import logging
import math
import time
from collections.abc import Awaitable, Callable

from ..bot.delivery_policy import compression_target, delivery_failure
from ..bot.messages import compression_outcome_alert
from ..domain import BrowserResult, DeliveryRequest, UploadJob
from ..observability.metrics import RuntimeMetrics

# Log name is part of the operations contract; it must not follow the module path.
logger = logging.getLogger("professor_compressor.application")

# Upper bound for one Discord upload; later outcomes are reported as unconfirmed.
SEND_TIMEOUT_SECONDS = 120


def delivery_timing(delivery: DeliveryRequest, started: float) -> dict[str, float]:
    return {
        "relay_receive_seconds": round(delivery.received_seconds, 3),
        "queue_seconds": round(max(0, started - delivery.queued_at), 3),
        "discord_delivery_seconds": round(max(0, time.monotonic() - started), 3),
    }


def log_delivery_diagnostics(
    delivery: DeliveryRequest, started: float, outcome: str
) -> None:
    files = []
    for index, diagnostic in enumerate(delivery.diagnostics):
        item = {
            "file": index + 1,
            **diagnostic.telemetry,
            "encoding_seconds": diagnostic.encoding_seconds,
            "duration_seconds": diagnostic.duration_seconds,
            "input_bytes": diagnostic.input_bytes,
            "output_bytes": len(delivery.results[index].data),
            "input_format": diagnostic.input_format,
            "video_kbps": diagnostic.video_kbps,
        }
        if diagnostic.encoding_seconds and diagnostic.duration_seconds:
            speed = diagnostic.duration_seconds / diagnostic.encoding_seconds
            if math.isfinite(speed):
                item["encoding_speed"] = round(speed, 3)
        files.append(item)
    logger.info(
        "Processing diagnostics %s",
        json.dumps(
            {
                "outcome": outcome,
                "diagnostic_id": delivery.job.diagnostic_id,
                "reference": delivery.job.diagnostic_reference,
                "run_id": delivery.run_id,
                "files": files,
                **delivery_timing(delivery, started),
            },
            allow_nan=False,
        ),
    )


def outcome_alert(
    delivery: DeliveryRequest,
    started: float,
    batch_limit: int,
    *,
    succeeded: bool,
    total_bytes: int,
    stage: str | None = None,
) -> str:
    """Operator alert for a finished delivery, with the targets the page used."""
    per_file_limit = batch_limit // len(delivery.results)
    return compression_outcome_alert(
        delivery.job,
        succeeded=succeeded,
        file_count=len(delivery.results),
        total_bytes=total_bytes,
        stage=stage,
        results=delivery.results,
        diagnostics=delivery.diagnostics,
        timing=delivery_timing(delivery, started),
        effective_target=min(
            compression_target(delivery.job.discord_limit), per_file_limit
        ),
        unchanged_limit=min(delivery.job.discord_limit, per_file_limit),
    )


def report_outcome(
    delivery: DeliveryRequest,
    started: float,
    *,
    track: Callable[..., None],
    notify: Callable[[str], None],
    batch_limit: int,
    error_code: str | None = None,
) -> None:
    """Log, record, and alert a finished delivery; never affects its result."""
    succeeded = error_code is None
    try:
        log_delivery_diagnostics(
            delivery, started, "delivered" if succeeded else "delivery_failed"
        )
    except Exception:
        logger.warning("Optional delivery diagnostic formatting failed")
    try:
        timing = delivery_timing(delivery, started)
        total_bytes = sum(len(result.data) for result in delivery.results)
        if succeeded:
            data: dict[str, object] = {
                "files": len(delivery.results),
                "output_bytes": total_bytes,
                **timing,
            }
        else:
            data = {"error_code": error_code, "stage": "delivery", **timing}
        track(
            delivery.job,
            "delivered" if succeeded else "delivery_failed",
            run_id=delivery.run_id,
            data=data,
        )
        notify(
            outcome_alert(
                delivery,
                started,
                batch_limit,
                succeeded=succeeded,
                total_bytes=total_bytes,
                **({} if succeeded else {"stage": "Discord delivery"}),
            )
        )
    except Exception:
        logger.warning("Optional delivery report could not be completed")


async def run_delivery_worker(
    worker_number: int,
    *,
    queue: asyncio.Queue[DeliveryRequest],
    send: Callable[[UploadJob, list[BrowserResult], str], Awaitable[str]],
    is_closed: Callable[[], bool],
    track: Callable[..., None],
    metrics: RuntimeMetrics,
    record_outcome: Callable[[UploadJob, int, dict[str, object]], None],
    notify: Callable[[str], None],
    finish: Callable[[DeliveryRequest], None],
    batch_limit: int,
) -> None:
    """Process queue entries; release each dequeued entry even if cancelled."""
    while not is_closed():
        delivery = await queue.get()
        delivery_started = time.monotonic()
        track(
            delivery.job,
            "delivery_started",
            run_id=delivery.run_id,
            data={"queue_seconds": max(0, delivery_started - delivery.queued_at)},
        )
        try:
            message = await asyncio.wait_for(
                send(delivery.job, delivery.results, delivery.caption),
                timeout=SEND_TIMEOUT_SECONDS,
            )
        except Exception as error:
            # The send failed: record the receipt and answer the browser first,
            # then report. Reporting can fail without stopping this worker.
            failure = delivery_failure(error)
            message = str(failure)
            metrics.increment("deliveries_failed")
            record_outcome(
                delivery.job,
                502,
                {
                    "ok": False,
                    "error": message,
                    "code": "discord_delivery_failed",
                    "delivery_outcome": failure.outcome,
                    "error_code": failure.error_code,
                },
            )
            if not delivery.completed.done():
                delivery.completed.set_exception(RuntimeError(message))
            report_outcome(
                delivery,
                delivery_started,
                track=track,
                notify=notify,
                batch_limit=batch_limit,
                error_code=failure.error_code,
            )
        else:
            # Discord accepted the batch. That result is final: record it and
            # answer the browser before any optional diagnostics or alerts.
            total_bytes = sum(len(result.data) for result in delivery.results)
            metrics.increment("deliveries_succeeded")
            metrics.increment("files_delivered", len(delivery.results))
            metrics.increment("bytes_delivered", total_bytes)
            record_outcome(
                delivery.job,
                200,
                {
                    "ok": True,
                    "message": message,
                    "count": len(delivery.results),
                },
            )
            if not delivery.completed.done():
                delivery.completed.set_result(message)
            report_outcome(
                delivery,
                delivery_started,
                track=track,
                notify=notify,
                batch_limit=batch_limit,
            )
        finally:
            try:
                finish(delivery)
            finally:
                queue.task_done()
            logger.info("Delivery worker %d completed a session", worker_number)
            del delivery
