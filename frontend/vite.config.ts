import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

const DEFAULT_DEV_API_PROXY_TARGET = "http://127.0.0.1:8080";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "VITE_");
  const devApiProxyTarget =
    env.VITE_DEV_API_PROXY_TARGET?.trim() || DEFAULT_DEV_API_PROXY_TARGET;
  const backendProxy = {
    target: devApiProxyTarget,
    changeOrigin: true
  };

  return {
    plugins: [react()],
    server: {
      host: "127.0.0.1",
      port: 5173,
      proxy: {
        "/api": backendProxy,
        "/health": backendProxy,
        "/version": backendProxy,
        "/preflight": backendProxy
      }
    }
  };
});
