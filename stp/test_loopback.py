"""
test_loopback.py — STP Full Loopback Integration Test
=======================================================
Tests the complete STP transfer flow (TRANSFER_REQ → CHUNK_DATA → TRANSFER_END)
using two threads on localhost — no server or client code needed.

Folder layout created by this script:
  stp/
  └── test_data/
      ├── source/
      │   └── dummy_song.bin   ← fake audio file generated here
      └── received/
          └── dummy_song.bin   ← output written by STPReceiver

Run from the project root:
    python stp/test_loopback.py

Or from inside the stp/ directory:
    cd stp && python test_loopback.py
"""

import os
import sys
import socket
import threading
import hashlib

# ── Path setup: allow running from either project root or stp/ ─────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_HERE)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from stp import (
    STPSender,
    STPReceiver,
    STPConfig,
    sha256_file,
    calculate_total_chunks,
    VALID_CHUNK_SIZES,
)

# ── Test data directories ──────────────────────────────────────────────────
TEST_DIR      = os.path.join(_HERE, "test_data")
SOURCE_DIR    = os.path.join(TEST_DIR, "source")
RECEIVED_DIR  = os.path.join(TEST_DIR, "received")

os.makedirs(SOURCE_DIR,   exist_ok=True)
os.makedirs(RECEIVED_DIR, exist_ok=True)

# ── Test parameters ────────────────────────────────────────────────────────
PEER_TOKEN   = "TRX_TEST_001"
MUSIC_ID     = "test_music_abc123"
FILENAME     = "dummy_song.bin"
MIME_TYPE    = "audio/mpeg"
CHUNK_SIZE   = 64 * 1024          # 64 KB
HOST         = "127.0.0.1"
PORT         = 59876              # arbitrary high port for loopback

SOURCE_PATH   = os.path.join(SOURCE_DIR,   FILENAME)
RECEIVED_PATH = os.path.join(RECEIVED_DIR, FILENAME)

# ── Colour helpers (Windows-safe fallback) ────────────────────────────────
try:
    import colorama
    colorama.init()
    GREEN  = "\033[92m"
    RED    = "\033[91m"
    YELLOW = "\033[93m"
    CYAN   = "\033[96m"
    RESET  = "\033[0m"
except ImportError:
    GREEN = RED = YELLOW = CYAN = RESET = ""


def _ok(msg):   print(f"  {GREEN}[PASS]{RESET} {msg}")
def _fail(msg): print(f"  {RED}[FAIL]{RESET} {msg}")
def _info(msg): print(f"  {CYAN}[INFO]{RESET} {msg}")
def _warn(msg): print(f"  {YELLOW}[WARN]{RESET} {msg}")


# ═══════════════════════════════════════════════════════════════════════════
# Step 1: Generate a dummy source file
# ═══════════════════════════════════════════════════════════════════════════

def create_dummy_file(path: str, size_bytes: int = 200 * 1024) -> str:
    """
    Write a deterministic pseudo-random binary file of `size_bytes` bytes.
    Using a fixed seed so the hash is reproducible across runs.
    Returns the SHA-256 hex digest of the file.
    """
    import random
    rng = random.Random(42)
    data = bytes(rng.randint(0, 255) for _ in range(size_bytes))
    with open(path, "wb") as f:
        f.write(data)
    return hashlib.sha256(data).hexdigest()


# ═══════════════════════════════════════════════════════════════════════════
# Step 2: Sender thread — runs STPSender
# ═══════════════════════════════════════════════════════════════════════════

sender_result: dict = {}

def sender_thread_fn():
    """
    Accepts one connection, performs the STP sender handshake, streams
    all chunks, and sends TRANSFER_END.
    """
    try:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((HOST, PORT))
        srv.listen(1)
        srv.settimeout(15.0)

        _info(f"[Sender] Listening on {HOST}:{PORT}…")
        conn, addr = srv.accept()
        srv.close()
        _info(f"[Sender] Connection from {addr}")

        config = STPConfig(chunk_size=CHUNK_SIZE)
        sender = STPSender(conn, peer_token=PEER_TOKEN, config=config)

        # Handle TRANSFER_REQ / RESUME_REQ
        meta = sender.handle_transfer_req()
        _info(f"[Sender] Got request for music_id={meta.get('music_id')}")

        # Stream the file
        file_hash = "sha256:" + sha256_file(SOURCE_PATH).replace("sha256:", "")
        success = sender.send_file(
            filepath=SOURCE_PATH,
            music_id=MUSIC_ID,
            file_hash=sha256_file(SOURCE_PATH),
        )

        conn.close()
        sender_result["success"] = success
        sender_result["state"]   = sender.state
        _info(f"[Sender] Done — state={sender.state}, success={success}")

    except Exception as exc:
        sender_result["success"] = False
        sender_result["error"]   = str(exc)
        _fail(f"[Sender] Exception: {exc}")


# ═══════════════════════════════════════════════════════════════════════════
# Step 3: Receiver thread — runs STPReceiver
# ═══════════════════════════════════════════════════════════════════════════

receiver_result: dict = {}

def receiver_thread_fn(file_size: int, file_hash: str, total_chunks: int):
    """
    Connects to the sender, initiates transfer, receives all chunks,
    verifies integrity, and writes the output file.
    """
    import time
    time.sleep(0.3)  # give sender a moment to start listening

    try:
        sock = socket.create_connection((HOST, PORT), timeout=10)
        _info(f"[Receiver] Connected to {HOST}:{PORT}")

        config = STPConfig(chunk_size=CHUNK_SIZE)
        receiver = STPReceiver(sock, peer_token=PEER_TOKEN, config=config)

        # Clean up old received file if present
        if os.path.exists(RECEIVED_PATH):
            os.remove(RECEIVED_PATH)

        receiver.initiate_transfer(
            music_id=MUSIC_ID,
            filename=FILENAME,
            mime_type=MIME_TYPE,
            file_size=file_size,
            file_hash=file_hash,
            total_chunks=total_chunks,
            output_dir=RECEIVED_DIR,
        )
        _info(f"[Receiver] Transfer initiated, receiving {total_chunks} chunks…")

        output_path = receiver.receive_file()

        sock.close()
        receiver_result["success"]     = True
        receiver_result["output_path"] = output_path
        receiver_result["state"]       = receiver.state
        _info(f"[Receiver] Done — state={receiver.state}")
        _info(f"[Receiver] File saved: {output_path}")

    except Exception as exc:
        receiver_result["success"] = False
        receiver_result["error"]   = str(exc)
        _fail(f"[Receiver] Exception: {exc}")


# ═══════════════════════════════════════════════════════════════════════════
# Step 4: Verify received file matches source
# ═══════════════════════════════════════════════════════════════════════════

def verify_output(source_path: str, received_path: str) -> bool:
    if not os.path.isfile(received_path):
        _fail("Received file does not exist.")
        return False

    src_size  = os.path.getsize(source_path)
    recv_size = os.path.getsize(received_path)
    if src_size != recv_size:
        _fail(f"File size mismatch: source={src_size}B, received={recv_size}B")
        return False

    src_hash  = sha256_file(source_path)
    recv_hash = sha256_file(received_path)
    if src_hash != recv_hash:
        _fail(f"SHA-256 mismatch:\n    source:   {src_hash}\n    received: {recv_hash}")
        return False

    return True


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def main():
    print()
    print("=" * 60)
    print("  STP Loopback Integration Test")
    print("=" * 60)

    # ── 1. Create dummy source file ───────────────────────────────────────
    FILE_SIZE = 200 * 1024  # 200 KB — spans multiple 64 KB chunks
    _info(f"Creating dummy source file ({FILE_SIZE // 1024} KB)…")
    raw_hash    = create_dummy_file(SOURCE_PATH, FILE_SIZE)
    file_hash   = f"sha256:{raw_hash}"
    total_chunks = calculate_total_chunks(FILE_SIZE, CHUNK_SIZE)
    _ok(f"Source file created: {SOURCE_PATH}")
    _info(f"SHA-256: {file_hash}")
    _info(f"Total chunks: {total_chunks} × {CHUNK_SIZE // 1024} KB")
    print()

    # ── 2. Start sender thread ────────────────────────────────────────────
    t_sender = threading.Thread(target=sender_thread_fn, daemon=True)
    t_sender.start()

    # ── 3. Start receiver thread ──────────────────────────────────────────
    t_receiver = threading.Thread(
        target=receiver_thread_fn,
        args=(FILE_SIZE, file_hash, total_chunks),
        daemon=True,
    )
    t_receiver.start()

    # ── 4. Wait for both to finish (30 s timeout) ─────────────────────────
    t_sender.join(timeout=30)
    t_receiver.join(timeout=30)
    print()

    # ── 5. Check results ──────────────────────────────────────────────────
    print("=" * 60)
    print("  Results")
    print("=" * 60)

    all_passed = True

    # Sender
    if sender_result.get("success"):
        _ok(f"Sender completed (state={sender_result['state']})")
    else:
        _fail(f"Sender failed: {sender_result.get('error', sender_result.get('state', '?'))}")
        all_passed = False

    # Receiver
    if receiver_result.get("success"):
        _ok(f"Receiver completed (state={receiver_result['state']})")
    else:
        _fail(f"Receiver failed: {receiver_result.get('error', receiver_result.get('state', '?'))}")
        all_passed = False

    # File integrity
    if all_passed:
        if verify_output(SOURCE_PATH, RECEIVED_PATH):
            _ok("File integrity verified: source == received (SHA-256 match)")
        else:
            all_passed = False

    print()
    if all_passed:
        print(f"  {GREEN}** ALL TESTS PASSED **{RESET}")
        print(f"  Source:   {SOURCE_PATH}")
        print(f"  Received: {RECEIVED_PATH}")
    else:
        print(f"  {RED}** SOME TESTS FAILED - check output above **{RESET}")

    print("=" * 60)
    print()
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
