import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

// 开发时把 /api 代理到本机 backend（VITE_BACKEND_URL 可改）；生产由 nginx 反向代理（deploy/frontend/nginx.conf）。
const backend = process.env.VITE_BACKEND_URL ?? 'http://127.0.0.1:8081'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: { '/api': { target: backend, changeOrigin: true } },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts'],
  },
})
