"""Liveness, readiness, aggregate metrics, and the dashboard runtime snapshot."""

import math

import discord
from aiohttp import web

from ..bot.delivery import DeliveryService
from ..bot.events import CommandSync
from ..config import MIB, Settings
from ..observability.metrics import RuntimeMetrics
from ..relay.upload import RelayState
from ..sessions.registry import SessionRegistry


class HealthService:
    """Report process state from cached values; never calls Discord."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: discord.Client,
        relay: RelayState,
        sessions: SessionRegistry,
        delivery: DeliveryService,
        command_sync: CommandSync,
        metrics: RuntimeMetrics,
    ) -> None:
        self.settings = settings
        self.client = client
        self.relay = relay
        self.sessions = sessions
        self.delivery = delivery
        self.command_sync = command_sync
        self.metrics = metrics

    def is_ready(self) -> bool:
        """Discord connected, commands synced, workers running, and not draining."""
        return (
            self.client.is_ready()
            and self.command_sync.synced
            and self.delivery.workers_ready()
            and not self.sessions.draining
        )

    async def health(self, request: web.Request) -> web.Response:
        del request
        return web.json_response(
            {
                "status": "ok",
                "revision": self.settings.app_revision[:64],
                "discord_ready": self.client.is_ready(),
                "active_upload_links": len(self.sessions.jobs),
                "active_relay_uploads": self.relay.active_uploads,
                "delivery_queue_depth": self.relay.queue.qsize()
                if self.relay.queue
                else 0,
                "delivery_queue_capacity": self.settings.delivery_queue_size,
                "delivery_buffer_mib": round(self.relay.buffered_bytes / MIB, 1),
                "delivery_buffer_limit_mib": round(
                    self.settings.max_delivery_buffer_bytes / MIB, 1
                ),
            }
        )

    async def readiness(self, request: web.Request) -> web.Response:
        del request
        ready = self.is_ready()
        return web.json_response(
            {
                "status": "ready" if ready else "not_ready",
                "discord_ready": self.client.is_ready(),
                "workers_ready": self.delivery.workers_ready(),
                "commands_synced": self.command_sync.synced,
                "draining": self.sessions.draining,
            },
            status=200 if ready else 503,
            headers={"Cache-Control": "no-store"},
        )

    async def aggregate_metrics(self, request: web.Request) -> web.Response:
        del request
        return web.json_response(
            {
                "privacy": "aggregate_process_counters_only",
                **self.metrics.snapshot(),
            },
            headers={"Cache-Control": "no-store"},
        )

    def dashboard_snapshot(self) -> dict:
        """Cached Discord/process facts; no API requests or private identifiers."""
        guilds = self.client.guilds
        connected = self.client.is_ready()
        latency = self.client.latency
        return {
            "connected_servers": len(guilds) if connected or guilds else None,
            "server_count_current": connected,
            "unavailable_servers": sum(bool(guild.unavailable) for guild in guilds),
            "discord_ready": connected,
            "ready": self.is_ready(),
            "commands_synced": self.command_sync.synced,
            "draining": self.sessions.draining,
            "gateway_latency_ms": round(latency * 1000)
            if math.isfinite(latency)
            else None,
            "revision": self.settings.app_revision[:64],
            "active_sessions": len(self.sessions.jobs),
            "active_uploads": self.relay.active_uploads,
            "queue_depth": self.relay.queue.qsize() if self.relay.queue else 0,
            "queue_capacity": self.settings.delivery_queue_size,
            "delivery_workers": sum(
                not worker.done() for worker in self.delivery.workers
            ),
            "worker_capacity": self.settings.delivery_workers,
            "buffer_bytes": self.relay.buffered_bytes,
            "buffer_limit_bytes": self.settings.max_delivery_buffer_bytes,
            "counters": self.metrics.snapshot(),
        }
