import test from "node:test";
import assert from "node:assert/strict";
import { createHmacProof } from "../extension/auth.js";

test("client and server proofs are domain-separated and deterministic", async () => {
  const token = "0123456789abcdef";
  const challenge = "abcdefghijklmnop";
  const clientProof = await createHmacProof(token, "client", challenge);
  const serverProof = await createHmacProof(token, "server", challenge);

  assert.equal(clientProof.length, 64);
  assert.equal(serverProof.length, 64);
  assert.notEqual(clientProof, serverProof);
  assert.equal(clientProof, await createHmacProof(token, "client", challenge));
});

test("unknown HMAC roles are rejected", async () => {
  await assert.rejects(createHmacProof("0123456789abcdef", "peer", "abcdefghijklmnop"), /角色/);
});
