// Ключі доступу (WebAuthn / passkeys) у браузері: перетворення опцій сервера (JSON, base64url)
// у формат navigator.credentials і назад. Без сторонніх бібліотек.
import { post } from "../api/client";

type Json = Record<string, unknown>;

function b64urlToBuf(value: string): ArrayBuffer {
  const b64 = value.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out.buffer;
}

function bufToB64url(buf: ArrayBuffer | null | undefined): string {
  if (!buf) return "";
  const bytes = new Uint8Array(buf);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

type Descriptor = { id: string; type: string; transports?: string[] };

const toDescriptors = (list: unknown): PublicKeyCredentialDescriptor[] =>
  ((list as Descriptor[] | undefined) ?? []).map((d) => ({
    id: b64urlToBuf(d.id),
    type: "public-key",
    transports: d.transports as AuthenticatorTransport[] | undefined,
  }));

/** Чи вміє браузер ключі доступу взагалі (будь-який спосіб: вбудований, телефон, USB-ключ). */
export function passkeysSupported(): boolean {
  return typeof window !== "undefined" && !!window.PublicKeyCredential && !!navigator.credentials;
}

/** Чи є на цьому пристрої вбудований спосіб: Face ID / Touch ID / Windows Hello / відбиток. */
export async function platformAuthenticatorAvailable(): Promise<boolean> {
  if (!passkeysSupported()) return false;
  try {
    return await PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable();
  } catch {
    return false;
  }
}

/** Назва вбудованого способу для поточного пристрою (ключ i18n). */
export function biometricKey(): "passkey.bio.apple" | "passkey.bio.windows" | "passkey.bio.android" | "passkey.bio.generic" {
  const ua = navigator.userAgent;
  if (/iPhone|iPad|Macintosh/.test(ua)) return "passkey.bio.apple";
  if (/Windows/.test(ua)) return "passkey.bio.windows";
  if (/Android/.test(ua)) return "passkey.bio.android";
  return "passkey.bio.generic";
}

/** Користувач скасував вікно браузера (або минув час) — не показуємо це як помилку. */
export function isCancelled(err: unknown): boolean {
  return err instanceof DOMException && (err.name === "NotAllowedError" || err.name === "AbortError");
}

/** Ключ уже є на цьому пристрої (excludeCredentials). */
export function isAlreadyRegistered(err: unknown): boolean {
  return err instanceof DOMException && err.name === "InvalidStateError";
}

async function create(options: Json): Promise<Json> {
  const user = options.user as Json;
  const publicKey: PublicKeyCredentialCreationOptions = {
    ...(options as unknown as PublicKeyCredentialCreationOptions),
    challenge: b64urlToBuf(options.challenge as string),
    user: { ...(user as unknown as PublicKeyCredentialUserEntity), id: b64urlToBuf(user.id as string) },
    excludeCredentials: toDescriptors(options.excludeCredentials),
  };
  const cred = (await navigator.credentials.create({ publicKey })) as PublicKeyCredential | null;
  if (!cred) throw new DOMException("cancelled", "NotAllowedError");
  const response = cred.response as AuthenticatorAttestationResponse;
  return {
    id: cred.id,
    rawId: bufToB64url(cred.rawId),
    type: cred.type,
    response: {
      clientDataJSON: bufToB64url(response.clientDataJSON),
      attestationObject: bufToB64url(response.attestationObject),
      transports: typeof response.getTransports === "function" ? response.getTransports() : [],
    },
    clientExtensionResults: cred.getClientExtensionResults?.() ?? {},
    authenticatorAttachment: cred.authenticatorAttachment ?? undefined,
  };
}

async function get(options: Json): Promise<Json> {
  const publicKey: PublicKeyCredentialRequestOptions = {
    ...(options as unknown as PublicKeyCredentialRequestOptions),
    challenge: b64urlToBuf(options.challenge as string),
    allowCredentials: toDescriptors(options.allowCredentials),
  };
  const cred = (await navigator.credentials.get({ publicKey })) as PublicKeyCredential | null;
  if (!cred) throw new DOMException("cancelled", "NotAllowedError");
  const response = cred.response as AuthenticatorAssertionResponse;
  return {
    id: cred.id,
    rawId: bufToB64url(cred.rawId),
    type: cred.type,
    response: {
      clientDataJSON: bufToB64url(response.clientDataJSON),
      authenticatorData: bufToB64url(response.authenticatorData),
      signature: bufToB64url(response.signature),
      userHandle: bufToB64url(response.userHandle),
    },
    clientExtensionResults: cred.getClientExtensionResults?.() ?? {},
  };
}

/** Вхід без пароля: браузер покаже ключі, збережені для цього сайту. */
export async function loginWithPasskey(): Promise<void> {
  const { options } = await post<{ options: Json }>("/api/auth/login/passkey/begin/");
  const credential = await get(options);
  await post("/api/auth/login/passkey/finish/", { credential });
}

/** Другий фактор після пароля. */
export async function confirmWithPasskey(): Promise<void> {
  const { options } = await post<{ options: Json }>("/api/auth/login/2fa/passkey/begin/");
  const credential = await get(options);
  await post("/api/auth/login/2fa/passkey/finish/", { credential });
}

/** Додати ключ доступу. platform — саме вбудований спосіб цього пристрою (Face ID / Touch ID). */
export async function registerPasskey(opts: { password?: string; platform?: boolean; name?: string } = {}): Promise<void> {
  const { options } = await post<{ options: Json }>("/api/account/security/passkeys/register/begin/", {
    password: opts.password || undefined,
    platform: !!opts.platform,
  });
  const credential = await create(options);
  await post("/api/account/security/passkeys/register/finish/", { credential, name: opts.name || "" });
}
