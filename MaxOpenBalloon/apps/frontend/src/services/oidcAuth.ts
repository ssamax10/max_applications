const OIDC_STATE_KEY = "mob_oidc_state";
const OIDC_VERIFIER_KEY = "mob_oidc_verifier";
const OIDC_SESSION_KEY = "mob_oidc_session";

type OidcMetadata = {
  authorization_endpoint: string;
  token_endpoint: string;
};

export type OidcSession = {
  accessToken: string;
  refreshToken: string | null;
  expiresAt: number | null;
  roles: string[];
  tenantIdFromToken: string | null;
};

type OidcTokenResponse = {
  access_token?: string;
  id_token?: string;
  refresh_token?: string;
  expires_in?: number;
  error?: string;
  error_description?: string;
};

function envValue(
  key:
    | "issuer"
    | "clientId"
    | "redirectUri"
    | "scope"
    | "audience"
    | "tenantClaim"
    | "authorizationEndpoint"
    | "tokenEndpoint",
): string {
  const env = import.meta.env;

  if (key === "issuer") {
    return String(env.VITE_OIDC_ISSUER ?? "").trim();
  }

  if (key === "clientId") {
    return String(env.VITE_OIDC_CLIENT_ID ?? "maxopenballoon-frontend").trim();
  }

  if (key === "redirectUri") {
    return String(env.VITE_OIDC_REDIRECT_URI ?? window.location.origin).trim();
  }

  if (key === "scope") {
    return String(env.VITE_OIDC_SCOPE ?? "openid profile email").trim();
  }

  if (key === "audience") {
    return String(env.VITE_OIDC_AUDIENCE ?? "").trim();
  }

  if (key === "authorizationEndpoint") {
    return String(env.VITE_OIDC_AUTHORIZATION_ENDPOINT ?? "").trim();
  }

  if (key === "tokenEndpoint") {
    return String(env.VITE_OIDC_TOKEN_ENDPOINT ?? "").trim();
  }

  return String(env.VITE_TENANT_CLAIM ?? "tenant_id").trim();
}

function ensureIssuerBase(issuer: string): string {
  return issuer.endsWith("/") ? issuer : `${issuer}/`;
}

function toBase64Url(bytes: Uint8Array): string {
  let binary = "";
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });

  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function randomBase64Url(size: number): string {
  const bytes = new Uint8Array(size);
  crypto.getRandomValues(bytes);
  return toBase64Url(bytes);
}

async function createCodeChallenge(codeVerifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(codeVerifier));
  return toBase64Url(new Uint8Array(digest));
}

async function discoverOidcMetadata(): Promise<OidcMetadata> {
  const configuredAuthorizationEndpoint = envValue("authorizationEndpoint");
  const configuredTokenEndpoint = envValue("tokenEndpoint");

  if (configuredAuthorizationEndpoint && configuredTokenEndpoint) {
    return {
      authorization_endpoint: configuredAuthorizationEndpoint,
      token_endpoint: configuredTokenEndpoint,
    };
  }

  const issuer = envValue("issuer");
  if (!issuer) {
    // Local development mode - auth disabled
    console.warn("OIDC issuer not configured. Running in local development mode without authentication.");
    return {
      authorization_endpoint: "http://localhost:19000/application/o/maxopenballoon/authorize",
      token_endpoint: "http://localhost:19000/application/o/maxopenballoon/token",
    };
  }

  const discoveryUrl = `${ensureIssuerBase(issuer)}.well-known/openid-configuration`;
  try {
    const response = await fetch(discoveryUrl, {
      method: "GET",
      mode: "cors",
    });
    if (!response.ok) {
      throw new Error(
        `OIDC discovery failed at ${discoveryUrl}: ${response.status} ${response.statusText}. `
        + "If your Authentik issuer URL differs, configure explicit VITE_OIDC_AUTHORIZATION_ENDPOINT and VITE_OIDC_TOKEN_ENDPOINT.",
      );
    }

    const metadata = (await response.json()) as Partial<OidcMetadata>;
    if (!metadata.authorization_endpoint || !metadata.token_endpoint) {
      throw new Error("OIDC discovery response missing authorization/token endpoints.");
    }

    return {
      authorization_endpoint: metadata.authorization_endpoint,
      token_endpoint: metadata.token_endpoint,
    };
  } catch (error) {
    console.error("OIDC discovery failed:", error);
    throw new Error(
      `OIDC discovery failed. If running locally without auth, leave VITE_OIDC_ISSUER empty. Error: ${error}`,
    );
  }
}

function parseJwtPayload(token: string): Record<string, unknown> {
  const parts = token.split(".");
  if (parts.length < 2) {
    return {};
  }

  try {
    const normalized = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const padding = "=".repeat((4 - (normalized.length % 4)) % 4);
    const decoded = atob(normalized + padding);
    return JSON.parse(decoded) as Record<string, unknown>;
  } catch {
    return {};
  }
}

function parseTenantFromToken(token: string): string | null {
  const payload = parseJwtPayload(token);
  const claim = envValue("tenantClaim");
  const value = payload[claim];
  return typeof value === "string" && value ? value : null;
}

function parseJwtRoles(token: string): string[] {
  const parts = token.split(".");
  if (parts.length < 2) {
    return [];
  }

  try {
    const payload = JSON.parse(atob(parts[1].replace(/-/g, "+").replace(/_/g, "/"))) as {
      roles?: unknown;
      realm_access?: { roles?: unknown };
      resource_access?: Record<string, { roles?: unknown }>;
    };

    const collected = new Set<string>();

    if (Array.isArray(payload.roles)) {
      payload.roles.forEach((role) => {
        if (typeof role === "string") {
          collected.add(role);
        }
      });
    }

    const realmRoles = payload.realm_access?.roles;
    if (Array.isArray(realmRoles)) {
      realmRoles.forEach((role) => {
        if (typeof role === "string") {
          collected.add(role);
        }
      });
    }

    if (payload.resource_access && typeof payload.resource_access === "object") {
      Object.values(payload.resource_access).forEach((entry) => {
        if (Array.isArray(entry.roles)) {
          entry.roles.forEach((role) => {
            if (typeof role === "string") {
              collected.add(role);
            }
          });
        }
      });
    }

    return [...collected];
  } catch {
    return [];
  }
}

function parseTokenExpiry(token: string): number | null {
  const payload = parseJwtPayload(token);
  const exp = payload.exp;
  if (typeof exp !== "number" || !Number.isFinite(exp)) {
    return null;
  }

  return exp * 1000;
}

function chooseBearerToken(payload: OidcTokenResponse): string {
  const accessToken = typeof payload.access_token === "string" ? payload.access_token : "";
  const idToken = typeof payload.id_token === "string" ? payload.id_token : "";

  if (accessToken.split(".").length >= 3) {
    return accessToken;
  }

  if (idToken.split(".").length >= 3) {
    return idToken;
  }

  if (accessToken) {
    return accessToken;
  }

  throw new Error("OIDC token exchange did not return a usable bearer token.");
}

function toOidcSession(payload: OidcTokenResponse, previousRefreshToken?: string | null): OidcSession {
  const accessToken = chooseBearerToken(payload);
  const refreshToken = typeof payload.refresh_token === "string"
    ? payload.refresh_token
    : (previousRefreshToken ?? null);

  const expiryFromJwt = parseTokenExpiry(accessToken);
  const expiryFromResponse = typeof payload.expires_in === "number" && Number.isFinite(payload.expires_in)
    ? Date.now() + (payload.expires_in * 1000)
    : null;

  return {
    accessToken,
    refreshToken,
    expiresAt: expiryFromJwt ?? expiryFromResponse,
    roles: parseJwtRoles(accessToken),
    tenantIdFromToken: parseTenantFromToken(accessToken),
  };
}

function saveOidcSession(session: OidcSession): void {
  localStorage.setItem(OIDC_SESSION_KEY, JSON.stringify(session));
}

export function getStoredOidcSession(): OidcSession | null {
  const raw = localStorage.getItem(OIDC_SESSION_KEY);
  if (!raw) {
    return null;
  }

  try {
    const parsed = JSON.parse(raw) as Partial<OidcSession>;
    if (!parsed || typeof parsed.accessToken !== "string" || !parsed.accessToken) {
      return null;
    }

    return {
      accessToken: parsed.accessToken,
      refreshToken: typeof parsed.refreshToken === "string" ? parsed.refreshToken : null,
      expiresAt: typeof parsed.expiresAt === "number" && Number.isFinite(parsed.expiresAt) ? parsed.expiresAt : null,
      roles: Array.isArray(parsed.roles) ? parsed.roles.filter((role): role is string => typeof role === "string") : [],
      tenantIdFromToken: typeof parsed.tenantIdFromToken === "string" ? parsed.tenantIdFromToken : null,
    };
  } catch {
    return null;
  }
}

export function clearStoredOidcSession(): void {
  localStorage.removeItem(OIDC_SESSION_KEY);
}

async function exchangeRefreshToken(metadata: OidcMetadata, refreshToken: string): Promise<OidcSession> {
  const tokenBody = new URLSearchParams({
    grant_type: "refresh_token",
    refresh_token: refreshToken,
    client_id: envValue("clientId"),
  });

  const audience = envValue("audience");
  if (audience) {
    tokenBody.set("audience", audience);
  }

  const response = await fetch(metadata.token_endpoint, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: tokenBody.toString(),
  });

  const payload = await response.json() as OidcTokenResponse;
  if (!response.ok) {
    const message = payload.error_description ?? payload.error ?? "OIDC refresh token exchange failed.";
    throw new Error(message);
  }

  return toOidcSession(payload, refreshToken);
}

export async function refreshOidcSession(force = false): Promise<OidcSession | null> {
  const current = getStoredOidcSession();
  if (!current) {
    return null;
  }

  const expirationSafetyMs = 60_000;
  if (!force && current.expiresAt && current.expiresAt > (Date.now() + expirationSafetyMs)) {
    return current;
  }

  if (!current.refreshToken) {
    return current;
  }

  const metadata = await discoverOidcMetadata();
  const refreshed = await exchangeRefreshToken(metadata, current.refreshToken);
  saveOidcSession(refreshed);
  return refreshed;
}

export async function startOidcLogin(tenantIdHint: string): Promise<void> {
  const metadata = await discoverOidcMetadata();
  const clientId = envValue("clientId");
  const redirectUri = envValue("redirectUri");
  const scope = envValue("scope");
  const audience = envValue("audience");

  const state = randomBase64Url(24);
  const codeVerifier = randomBase64Url(48);
  const codeChallenge = await createCodeChallenge(codeVerifier);

  sessionStorage.setItem(OIDC_STATE_KEY, state);
  sessionStorage.setItem(OIDC_VERIFIER_KEY, codeVerifier);

  const params = new URLSearchParams({
    client_id: clientId,
    response_type: "code",
    redirect_uri: redirectUri,
    scope,
    state,
    code_challenge: codeChallenge,
    code_challenge_method: "S256",
  });

  const tenantHint = tenantIdHint.trim();
  if (tenantHint) {
    params.set("tenant", tenantHint);
  }

  if (audience) {
    params.set("audience", audience);
  }

  window.location.assign(`${metadata.authorization_endpoint}?${params.toString()}`);
}

export async function completeOidcLoginFromUrl(): Promise<OidcSession | null> {
  const params = new URLSearchParams(window.location.search);
  const error = params.get("error");
  if (error) {
    const description = params.get("error_description") ?? "OIDC login failed.";
    throw new Error(`${error}: ${description}`);
  }

  const code = params.get("code");
  const state = params.get("state");
  if (!code) {
    return null;
  }

  const expectedState = sessionStorage.getItem(OIDC_STATE_KEY);
  const codeVerifier = sessionStorage.getItem(OIDC_VERIFIER_KEY);
  if (!expectedState || !codeVerifier || !state || state !== expectedState) {
    throw new Error("Invalid OIDC callback state.");
  }

  const metadata = await discoverOidcMetadata();
  const clientId = envValue("clientId");
  const redirectUri = envValue("redirectUri");

  const tokenBody = new URLSearchParams({
    grant_type: "authorization_code",
    code,
    client_id: clientId,
    redirect_uri: redirectUri,
    code_verifier: codeVerifier,
  });

  const response = await fetch(metadata.token_endpoint, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: tokenBody.toString(),
  });

  const payload = await response.json() as OidcTokenResponse;
  if (!response.ok) {
    const message = payload.error_description ?? payload.error ?? "Token exchange failed.";
    throw new Error(message);
  }

  const session = toOidcSession(payload);

  sessionStorage.removeItem(OIDC_STATE_KEY);
  sessionStorage.removeItem(OIDC_VERIFIER_KEY);

  const cleanUrl = `${window.location.origin}${window.location.pathname}`;
  window.history.replaceState({}, document.title, cleanUrl);

  saveOidcSession(session);

  return session;
}
