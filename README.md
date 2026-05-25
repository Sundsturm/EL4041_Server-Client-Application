# Hybrid Server-Client Music Sharing System with P2P Song Transfer

<!-- Badges -->
<div align="center">
  
  [![Python Version](https://img.shields.io/badge/python-3.10%20%7C%203.11-blue.svg)](https://www.python.org/)
  [![UI Framework](https://img.shields.io/badge/UI-PySide6%20%2F%20Qt6-teal.svg)](https://doc.qt.io/qtforpython-6/)
  [![Protocol](https://img.shields.io/badge/Protocol-Custom%20Socket%20%2F%20STP-orange.svg)]()
  [![Security](https://img.shields.io/badge/Security-TLS%201.3%20%7C%20JWT-red.svg)]()
  
</div>

A secure, high-performance hybrid music sharing system. The system combines a client-server architecture for centralized coordination, user authentication, and metadata discovery, with a direct peer-to-peer (P2P) data plane using a custom Song Transfer Protocol (STP) over TCP/TLS for direct audio transfer between clients.

---

## 📌 Table of Contents
1. [System Architecture](#-system-architecture)
2. [Key Features](#-key-features)
3. [Server Architecture](#-server-architecture)
4. [Client Architecture](#-client-architecture)
5. [Application Protocols](#-application-protocols)
   - [Control Plane Protocols](#control-plane-protocols)
   - [Data Plane Protocol (STP)](#data-plane-protocol-stp)
6. [Security Model](#-security-model)
7. [Tech Stack Summary](#-tech-stack-summary)

---

## 🏗️ System Architecture

The project implements a hybrid architecture:
* **Control Plane (Client-Server):** Used for authentication, session management, discovery, and publishing metadata.
* **Data Plane (P2P):** Used for direct client-to-client transfer of audio files using TCP. The server **never** stores or proxies the actual audio file bytes.

```mermaid
graph TD
    subgraph ControlPlane ["Control Plane"]
        DesktopClient["Desktop Client"] -->|REST / HTTPS| Server["Server"]
        AndroidClient["Android / Termux Client"] -->|Custom Socket Protocol (CSP)| Server
    end

    subgraph DataPlaneP2P ["Data Plane (P2P)"]
        DesktopClient -->|Song Transfer Protocol (STP) / TCP| AndroidClient
    end
```

---

## 🌟 Key Features

- **Hybrid P2P Architecture:** Eliminates server storage and bandwidth bottlenecks by transferring files directly between peers.
- **Dual-Client Support:**
  - **Desktop Client:** Feature-rich PySide6 (Qt6) GUI.
  - **Android Client:** Lightweight command-line interface (CLI) optimized for Termux.
- **Secure Control & Data Transmission:** Powered by JWT access tokens, temporary session tokens, peer transfer tokens, and TLS 1.3 encryption.
- **Custom Application Protocols:**
  - **Custom Socket Protocol (CSP):** Low-overhead, TCP-based protocol for Android control messages.
  - **Song Transfer Protocol (STP):** Custom protocol with frame headers, JSON metadata, and binary chunk payloads supporting pause, resume, and hash validation.
- **Data Integrity:** Double-layer hashing verification (per-chunk and whole-file SHA256 / HMAC-SHA256).

---

## 🖥️ Server Architecture

The server acts as the coordinator and discovery registry. It is structured into five logical layers:

```
┌─────────────────────────────────────────┐
│         Network Interface Layer         │  <-- REST & Custom Socket Protocol (CSP)
├─────────────────────────────────────────┤
│           Message Router Layer          │  <-- Route parsing & request validation
├─────────────────────────────────────────┤
│              Security Layer             │  <-- Token verification & validation
├─────────────────────────────────────────┤
│        Application Services Layer       │  <-- Auth, Session, Discovery, Pub/Sub, Transfer Neg.
├─────────────────────────────────────────┤
│              Database Layer             │  <-- SQLite
└─────────────────────────────────────────┘
```

### 1. Network Interface Layer
- **Desktop (HTTPS):** Accepts REST requests from the Desktop Client.
- **Android (CSP/TCP):** Accepts custom socket connections. Supports a future upgrade path to QUIC.
- **Components:** REST Listener, Socket Listener, Request Dispatcher.

### 2. Message Router Layer
Parses incoming packets, validates protocol message types, and dispatches them to respective services:
- **Authentication:** `LOGIN_REQ`, `REGISTER_REQ`, `LOGOUT_REQ`
- **Session:** `REFRESH_REQ`, `SESSION_VERIFY`
- **Discovery:** `DISCOVERY_REQ`, `PEER_STATUS_REQ`
- **Metadata Pub/Sub:** `PUBLISH_REQ`, `SUBSCRIBE_REQ`
- **Transfer Negotiation:** `DOWNLOAD_REQ`, `NEGOTIATION_REQ`
- **Logging & System Sync:** `LOG_REQ`, `HISTORY_REQ`, `TIME_SYNC_REQ`

### 3. Security Layer
Validates sessions and handles peer authorizations. It manages three key token classes:

| Token Type | Purpose | Token Format | Lifetime | Storage Location |
| :--- | :--- | :--- | :--- | :--- |
| **Access Token** | General control plane requests validation | JWT | 10–15 Minutes | Client memory / local file |
| **Session Token** | Refreshing expired access tokens | Random string | 7–14 Days | Client file & Server DB |
| **Peer Token** | Authorizing specific client-to-client transfers | Random short token | 1–5 Minutes | Server SQLite DB |

### 4. Application Services Layer
- **Authentication Service:** Registration, Login, Logout, Password verification.
- **Session Service:** Refreshing, expiration checks, and token revoking.
- **Peer Registry & Discovery Service:** Manages registry database containing online peer information: `peer_id`, `ip`, `port`, `status`, and `last_seen`.
- **Publish/Subscribe Service:** Manages song metadata indexing (`music_id`, `filename`, `mime_type`, `size`, `hash`, `owner`).
- **Time Synchronization Service:** NTP-based sync for token expiration validation and system logs.
- **Logging Service:** Keeps track of login history, publishing events, downloads, and transfers.
- **Transfer Negotiation Service:** Resolves `DOWNLOAD_REQ` requests, checks peer availability, and generates a short-lived `peer_token` containing direct connection info (`peer_id`, `peer_ip`, `peer_port`, `peer_token`) to initiate direct client-to-client connections.

### 5. Database Layer
Uses SQLite for relational database schemas.
- **Database:** SQLite
- **Key Tables:** `users`, `profiles`, `sessions`, `peer_registry`, `music_metadata`, `publish_history`, `download_history`, `logs`, `peer_tokens`, `transfer_negotiation`.

---

## 📱 Client Architecture

Clients run a modular 6-layer architecture:
1. **Network Interface:** Handles HTTP client actions (`requests`/`httpx`) on Desktop, or Socket/QUIC (using `aioquic`) on Android.
2. **Client Controller:** Converts user UI actions (Qt GUI events on Desktop, CLI commands on Android) into protocol requests.
3. **Security Module:** Locally stores `access.jwt`, `session.token`, and `profile.json` files and appends appropriate authorization headers to outgoing requests.
4. **Publish/Subscribe Module:** Publishes local song metadata and queries available songs on the server.
5. **Peer Transfer Module:** Executes P2P transfers. Contains a **Chunk Sender**, **Chunk Receiver**, **Resume Manager**, and **Integrity Checker**.
6. **Local Storage:** Manages raw files, JSON configs, and credentials.

### Client Local File Structure
Clients store data entirely using structured folders and local JSON files (no client-side database runtime like SQLite is used):

```
client/
├── profile/
│   └── profile.json         # User profile details
├── tokens/
│   ├── access.jwt           # JWT control plane token
│   └── session.token        # Permanent session token
├── settings/
│   └── config.json          # Client configuration parameters
├── history/
│   └── history.json         # Local transfer/listening history log
└── music/
    └── [audio files]        # Directory containing MP3, FLAC, WAV, AAC, etc.
```

---

## 🔌 Application Protocols

### Control Plane Protocols

#### Desktop Client (REST/HTTPS API)
- `POST /login` - User authentication
- `POST /register` - User registration
- `POST /publish` - Publish local song metadata to index
- `GET /songs` - Retrieve published song list
- `GET /history` - Get user action history
- `POST /download` - Request transfer negotiation parameters

#### Android Client (Custom Socket Protocol - CSP)
A message-based socket protocol utilizing JSON payloads.

*Supported Messages:* `LOGIN_REQ`, `REGISTER_REQ`, `PUBLISH_REQ`, `SUBSCRIBE_REQ`, `DISCOVERY_REQ`, `DOWNLOAD_REQ`, `HEARTBEAT`, `ACK`, `NACK`.

---

### Data Plane Protocol (STP)
The **Song Transfer Protocol (STP)** is a custom protocol operating over TCP for transferring audio data in binary chunks.

#### STP Frame Structure
```
┌──────────────────────────────────────────────┐
│  Frame Header (Metadata/Payload boundaries)  │
├──────────────────────────────────────────────┤
│  JSON Metadata Header (chunk/track info)     │
├──────────────────────────────────────────────┤
│  Binary Payload (raw file bytes)             │
└──────────────────────────────────────────────┘
```

- **JSON Metadata Fields:** `version`, `msg_type`, `music_id`, `filename`, `mime_type`, `chunk_id`, `total_chunks`, `chunk_size`, `chunk_hash`.
- **Binary Payload:** Raw file chunk bytes.
- **Message Types:**
  - `TRANSFER_REQ` / `TRANSFER_ACCEPT` / `TRANSFER_FAIL`
  - `CHUNK_DATA` / `CHUNK_ACK` / `CHUNK_NACK`
  - `RESUME_REQ`
  - `TRANSFER_END`

#### STP Design Options
* **Supported Formats:** MP3, FLAC, WAV, AAC, OGG, M4A.
* **Chunk Size:** Configurable between `32 KB` and `256 KB` (Recommended default: `64 KB`).
* **Integrity Validation:** SHA256 per chunk (`chunk_hash`) and SHA256 check on the fully reassembled file.

---

## 🔒 Security Model

- **Access Protection:** Control plane requests are authorized using short-lived Access Tokens (JWT, 10-15 minute expiry).
- **Session Continuity:** Session renewal is managed via server-stored Session Tokens (7-14 day expiry).
- **P2P Transfer Security:** Direct peer negotiation uses temporary tokens (`peer_token`, 1-5 minute expiry) verified through the server.
- **Data Integrity:** Verification uses HMAC-SHA256 (both per chunk and on the final reassembled file).
- **Transport Security:** TLS 1.3 (REST/HTTPS for Desktop, and custom secure sockets for Android).

---

## 🛠️ Tech Stack Summary

| Component | Technology | Description |
| :--- | :--- | :--- |
| **Backend Language** | Python | Main runtime for Server & Clients |
| **Desktop Client UI** | PySide6 (Qt6) | GUI Framework |
| **Android Client CLI** | Python CLI / Termux | Command-Line Interface client |
| **Desktop Control Plane** | HTTPS / REST (`requests` or `httpx`) | HTTP client operations |
| **Android Control Plane** | Custom Socket Protocol (CSP) / TCP | Custom TCP Socket (or `aioquic` for QUIC) |
| **Server Database** | SQLite | Central SQL database |
| **Peer Data Plane** | TCP / STP | Song Transfer Protocol |
