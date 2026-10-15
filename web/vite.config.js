import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Сборка — в серверную часть плагина: её отдаёт процесс плагина по /plugin-apps/excalidraw/…
// base "./" — адреса ресурсов относительно страницы /plugin-apps/excalidraw/editor.
export default defineConfig({
  base: "./",
  plugins: [react()],
  define: { "process.env.IS_PREACT": JSON.stringify("false") },
  build: {
    outDir: process.env.XD_OUT || "../backend/excalidraw_plugin/static",
    emptyOutDir: true,
    chunkSizeWarningLimit: 4000,
    sourcemap: false,
  },
});
