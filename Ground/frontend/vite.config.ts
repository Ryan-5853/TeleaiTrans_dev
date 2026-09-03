import vue from "@vitejs/plugin-vue";
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url))
    }
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_PROXY_TARGET || "http://127.0.0.1:8000",
        changeOrigin: true
      },
      "/ui/v1/ws": {
        target: process.env.VITE_WS_PROXY_TARGET || "ws://127.0.0.1:8000",
        ws: true
      },
      "/plane/v1/ws": {
        target: process.env.VITE_WS_PROXY_TARGET || "ws://127.0.0.1:8000",
        ws: true
      },
      "/plane/v2/ws": {
        target: process.env.VITE_WS_PROXY_TARGET || "ws://127.0.0.1:8000",
        ws: true
      }
    }
  }
});
