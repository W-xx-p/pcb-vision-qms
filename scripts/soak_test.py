from __future__ import annotations

import argparse
import concurrent.futures
import statistics
import time

import httpx


def request(base_url: str, api_key: str) -> tuple[bool, float]:
    started = time.perf_counter()
    try:
        response = httpx.get(f"{base_url}/api/dashboard", headers={"X-API-Key": api_key}, timeout=10)
        return response.status_code == 200, (time.perf_counter() - started) * 1000
    except httpx.HTTPError:
        return False, (time.perf_counter() - started) * 1000


def main() -> None:
    parser = argparse.ArgumentParser(description="PCB QMS 72-hour stability test")
    parser.add_argument("--url", default="http://127.0.0.1:8013")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--duration-hours", type=float, default=72)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--requests-per-second", type=float, default=5)
    parser.add_argument("--max-error-rate", type=float, default=0.001)
    parser.add_argument("--max-p95-ms", type=float, default=1000)
    args = parser.parse_args()
    deadline = time.monotonic() + args.duration_hours * 3600
    latencies: list[float] = []
    successes = failures = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        while time.monotonic() < deadline:
            batch = [pool.submit(request, args.url.rstrip("/"), args.api_key) for _ in range(args.concurrency)]
            for future in batch:
                ok, latency = future.result()
                latencies.append(latency)
                successes += int(ok)
                failures += int(not ok)
            time.sleep(max(0, args.concurrency / args.requests_per_second))
    ordered = sorted(latencies)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    error_rate = failures / max(1, successes + failures)
    print({"requests": successes + failures, "failures": failures, "error_rate": error_rate, "p95_ms": round(p95, 2), "mean_ms": round(statistics.mean(latencies), 2)})
    if error_rate > args.max_error_rate or p95 > args.max_p95_ms:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
