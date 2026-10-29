import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  js.configs.recommended,
  tseslint.configs.recommended,
  pluginVue.configs['flat/recommended'],
  {
    files: ['**/*.vue'],
    languageOptions: {
      parserOptions: { parser: tseslint.parser },
    },
  },
  {
    files: ['custom-nav-controls.vue', 'components/**/*.vue'],
    languageOptions: {
      globals: Object.fromEntries(
        [
          'window', 'document', 'navigator', 'fetch', 'WebSocket', 'AudioContext',
          'AudioWorkletNode', 'AbortController', 'TextDecoder', 'atob', 'URL', 'Blob', 'Audio',
        ].map((name) => [name, 'readonly']),
      ),
    },
  },
  {
    files: ['worklets/**/*.js'],
    languageOptions: {
      globals: { AudioWorkletProcessor: 'readonly', registerProcessor: 'readonly' },
    },
  },
)
