'use client';
/** A country box that accepts a name in the reader's language (or a code) and keeps the ISO code. */
import { useMemo, useState } from 'react';

// Every ISO 3166-1 alpha-2 code in use; names come from the browser in the reader's language.
const CODES =
  'AD AE AF AG AL AM AO AR AT AU AZ BA BB BD BE BF BG BH BI BJ BN BO BR BS BT BW BY BZ CA CD CF CG CH CI CL CM CN CO CR CU CV CY CZ DE DJ DK DM DO DZ EC EE EG ER ES ET FI FJ FM FR GA GB GD GE GH GM GN GQ GR GT GW GY HK HN HR HT HU ID IE IL IN IQ IR IS IT JM JO JP KE KG KH KI KM KN KP KR KW KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MG MH MK ML MM MN MO MR MT MU MV MW MX MY MZ NA NE NG NI NL NO NP NR NZ OM PA PE PG PH PK PL PS PT PW PY QA RO RS RU RW SA SB SC SD SE SG SI SK SL SM SN SO SR SS ST SV SY SZ TD TG TH TJ TL TM TN TO TR TT TV TW TZ UA UG US UY UZ VA VC VE VN VU WS YE ZA ZM ZW'.split(
    ' ',
  );

export function countryName(code: string, locale: string): string {
  try {
    return new Intl.DisplayNames([locale], { type: 'region' }).of(code) ?? code;
  } catch {
    return code;
  }
}

export function CountryInput({
  value,
  onChange,
  locale,
  className,
}: {
  value: string;
  onChange: (code: string) => void;
  locale: string;
  className?: string;
}) {
  const names = useMemo(
    () => CODES.map((c) => ({ code: c, name: countryName(c, locale) })).sort((a, b) => a.name.localeCompare(b.name, locale)),
    [locale],
  );
  const [text, setText] = useState(() => countryName(value, locale));
  const listId = 'al-countries';
  return (
    <>
      <input
        value={text}
        list={listId}
        autoComplete="off"
        onChange={(e) => {
          const v = e.target.value;
          setText(v);
          const hit =
            names.find((n) => n.name.toLowerCase() === v.trim().toLowerCase()) ?? names.find((n) => n.code === v.trim().toUpperCase());
          if (hit) onChange(hit.code);
        }}
        className={className ?? 'min-h-[52px] rounded-[10px] border-0 bg-white px-3.5 text-[18px] text-ink'}
      />
      <datalist id={listId}>
        {names.map((n) => (
          <option key={n.code} value={n.name} />
        ))}
      </datalist>
    </>
  );
}
