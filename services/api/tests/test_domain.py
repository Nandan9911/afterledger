"""The exact part: conditions, clocks, compensation, money, dates, names, identifiers, quote and number checks, and
the calibration lookup. None of it calls a model, so every answer is pinned."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from afterledger.domain.calendars import format_date, parse_date, to_japanese_era
from afterledger.domain.calibration import Calibrator, lead_label, value_certainty
from afterledger.domain.checks import (
    fidelity_violations,
    japanese_era_to_iso,
    normalise,
    parse_amount,
    quote_in_page,
    reconcile,
)
from afterledger.domain.identifiers import check, mask
from afterledger.domain.money import amount_in_words, format_amount
from afterledger.domain.names import (
    initials_compatible,
    katakana_to_romaji,
    name_similarity,
    script_of,
    strip_honorifics,
)
from afterledger.domain.plan_language import Clock, Cond, ConditionSyntaxError, cond_to_text, parse_cond
from afterledger.domain.plan_runtime import compensation, due_date, evaluate, per_day, unknown_vars

# ---- the condition language ------------------------------------------------------------------------


def test_conditions_parse_with_precedence_and_print_back() -> None:
    c = parse_cond("nominee_or_survivor = no and (bank_type = commercial and amount <= 1500000) or dispute = yes")
    assert c.op == "any" and [a.op for a in c.args] == ["all", "eq"]
    assert [a.op for a in c.args[0].args] == ["eq", "eq", "le"]          # nested 'and' flattened
    assert parse_cond(c.text()) == c
    assert parse_cond("kind in [savings, 'fixed deposit']").value == ["savings", "fixed deposit"]
    assert parse_cond("not will = yes").op == "not"


@pytest.mark.parametrize("text", ["amount <=", "x in [a, b", "(a = 1", "a = 1 b = 2", "= 3", "a ! 3"])
def test_bad_conditions_are_refused(text: str) -> None:
    with pytest.raises(ConditionSyntaxError):
        parse_cond(text)


def test_three_valued_logic() -> None:
    c = parse_cond("nominee_or_survivor = no and amount <= 1500000 or dispute = yes")
    assert evaluate(c, {}) == "unknown"
    assert unknown_vars(c, {}) == {"nominee_or_survivor", "amount", "dispute"}
    assert evaluate(c, {"dispute": "yes"}) == "yes"                       # settled without the rest
    assert evaluate(c, {"dispute": "no", "nominee_or_survivor": "yes"}) == "no"
    assert evaluate(c, {"dispute": "no", "nominee_or_survivor": "no", "amount": 2_000_000}) == "no"
    assert evaluate(c, {"dispute": "no", "nominee_or_survivor": "no", "amount": "unknown"}) == "unknown"
    assert evaluate(None, {}) == "yes"


_TEXT_VARS = ["a", "b"]
_WORDS = ["yes", "no", "maybe"]


def _leaf() -> st.SearchStrategy[Cond]:
    text = st.builds(lambda v, op, w: Cond(op=op, var=v, value=w),
                     st.sampled_from(_TEXT_VARS), st.sampled_from(["eq", "ne"]), st.sampled_from(_WORDS))
    member = st.builds(lambda v, ws: Cond(op="in", var=v, value=ws),
                       st.sampled_from(_TEXT_VARS), st.lists(st.sampled_from(_WORDS), min_size=1, max_size=3))
    number = st.builds(lambda op, n: Cond(op=op, var="n", value=n),
                       st.sampled_from(["lt", "le", "gt", "ge", "eq", "ne"]), st.integers(-5, 5))
    return st.one_of(text, member, number)


conditions = st.recursive(
    _leaf(),
    lambda inner: st.one_of(
        st.builds(lambda xs: Cond(op="all", args=xs), st.lists(inner, min_size=2, max_size=3)),
        st.builds(lambda xs: Cond(op="any", args=xs), st.lists(inner, min_size=2, max_size=3)),
        st.builds(lambda x: Cond(op="not", args=[x]), inner)),
    max_leaves=8)

facts = st.fixed_dictionaries({}, optional={"a": st.sampled_from(_WORDS), "b": st.sampled_from(_WORDS),
                                            "n": st.integers(-6, 6)})


@settings(max_examples=300, deadline=None)
@given(conditions, facts)
def test_printed_conditions_mean_the_same(c: Cond, f: dict) -> None:
    """What a plan shows people (the condition as text) is what code runs."""
    assert evaluate(parse_cond(cond_to_text(c)), f) == evaluate(c, f)


@settings(max_examples=300, deadline=None)
@given(conditions, facts, st.sampled_from(_WORDS), st.sampled_from(_WORDS), st.integers(-6, 6))
def test_a_settled_answer_never_changes_when_more_is_known(c: Cond, f: dict, a: str, b: str, n: int) -> None:
    before = evaluate(c, f)
    full = {"a": a, "b": b, "n": n, **f}
    if before != "unknown":
        assert evaluate(c, full) == before
    else:
        assert unknown_vars(c, f)               # an open answer always names the facts that would settle it
    assert evaluate(c, full) != "unknown"


# ---- clocks and compensation ---------------------------------------------------------------------------


def _clock(length: int, unit: str, holidays: str | None = None) -> Clock:
    return Clock(id="c", owner="institution", starts_on="start", length=length, unit=unit, holidays=holidays,
                 cites=["test"])


def test_clocks() -> None:
    assert due_date(_clock(15, "calendar_days"), date(2026, 9, 20)) == date(2026, 10, 5)
    # 24 Dec, then Christmas (Friday), the weekend and the Boxing Day substitute (Monday 28) are skipped
    assert due_date(_clock(3, "business_days", "GB"), date(2026, 12, 23)) == date(2026, 12, 30)
    assert due_date(_clock(1, "months"), date(2026, 1, 31)) == date(2026, 2, 28)       # month end kept
    assert due_date(_clock(3, "months"), date(2026, 11, 30)) == date(2027, 2, 28)


def test_the_hand_worked_clock_vectors() -> None:
    from afterledger.eval.runner import check_clocks
    result = check_clocks()
    assert result["ok"], result["detail"]


def test_compensation_formulas() -> None:
    rbi = {"base_rate_pct": Decimal("6"), "spread_pct": Decimal("4")}
    # interest for the 5 days after the due date: 100000 x 10% x 5/365
    assert compensation("rate_per_year_on_amount", Decimal("100000"), date(2026, 10, 1), date(2026, 10, 6),
                        rbi) == Decimal("136.99")
    assert per_day("rate_per_year_on_amount", Decimal("100000"), rbi) == Decimal("27.40")
    assert compensation("fixed_per_day", Decimal("0"), date(2026, 10, 1), date(2026, 10, 4),
                        {"per_day": Decimal("5000")}) == Decimal("15000.00")
    irdai = {"base_rate_pct": Decimal("6"), "spread_pct": Decimal("2")}
    # paid on time: nothing; late: interest from the start of the clock (40 days at 8%)
    assert compensation("rate_per_year_from_start", Decimal("100000"), date(2026, 10, 1), date(2026, 10, 1),
                        irdai, start=date(2026, 9, 1)) == Decimal("0.00")
    assert compensation("rate_per_year_from_start", Decimal("100000"), date(2026, 10, 1), date(2026, 10, 11),
                        irdai, start=date(2026, 9, 1)) == Decimal("876.71")
    with pytest.raises(ValueError):
        compensation("a_formula_no_rule_states", Decimal("1"), date(2026, 1, 1), date(2026, 2, 1), {})


# ---- money, dates, names, identifiers -------------------------------------------------------------------


def test_money_is_written_the_way_each_country_writes_it() -> None:
    assert format_amount(Decimal("641870"), "INR") == "₹6,41,870.00"
    assert format_amount(Decimal("1234000"), "JPY") == "¥1,234,000"
    assert format_amount(Decimal("12400"), "EUR") == "12.400,00 €"
    assert format_amount(Decimal("18240.55"), "SGD") == "S$18,240.55"
    assert amount_in_words(Decimal("641870")) == "Rupees Six Lakh Forty-One Thousand Eight Hundred and Seventy only"
    assert amount_in_words(Decimal("1234000"), "JPY", "ja") == "金1,234,000円也"
    assert parse_amount("₹6,00,000.00") == Decimal("600000.00")
    assert parse_amount("1.234,56", ",") == Decimal("1234.56")
    assert parse_amount("１２，０００円") == Decimal("12000")
    # a live reader copied 2,915.40 as 2.915.40: groups of three with two decimals read the same either way
    assert parse_amount("2.915.40") == parse_amount("2,915.40") == Decimal("2915.40")
    assert parse_amount("6,41,870.00") == Decimal("641870.00") and parse_amount("1,234,567") == Decimal("1234567")


def test_dates_in_the_calendars_families_use() -> None:
    assert to_japanese_era(date(2019, 5, 1)) == "令和元年5月1日"
    assert to_japanese_era(date(2026, 10, 14), with_romaji=True) == "令和8年10月14日 (Reiwa 8)"
    assert japanese_era_to_iso("昭和64年1月7日") == "1989-01-07"
    for text in ("03/10/2026", "2026-10-03", "3 Oct 2026", "October 3, 2026", "令和8年10月3日"):
        assert parse_date(text) == date(2026, 10, 3), text
    assert parse_date("not a date") is None
    assert format_date(date(2026, 9, 2), "hi") == "2 सितंबर 2026"            # Latin digits on Hindi pages
    assert format_date(date(2026, 9, 2), "ja") == "2026年9月2日"


def test_names_across_scripts() -> None:
    assert [script_of(x) for x in ("राजेश", "サトウ", "佐藤", "Nair")] == ["Deva", "Kana", "Hani", "Latn"]
    assert katakana_to_romaji("サトウ ハルコ") == "sato haruko"
    assert katakana_to_romaji("ｻﾄｳ") == "sato"                                # half-width katakana
    assert strip_honorifics("Late Shri R K Verma") == ("R K Verma", ["Late", "Shri"])
    assert initials_compatible("R K VERMA", "Rajesh Kumar Verma")
    assert initials_compatible("NAIR SURESH N", "Suresh Narayanan Nair")
    assert not initials_compatible("A K VERMA", "Rajesh Kumar Verma")
    assert name_similarity("Verma Rajesh", "Rajesh Verma") == 1.0


def test_identifiers_are_checked_by_code_and_shown_masked() -> None:
    assert check("iban", "GB82 WEST 1234 5698 7654 32")[0] == "pass"
    assert check("iban", "GB82 WEST 1234 5698 7654 33")[0] == "fail"
    assert check("ifsc", "SBIN0001234")[0] == "pass"
    assert check("ifsc", "SBIN1001234")[0] == "fail"
    assert check("passport", "X")[0] == "unknown"
    assert mask("123456789") == "ending 6789"


# ---- checks ----------------------------------------------------------------------------------------------


def test_normalising_across_scripts_and_digits() -> None:
    assert normalise("२०२६") == normalise("２０２６") == "2026"
    assert normalise("“Hello”  —  World") == '"hello" - world'


def test_quotes_must_be_on_their_page_word_for_word() -> None:
    page = "... The Bank shall settle   the claim within 15 days of receipt of the papers ..."
    assert quote_in_page("the bank shall settle the claim within 15 days", page)
    assert not quote_in_page("the bank shall settle the claim within 30 days", page)
    assert not quote_in_page("15 days", page)                                  # too short to prove anything


def test_two_readings_are_compared_by_code() -> None:
    assert reconcile("amount", "₹6,41,870.00", "641870")[0] == "agree"
    assert reconcile("identifier", "SBIN 0001234", "sbin-0001234")[0] == "agree"
    assert reconcile("text", "Verma", "Varma") == ("disagree", None)
    assert reconcile("amount", None, "100") == ("one_path_only", "100")


def test_no_number_in_a_letter_unless_it_is_in_the_facts() -> None:
    facts = "Amount 641870.00; date 2026-10-14; 14 October"
    assert fidelity_violations("We ask you to pay Rs 6,41,870 by 14 October 2026.", facts) == set()
    assert fidelity_violations("We ask you to pay Rs 6,41,780.", facts) == {"641780"}


# ---- calibration ------------------------------------------------------------------------------------------


def test_certainty_comes_from_the_most_specific_measured_bin() -> None:
    cal = Calibrator({"reading": {"base_rate": 0.7, "bins": [
        {"signals": {"status": "matched"}, "p": 0.99, "n": 0},
        {"signals": {"status": "matched", "photo": True}, "p": 0.9, "n": 0}]}})
    assert cal.probability("reading", {"status": "matched", "photo": False}) == 0.99
    assert cal.probability("reading", {"status": "matched", "photo": True}) == 0.9
    assert cal.probability("reading", {"status": "disagree"}) == 0.7
    assert cal.probability("people", {"evidence": "name_only"}) == 0.5     # no table: a coin toss, not certainty
    assert [value_certainty(p) for p in (0.95, 0.94, 0.8, 0.79)] == ["sure", "check", "check", "unknown"]
    assert [lead_label(p) for p in (0.96, 0.7, 0.35, 0.2)] == ["sure", "likely", "possible", "unlikely"]


# ---- the decision controller --------------------------------------------------------------------------------------

def test_the_controller_picks_each_move() -> None:
    from afterledger.domain.decision import Fork, choose
    sure = Fork(tier="B", p_now=0.9, p_if_asked=0.99, p_if_researched=0.0, research_minutes=0, stakes=0.5)
    assert choose(sure)[0] == "act"                                   # above its tier's bar: used as it is
    ask = Fork(tier="B", p_now=0.4, p_if_asked=0.99, p_if_researched=0.0, research_minutes=0, stakes=0.6,
               family_can_answer=True)
    assert choose(ask)[0] == "ask"
    research = Fork(tier="B", p_now=0.4, p_if_asked=0.0, p_if_researched=0.9, research_minutes=5, stakes=0.8)
    assert choose(research)[0] == "research"                          # the family cannot know; a rule can settle it
    wait = Fork(tier="B", p_now=0.4, p_if_asked=0.0, p_if_researched=0.0, research_minutes=0, stakes=0.8,
                event_due_days=3, p_if_waited=0.97)
    assert choose(wait)[0] == "wait"                                  # the answer is already on its way
    assert choose(Fork(tier="B", p_now=0.4, p_if_asked=0.99, p_if_researched=0.0, research_minutes=0, stakes=0.6,
                       family_can_answer=True, needs_professional=True))[0] == "hand_off"
    stuck = Fork(tier="A", p_now=0.6, p_if_asked=0.0, p_if_researched=0.7, research_minutes=5, stakes=1.0)
    assert choose(stuck)[0] == "hand_off"                             # tier A: only a move that makes it safe counts


def test_research_depth_for_each_kind_of_stored_plan() -> None:
    from afterledger.domain.decision import research_depth
    assert research_depth("A", "gold", fresh=True, thin_coverage=False) == "recall"
    assert research_depth("B", "sourced", fresh=True, thin_coverage=False) == "recall"
    assert research_depth("A", "sourced", fresh=True, thin_coverage=False) == "standard"
    assert research_depth("A", "dual_verified", fresh=False, thin_coverage=True) == "deep"
    assert research_depth("B", "sourced", fresh=False, thin_coverage=False) == "quick"


async def test_a_tier_b_value_above_its_bar_is_used_and_a_tier_a_value_is_asked(tmp_path: Any) -> None:
    from afterledger.domain.case import CaseFile, Paper, ReadValue
    from afterledger.modules.reading import _who_checks
    from afterledger.runtime import build_deps
    from conftest import offline_settings
    deps = build_deps(offline_settings(tmp_path))
    case = CaseFile(id="case-d", title="Test", created_at=deps.now())
    await deps.cases.put_case(case)
    paper = Paper(id="pp1", case_id="case-d", title="Passbook", file="local:x", uploaded_at=deps.now())
    b = ReadValue(id="rv_b", case_id="case-d", paper_id="pp1", field="branch", label="Branch", kind="text",
                  value="Gomti Nagar", status="resolved", tier="B")
    a = ReadValue(id="rv_a", case_id="case-d", paper_id="pp1", field="account_no", label="Account number",
                  kind="identifier", value="1234", status="resolved", tier="A")
    asked = await _who_checks(deps, case, paper, [a, b], {"rv_a": 0.9, "rv_b": 0.9})
    assert asked == [a] and "above_its_bar" in b.checks
    lines = [x.text for x in await deps.cases.list("case-d", "activity")]
    assert any(t.startswith("Used as read") and "Branch" in t for t in lines)
    rules = build_deps(offline_settings(tmp_path / "rules", decisions="rules"))
    assert await _who_checks(rules, case, paper, [a, b], {"rv_a": 0.9, "rv_b": 0.9}) == [a, b]


def test_a_thing_proposed_twice_is_one_lead() -> None:
    """In a live run Gemini proposed the same rental deposit twice and a utility bill as a debt and as a mandate:
    the same title, or the same payments for the same kind of thing, is one lead."""
    from afterledger.domain.case import Lead
    from afterledger.modules.discovery import _covered
    have = [Lead(id="l1", case_id="c", kind="asset", category="deposit", title="Rental deposit for Leopoldstraße 112",
                 sentence="", group_ids=["g_rent"]),
            Lead(id="l2", case_id="c", kind="debt", category="subscription", title="Stadtwerke München Utility Payment",
                 sentence="", group_ids=["g_power"])]
    assert _covered("rental deposit for  Leopoldstraße 112", [], have)                  # the same words
    assert _covered("Stadtwerke München utility mandate", ["g_power"], have)          # the same payments
    assert _covered("Overdue rent", ["g_rent"], have, kind="asset")
    assert not _covered("A loan for the flat", ["g_rent"], have, kind="debt")         # another kind of thing


def test_a_live_category_is_put_in_the_knowledge_words() -> None:
    """A live run wrote "lockers", the coverage item, for the category "locker": the seeder, the plans and the
    coverage answers look for the knowledge's own words."""
    from afterledger.modules.discovery import canonical_category
    items = [{"id": "lockers", "categories": ["locker"]},
             {"id": "loans", "categories": ["loan", "credit_card", "card_loan"]},
             {"id": "life_cover", "categories": ["life_policy", "government_cover", "life_cover"]}]
    assert canonical_category("lockers", "locker", items) == "locker"
    assert canonical_category("lockers", None, items) == "locker"          # the item's only category
    assert canonical_category("loans", "credit_card", items) == "credit_card"
    assert canonical_category("loans", None, items) == "loan"               # the singular of a known category
    assert canonical_category("life_cover", "government_cover", items) == "life_cover"
    assert canonical_category("chit_fund", None, items) == "chit_fund"      # a word the knowledge lacks is kept


def test_a_lead_rests_on_evidence_in_the_case() -> None:
    """A live model proposed a locker, a provident fund and a post office account from a death certificate alone:
    a lead needs a payment group, or a paper that can show a holding (a note in his hand can)."""
    from types import SimpleNamespace as NS

    from afterledger.modules.discovery import _covering, _evidence_of
    groups = {"g1": object()}
    papers = {"pp_dc": NS(doc_type="death_certificate"), "pp_pan": NS(doc_type="identity_card"),
              "pp_note": NS(doc_type="handwritten_note")}
    assert _evidence_of([], ["pp_dc"], groups, papers) == ([], ["pp_dc"], False)
    assert _evidence_of([], ["pp_dc", "pp_pan"], groups, papers)[2] is False
    assert _evidence_of(["g1", "g_gone"], ["pp_dc"], groups, papers) == (["g1"], ["pp_dc"], True)
    assert _evidence_of([], ["pp_note", "pp_gone"], groups, papers) == ([], ["pp_note"], True)
    from afterledger.domain.case import Lead
    old = Lead(id="l_old", case_id="c", kind="stop_payment", category="mutual_fund", sentence="",
               title="Stop CAMS mutual fund SIP auto-debit mandate", group_ids=["g1"])
    # a later run of discovery names it again under another key and title: the lead found before is the one
    assert _covering("Stop payment for CAMS mutual fund SIP mandate", ["g1"], [old], kind="stop_payment") is old
    assert _covering("CAMS mutual fund SIP", ["g1"], [old], kind="asset") is None


async def test_a_lead_names_the_institution_its_payments_name(deps) -> None:
    """A live model left the institution out of a PMJJBY lead, so no claim was made: the payments' narration names
    it, when it names exactly one."""
    from types import SimpleNamespace as NS

    from afterledger.modules.common import knowledge
    from afterledger.modules.discovery import _institution_of
    k = knowledge(deps)
    by_group = {"g1": NS(label="PMJJBY PREM RENEWAL 2026-27", counterparty="PMJJBY"),
                "g2": NS(label="ACH D- NACH MF SIP CAMS 4471", counterparty=None),
                "g3": NS(label="UPI/SHIVAM MEDICALS/LKO", counterparty="Shivam Medicals")}
    assert await _institution_of(k, ["g1"], by_group, ["IN"]) == "in_pmjjby"
    assert await _institution_of(k, ["g2"], by_group, ["IN"]) == "in_cams"
    assert await _institution_of(k, ["g3"], by_group, ["IN"]) is None
    assert await _institution_of(k, ["g1", "g2"], by_group, ["IN"]) is None       # two institutions, not one


def test_the_paper_settles_what_a_live_model_left_open() -> None:
    """A live model made two new people of 'मोहन लाल वर्मा / MOHAN LAL VERMA' on one line of the death certificate,
    a second wife of the Latin spelling of the wife's name, and a stranger of the holder printed on his passbook."""
    from afterledger.agents.schemas import Merge
    from afterledger.domain.case import NameForm, Person
    from afterledger.modules.people import settle_merges
    dead = Person(id="p_dead", case_id="c", display_name="Rajesh Kumar Verma",
                  names=[NameForm(text="RAJESH KUMAR VERMA", seen_on="pp_dc")])
    wife = Person(id="p_wife", case_id="c", display_name="सुनीता वर्मा", relationship="wife",
                  names=[NameForm(text="सुनीता वर्मा", script="Deva")])

    def form(text: str, paper: str, role: str, value: str) -> dict:
        return {"text": text, "seen_on": paper, "paper": paper, "role": role, "value_id": value}
    forms = [form("मोहन लाल वर्मा", "pp_dc", "father", "v_f"), form("MOHAN LAL VERMA", "pp_dc", "father", "v_f"),
             form("सुनीता वर्मा", "pp_dc", "spouse", "v_s"), form("SUNITA VERMA", "pp_dc", "spouse", "v_s"),
             form("R K VERMA", "pp_pb", "holder", "v_h"), form("AMIT VERMA", "pp_pb", "nominee", "v_n"),
             form("S VERMA", "pp_st", "holder", "v_o"), form("VERMA", "pp_x", "holder", "v_x")]
    merges = [Merge(name_text="मोहन लाल वर्मा", seen_on="pp_dc", new_person="मोहन लाल वर्मा", relationship="father"),
              Merge(name_text="MOHAN LAL VERMA", seen_on="pp_dc", new_person="MOHAN LAL VERMA", relationship="father"),
              Merge(name_text="सुनीता वर्मा", seen_on="pp_dc", person_id="p_wife"),
              Merge(name_text="SUNITA VERMA", seen_on="pp_dc", new_person="SUNITA VERMA", relationship="spouse"),
              Merge(name_text="R K VERMA", seen_on="pp_pb", new_person="R K VERMA"),
              Merge(name_text="AMIT VERMA", seen_on="pp_pb", new_person="AMIT VERMA", relationship="nominee"),
              Merge(name_text="S VERMA", seen_on="pp_st", new_person="S VERMA"),
              Merge(name_text="VERMA", seen_on="pp_x", new_person="VERMA")]
    settle_merges(merges, forms, [dead, wife], "p_dead")
    m = {x.name_text: x for x in merges}
    assert (m["MOHAN LAL VERMA"].person_id, m["MOHAN LAL VERMA"].new_person) == (None, "मोहन लाल वर्मा")
    assert (m["SUNITA VERMA"].person_id, m["SUNITA VERMA"].new_person) == ("p_wife", None)
    assert m["R K VERMA"].person_id == "p_dead" and "Rajesh Kumar Verma" in m["R K VERMA"].evidence
    assert m["AMIT VERMA"].new_person == "AMIT VERMA"          # a nominee is someone else
    assert m["S VERMA"].new_person == "S VERMA"                # initials that do not fit his name
    assert m["VERMA"].new_person == "VERMA"                    # one word fits too many people


async def test_a_parent_on_a_family_members_card_is_not_his_family(deps) -> None:
    """A live run added the father printed on his wife's PAN card as his father."""
    from afterledger.domain.case import CaseFile, Paper, Person, ReadValue
    from afterledger.modules.people import resolve_people
    case = CaseFile(id="case-p", title="Test", created_at=deps.now(), deceased_id="p_dead")
    await deps.cases.put_case(case)
    await deps.cases.put("case-p", "people", Person(id="p_dead", case_id="case-p", display_name="Rajesh Kumar Verma"))
    await deps.cases.put("case-p", "papers", Paper(id="pp_pan", case_id="case-p", title="Sunita's PAN card",
                                                   doc_type="identity_card"))
    for i, (field, value) in enumerate([("holder_name", "SUNITA VERMA"), ("father_name", "RAMESH CHANDRA SRIVASTAVA")]):
        await deps.cases.put("case-p", "values", ReadValue(id=f"rv{i}", case_id="case-p", paper_id="pp_pan", field=field,
                                                           label=field, kind="text", value=value, status="resolved",
                                                           tier="B"))
    await resolve_people(deps, "case-p")
    names = [n.text for p in await deps.cases.list("case-p", "people") for n in p.names] + \
        [p.display_name for p in await deps.cases.list("case-p", "people")]
    assert not any("RAMESH" in n.upper() for n in names)


async def test_the_seeder_finds_a_person_the_family_named_by_first_name(deps) -> None:
    """A live intake kept only "अमित" for "my son Amit": the replay still finds him, by part of his name and the
    relationship, when only one person fits."""
    from types import SimpleNamespace as NS

    from afterledger.domain.case import CaseFile, NameForm, Person
    from afterledger.seed.seeder import SeedReport, _Run
    await deps.cases.put_case(CaseFile(id="case-s", title="Test", created_at=deps.now(), deceased_id="p_dead"))
    for p in (Person(id="p_dead", case_id="case-s", display_name="राजेश कुमार वर्मा"),
              Person(id="p_1", case_id="case-s", display_name="अमित", relationship="son",
                     names=[NameForm(text="अमित", script="Deva")]),
              Person(id="p_2", case_id="case-s", display_name="SUNITA VERMA", relationship="spouse",
                     names=[NameForm(text="SUNITA VERMA")])):
        await deps.cases.put("case-s", "people", p)
    truth = {"p_amit": {"display_name": "Amit Verma", "relationship": "son", "names": [{"text": "अमित वर्मा"}]},
             "p_sunita": {"display_name": "Sunita Verma", "relationship": "wife", "names": []},
             "p_raj": {"display_name": "Raj Verma", "relationship": "brother", "names": [{"text": "अमित वर्मा"}]}}
    fam = NS(key="t", deceased={"id": "p_x"}, person=truth.get)
    run = _Run(deps=deps, fam=fam, case_id="case-s",
               report=SeedReport(key="t", case_id="case-s", story_day=deps.now().date()))
    assert await run.person_id("p_amit") == "p_1"
    assert await run.person_id("p_sunita") == "p_2"           # the same name, exactly
    assert await run.person_id("p_raj") is None               # another relationship
    # only in a script the file does not spell him in: the one son there is
    await deps.cases.put("case-s", "people", Person(id="p_3", case_id="case-s", display_name="അർജുൻ", relationship="son",
                                                    names=[NameForm(text="അർജുൻ", script="Mlym")]))
    truth["p_arjun"] = {"display_name": "Arjun Nair", "relationship": "son", "names": [{"text": "ARJUN SURESH NAIR"}]}
    assert await run.person_id("p_arjun") is None             # two sons now: not one to pick


def test_a_lead_category_the_plans_know_is_kept() -> None:
    from afterledger.modules.discovery import canonical_category
    items = [{"id": "employer", "categories": ["employer_benefit", "salary"]}]
    assert canonical_category("employer", "employer_benefit", items) == "employer"     # planning.LEAD_KINDS knows it


async def test_the_photo_reading_is_used_where_only_it_adds_up(deps) -> None:
    """A live text reader misread a balance; the photo's reading of the same row adds up, so it is used, and the
    rows after it are checked against it rather than failing one after another."""
    from afterledger.agents.schemas import RowReading
    from afterledger.domain.case import CaseFile, Paper
    from afterledger.modules.reading import _reconcile_rows
    await deps.cases.put_case(CaseFile(id="case-r", title="Test", created_at=deps.now()))
    paper = Paper(id="pp_s", case_id="case-r", title="Statement", country="GB", doc_type="bank_statement")

    def rows(second: str) -> list:
        return [RowReading(row=1, date="1 Nov 25", narration="PENSION", amount="100.00", direction="credit",
                           balance="1,000.00"),
                RowReading(row=2, date="3 Nov 25", narration="COUNCIL TAX", amount="50.00", direction="debit",
                           balance=second),
                RowReading(row=3, date="9 Nov 25", narration="CO-OP", amount="5.00", direction="debit", balance="945.00")]
    case = await deps.cases.get_case("case-r")
    txns, values = await _reconcile_rows(deps, case, paper, {}, rows("905.00"), rows("950.00"), {}, None)
    assert [str(t.balance) for t in txns] == ["1000.00", "950.00", "945.00"]
    assert [t.balance_ok for t in txns] == [None, True, True]
    assert any(v.field == "row_2_balance" and v.status == "resolved" and v.value == "950.00" for v in values)


async def test_the_running_balance_settles_the_column_and_a_card_counts_up(deps) -> None:
    """Live readers put an Indian statement's withdrawal in the deposit column (the balance then differs by twice the
    amount), and a card statement's balance is what is owed: a purchase raises it."""
    from afterledger.agents.schemas import RowReading
    from afterledger.domain.case import CaseFile, Paper
    from afterledger.modules.reading import _reconcile_rows
    await deps.cases.put_case(CaseFile(id="case-d2", title="Test", created_at=deps.now()))
    case = await deps.cases.get_case("case-d2")

    def row(n: int, amount: str, direction: str, balance: str) -> Any:
        return RowReading(row=n, date=f"{n} Sep 26", narration="X", amount=amount, direction=direction, balance=balance)
    bank = Paper(id="pp_nro", case_id="case-d2", title="NRO statement", country="IN", doc_type="bank_statement")
    rows = [row(1, "1,000.00", "credit", "1,72,390.20"), row(2, "5,000.00", "credit", "1,67,390.20"),
            row(3, "200.00", "debit", "1,67,190.20")]
    txns, _ = await _reconcile_rows(deps, case, bank, {}, rows, rows, {}, None)
    assert [t.direction for t in txns] == ["credit", "debit", "debit"] and [t.balance_ok for t in txns] == [None, True, True]
    card = Paper(id="pp_card", case_id="case-d2", title="Card statement", country="SG", doc_type="card_statement")
    rows = [row(1, "0.00", "credit", "1,744.95"), row(2, "214.35", "debit", "1,959.30"),
            row(3, "1,744.95", "credit", "214.35")]
    txns, _ = await _reconcile_rows(deps, case, card, {}, rows, rows, {}, None)
    assert [t.direction for t in txns] == ["credit", "debit", "credit"] and txns[1].balance_ok and txns[2].balance_ok


async def test_a_will_is_kept_only_when_the_family_spoke_of_one(deps) -> None:
    """A live model set will = yes for the Vermas, who never mentioned a will, and sent the Narmada claim to probate."""
    from afterledger.domain.case import CaseFile, SituationModel, Utterance
    from afterledger.modules.intake import _only_what_was_said
    await deps.cases.put_case(CaseFile(id="case-w", title="Test", created_at=deps.now()))
    await deps.cases.put("case-w", "utterances", Utterance(
        id="ut_1", case_id="case-w", role="family", lang="hi", text="सब वही देखते थे। दो बैंक हैं, सह्याद्रि और नर्मदा।",
        translation="He handled everything. There are two banks, Sahyadri and Narmada.", at=deps.now()))
    sit = SituationModel(will="yes", dispute="yes")
    await _only_what_was_said(deps, "case-w", sit)
    assert (sit.will, sit.dispute) == ("unknown", "unknown")
    await deps.cases.put("case-w", "utterances", Utterance(
        id="ut_2", case_id="case-w", role="family", lang="hi", text="उन्होंने वसीयत लिखी थी, पर भाई उससे सहमत नहीं है।",
        translation="He wrote a will, but my brother disagrees with it.", at=deps.now()))
    sit = SituationModel(will="yes", dispute="yes")
    await _only_what_was_said(deps, "case-w", sit)
    assert (sit.will, sit.dispute) == ("yes", "yes")


async def test_the_seeder_finds_the_one_son_named_in_another_script(deps) -> None:
    from types import SimpleNamespace as NS

    from afterledger.domain.case import CaseFile, NameForm, Person
    from afterledger.seed.seeder import SeedReport, _Run
    await deps.cases.put_case(CaseFile(id="case-n", title="Test", created_at=deps.now(), deceased_id="p_dead"))
    for p in (Person(id="p_dead", case_id="case-n", display_name="സുരേഷ് നാരായണൻ നായർ"),
              Person(id="p_1", case_id="case-n", display_name="അർജുൻ", relationship="son",
                     names=[NameForm(text="അർജുൻ", script="Mlym")]),
              Person(id="p_2", case_id="case-n", display_name="Lakshmi Nair", relationship="wife",
                     names=[NameForm(text="Lakshmi Nair")])):
        await deps.cases.put("case-n", "people", p)
    truth = {"p_arjun": {"display_name": "Arjun Nair", "relationship": "son", "names": [{"text": "ARJUN SURESH NAIR"}]},
             "p_saraswathy": {"display_name": "Saraswathy Amma", "relationship": "mother", "names": []}}
    run = _Run(deps=deps, fam=NS(key="t", deceased={"id": "p_x"}, person=truth.get), case_id="case-n",
               report=SeedReport(key="t", case_id="case-n", story_day=deps.now().date()))
    assert await run.person_id("p_arjun") == "p_1"
    assert await run.person_id("p_saraswathy") is None        # nobody with that relationship


async def test_a_holding_kind_the_money_map_does_not_know_gives_way_to_the_category(deps) -> None:
    """A live model gave a lead a holding_kind outside the money map's kinds, and every later step of the Vermas'
    story failed on it: the category stands in, and a lead with neither is left off the map, not crashed on."""
    from afterledger.domain.case import CaseFile, Lead
    from afterledger.modules.discovery import _valid_kind
    from afterledger.modules.planning import holding_kind_of, holdings_from_leads
    await deps.cases.put_case(CaseFile(id="case-k", title="Test", created_at=deps.now()))
    odd = Lead(id="l1", case_id="case-k", kind="entitlement", category="life_cover", title="PMJJBY life cover",
               sentence="", holding_kind="insurance_scheme", status="confirmed")
    none = Lead(id="l2", case_id="case-k", kind="asset", category="artwork", title="A painting", sentence="",
                holding_kind="painting", status="confirmed")
    assert holding_kind_of(odd) == "government_cover" and holding_kind_of(none) is None
    assert _valid_kind("insurance_scheme") is None and _valid_kind("locker") == "locker"
    await deps.cases.put_many("case-k", "leads", [odd, none])
    await holdings_from_leads(deps, "case-k")
    kinds = sorted(h.kind for h in await deps.cases.list("case-k", "holdings"))
    assert kinds == ["government_cover"]


def test_a_claim_title_keeps_a_name_in_title_case() -> None:
    from afterledger.modules.planning import _lower_first
    assert _lower_first("Pradhan Mantri Jeevan Jyoti Bima Yojana (PMJJBY)").startswith("Pradhan Mantri")
    assert _lower_first("Savings account") == "savings account"
    assert _lower_first("Mutual fund SIP via CAMS") == "mutual fund SIP via CAMS"


def test_a_claim_title_names_the_institution_once() -> None:
    """Titles from live leads on 15 October: 'Straits Marine: final salary and entitlements Engineering' (the short
    name was taken out and the rest of the full name left behind) and 'Riverwalk Logistics: RIVERWALK LOGISTICS final
    pay'."""
    from types import SimpleNamespace

    from afterledger.modules.planning import claim_title

    straits = SimpleNamespace(short_name="Straits Marine", name="Straits Marine Engineering Pte Ltd")
    assert claim_title(straits, "sg_straits", ["Final salary and entitlements from Straits Marine Engineering"]) == \
        "Straits Marine: final salary and entitlements"
    riverwalk = SimpleNamespace(short_name="Riverwalk Logistics", name="Riverwalk Logistics LLC")
    assert claim_title(riverwalk, "us_riverwalk", ["RIVERWALK LOGISTICS final pay and employment benefits"]) == \
        "Riverwalk Logistics: final pay and employment benefits"
    cams = SimpleNamespace(short_name="CAMS", name="Computer Age Management Services")
    assert claim_title(cams, "in_cams", ["Mutual fund SIP via CAMS (₹5,000/month)"]) == "CAMS: mutual fund SIP (₹5,000/month)"
    narmada = SimpleNamespace(short_name="Narmada Bank", name="Narmada Bank Ltd")
    assert claim_title(narmada, "in_narmada", ["Savings account", "A fixed deposit at Narmada Bank (joint)"]) == \
        "Narmada Bank: savings account and fixed deposit (joint)"
    assert claim_title(narmada, "in_narmada", ["Narmada Bank savings account"]) == "Narmada Bank savings account"


async def test_the_third_reader_looks_once_where_the_arithmetic_already_decided(deps) -> None:
    """Live, a photo reader read a passbook's balances one row off: the running balance settled every row, and the
    third reader (a pro model, with an image) was asked 35 times. It now looks once a paper, as a spot check."""
    from afterledger.agents.schemas import RowReading
    from afterledger.domain.case import CaseFile, Paper
    from afterledger.modules.reading import _reconcile_rows
    await deps.cases.put_case(CaseFile(id="case-c", title="Test", created_at=deps.now()))
    case = await deps.cases.get_case("case-c")
    asked: list[str] = []
    original = deps.models._oracle

    def oracle_for(name: str):
        if name != "reader_c":
            return original(name)

        def answer(req: Any) -> dict:
            asked.append(req.payload["field"])
            return {"field": req.payload["field"], "value": req.payload["readings"][0], "reason": "printed so"}
        return answer
    deps.models._oracle = oracle_for

    def rows(balances: list[str]) -> list:
        return [RowReading(row=n + 1, date=f"{n + 1} Sep 26", narration="X", amount="100", direction="debit",
                           balance=b) for n, b in enumerate(balances)]
    text = rows(["1,000", "900", "800", "700"])
    photo = rows(["1,000", "1,000", "900", "800"])              # one row off
    paper = Paper(id="pp_p", case_id="case-c", title="Passbook", country="JP", doc_type="passbook")
    txns, values = await _reconcile_rows(deps, case, paper, {}, text, photo, {}, None)
    assert [t.balance_ok for t in txns] == [None, True, True, True]
    assert asked == ["row_2_balance"]                          # one look, not three
    assert sum(1 for v in values if v.status == "resolved") == 3
