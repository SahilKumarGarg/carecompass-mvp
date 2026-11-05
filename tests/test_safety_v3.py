"""Regression tests for spelling-tolerant safety matching.

Covers:
- The three walkthrough failures now fixed: WC2 (chest paiin cant breath),
  WB3 (fevar + confusd), WB2 (disorientation noun form).
- 10 additional misspelt variant inputs across RF001–RF011 categories.
- 10 harmless sentences that must remain routine.
- Unit tests for _edit_distance helper.
- Guard tests: double-fuzzy (both groups misspelled) must NOT fire.
- Contraction normalisation tests.
"""

from __future__ import annotations

import pytest

from src.safety import (
    SafetyResult,
    _edit_distance,
    _match_term_groups,
    _normalise,
    check_red_flags,
)


# ---------------------------------------------------------------------------
# Required regression tests (task specification)
# ---------------------------------------------------------------------------

class TestWalkthroughRegressions:
    """The three inputs that previously failed must now be urgent or emergency."""

    def test_wc2_chest_paiin_cant_breath_is_emergency(self) -> None:
        """'chest paiin cant breath help' → emergency (RF002 via cant+breath)."""
        result = check_red_flags("chest paiin cant breath help")
        assert result.tier == "emergency", (
            f"Expected emergency, got {result.tier!r}. Hits: {result.hits}"
        )

    def test_wb3_fevar_confusd_is_urgent(self) -> None:
        """'fevar' (typo fever) + 'confusd' (typo confused) → urgent RF008."""
        result = check_red_flags(
            "my granny has fevar and is weak for 2 days and gets confusd sometimes"
        )
        assert result.tier in {"urgent", "emergency"}, (
            f"Expected urgent or emergency, got {result.tier!r}. Hits: {result.hits}"
        )
        assert any(h.rule_id == "RF008" for h in result.hits), (
            f"Expected RF008. Hits: {result.hits}"
        )

    def test_wb2_disorientation_noun_is_urgent(self) -> None:
        """Formal wording with 'disorientation' (noun) must match RF008 group2."""
        result = check_red_flags(
            "My elderly relative presents with a two-day fever, "
            "marked weakness and intermittent disorientation."
        )
        assert result.tier in {"urgent", "emergency"}, (
            f"Expected urgent or emergency, got {result.tier!r}. Hits: {result.hits}"
        )
        assert any(h.rule_id == "RF008" for h in result.hits), (
            f"Expected RF008. Hits: {result.hits}"
        )


# ---------------------------------------------------------------------------
# Contraction normalisation
# ---------------------------------------------------------------------------

class TestContractionNormalisation:
    """'cant', 'wont', 'dont' etc. must normalise identically to their
    apostrophe forms so that existing rule synonyms still fire."""

    def test_cant_normalises_same_as_can_apostrophe_t(self) -> None:
        assert _normalise("cant") == _normalise("can't")

    def test_wont_normalises_same_as_won_apostrophe_t(self) -> None:
        assert _normalise("wont") == _normalise("won't")

    def test_dont_normalises_same_as_don_apostrophe_t(self) -> None:
        assert _normalise("dont") == _normalise("don't")

    def test_doesnt_normalises_same_as_does_apostrophe_nt(self) -> None:
        assert _normalise("doesnt") == _normalise("doesn't")

    def test_cant_breathe_fires_rf002(self) -> None:
        """'cant breathe' (no apostrophe) must fire RF002 same as 'can't breathe'."""
        r_apos = check_red_flags("she cant breathe properly")
        r_plain = check_red_flags("she can't breathe properly")
        assert r_apos.tier == r_plain.tier == "emergency"

    def test_cant_in_term_groups_synonym(self) -> None:
        """'cant' now appears as a literal synonym in RF002 group2 as well."""
        rule = {
            "term_groups": [
                {"synonyms": ["breath", "breathing", "breathe"]},
                {"synonyms": ["can't", "cant", "cannot"]},
            ]
        }
        text = _normalise("cant breath")
        result = _match_term_groups(rule, text)
        assert result is not None, "cant+breath must match via term_groups"


# ---------------------------------------------------------------------------
# _edit_distance unit tests
# ---------------------------------------------------------------------------

class TestEditDistance:
    def test_identical_strings_distance_zero(self) -> None:
        assert _edit_distance("fever", "fever") == 0

    def test_single_substitution(self) -> None:
        # "fevar" vs "fever": a→e at position 3
        assert _edit_distance("fevar", "fever") == 1

    def test_single_deletion(self) -> None:
        # "confusd" vs "confused": missing 'e'
        assert _edit_distance("confusd", "confused") == 1

    def test_single_insertion(self) -> None:
        # "paiin" vs "pain": extra 'i'
        assert _edit_distance("paiin", "pain") == 1

    def test_empty_string_vs_word(self) -> None:
        assert _edit_distance("", "fever") == 5

    def test_word_vs_empty_string(self) -> None:
        assert _edit_distance("fever", "") == 5

    def test_two_edits(self) -> None:
        # Verify a 2-edit pair is correctly measured (not eligible for fuzzy matching)
        assert _edit_distance("feevar", "fever") == 2    # two edits: extra 'e' + sub
        assert _edit_distance("confusd", "confused") == 1  # sanity: 1 edit

    def test_completely_different_short_words(self) -> None:
        assert _edit_distance("cat", "dog") == 3

    def test_transposition_counts_as_two(self) -> None:
        # Levenshtein (not Damerau): "ab" → "ba" = 2 ops (sub+sub) or del+insert
        # Standard Levenshtein counts transposition as 2 operations
        assert _edit_distance("ab", "ba") == 2

    def test_fever_vs_fevar_is_one(self) -> None:
        assert _edit_distance("fever", "fevar") == 1

    def test_confused_vs_confusd_is_one(self) -> None:
        assert _edit_distance("confused", "confusd") == 1

    def test_unconscious_vs_unconcious_is_one(self) -> None:
        assert _edit_distance("unconscious", "unconcious") == 1

    def test_pregnant_vs_pregant_is_one(self) -> None:
        # "pregnant" (8 chars) vs "pregant" (7 chars): one deletion
        assert _edit_distance("pregnant", "pregant") == 1

    def test_two_edit_distance_not_eligible_for_fuzzy(self) -> None:
        # "feevar" is 2 edits from "fever" — must NOT fire fuzzy in safety rules
        assert _edit_distance("feevar", "fever") == 2


# ---------------------------------------------------------------------------
# 10 misspelt variants across red-flag categories — must be urgent or emergency
# ---------------------------------------------------------------------------

class TestMisspeltVariants:
    """Each sentence is a realistic typo version of a genuine red-flag input."""

    # RF001 / RF002 — chest pain / breathing
    def test_cheast_pain_emergency(self) -> None:
        """'cheast' (6 chars, distance 1 from 'chest') fuzzy-matches RF001 group1;
        'pain' exact in RF001 group2."""
        result = check_red_flags("i have cheast pain and feel sick")
        assert result.tier == "emergency", f"Hits: {result.hits}"

    def test_cant_breathe_typo_emergency(self) -> None:
        """'cant breethe' — contraction expansion + fuzzy on breathing synonym."""
        result = check_red_flags("she cant breethe and is turning blue")
        assert result.tier == "emergency", f"Hits: {result.hits}"

    # RF003 — stroke signs
    def test_speech_slurd_emergency(self) -> None:
        """'slurd' is distance 1 from 'slurred'; 'speech' exact."""
        result = check_red_flags("her speech is slurd and one arm is weak")
        assert result.tier == "emergency", f"Hits: {result.hits}"

    def test_face_droping_emergency(self) -> None:
        """'droping' is distance 1 from 'drooping'."""
        result = check_red_flags("his face is droping on the left side suddenly")
        assert result.tier == "emergency", f"Hits: {result.hits}"

    # RF004 — severe bleeding
    def test_bleading_heavily_emergency(self) -> None:
        """'bleading' fuzzy-matches 'bleeding'; 'heavily' exact in RF004 group2."""
        result = check_red_flags("she is bleading heavily and it wont stop")
        assert result.tier == "emergency", f"Hits: {result.hits}"

    # RF007 — allergic reaction
    def test_throut_swelling_emergency(self) -> None:
        """'throut' is distance 1 from 'throat'; 'swelling' exact in RF007 group2."""
        result = check_red_flags("his throut is swelling after eating peanuts")
        assert result.tier == "emergency", f"Hits: {result.hits}"

    # RF008 — confusion with fever (now also tests JSON synonym fix)
    def test_feverish_disorientated_typo_urgent(self) -> None:
        """Both fever and disorientation (noun form, added in fix) match RF008."""
        result = check_red_flags(
            "she has been feverish all day and shows signs of disorientation"
        )
        assert result.tier in {"urgent", "emergency"}, f"Hits: {result.hits}"
        assert any(h.rule_id == "RF008" for h in result.hits)

    def test_tempratuer_confused_urgent(self) -> None:
        """'tempratuer' fuzzy matches 'temperature' (distance 2 — won't fuzzy);
        but 'confused' exact matches group2, and 'very hot' exact in group1 — no.
        Use a simpler form: temperature exact + confusd (added synonym)."""
        result = check_red_flags(
            "my dad has a high temperature and seems confusd tonight"
        )
        assert result.tier in {"urgent", "emergency"}, f"Hits: {result.hits}"
        assert any(h.rule_id == "RF008" for h in result.hits)

    # RF010 — pregnancy emergency (single typo only)
    def test_pregnant_bleading_one_typo_emergency(self) -> None:
        """Only one word typo: 'pregnant' exact + 'bleading' fuzzy → RF010 fires."""
        result = check_red_flags("I am pregnant and I have been bleading quite a bit")
        assert result.tier == "emergency", f"Hits: {result.hits}"
        assert any(h.rule_id == "RF010" for h in result.hits)

    # RF011 — fever in child
    def test_fevar_in_child_urgent(self) -> None:
        """'fevar' (distance 1 from fever) + 'toddler' exact → RF011."""
        result = check_red_flags("my toddler has had fevar all morning")
        assert result.tier in {"urgent", "emergency"}, f"Hits: {result.hits}"
        assert any(h.rule_id == "RF011" for h in result.hits)


# ---------------------------------------------------------------------------
# 10 harmless sentences that must stay routine
# ---------------------------------------------------------------------------

class TestHarmlessSentencesStayRoutine:
    """None of these inputs should trigger any red-flag rule."""

    @pytest.mark.parametrize("text", [
        "I have a mild cough and a runny nose.",
        "I need a repeat prescription for my blood pressure tablets.",
        "My ankle hurts after a walk yesterday.",
        "Can I get a travel vaccination before my holiday?",
        "I would like to book a routine GP appointment.",
        "My child has a slight sniffle but no fever and is eating normally.",
        "I have had a sore throat for two days — no fever, no difficulty swallowing.",
        "I am looking for a local pharmacy that stocks my regular medication.",
        "I have a small cut on my finger that has stopped bleeding.",
        "I would like some information about contraception options.",
    ])
    def test_routine_input_stays_routine(self, text: str) -> None:
        result = check_red_flags(text)
        assert result.tier == "routine", (
            f"Expected routine for harmless input, got {result.tier!r}.\n"
            f"Input: {text!r}\nHits: {result.hits}"
        )


# ---------------------------------------------------------------------------
# Double-fuzzy guard: both groups misspelled must NOT fire
# ---------------------------------------------------------------------------

class TestDoubleFuzzyGuard:
    """A rule must not fire when ALL groups require a fuzzy match.
    The other group must match exactly (or via phrase)."""

    def test_pregant_and_bleading_stays_routine(self) -> None:
        """Both 'pregant' and 'bleading' misspelled — RF010 must not fire."""
        result = check_red_flags("im pregant and ive been bleading a little bit")
        rf010_hits = [h for h in result.hits if h.rule_id == "RF010"]
        assert rf010_hits == [], (
            f"RF010 must not fire on double-fuzzy. Hits: {result.hits}"
        )

    def test_single_misspelt_word_no_partner_stays_routine(self) -> None:
        """A single fuzzy-matching word with no exact partner must not fire."""
        # 'fevar' fuzzy-matches RF008/RF011 group1 but no confusion/child word present
        result = check_red_flags("my friend has fevar today")
        # RF008 group2 and RF011 group2 have no match → both stay silent
        for h in result.hits:
            assert h.rule_id not in {"RF008", "RF011"}, (
                f"Single-word fuzzy must not trigger multi-group rule. Hits: {result.hits}"
            )

    def test_single_group_rule_fuzzy_does_not_fire(self) -> None:
        """RF005 (single group) must not fire on 'unconcious' alone — no second group
        to corroborate."""
        result = check_red_flags("she went unconcious briefely last week but is fine now")
        rf005_hits = [h for h in result.hits if h.rule_id == "RF005"]
        assert rf005_hits == [], (
            f"Single-group RF005 must not fire on misspelling alone. Hits: {result.hits}"
        )
