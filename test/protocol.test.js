import test from "node:test";
import assert from "node:assert/strict";
import {
  isValidHelloAck,
  parseServerMessage,
  validateCaptureRequest
} from "../extension/protocol.js";

test("valid capture request is reduced to trusted fields", () => {
  assert.deepEqual(validateCaptureRequest({
    id: "0192f0cb-1234",
    type: "capture_auth",
    site_id: "example-main",
    nonce: "abcdefghijklmnop",
    ignored: "value"
  }), {
    id: "0192f0cb-1234",
    siteId: "example-main",
    nonce: "abcdefghijklmnop"
  });
});

test("arbitrary message types and short nonces are rejected", () => {
  assert.throws(() => validateCaptureRequest({ type: "evaluate_javascript" }), /消息类型/);
  assert.throws(() => validateCaptureRequest({
    id: "1",
    type: "capture_auth",
    site_id: "example-main",
    nonce: "short"
  }), /nonce/);
});

test("invalid JSON is rejected", () => {
  assert.throws(() => parseServerMessage("{"), /有效 JSON/);
});

test("hello ack is rejected before a server challenge was verified", () => {
  const clientChallenge = "abcdefghijklmnop";
  assert.equal(isValidHelloAck({
    type: "hello_ack",
    ok: true,
    client_challenge: clientChallenge,
    server_challenge: null
  }, clientChallenge, null), false);

  const serverChallenge = "ponmlkjihgfedcba";
  assert.equal(isValidHelloAck({
    type: "hello_ack",
    ok: true,
    client_challenge: clientChallenge,
    server_challenge: serverChallenge
  }, clientChallenge, serverChallenge), true);
});
