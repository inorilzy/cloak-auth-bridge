export async function createHmacProof(pairingToken, role, challenge) {
  if (role !== "client" && role !== "server") {
    throw new Error("无效 HMAC 角色");
  }
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(pairingToken),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const payload = encoder.encode(`${role}:${challenge}`);
  const signature = new Uint8Array(await crypto.subtle.sign("HMAC", key, payload));
  return [...signature].map((value) => value.toString(16).padStart(2, "0")).join("");
}

