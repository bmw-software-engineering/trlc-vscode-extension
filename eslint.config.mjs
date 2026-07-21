// @ts-check

import eslint from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(eslint.configs.recommended, {
    files: ["src/client/src/**/*.ts"],
    extends: [...tseslint.configs.recommended],
    languageOptions: {
        parserOptions: {
            project: true,
        },
    },
});
