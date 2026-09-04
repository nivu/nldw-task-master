import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // Netlify writes generated edge-function code and vendored Deno stdlib
    // here during a deploy. It is build output, not source, and linting it
    // reports dozens of errors in code nobody in this repo wrote.
    ".netlify/**",
  ]),
]);

export default eslintConfig;
