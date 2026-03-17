"""Stress test: reproduce SSE stuck on same session_id with multiple workers.

Simulates real browser behavior: as soon as 'done' SSE event arrives,
immediately fire the next request WITHOUT waiting for the HTTP response
to fully close. The server's finally block (delete_queue) may still be
running when the next request's purge+ensure_group executes.
"""
import asyncio
import os
import time
import httpx

# Bypass any SOCKS/HTTP proxy
for k in list(os.environ):
    if "proxy" in k.lower():
        del os.environ[k]

BASE = "http://127.0.0.1:8765"
TOKEN = "sk-8bed7608668dc4f4b8b50d50cb4caed2d2279952ddd6ccc2"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
TOTAL = 20
TIMEOUT = 30.0
SESSION_ID = "8c82ce11-921c-46cf-81e6-51b4e9953e7b"


async def send_and_signal(client: httpx.AsyncClient, idx: int, done_event: asyncio.Event) -> dict:
    """Send one chat. Signal done_event as soon as 'done' SSE is seen (before stream closes)."""
    t0 = time.time()
    result = {"idx": idx, "status": "unknown", "duration": 0}
    heartbeat_count = 0

    try:
        async with client.stream(
            "POST",
            f"{BASE}/bridge/chat",
            headers={**HEADERS, "Accept": "text/event-stream"},
            json={"sessionId": SESSION_ID, "content": f"hello {idx}"},
            timeout=TIMEOUT,
        ) as resp:
            if resp.status_code != 200:
                result["status"] = f"http_{resp.status_code}"
                body = await resp.aread()
                result["detail"] = body.decode()[:200]
                result["duration"] = time.time() - t0
                done_event.set()
                return result

            async for line in resp.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                if line.startswith("event:"):
                    etype = line.split(":", 1)[1].strip()
                    if etype == "done":
                        result["status"] = "done"
                        result["duration"] = time.time() - t0
                        # Signal IMMEDIATELY — next request fires before this stream closes
                        done_event.set()
                        return result
                    elif etype == "error":
                        result["status"] = "error"
                        result["duration"] = time.time() - t0
                        done_event.set()
                        return result
                elif line.startswith(": heartbeat"):
                    heartbeat_count += 1
                    if time.time() - t0 > 25:
                        result["status"] = f"STUCK ({heartbeat_count} heartbeats)"
                        result["duration"] = time.time() - t0
                        done_event.set()
                        return result

            result["status"] = "stream_ended_no_done"
            result["duration"] = time.time() - t0
    except httpx.ReadTimeout:
        result["status"] = f"STUCK (timeout, {heartbeat_count} hb)"
        result["duration"] = time.time() - t0
    except Exception as e:
        result["status"] = f"error: {type(e).__name__}: {e}"
        result["duration"] = time.time() - t0

    done_event.set()
    return result


async def main():
    print(f"Session: {SESSION_ID}")
    print(f"Sequential (browser-style): {TOTAL} requests, fire next on 'done' immediately\n")

    results = []
    pending_tasks = []
    async with httpx.AsyncClient() as client:
        for i in range(TOTAL):
            done_event = asyncio.Event()
            # Launch request in background
            task = asyncio.create_task(send_and_signal(client, i, done_event))
            pending_tasks.append(task)
            # Wait for 'done' signal (NOT for stream close / finally cleanup)
            await done_event.wait()
            # DO NOT await task — let the stream close asynchronously
            # This simulates browser: JS gets 'done', immediately sends next fetch()
            # Meanwhile server's finally block (delete_queue) is still running

        # Now collect all results
        for task in pending_tasks:
            r = await task
            results.append(r)

    for r in results:
        icon = "✅" if r["status"] == "done" else "❌"
        print(f"  {icon} [{r['idx']:2d}] {r['status']:<40s} ({r['duration']:.1f}s)")

    # Summary
    stuck = sum(1 for r in results if "STUCK" in r.get("status", ""))
    ok = sum(1 for r in results if r["status"] == "done")
    print(f"\n{'='*60}")
    print(f"  Total: {len(results)}  |  OK: {ok}  |  STUCK: {stuck}")
    if stuck > 0:
        print(f"\n  Race condition REPRODUCED (sequential browser-style)!")
    else:
        print(f"\n  All passed.")


if __name__ == "__main__":
    asyncio.run(main())
