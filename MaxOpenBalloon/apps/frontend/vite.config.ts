import { defineConfig } from "vite";

// OIDC proxy target for development only.
// In production (Helm/K8s), the Vite dev server is not used, so this proxy never runs.
// Override via OIDC_PROXY_TARGET env var if your Authentik instance is on a different host.
const oidcProxyTarget = process.env.OIDC_PROXY_TARGET || "https://apps.maxautocables.com";

export default defineConfig({
  server: {
    port: 5173,
    allowedHosts: ["openballoon.maxautocables.com", ".maxautocables.com", "localhost", "127.0.0.1"],
    proxy: {
      // Proxies /authentik/... to the Authentik server, bypassing CORS for token exchanges.
      // Only the token endpoint fetch is affected (authorization is a browser redirect, no CORS).
      "/authentik": {
        target: oidcProxyTarget,
        changeOrigin: true,
        secure: true,
        rewrite: (path) => path.replace(/^\/authentik/, ""),
      },
    },
  },
});
