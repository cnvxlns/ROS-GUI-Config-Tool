import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 빌드 산출물은 백엔드 패키지 안의 static/ 으로 떨어진다.
// Config App 이 이 디렉터리를 직접 서빙하므로 별도 웹 서버가 없다 (명세 §8).
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../src/av_config/av_config/static',
    emptyOutDir: true,
  },
  server: {
    // 개발 중에는 호스트의 vite dev server 가 컨테이너의 Config App 으로 프록시한다.
    // --net=host 라서 localhost 로 바로 닿는다.
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8080',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
