import js from "@eslint/js";
import globals from "globals";

export default [
  {
    ignores: ["node_modules/", "test-results/", "playwright-report/", ".venv/"],
  },
  js.configs.recommended,
  {
    rules: {
      "no-empty": ["error", { allowEmptyCatch: true }],
      "no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", caughtErrorsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
  {
    // Browser modules served from /brand/ to the compressor and delivery pages.
    files: ["professor_compressor/static/**/*.js"],
    languageOptions: {
      ecmaVersion: 2024,
      sourceType: "module",
      globals: globals.browser,
    },
  },
  {
    // The operator dashboard is a classic deferred script, not a module. Its
    // top-level helpers may share names with newer browser globals (`when`).
    files: ["professor_compressor/static/dashboard.js"],
    languageOptions: { sourceType: "script" },
    rules: { "no-redeclare": ["error", { builtinGlobals: false }] },
  },
  {
    // Playwright specs and config run in Node; page.evaluate callbacks run in the browser.
    files: ["**/*.cjs"],
    languageOptions: {
      ecmaVersion: 2024,
      sourceType: "commonjs",
      globals: { ...globals.node, ...globals.browser },
    },
  },
];
