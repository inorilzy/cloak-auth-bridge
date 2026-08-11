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
    cookie_domains: [".Example.com", "example.com"],
    origins: ["https://www.example.com/path", "https://www.example.com"],
    nonce: "abcdefghijklmnop",
    ignored: "value"
  }), {
    id: "0192f0cb-1234",
    siteId: "example-main",
    cookieDomains: ["example.com"],
    origins: ["https://www.example.com"],
    nonce: "abcdefghijklmnop"
  });
});

test("arbitrary message types and short nonces are rejected", () => {
  assert.throws(() => validateCaptureRequest({ type: "evaluate_javascript" }), /消息类型/);
  assert.throws(() => validateCaptureRequest({
    id: "1",
    type: "capture_auth",
    site_id: "example-main",
    cookie_domains: ["example.com"],
    origins: ["https://www.example.com"],
    nonce: "short"
  }), /nonce/);
});

test("capture request without site scope is rejected", () => {
  assert.throws(() => validateCaptureRequest({
    id: "1",
    type: "capture_auth",
    site_id: "example-main",
    nonce: "abcdefghijklmnop"
  }), /Cookie 域名|origin/i);
});

test("invalid JSON is rejected", () => {
  assert.throws(() => parseServerMessage("{"), /有效 JSON/);
});

test("hello ack supports token mode and loopback trust", () => {
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

  assert.equal(isValidHelloAck({
    type: "hello_ack",
    ok: true,
    mode: "loopback_trust",
    client_challenge: clientChallenge,
    server_challenge: serverChallenge
  }, clientChallenge, null), true);
});
