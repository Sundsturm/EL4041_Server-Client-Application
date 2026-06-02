# Termux / Android CLI Client

Hybrid Server-Client Music Sharing System with P2P Song Transfer.

This version is adjusted to your server files:

- `server/network/quic_server.py`
- `server/router/message_router.py`
- `server/stp/protocol.py`
- `server/stp/encoder.py`
- `server/stp/decoder.py`

## Matching protocol

### CSP over QUIC

Wire format:

```text
[4-byte uint32 big-endian message length][UTF-8 JSON payload]
```

Request JSON is flat:

```json
{
  "msg_type": "LOGIN_REQ",
  "username": "android",
  "password": "123456"
}
```

Authenticated messages include:

```json
{
  "msg_type": "SUBSCRIBE_REQ",
  "q": "numb",
  "access_token": "..."
}
```

`LOGOUT_REQ`, `REFRESH_REQ`, and `HEARTBEAT` use `session_token`.

### STP over TCP

STP frames use:

```text
[16-byte binary header][JSON metadata][binary payload]
```

Header format follows `!BBIIIH`.

## Install on Termux

```bash
pkg update && pkg upgrade
pkg install python git clang openssl libffi rust make pkg-config
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

## Configure

Edit `config.py`:

```python
SERVER_HOST = "your-server-tailscale-ip-or-hostname"
SERVER_QUIC_PORT = 4433
SERVER_REST_BASE_URL = "https://your-server-host:8443"
STP_LISTEN_PORT = 5050
```

## Run

CSP/QUIC mode:

```bash
python main.py
```

REST fallback mode:

```bash
python main.py --mode rest
```

## CLI commands

```text
help
register <username> <password> [display_name]
login <username> <password>
logout
profile
publish <path_to_audio>
search <query>
download <music_id> [filename]
history [download|publish|login|logs]
serve-stp [port]
exit
```

## Suggested demo flow

Terminal A / peer that shares file:

```text
login android1 123456
publish music/song.mp3
serve-stp 5050
```

Terminal B / peer that downloads:

```text
login android2 123456
search song
download <music_id>
```

The negotiation returns `peer_ip`, `peer_port`, and `peer_token`. Use those for STP transfer integration/testing.
