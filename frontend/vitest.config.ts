import { defineConfig } from "vitest/config";

// Unit tests cover pure logic only; keep the app's Vite plugins out of the test run.
export default defineConfig({
  test: { include: ["src/**/*.test.ts"] },
});
