import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "VITE_");

  return {
    plugins: [react()],
    server: {
      host: "0.0.0.0",
      port: 5173,
      proxy: {
        "/api/v1": {
          target: env.VITE_BACKEND_ORIGIN || "http://localhost:8000",
          changeOrigin: true,
        },
        "/docs": {
          target: env.VITE_BACKEND_ORIGIN || "http://localhost:8000",
          changeOrigin: true,
        },
        "/openapi.json": {
          target: env.VITE_BACKEND_ORIGIN || "http://localhost:8000",
          changeOrigin: true,
        },
      },
    },
  };
});

