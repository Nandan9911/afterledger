// Checks every message catalogue against en.json: the same keys (none missing, none extra), every message
// parses as ICU, arguments are a subset of the English ones and keep their kind (plural, select), every
// plural and select has an "other" branch, and no argument that carries a value is dropped. A language
// without grammatical number may leave out a plural the English needs; that is reported as a note.
//
// Usage: node scripts/check-messages.mjs [locale ...]   (default: every catalogue in messages/)
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse, TYPE } from '@formatjs/icu-messageformat-parser';

const dir = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'messages');
const en = JSON.parse(fs.readFileSync(path.join(dir, 'en.json'), 'utf8'));
const flat = (o, p = '') => Object.entries(o).flatMap(([k, v]) => (typeof v === 'object' ? flat(v, p + k + '.') : [[p + k, v]]));
const args = (ast, out = new Map()) => {
  for (const el of ast) {
    if (el.type === TYPE.argument || el.type === TYPE.number || el.type === TYPE.date || el.type === TYPE.time)
      out.set(el.value, out.get(el.value) ?? 'simple');
    if (el.type === TYPE.plural || el.type === TYPE.select) {
      out.set(el.value, el.type === TYPE.plural ? 'plural' : 'select');
      if (!el.options.other) throw new Error(`no "other" branch for ${el.value}`);
      for (const o of Object.values(el.options)) args(o.value, out);
    }
    if (el.type === TYPE.tag) args(el.children, out);
  }
  return out;
};
const enFlat = new Map(flat(en));
const enArgs = new Map([...enFlat].map(([k, v]) => [k, args(parse(v))]));
let bad = 0;
const locales = process.argv.slice(2).length
  ? process.argv.slice(2)
  : fs
      .readdirSync(dir)
      .filter((f) => f.endsWith('.json'))
      .map((f) => f.replace(/\.json$/, ''));
for (const loc of locales) {
  const file = path.join(dir, `${loc}.json`);
  const cat = JSON.parse(fs.readFileSync(file, 'utf8'));
  const f = new Map(flat(cat));
  const missing = [...enFlat.keys()].filter((k) => !f.has(k));
  const extra = [...f.keys()].filter((k) => !enFlat.has(k));
  const problems = [];
  const notes = [];
  for (const [k, v] of f) {
    if (!enFlat.has(k)) continue;
    if (typeof v !== 'string') {
      problems.push(`${k}: not a string`);
      continue;
    }
    if (enFlat.get(k) !== '' && v.trim() === '') problems.push(`${k}: empty`);
    let a;
    try {
      a = args(parse(v));
    } catch (e) {
      problems.push(`${k}: ${e.message}`);
      continue;
    }
    const ea = enArgs.get(k);
    for (const [name, kind] of a) {
      if (!ea.has(name)) problems.push(`${k}: unknown argument {${name}}`);
      else if (kind !== 'simple' && ea.get(name) !== 'simple' && kind !== ea.get(name))
        problems.push(`${k}: {${name}} is ${kind}, English has ${ea.get(name)}`);
    }
    for (const [name, kind] of ea) {
      if (!a.has(name) && kind === 'simple') problems.push(`${k}: drops {${name}}`);
      else if (!a.has(name) && kind === 'plural') notes.push(`${k}: no {${name}} (plural only in English)`);
    }
  }
  console.log(`${loc}: ${f.size} messages, ${missing.length} missing, ${extra.length} extra, ${problems.length} problems`);
  for (const m of missing) console.log('  missing', m);
  for (const m of extra) console.log('  extra', m);
  for (const p of problems) console.log('  !!', p);
  for (const p of notes) console.log('  note', p);
  bad += extra.length + problems.length + missing.length;
}
process.exit(bad ? 1 : 0);
