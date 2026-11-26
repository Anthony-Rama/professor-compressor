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
logger = logging.getLogger("professor_compressor.delivery_worker")

# Upper bound for one Discord upload; later outcomes are reported as unconfirmed.
SEND_TIMEOUT_SECONDS = 120


def delivery_timing(delivery: DeliveryRequest, started: float) -> dict:
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
            total_bytes = sum(len(result.data) for result in delivery.results)
            try:
                log_delivery_diagnostics(delivery, delivery_started, "delivered")
            except Exception:
                logger.warning("Optional delivery diagnostic formatting failed")
            track(
                delivery.job,
                "delivered",
                run_id=delivery.run_id,
                data={
                    "files": len(delivery.results),
                    "output_bytes": total_bytes,
                    **delivery_timing(delivery, delivery_started),
                },
            )
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
            notify(
                outcome_alert(
                    delivery,
                    delivery_started,
                    batch_limit,
                    succeeded=True,
                    total_bytes=total_bytes,
                )
            )
            if not delivery.completed.done():
                delivery.completed.set_result(message)
        except Exception as error:
            failure = delivery_failure(error)
            try:
                log_delivery_diagnostics(delivery, delivery_started, "delivery_failed")
            except Exception:
                logger.warning("Optional delivery diagnostic formatting failed")
            track(
                delivery.job,
                "delivery_failed",
                run_id=delivery.run_id,
                data={
                    "error_code": failure.error_code,
                    "stage": "delivery",
                    **delivery_timing(delivery, delivery_started),
                },
            )
            metrics.increment("deliveries_failed")
            message = str(failure)
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
            notify(
                outcome_alert(
                    delivery,
                    delivery_started,
                    batch_limit,
                    succeeded=False,
                    total_bytes=sum(len(result.data) for result in delivery.results),
                    stage="Discord delivery",
                )
            )
            if not delivery.completed.done():
                delivery.completed.set_exception(RuntimeError(message))
        finally:
            try:
                finish(delivery)
            finally:
                queue.task_done()
            logger.info("Delivery worker %d completed a session", worker_number)
            del delivery
