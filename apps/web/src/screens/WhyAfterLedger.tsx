'use client';
/**
 * Why AfterLedger: what it is for, the problem it solves (with figures and their sources), why demand is growing,
 * what sets it apart, and why a team of AI agents is the right way to do it. Written in English; on a page in
 * another language it says so to the browser (lang="en").
 */
import { useLocale } from 'next-intl';

type Source = { id: number; label: string; url: string };

const SOURCES: Source[] = [
  {
    id: 1,
    label: 'Lok Sabha answer, 20 July 2026 (via Outlook Money)',
    url: 'https://www.outlookmoney.com/personal-finance/lic-unclaimed-funds-cross-rs-7300-crore-epfo-inoperative-accounts-at-rs-9330-crore',
  },
  {
    id: 2,
    label: 'Press Information Bureau, Government of India, 31 December 2025',
    url: 'https://www.pib.gov.in/PressReleasePage.aspx?PRID=2210323',
  },
  {
    id: 3,
    label: 'SEBI circular, 19 March 2025',
    url: 'https://www.sebi.gov.in/legal/circulars/mar-2025/harnessing-digilocker-as-a-digital-public-infrastructure-for-reducing-unclaimed-assets-in-the-indian-securities-market_92769.html',
  },
  { id: 4, label: 'NAUPA, What is unclaimed property', url: 'https://unclaimed.org/what-is-unclaimed-property/' },
  {
    id: 5,
    label: 'NAIC, Life Insurance Policy Locator, 3 September 2026',
    url: 'https://content.naic.org/article/naic-life-insurance-policy-locator-tool-helps-match-consumers-more-16-billion-lost-and-unclaimed',
  },
  { id: 6, label: 'ASIC Moneysmart, Find unclaimed money', url: 'https://moneysmart.gov.au/find-unclaimed-money' },
  { id: 7, label: 'Reclaim Fund (UK dormant assets)', url: 'https://www.reclaimfund.co.uk/' },
  {
    id: 8,
    label: 'Empathy, The Grief Tax 2025',
    url: 'https://www.empathy.com/blog/what-loss-really-costs-key-takeaways-from-the-grief-tax',
  },
  {
    id: 9,
    label: 'LocalCircles survey, 2026 (via Moneylife)',
    url: 'https://www.moneylife.in/article/73-percentage-families-paying-bribes-at-property-registration-land-offices-to-transfer-deceased-relatives-assets-survey/81571.html',
  },
  {
    id: 10,
    label: 'UN World Population Prospects (via Our World in Data)',
    url: 'https://ourworldindata.org/grapher/number-of-deaths-per-year',
  },
  {
    id: 11,
    label: 'Ministry of Health, Labour and Welfare, Japan, vital statistics 2024',
    url: 'https://www.mhlw.go.jp/toukei/saikin/hw/jinkou/kakutei24/dl/02_kek.pdf',
  },
  {
    id: 12,
    label: 'Cerulli Associates, 5 December 2024',
    url: 'https://www.cerulli.com/press-releases/cerulli-anticipates-124-trillion-in-wealth-will-transfer-through-2048',
  },
  {
    id: 13,
    label: 'Julius Baer and EY, August 2026',
    url: 'https://www.ey.com/en_in/newsroom/2026/08/indian-family-office-assets-projected-to-grow-1-point-5x-over-the-next-three-years-julius-baer-ey-report',
  },
  {
    id: 14,
    label: 'Reserve Bank of India, settlement of claims of deceased customers',
    url: 'https://rbidocs.rbi.org.in/rdocs/notification/PDFs/170MD.PDF',
  },
  {
    id: 15,
    label: 'Tow Center for Digital Journalism (via Nieman Lab), March 2025',
    url: 'https://www.niemanlab.org/2025/03/ai-search-engines-fail-to-produce-accurate-citations-in-over-60-of-tests-according-to-new-tow-center-study/',
  },
  { id: 16, label: 'Magesh et al., Stanford, 2024: hallucination in legal research tools', url: 'https://arxiv.org/abs/2405.20362' },
  { id: 17, label: 'Huang et al., ICLR 2024: LLMs cannot self-correct reasoning yet', url: 'https://arxiv.org/abs/2310.01798' },
  {
    id: 18,
    label: 'Anthropic, How we built our multi-agent research system, June 2025',
    url: 'https://www.anthropic.com/engineering/multi-agent-research-system',
  },
];

function Cite({ n }: { n: number[] }) {
  return (
    <sup className="ms-0.5 text-[12px] font-bold text-ledger">
      {n.map((id, i) => (
        <span key={id}>
          {i ? ',' : ''}
          <a href={`#src-${id}`} className="no-underline hover:underline">
            {id}
          </a>
        </span>
      ))}
    </sup>
  );
}

const h3 = 'm-0 font-serif text-[23px] font-semibold leading-snug';
const card = 'rounded-[14px] border border-rule bg-sheet px-5 py-5';

export function WhyAfterLedger() {
  const locale = useLocale();
  return (
    <div lang={locale === 'en' ? undefined : 'en'} dir="ltr" className="flex flex-col gap-10">
      <div className="max-w-[52em]">
        <p className="m-0 text-[19px] leading-[1.6]">
          When someone dies, the family inherits a paperwork problem. There are bank accounts, insurance policies, pensions, investments and
          debts that nobody listed, each held by an institution with its own forms, documents and deadlines, often in another language or
          another country. Money is lost not because it is not owed, but because nobody knows it exists, or how to claim it.{' '}
          <strong>AfterLedger</strong> is the family&apos;s own guide: it reads the papers they already have, finds what nobody listed, and
          prepares each claim the way that institution&apos;s regulator says it must be handled, with the source behind every step.
        </p>
      </div>

      <div className="grid gap-5 [grid-template-columns:repeat(auto-fit,minmax(min(330px,100%),1fr))]">
        <section className={card} aria-labelledby="why-problem">
          <h3 id="why-problem" className={h3}>
            The problem, in numbers
          </h3>
          <ul className="m-0 mt-3 flex list-disc flex-col gap-2 ps-5 text-[16px]">
            <li>
              In India, LIC alone held ₹7,318.50 crore of unclaimed policy money, and inoperative provident fund accounts ₹9,330.56 crore,
              on 31 March 2026.
              <Cite n={[1]} /> A national campaign returned about ₹4,200 crore to families in three months.
              <Cite n={[2]} />
            </li>
            <li>
              India&apos;s securities regulator says payouts to deceased investors go unclaimed because families do not know the investment
              exists.
              <Cite n={[3]} />
            </li>
            <li>
              In the United States about 1 in 7 people has unclaimed property held by a state,
              <Cite n={[4]} /> and the insurance regulators&apos; policy locator has matched US$17 billion of lost life insurance to
              families since 2016.
              <Cite n={[5]} />
            </li>
            <li>
              Australia holds A$2.7 billion in lost bank accounts, shares and life insurance;
              <Cite n={[6]} /> the UK&apos;s dormant-assets scheme has already released £1.6 billion.
              <Cite n={[7]} />
            </li>
            <li>
              Executors in the US take 20 months on average and two in three could not reach essential documents;
              <Cite n={[8]} /> in India only 32% of families found it easy to transfer a relative&apos;s assets.
              <Cite n={[9]} />
            </li>
          </ul>
        </section>

        <section className={card} aria-labelledby="why-demand">
          <h3 id="why-demand" className={h3}>
            Why demand keeps growing
          </h3>
          <ul className="m-0 mt-3 flex list-disc flex-col gap-2 ps-5 text-[16px]">
            <li>
              About 62 million people die every year, 9.5 million of them in India.
              <Cite n={[10]} /> Japan recorded 1.6 million deaths in 2024, its highest ever.
              <Cite n={[11]} />
            </li>
            <li>
              US$124 trillion will pass between generations in the US by 2048,
              <Cite n={[12]} /> and US$1.3 to 1.5 trillion in India over the coming decade.
              <Cite n={[13]} />
            </li>
            <li>
              Families now hold more accounts, apps and policies than any paper file shows, and more of them live in two countries at once.
            </li>
            <li>
              Regulators set clocks that work in the family&apos;s favour: an Indian bank must settle a deceased customer&apos;s claim
              within 15 days of complete papers, or pay compensation for every day of delay.
              <Cite n={[14]} /> Only a family that knows the rule can hold the bank to it.
            </li>
          </ul>
        </section>

        <section className={card} aria-labelledby="why-different">
          <h3 id="why-different" className={h3}>
            What makes it different
          </h3>
          <ul className="m-0 mt-3 flex list-disc flex-col gap-2 ps-5 text-[16px]">
            <li>
              Today&apos;s tools search databases, notify organisations, or are concierge services paid a share of the estate. None reads
              the family&apos;s own papers to find what nobody listed.
            </li>
            <li>
              AfterLedger infers from the lines of a statement: a ₹436 debit every May is the premium of a government life cover worth
              ₹2,00,000.
            </li>
            <li>
              It prepares each institution&apos;s own forms, covering letter, document list and deadline from the regulator&apos;s current
              rules, and drafts the objection when an institution asks for more than its rules allow.
            </li>
            <li>
              It works in the family&apos;s languages, by voice or text, and across borders, and it researches any country&apos;s rules
              live, quoting the official page.
            </li>
          </ul>
        </section>

        <section className={card} aria-labelledby="why-agents">
          <h3 id="why-agents" className={h3}>
            Why a team of AI agents
          </h3>
          <ul className="m-0 mt-3 flex list-disc flex-col gap-2 ps-5 text-[16px]">
            <li>
              One model on its own is not safe with money and law: AI search engines cited sources wrongly in over 60% of tests,
              <Cite n={[15]} /> legal research tools hallucinate 17 to 33% of the time,
              <Cite n={[16]} /> and models struggle to correct themselves without outside checks.
              <Cite n={[17]} />
            </li>
            <li>
              So the work is split between specialist agents on Google Gemini that check each other: two readers read every value by
              different methods; an investigator proposes what a payment means while a critic argues against it; two researchers verify each
              rule separately and a checker drops any quote not found word for word on its official page.
            </li>
            <li>
              Code, not a model, computes deadlines and compensation and checks that no letter contains a number the case does not. Teams of
              agents outperform a single agent on open-ended research.
              <Cite n={[18]} />
            </li>
          </ul>
        </section>

        <section className={card} aria-labelledby="why-easy">
          <h3 id="why-easy" className={h3}>
            Made for a family in grief
          </h3>
          <ul className="m-0 mt-3 flex list-disc flex-col gap-2 ps-5 text-[16px]">
            <li>Speak or type in your own language; it asks one question at a time, and only what helps.</li>
            <li>You confirm only what you can know, at seven points along the way; the agents do the rest.</li>
            <li>Every step says why, in the regulator&apos;s own words, with a link to the page.</li>
            <li>Each family member sees the case in their own language, on a phone or a laptop.</li>
          </ul>
        </section>
      </div>

      <section aria-labelledby="why-sources" className="text-[15px] text-ink-soft">
        <h3 id="why-sources" className="m-0 text-[16px] font-bold text-ink">
          Sources
        </h3>
        <ol className="m-0 mt-2 grid gap-x-8 gap-y-1.5 ps-6 [grid-template-columns:repeat(auto-fit,minmax(min(420px,100%),1fr))]">
          {SOURCES.map((s) => (
            <li key={s.id} id={`src-${s.id}`}>
              <a href={s.url} target="_blank" rel="noreferrer" className="break-words">
                {s.label}
              </a>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
