Written with the assistance of [Claude Code].

# truenas-zfs-exporter

A small Prometheus exporter that exposes ZFS pool health, capacity, fragmentation, scrub state/age, and per-vdev read/write/checksum errors from a TrueNAS system.

It queries the TrueNAS JSON-RPC 2.0 websocket API (`pool.query` over `wss://<host>/api/current`) with an API key, so it runs as an ordinary container with no `/dev/zfs`, no privileged access, and no host scripts. This is the tier that node-exporter's ZFS collector and property-based exporters don't provide (scrub plus per-vdev errors).

The pinned client (`@TS-25.10.3`) uses plaintext API-key auth. TrueNAS 26 switches API keys to SCRAM-SHA-512 (and removes the legacy REST API entirely); when upgrading to 26, bump the client tag in `requirements.txt` and adjust the `auth.login_with_api_key` call.
2-7rsbzAoBCgHbYK1xqKoXIulzxy1gyHBewoapPVcK5OXuAU0QNz78dazg6fsPibMF
## Metrics2-7rsbzAoBCgHbYK1xqKoXIulzxy1gyHBewoapPVcK5OXuAU0QNz78dazg6fsPibMF

| Metric | Type | Labels | Description |
|---|---|---|---|
| `truenas_zfs_up` | gauge | | 1 if the last API scrape succeeded |
| `truenas_zfs_scrape_duration_seconds` | gauge | | API scrape duration |
| `truenas_zfs_pool_health` | gauge | `pool`, `status` | 1 for the pool's current status (ONLINE/DEGRADED/FAULTED/...) |
| `truenas_zfs_pool_healthy` | gauge | `pool` | 1 if the pool reports healthy |
| `truenas_zfs_pool_size_bytes` | gauge | `pool` | Total pool size |
| `truenas_zfs_pool_allocated_bytes` | gauge | `pool` | Allocated (used) bytes |
| `truenas_zfs_pool_free_bytes` | gauge | `pool` | Free bytes |
| `truenas_zfs_pool_capacity_ratio` | gauge | `pool` | Allocated / size (0 to 1) |
| `truenas_zfs_pool_fragmentation_ratio` | gauge | `pool` | Fragmentation (0 to 1) |
| `truenas_zfs_pool_scrub_state` | gauge | `pool`, `state` | 1 for the current scan state (FINISHED/SCANNING/...) |
| `truenas_zfs_pool_scrub_errors` | gauge | `pool` | Errors found by the last scan |
| `truenas_zfs_pool_scrub_last_finished_timestamp_seconds` | gauge | `pool` | Unix time the last scan finished |
| `truenas_zfs_vdev_state` | gauge | `pool`, `vdev`, `status` | 1 for a vdev's current status |
| `truenas_zfs_vdev_read_errors_total` | counter | `pool`, `vdev` | Cumulative read errors |
| `truenas_zfs_vdev_write_errors_total` | counter | `pool`, `vdev` | Cumulative write errors |
| `truenas_zfs_vdev_checksum_errors_total` | counter | `pool`, `vdev` | Cumulative checksum errors |

## Configuration

| Variable | Default | Description |
|---|---|---|
| `TRUENAS_API_URL` | `https://127.0.0.1` | Base URL of the TrueNAS API |
| `TRUENAS_API_KEY` | (required) | Bearer token from System Settings > API Keys |
| `TRUENAS_VERIFY_SSL` | `true` | Set `false` for the self-signed localhost cert |
| `EXPORTER_PORT` | `9134` | Port to serve `/metrics` on |
| `TRUENAS_TIMEOUT` | `30` | API request timeout (seconds) |
| `LOG_LEVEL` | `INFO` | Log level |

## Create an API key

TrueNAS UI, top-right user menu, API Keys, Add, then copy the token.

## Build and run

```bash
docker build -t truenas-zfs-exporter:latest .

docker run -d --name truenas-zfs-exporter \
  -e TRUENAS_API_URL=https://127.0.0.1 \
  -e TRUENAS_API_KEY=YOUR_TOKEN \
  -e TRUENAS_VERIFY_SSL=false \
  -p 9134:9134 \
  truenas-zfs-exporter:latest

curl -s localhost:9134/metrics | grep truenas_zfs
```

## Scrape config

```yaml
scrape_configs:
  - job_name: truenas-zfs
    static_configs:
      - targets: ["truenas-zfs-exporter:9134"]
```

## Example alerts

```promql
truenas_zfs_pool_health{status!="ONLINE"} == 1

truenas_zfs_vdev_checksum_errors_total > 0
truenas_zfs_vdev_read_errors_total > 0
truenas_zfs_vdev_write_errors_total > 0

time() - truenas_zfs_pool_scrub_last_finished_timestamp_seconds > 35 * 24 * 3600

truenas_zfs_pool_capacity_ratio > 0.85
```

