import type { Config } from "jest";

// Jest + React Testing Library (tech-stack.md §3). SWC transform keeps it fast.
// Coverage threshold (NFR-025, 60 %) is enforced from B-32 on, when real code exists.
const swcOptions = { jsc: { transform: { react: { runtime: "automatic" } } } };

const config: Config = {
  testEnvironment: "jsdom",
  roots: ["<rootDir>/src"],
  setupFilesAfterEnv: ["<rootDir>/src/setupTests.ts"],
  transform: {
    "^.+\\.(t|j)sx?$": ["@swc/jest", swcOptions],
  },
  moduleNameMapper: {
    "\\.(css)$": "identity-obj-proxy",
    "^@/(.*)$": "<rootDir>/src/$1",
  },
  collectCoverageFrom: [
    "src/**/*.{ts,tsx}",
    "!src/**/*.stories.tsx",
    "!src/api/generated/**",
    "!src/main.tsx",
  ],
};

export default config;
