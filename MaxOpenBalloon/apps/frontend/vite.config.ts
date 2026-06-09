import { defineConfig } from "vite";

export default defineConfig({
  server: {
    port: 5173,
    allowedHosts: ["openballoon.maxautocables.com", ".maxautocables.com", "localhost", "127.0.0.1"],
  },
});
