import type { KitLanguage } from 'overnightworks-design';
import { languages } from '#lib/words/index.ts';

// The root load imports this module beside the kit's words: a language the app
// offers and the kit lacks fails the type check here.
languages satisfies readonly KitLanguage[];
