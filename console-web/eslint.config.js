// ESLint 9 flat config — Vite + React + TypeScript.
// 파이썬 쪽 ruff 게이트와 대칭: 프론트도 타입체크(tsc)에 더해 린트로 실버그류(미사용·
// hooks 규칙 위반·잘못된 refresh export)를 CI 에서 막는다. 스타일 과잉규칙은 넣지 않는다.
import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist', 'coverage', 'node_modules'] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': [
        'warn',
        { allowConstantExport: true },
      ],
      // 미사용 변수는 `_` 접두로 명시 무시 가능(테스트 헬퍼 등).
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
    },
  },
  {
    // 테스트는 jsdom/node 글로벌(vitest) 을 함께 쓴다.
    files: ['tests/**/*.{ts,tsx}'],
    languageOptions: { globals: { ...globals.browser, ...globals.node } },
  },
)
