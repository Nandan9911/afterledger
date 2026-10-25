import { describe, expect, it } from 'vitest';
import {
  asDate,
  caseTitle,
  cls,
  dayMonth,
  daysBetween,
  eraDate,
  inLanguage,
  institutionLabel,
  listOf,
  localPart,
  localText,
  longDate,
  personFirst,
  personName,
  sentence,
  shortName,
  weekdayDate,
} from './format';

describe('dates and times are shown as written', () => {
  it('reads a date at midnight UTC', () => {
    expect(asDate('2026-10-14')?.toISOString()).toBe('2026-10-14T00:00:00.000Z');
  });

  it("keeps a family's 09:00 at 09:00, whatever its offset and the browser's zone", () => {
    for (const iso of [
      '2026-10-13T09:00:00+05:30',
      '2026-10-13T09:00:00+09:00',
      '2026-10-13T09:00:00-05:00',
      '2026-10-13T09:00:00Z',
      '2026-10-13T09:00:00',
    ]) {
      expect(asDate(iso)?.toISOString()).toBe('2026-10-13T09:00:00.000Z');
    }
    // 08:30 in Osaka is still the story day, not the evening before in UTC
    expect(dayMonth('2026-10-12T08:30:00+09:00', 'en')).toBe('12 Oct');
  });

  it('refuses what is not a date', () => {
    expect(asDate('')).toBeNull();
    expect(asDate(null)).toBeNull();
    expect(asDate('not a date')).toBeNull();
  });

  it('counts whole days', () => {
    expect(daysBetween('2026-10-01', '2026-10-14')).toBe(13);
    expect(daysBetween('2026-10-14', '2026-10-01')).toBe(-13);
    expect(daysBetween(null, '2026-10-01')).toBe(0);
  });

  it("formats in each page language's own way", () => {
    expect(longDate('2026-09-02', 'en')).toBe('2 September 2026');
    expect(longDate('2026-09-02', 'ja')).toBe('2026年9月2日');
    expect(longDate('2026-09-02', 'es')).toBe('2 de septiembre de 2026');
    expect(weekdayDate('2026-10-14', 'en')).toBe('Wednesday 14 October');
    expect(eraDate('2026-09-07')).toBe('令和8年9月7日（2026年9月7日）');
  });
});

describe('names are never translated', () => {
  const sato = { name: '佐藤 大輔', name_en: 'Daisuke Sato' };

  it('shows the own-script name only on a page in that script', () => {
    expect(personName(sato, 'ja')).toBe('佐藤 大輔');
    expect(personName(sato, 'en')).toBe('Daisuke');
    expect(personFirst(sato, 'en')).toBe('Daisuke');
    expect(personName({ display_name: 'Rajesh Verma' }, 'hi')).toBe('Rajesh');
  });

  it('keeps names whose second word belongs to them', () => {
    expect(shortName('Kamla Devi')).toBe('Kamla Devi');
    expect(shortName('Saraswathy Amma')).toBe('Saraswathy Amma');
    expect(shortName('Amit Verma')).toBe('Amit');
    expect(shortName('R. P. Shukla')).toBe('R. P. Shukla');
    expect(shortName('R P Shukla')).toBe('R P Shukla');
  });

  it('names a case only once the person who died is known', () => {
    expect(caseTitle('New case')).toBe('');
    expect(caseTitle(undefined)).toBe('');
    expect(caseTitle('Rajesh Kumar Verma')).toBe('Rajesh Kumar Verma');
  });
});

describe('words in the reader’s language', () => {
  it('prefers the local text only for readers of that language', () => {
    expect(localText('Sign the papers', 'कागज़ों पर हस्ताक्षर करें', 'hi', 'hi')).toBe('कागज़ों पर हस्ताक्षर करें');
    expect(localText('Sign the papers', 'कागज़ों पर हस्ताक्षर करें', 'hi', 'en')).toBe('Sign the papers');
    expect(localText('Sign the papers', 'कागज़ों पर हस्ताक्षर करें', 'hi-IN', 'hi')).toBe('कागज़ों पर हस्ताक्षर करें');
  });

  it("recognises a page language's script", () => {
    expect(inLanguage('浪速銀行', 'ja')).toBe(true);
    expect(inLanguage('Naniwa Bank', 'ja')).toBe(false);
    expect(inLanguage('സുപ്രഭാതം', 'ml')).toBe(true);
    expect(inLanguage('anything', 'es')).toBe(false);
  });

  it('takes the local part of a bilingual title', () => {
    expect(localPart('Inheritance procedure at Naniwa Bank (浪速銀行 相続手続)', 'ja')).toBe('浪速銀行 相続手続');
    expect(localPart('Inheritance procedure at Naniwa Bank (浪速銀行 相続手続)', 'en')).toBeNull();
    expect(institutionLabel({ name: '浪速銀行', short_name: 'Naniwa Bank' }, 'ja')).toBe('浪速銀行');
    expect(institutionLabel({ name: '浪速銀行', short_name: 'Naniwa Bank' }, 'en')).toBe('Naniwa Bank');
  });

  it('joins lists and closes sentences in their own script', () => {
    expect(listOf(['A', 'B', 'C'], 'en')).toBe('A, B and C');
    expect(sentence('相続するか放棄するかを決めます')).toBe('相続するか放棄するかを決めます。');
    expect(sentence('लॉकर की चाबी ढूँढें')).toBe('लॉकर की चाबी ढूँढें।');
    expect(sentence('Look for the key')).toBe('Look for the key.');
    expect(sentence('Is it there?')).toBe('Is it there?');
  });

  it('builds class names', () => {
    expect(cls('a', false, null, undefined, 'b')).toBe('a b');
  });
});
