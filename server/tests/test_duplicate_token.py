"""
Verify the evict-on-reconnect fix for multi-worker duplicate token.

Strategy: use iptables DROP to simulate a true network partition.
  1. Connect WS#1.
  2. Add iptables rule to DROP all packets from the client's source port —
     the server never receives FIN/RST, so it's a true half-open connection.
  3. Try reconnecting with the same token from a new socket.
     After the fix, the server should immediately accept WS#2 and evict WS#1.

Must be run as root (for iptables).

Usage:
    SERVER_WORKERS=2 uv run python run.py   # in another terminal
    uv run python tests/test_duplicate_token.py
"""

import asyncio
import subprocess

import websockets

SERVER_URL = "ws://127.0.0.1:8765/bridge/bot"
TOKEN = "sk-cd5edf25c7369159318d3d46f5ea0bc8f3e9df3b7ed5672f"


def _iptables_drop_port(sport: int):
    """Block outgoing traffic from a specific source port (simulates network death)."""
    cmd = [
        "iptables", "-A", "OUTPUT",
        "-p", "tcp",
        "--sport", str(sport),
        "-d", "127.0.0.1", "--dport", "8765",
        "-j", "DROP",
    ]
    subprocess.run(cmd, check=True)
    # Also block incoming so the server's data/pings never reach us
    cmd2 = [
        "iptables", "-A", "INPUT",
        "-p", "tcp",
        "--dport", str(sport),
        "-s", "127.0.0.1", "--sport", "8765",
        "-j", "DROP",
    ]
    subprocess.run(cmd2, check=True)


def _iptables_cleanup(sport: int):
    """Remove the DROP rules."""
    for chain, flag_pair in [
        ("OUTPUT", ("--sport", str(sport), "-d", "127.0.0.1", "--dport", "8765")),
        ("INPUT",  ("--dport", str(sport), "-s", "127.0.0.1", "--sport", "8765")),
    ]:
        cmd = ["iptables", "-D", chain, "-p", "tcp", *flag_pair, "-j", "DROP"]
        subprocess.run(cmd, check=False)


async def main():
    print("=" * 60)
    print("Verifying evict-on-reconnect fix (multi-worker)")
    print("  Using iptables DROP to simulate true half-open connection")
    print("=" * 60)

    blocked_port = None
    try:
        # ── Step 1: Establish WS#1 ──────────────────────────────
        print("\n[Step 1] Connecting WS#1 ...")
        ws1 = await websockets.connect(
            f"{SERVER_URL}?token={TOKEN}",
            ping_interval=None,
            close_timeout=1,
        )
        local_port = ws1.local_address[1]
        print(f"  OK  WS#1 connected (local port {local_port})")

        # ── Step 2: iptables DROP — true half-open ───────────────
        print(f"\n[Step 2] Adding iptables DROP for sport={local_port} ...")
        _iptables_drop_port(local_port)
        blocked_port = local_port
        print(f"  OK  All packets from :{local_port} are now DROPped")
        print(f"  Server will NOT receive FIN/RST — true half-open connection")

        # Give a moment for any in-flight packets
        await asyncio.sleep(2)

        # ── Step 3: Reconnect — should succeed immediately ───────
        print("\n[Step 3] Attempting reconnection with the same token ...")
        print("  (Server should evict WS#1 and accept WS#2 immediately)\n")

        for attempt in range(1, 4):
            try:
                ws2 = await websockets.connect(
                    f"{SERVER_URL}?token={TOKEN}",
                    ping_interval=None,
                    close_timeout=2,
                    open_timeout=5,
                )
                try:
                    msg = await asyncio.wait_for(ws2.recv(), timeout=2)
                    if "error" in str(msg):
                        print(f"  Attempt {attempt}: REJECTED (server sent: {msg})")
                        await asyncio.sleep(3)
                        continue
                    print(f"  Attempt {attempt}: OK Connected! (received: {msg})")
                except asyncio.TimeoutError:
                    # No error message within 2s = connection is alive and healthy
                    print(f"  Attempt {attempt}: OK Connected successfully!")
                except websockets.exceptions.ConnectionClosed as e:
                    print(f"  Attempt {attempt}: FAIL Closed after accept code={e.code}")
                    await asyncio.sleep(3)
                    continue

                await ws2.close()

                print("\n" + "=" * 60)
                print("FIX VERIFIED: Reconnection succeeded immediately!")
                print("The evict-on-reconnect mechanism is working.")
                print("=" * 60)
                return
            except websockets.exceptions.ConnectionClosedError as e:
                print(f"  Attempt {attempt}: REJECTED code={e.code} reason='{e.reason}'")
            except websockets.exceptions.ConnectionClosedOK as e:
                print(f"  Attempt {attempt}: Closed  code={e.code} reason='{e.reason}'")
            except Exception as e:
                print(f"  Attempt {attempt}: Error: {type(e).__name__}: {e}")

            await asyncio.sleep(3)

        print("\n" + "=" * 60)
        print("FIX NOT WORKING: Reconnection attempts were still rejected!")
        print("=" * 60)

    finally:
        # Always clean up iptables rules
        if blocked_port:
            print(f"\n[Cleanup] Removing iptables rules for port {blocked_port} ...")
            _iptables_cleanup(blocked_port)
            print("  OK  iptables rules removed")


if __name__ == "__main__":
    asyncio.run(main())
