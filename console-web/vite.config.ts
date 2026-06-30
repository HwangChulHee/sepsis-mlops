import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 빌드/개발 서버 설정. 테스트는 vitest.config.ts 사용.
// 개발 중 /console/* 는 로컬 console-api(FastAPI)로 프록시 — 운영은 Ingress 동일출처(구현 3).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/console": {
        target: process.env.VITE_DEV_API ?? "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
  },
});
