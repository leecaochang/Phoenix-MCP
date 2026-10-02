import parser from "@typescript-eslint/parser";
import typescript from "@typescript-eslint/eslint-plugin";
import hooks from "eslint-plugin-react-hooks";

export default [{
  files: ["frontend_src/**/*.{ts,tsx}"],
  ignores: ["frontend_src/__tests__/**"],
  linterOptions: { reportUnusedDisableDirectives: false },
  languageOptions: { parser, parserOptions: { ecmaVersion: "latest", sourceType: "module", ecmaFeatures: { jsx: true } } },
  plugins: { "react-hooks": hooks, "@typescript-eslint": typescript },
  rules: { "react-hooks/rules-of-hooks": "error", "react-hooks/exhaustive-deps": "error" },
}];
