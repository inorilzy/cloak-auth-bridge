import asyncio
import json

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from cloak_auth_bridge.crypto import create_proof, verify_proof
from cloak_auth_bridge.extension_bridge import ExtensionBridge
from cloak_auth_bridge.websocket_server import ExtensionWebSocketServer


async def _hello(websocket, challenge: str = "abcdefghijklmnop") -> None:
    await websocket.send(
        json.dumps(
            {
                "type": "hello",
                "extension_id": "abcdefghijklmnopabcdefghijklmnop",
                "profile_alias": "chrome-default",
                "challenge": challenge,
            }
        )
    )


@pytest.mark.asyncio
async def test_loopback_trust_connects_without_token() -> None:
    bridge = ExtensionBridge(None, allow_loopback_trust=True)
    websocket_server = ExtensionWebSocketServer(bridge, port=0)

    async with websocket_server.serve() as running_server:
        port = running_server.sockets[0].getsockname()[1]
        async with connect(f"ws://127.0.0.1:{port}") as websocket:
            await _hello(websocket)
            ack = json.loads(await websocket.recv())
            assert ack == {
                "type": "hello_ack",
                "ok": True,
                "mode": "loopback_trust",
                "client_challenge": "abcdefghijklmnop",
                "server_challenge": ack["server_challenge"],
            }
            assert len(ack["server_challenge"]) >= 16
            assert bridge.connected is True

            async def respond_to_capture() -> None:
                request = json.loads(await websocket.recv())
                assert request["type"] == "capture_auth"
                assert request["site_id"] == "bilibili-main"
                await websocket.send(
                    json.dumps(
                        {
                            "id": request["id"],
                            "type": "capture_auth_result",
                            "nonce": request["nonce"],
                            "ok": True,
                            "payload": {
                                "version": 1,
                                "siteId": "bilibili-main",
                                "sourceProfile": "chrome-default",
                                "capturedAt": "2026-08-10T14:00:00Z",
                                "cookies": [],
                                "origins": [
                                    {
                                        "origin": "https://www.bilibili.com",
                                        "localStorage": [],
                                    }
                                ],
                            },
                        }
                    )
                )

            response_task = asyncio.create_task(respond_to_capture())
            bundle = await bridge.capture(
                "bilibili-main",
                ["bilibili.com"],
                ["https://www.bilibili.com"],
            )
            await response_task
            assert bundle.site_id == "bilibili-main"


@pytest.mark.asyncio
async def test_token_mode_pairing_and_capture_round_trip() -> None:
    token = "0123456789abcdef"
    bridge = ExtensionBridge(token, allow_loopback_trust=False)
    websocket_server = ExtensionWebSocketServer(bridge, port=0)

    async with websocket_server.serve() as running_server:
        port = running_server.sockets[0].getsockname()[1]
        async with connect(f"ws://127.0.0.1:{port}") as websocket:
            challenge = "abcdefghijklmnop"
            await _hello(websocket, challenge)
            challenge_response = json.loads(await websocket.recv())
            proof_payload = f"{challenge}:{challenge_response['server_challenge']}"
            assert verify_proof(token, "server", proof_payload, challenge_response["proof"])
            await websocket.send(
                json.dumps(
                    {
                        "type": "hello_response",
                        "client_challenge": challenge,
                        "server_challenge": challenge_response["server_challenge"],
                        "proof": create_proof(token, "client", proof_payload),
                    }
                )
            )
            ack = json.loads(await websocket.recv())
            assert ack["ok"] is True
            assert ack["mode"] == "token"

            async def respond_to_capture() -> None:
                request = json.loads(await websocket.recv())
                await websocket.send(
                    json.dumps(
                        {
                            "id": request["id"],
                            "type": "capture_auth_result",
                            "nonce": request["nonce"],
                            "ok": True,
                            "payload": {
                                "version": 1,
                                "siteId": "bilibili-main",
                                "sourceProfile": "chrome-default",
                                "capturedAt": "2026-08-10T14:00:00Z",
                                "cookies": [],
                                "origins": [
                                    {
                                        "origin": "https://www.bilibili.com",
                                        "localStorage": [],
                                    }
                                ],
                            },
                        }
                    )
                )

            response_task = asyncio.create_task(respond_to_capture())
            bundle = await bridge.capture(
                "bilibili-main",
                ["bilibili.com"],
                ["https://www.bilibili.com"],
            )
            await response_task
            assert bundle.site_id == "bilibili-main"


@pytest.mark.asyncio
async def test_recorded_token_handshake_cannot_be_replayed() -> None:
    token = "0123456789abcdef"
    bridge = ExtensionBridge(token, allow_loopback_trust=False)
    websocket_server = ExtensionWebSocketServer(bridge, port=0)
    hello = {
        "type": "hello",
        "extension_id": "abcdefghijklmnopabcdefghijklmnop",
        "profile_alias": "chrome-default",
        "challenge": "abcdefghijklmnop",
    }

    async with websocket_server.serve() as running_server:
        port = running_server.sockets[0].getsockname()[1]
        async with connect(f"ws://127.0.0.1:{port}") as first:
            await first.send(json.dumps(hello))
            recorded_challenge = json.loads(await first.recv())

        async with connect(f"ws://127.0.0.1:{port}") as second:
            await second.send(json.dumps(hello))
            fresh_challenge = json.loads(await second.recv())
            assert fresh_challenge["server_challenge"] != recorded_challenge["server_challenge"]

            old_payload = f"{hello['challenge']}:{recorded_challenge['server_challenge']}"
            await second.send(
                json.dumps(
                    {
                        "type": "hello_response",
                        "client_challenge": hello["challenge"],
                        "server_challenge": recorded_challenge["server_challenge"],
                        "proof": create_proof(token, "client", old_payload),
                    }
                )
            )
            with pytest.raises(ConnectionClosed):
                await second.recv()

    assert bridge.connected is False


@pytest.mark.asyncio
async def test_late_capture_result_does_not_close_connection() -> None:
    bridge = ExtensionBridge(None, allow_loopback_trust=True)
    websocket_server = ExtensionWebSocketServer(bridge, port=0)

    async with websocket_server.serve() as running_server:
        port = running_server.sockets[0].getsockname()[1]
        async with connect(f"ws://127.0.0.1:{port}") as websocket:
            await _hello(websocket, "abcdefghijklmnop")
            await websocket.recv()  # loopback hello_ack

            capture_task = asyncio.create_task(
                bridge.capture(
                    "bilibili-main",
                    ["bilibili.com"],
                    ["https://www.bilibili.com"],
                    timeout=0.01,
                )
            )
            request = json.loads(await websocket.recv())
            with pytest.raises(TimeoutError):
                await capture_task

            await websocket.send(
                json.dumps(
                    {
                        "id": request["id"],
                        "type": "capture_auth_result",
                        "nonce": request["nonce"],
                        "ok": True,
                        "payload": {
                            "version": 1,
                            "siteId": "bilibili-main",
                            "sourceProfile": "chrome-default",
                            "capturedAt": "2026-08-10T14:00:00Z",
                            "cookies": [],
                            "origins": [
                                {
                                    "origin": "https://www.bilibili.com",
                                    "localStorage": [],
                                }
                            ],
                        },
                    }
                )
            )
            await websocket.send(json.dumps({"type": "ping", "at": 1}))
            pong = json.loads(await websocket.recv())
            assert pong == {"type": "pong", "at": 1}
