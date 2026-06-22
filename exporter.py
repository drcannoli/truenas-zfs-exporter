"""Prometheus exporter for TrueNAS ZFS pool health via the REST API."""
from __future__ import annotations

import logging
import os
import signal
import time
from datetime import datetime
from typing import Any, Iterable
from urllib.parse import urljoin

import requests
from prometheus_client import REGISTRY, start_http_server
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

LOG = logging.getLogger("truenas_zfs_exporter")

NAMESPACE = "truenas_zfs"
POOL_STATUSES = ("ONLINE", "DEGRADED", "FAULTED", "OFFLINE", "UNAVAIL", "REMOVED")
SCAN_STATES = ("FINISHED", "SCANNING", "CANCELED", "NONE")
TOPOLOGY_CATEGORIES = ("data", "log", "cache", "spare", "special", "dedup")

TRUENAS_API_URL = os.environ.get("TRUENAS_API_URL", "https://127.0.0.1").rstrip("/")
TRUENAS_API_KEY = os.environ.get("TRUENAS_API_KEY", "")
VERIFY_SSL = os.environ.get("TRUENAS_VERIFY_SSL", "true").lower() in ("1", "true", "yes")
LISTEN_PORT = int(os.environ.get("EXPORTER_PORT", "9134"))
TIMEOUT = float(os.environ.get("TRUENAS_TIMEOUT", "30"))


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _epoch_seconds(value: Any) -> float | None:
    """Normalise a TrueNAS timestamp ($date wrapper, epoch s/ms, or ISO) to seconds."""
    if value is None:
        return None
    if isinstance(value, dict):
        inner = _to_float(value.get("$date"))
        return inner / 1000.0 if inner is not None else None
    num = _to_float(value)
    if num is not None:
        return num / 1000.0 if num > 1e12 else num
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _iter_vdevs(node: Any, parent: str = "") -> Iterable[tuple[str, dict]]:
    """Walk a topology category, yielding (hierarchical_name, vdev) for each level."""
    if isinstance(node, list):
        for child in node:
            yield from _iter_vdevs(child, parent)
        return
    if not isinstance(node, dict):
        return
    name = node.get("name") or node.get("type") or node.get("guid") or "unknown"
    full = f"{parent}/{name}" if parent else str(name)
    if node.get("stats") is not None or node.get("type") == "DISK":
        yield full, node
    for child in node.get("children", []) or []:
        yield from _iter_vdevs(child, full)


class TrueNASZFSCollector:
    def _fetch_pools(self) -> list[dict]:
        url = urljoin(TRUENAS_API_URL + "/", "api/v2.0/pool")
        headers = {"Authorization": f"Bearer {TRUENAS_API_KEY}"}
        resp = requests.get(url, headers=headers, timeout=TIMEOUT, verify=VERIFY_SSL)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        return data if isinstance(data, list) else []

    def collect(self):
        up = GaugeMetricFamily(f"{NAMESPACE}_up", "1 if the last TrueNAS API scrape succeeded, else 0")
        duration = GaugeMetricFamily(f"{NAMESPACE}_scrape_duration_seconds", "Duration of the TrueNAS API scrape")
        started = time.monotonic()

        if not TRUENAS_API_KEY:
            LOG.error("TRUENAS_API_KEY is not set")
            up.add_metric([], 0.0)
            duration.add_metric([], time.monotonic() - started)
            yield up
            yield duration
            return

        try:
            pools = self._fetch_pools()
        except Exception as exc:
            LOG.error("TrueNAS API fetch failed: %s", exc)
            up.add_metric([], 0.0)
            duration.add_metric([], time.monotonic() - started)
            yield up
            yield duration
            return

        health = GaugeMetricFamily(f"{NAMESPACE}_pool_health", "Pool health (1 for the current status)", labels=["pool", "status"])
        healthy = GaugeMetricFamily(f"{NAMESPACE}_pool_healthy", "1 if the pool reports healthy, else 0", labels=["pool"])
        size = GaugeMetricFamily(f"{NAMESPACE}_pool_size_bytes", "Total pool size in bytes", labels=["pool"])
        allocated = GaugeMetricFamily(f"{NAMESPACE}_pool_allocated_bytes", "Allocated pool space in bytes", labels=["pool"])
        free = GaugeMetricFamily(f"{NAMESPACE}_pool_free_bytes", "Free pool space in bytes", labels=["pool"])
        capacity = GaugeMetricFamily(f"{NAMESPACE}_pool_capacity_ratio", "Allocated fraction of pool size (0 to 1)", labels=["pool"])
        frag = GaugeMetricFamily(f"{NAMESPACE}_pool_fragmentation_ratio", "Pool fragmentation fraction (0 to 1)", labels=["pool"])

        scrub_state = GaugeMetricFamily(f"{NAMESPACE}_pool_scrub_state", "Scan state (1 for the current state)", labels=["pool", "state"])
        scrub_errors = GaugeMetricFamily(f"{NAMESPACE}_pool_scrub_errors", "Errors found by the last scan", labels=["pool"])
        scrub_done = GaugeMetricFamily(f"{NAMESPACE}_pool_scrub_last_finished_timestamp_seconds", "Unix time the last scan finished", labels=["pool"])

        vdev_state = GaugeMetricFamily(f"{NAMESPACE}_vdev_state", "Vdev health (1 for the current status)", labels=["pool", "vdev", "status"])
        read_err = CounterMetricFamily(f"{NAMESPACE}_vdev_read_errors", "Cumulative read errors for a vdev", labels=["pool", "vdev"])
        write_err = CounterMetricFamily(f"{NAMESPACE}_vdev_write_errors", "Cumulative write errors for a vdev", labels=["pool", "vdev"])
        cksum_err = CounterMetricFamily(f"{NAMESPACE}_vdev_checksum_errors", "Cumulative checksum errors for a vdev", labels=["pool", "vdev"])

        for pool in pools:
            name = str(pool.get("name") or pool.get("id") or "unknown")
            status = str(pool.get("status") or "UNKNOWN").upper()
            for state in sorted(set(POOL_STATUSES) | {status}):
                health.add_metric([name, state], 1.0 if state == status else 0.0)
            healthy_value = pool.get("healthy")
            if healthy_value is not None:
                healthy.add_metric([name], 1.0 if healthy_value else 0.0)

            for family, key in ((size, "size"), (allocated, "allocated"), (free, "free")):
                value = _to_float(pool.get(key))
                if value is not None:
                    family.add_metric([name], value)
            total = _to_float(pool.get("size"))
            used = _to_float(pool.get("allocated"))
            if total and used is not None and total > 0:
                capacity.add_metric([name], used / total)
            fragmentation = _to_float(pool.get("fragmentation"))
            if fragmentation is not None:
                frag.add_metric([name], fragmentation / 100.0)

            scan = pool.get("scan") or {}
            scan_state = str(scan.get("state") or "NONE").upper()
            for state in sorted(set(SCAN_STATES) | {scan_state}):
                scrub_state.add_metric([name, state], 1.0 if state == scan_state else 0.0)
            errors = _to_float(scan.get("errors"))
            if errors is not None:
                scrub_errors.add_metric([name], errors)
            finished = _epoch_seconds(scan.get("end_time"))
            if finished is not None:
                scrub_done.add_metric([name], finished)

            topology = pool.get("topology") or {}
            for category in TOPOLOGY_CATEGORIES:
                for vdev_name, vdev in _iter_vdevs(topology.get(category)):
                    vdev_status = str(vdev.get("status") or "UNKNOWN").upper()
                    vdev_state.add_metric([name, vdev_name, vdev_status], 1.0)
                    stats = vdev.get("stats") or {}
                    read = _to_float(stats.get("read_errors"))
                    write = _to_float(stats.get("write_errors"))
                    cksum = _to_float(stats.get("checksum_errors"))
                    if read is not None:
                        read_err.add_metric([name, vdev_name], read)
                    if write is not None:
                        write_err.add_metric([name, vdev_name], write)
                    if cksum is not None:
                        cksum_err.add_metric([name, vdev_name], cksum)

        up.add_metric([], 1.0)
        duration.add_metric([], time.monotonic() - started)
        yield from (
            up, duration, health, healthy, size, allocated, free, capacity, frag,
            scrub_state, scrub_errors, scrub_done, vdev_state, read_err, write_err, cksum_err,
        )


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    REGISTRY.register(TrueNASZFSCollector())
    start_http_server(LISTEN_PORT)
    LOG.info("listening on :%s (target=%s)", LISTEN_PORT, TRUENAS_API_URL)
    signal.pause()


if __name__ == "__main__":
    main()
