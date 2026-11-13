import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiTarget = process.env.API_PROXY || "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/admin/api": apiTarget,
      "/api": apiTarget,
      "/health": apiTarget,
    },
  },
});
