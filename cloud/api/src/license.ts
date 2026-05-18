// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1
//
// License-key system. Issues Ed25519-signed JWS-compact tokens that
// Business+ tier subscribers can install on offline / air-gapped
// systems. The Python CLI (`postrule license verify`) checks the
// signature against an embedded public key and the claims against
// local time, with no network call required.
//
// Format (JWS compact, RFC 7515-style):
//   base64url(header).base64url(payload).base64url(signature)
//
// Header fixed:
//   { "alg": "EdDSA", "typ": "PostruleLicense", "v": 1 }
//
// Payload claims:
//   {
//     "iss": "postrule.ai",
//     "sub": "<postrule_user_id>",
//     "tier": "business",
//     "account_hash": "<hex>",
//     "iat": <unix_seconds>,
//     "exp": <unix_seconds>,
//     "max_seats": <integer | null>,    // optional install-cap
//     "license_id": "<uuid>"             // for revocation lookup
//   }
//
// The private key is stored as wrangler secret LICENSE_SIGNING_PRIVATE_KEY
// (32-byte raw, hex-encoded). Generate once with scripts/generate-license-key.ts.
// The public key is published — embed in postrule Python package + sample
// docs so verifiers can check signatures without us in the loop.

const ENCODER = new TextEncoder();
const DECODER = new TextDecoder();

export interface LicenseClaims {
  iss: string;
  sub: string;
  tier: 'business' | 'scale' | 'pro' | 'free' | 'comp';
  account_hash: string;
  iat: number;
  exp: number;
  max_seats: number | null;
  license_id: string;
}

const HEADER = { alg: 'EdDSA', typ: 'PostruleLicense', v: 1 } as const;

function b64uEncode(bytes: Uint8Array): string {
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function b64uDecode(s: string): Uint8Array {
  const padded = s.replace(/-/g, '+').replace(/_/g, '/').padEnd(s.length + ((4 - (s.length % 4)) % 4), '=');
  const bin = atob(padded);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function hexToBytes(hex: string): Uint8Array {
  const clean = hex.replace(/[^0-9a-fA-F]/g, '');
  if (clean.length % 2 !== 0) {
    throw new Error('hex string has odd length');
  }
  const out = new Uint8Array(clean.length / 2);
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(clean.slice(i * 2, i * 2 + 2), 16);
  }
  return out;
}

export function bytesToHex(bytes: Uint8Array): string {
  return [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('');
}

/**
 * Generate a fresh Ed25519 keypair. Returns hex-encoded raw private key
 * (32 bytes) and hex-encoded raw public key (32 bytes). Use this once
 * per environment (staging, production) to provision the signing key.
 */
export async function generateKeypair(): Promise<{ privateKeyHex: string; publicKeyHex: string }> {
  const kp = (await crypto.subtle.generateKey(
    { name: 'Ed25519' },
    true,
    ['sign', 'verify'],
  )) as CryptoKeyPair;

  // PKCS#8 wraps the raw key in ASN.1; the last 32 bytes are the
  // raw private key.
  const pkcs8 = new Uint8Array(
    (await crypto.subtle.exportKey('pkcs8', kp.privateKey)) as ArrayBuffer,
  );
  const privateKeyRaw = pkcs8.slice(pkcs8.length - 32);

  // SPKI wraps the raw public key in ASN.1; the last 32 bytes are it.
  const spki = new Uint8Array(
    (await crypto.subtle.exportKey('spki', kp.publicKey)) as ArrayBuffer,
  );
  const publicKeyRaw = spki.slice(spki.length - 32);

  return {
    privateKeyHex: bytesToHex(privateKeyRaw),
    publicKeyHex: bytesToHex(publicKeyRaw),
  };
}

/**
 * Import a raw 32-byte Ed25519 private key (hex) for signing.
 */
async function importPrivateKey(hex: string): Promise<CryptoKey> {
  const raw = hexToBytes(hex);
  if (raw.length !== 32) {
    throw new Error(`private key must be 32 bytes; got ${raw.length}`);
  }
  // Wrap raw 32 bytes back into PKCS#8 envelope. Ed25519 ASN.1 prefix
  // is fixed (16 bytes); private key inserted after.
  const pkcs8Prefix = new Uint8Array([
    0x30, 0x2e, 0x02, 0x01, 0x00, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70,
    0x04, 0x22, 0x04, 0x20,
  ]);
  const pkcs8 = new Uint8Array(pkcs8Prefix.length + 32);
  pkcs8.set(pkcs8Prefix);
  pkcs8.set(raw, pkcs8Prefix.length);

  return crypto.subtle.importKey(
    'pkcs8',
    pkcs8.buffer as ArrayBuffer,
    { name: 'Ed25519' },
    false,
    ['sign'],
  );
}

/**
 * Import a raw 32-byte Ed25519 public key (hex) for verification.
 */
export async function importPublicKey(hex: string): Promise<CryptoKey> {
  const raw = hexToBytes(hex);
  if (raw.length !== 32) {
    throw new Error(`public key must be 32 bytes; got ${raw.length}`);
  }
  // SPKI prefix for Ed25519 is 12 bytes.
  const spkiPrefix = new Uint8Array([
    0x30, 0x2a, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x03, 0x21, 0x00,
  ]);
  const spki = new Uint8Array(spkiPrefix.length + 32);
  spki.set(spkiPrefix);
  spki.set(raw, spkiPrefix.length);

  return crypto.subtle.importKey(
    'spki',
    spki.buffer as ArrayBuffer,
    { name: 'Ed25519' },
    false,
    ['verify'],
  );
}

export interface SignArgs {
  privateKeyHex: string;
  user_id: number | string;
  tier: LicenseClaims['tier'];
  account_hash: string;
  /** Token validity in seconds. Default 30 days. */
  ttlSeconds?: number;
  max_seats?: number | null;
  license_id?: string;
  /** For deterministic tests; defaults to Date.now()/1000. */
  now?: () => number;
}

/**
 * Sign a license token. Returns the JWS compact serialization.
 */
export async function signLicense(args: SignArgs): Promise<{ token: string; claims: LicenseClaims }> {
  const now = args.now ? args.now() : Math.floor(Date.now() / 1000);
  const ttl = args.ttlSeconds ?? 30 * 86400;
  const claims: LicenseClaims = {
    iss: 'postrule.ai',
    sub: String(args.user_id),
    tier: args.tier,
    account_hash: args.account_hash,
    iat: now,
    exp: now + ttl,
    max_seats: args.max_seats ?? null,
    license_id: args.license_id ?? crypto.randomUUID(),
  };

  const headerB64 = b64uEncode(ENCODER.encode(JSON.stringify(HEADER)));
  const payloadB64 = b64uEncode(ENCODER.encode(JSON.stringify(claims)));
  const signingInput = `${headerB64}.${payloadB64}`;

  const key = await importPrivateKey(args.privateKeyHex);
  const sig = new Uint8Array(
    await crypto.subtle.sign('Ed25519', key, ENCODER.encode(signingInput)),
  );
  const sigB64 = b64uEncode(sig);

  return { token: `${signingInput}.${sigB64}`, claims };
}

/**
 * Verify a license token against the configured public key. Returns the
 * parsed claims if signature valid AND not expired; throws otherwise.
 *
 * (We export this so tests + admin tools can verify; production CLI
 * verification happens in the Python sibling at src/postrule/license.py.)
 */
export async function verifyLicense(token: string, publicKeyHex: string, now?: number): Promise<LicenseClaims> {
  const parts = token.split('.');
  if (parts.length !== 3) throw new Error('malformed_token');
  const [headerB64, payloadB64, sigB64] = parts;

  const headerJson = DECODER.decode(b64uDecode(headerB64!));
  const header = JSON.parse(headerJson);
  if (header.alg !== 'EdDSA' || header.typ !== 'PostruleLicense') {
    throw new Error('unsupported_header');
  }

  const sig = b64uDecode(sigB64!);
  const key = await importPublicKey(publicKeyHex);
  const ok = await crypto.subtle.verify(
    'Ed25519',
    key,
    sig.buffer as ArrayBuffer,
    ENCODER.encode(`${headerB64}.${payloadB64}`),
  );
  if (!ok) throw new Error('invalid_signature');

  const claims: LicenseClaims = JSON.parse(DECODER.decode(b64uDecode(payloadB64!)));
  const t = now ?? Math.floor(Date.now() / 1000);
  if (claims.exp < t) throw new Error('expired');
  if (claims.iat > t + 60) throw new Error('issued_in_future'); // 60s clock skew tolerance

  return claims;
}
