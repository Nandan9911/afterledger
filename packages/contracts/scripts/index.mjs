// Writes src/index.ts: one exported type per schema in openapi.json, so the web app imports names, not paths.
import { readFileSync, writeFileSync } from 'node:fs';

const doc = JSON.parse(readFileSync(new URL('../openapi.json', import.meta.url), 'utf8'));
const names = Object.keys(doc.components?.schemas ?? {})
  .filter((n) => !n.startsWith('Body_') && n !== 'HTTPValidationError' && n !== 'ValidationError')
  .sort();
const lines = [
  '/**',
  ' * Types for the AfterLedger API, generated from openapi.json (python -m afterledger.cli export-openapi,',
  ' * then `pnpm contracts`). Do not edit schema.d.ts or this file by hand.',
  ' */',
  "import type { components, paths } from './schema';",
  '',
  'export type { components, paths };',
  '',
  "type S = components['schemas'];",
  '',
  ...names.map((n) => `export type ${n} = S['${n}'];`),
  '',
];
writeFileSync(new URL('../src/index.ts', import.meta.url), lines.join('\n'));
console.log(`index.ts: ${names.length} types`);
