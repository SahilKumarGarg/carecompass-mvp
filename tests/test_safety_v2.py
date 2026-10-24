"""Extended safety tests: term_groups, paraphrases, negation, and the bug-fix sentence.

Covers:
- The originally failing sentence (mother-in-law, fever + confusion) → urgent
- 3 natural paraphrases for each of the 10 rules
- Negation examples: "no fever and no confusion" etc. → routine
- New unit tests for _is_negated and _match_term_groups helpers
"""

from __future__ import annotations

import pytest

from src.safety import (
    SafetyResult,
    _is_negated,
    _match_term_groups,
    check_red_flags,
)


# ---------------------------------------------------------------------------
# The original bug-fix sentence
# ---------------------------------------------------------------------------

class TestOriginalBugFix:
    def test_mother_in_law_fever_confusion_is_urgent(self) -> None:
        """Regression: this sentence must now return urgent via term_groups RF008."""
        result = check_red_flags(
            "My mother-in-law has had fever and weakness for two days and is confused at times."
        )
        assert result.tier in {"urgent", "emergency"}, (
            f"Expected urgent or emergency, got {result.tier!r}. Hits: {result.hits}"
        )
        hit_ids = {h.rule_id for h in result.hits}
        assert "RF008" in hit_ids, f"Expected RF008 to fire. Hits: {result.hits}"

    def test_term_group_match_recorded_in_hits(self) -> None:
        result = check_red_flags(
            "My mother-in-law has had fever and weakness for two days and is confused at times."
        )
        rf008 = next(h for h in result.hits if h.rule_id == "RF008")
        assert rf008.matched_phrase.startswith("groups:"), (
            f"Expected a groups: match, got {rf008.matched_phrase!r}"
        )


# ---------------------------------------------------------------------------
# Paraphrases for each rule (3 per rule)
# ---------------------------------------------------------------------------

class TestRF001ChestPainParaphrases:
    def test_sternum_pressure(self) -> None:
        assert check_red_flags("He has pressure on his sternum and can barely move.").tier == "emergency"

    def test_chest_aching(self) -> None:
        assert check_red_flags("She says her chest is aching badly.").tier == "emergency"

    def test_left_chest_squeezing(self) -> None:
        assert check_red_flags("There is a squeezing feeling in the left side of his chest.").tier == "emergency"


class TestRF002TroubleBreathingParaphrases:
    def test_finding_it_hard_to_breathe(self) -> None:
        assert check_red_flags("She is finding it hard to breathe.").tier == "emergency"

    def test_struggling_for_air(self) -> None:
        assert check_red_flags("He is struggling for air and cannot catch his breath.").tier == "emergency"

    def test_very_hard_to_breathe(self) -> None:
        assert check_red_flags("Breathing is very hard for her right now.").tier == "emergency"


class TestRF003StrokeParaphrases:
    def test_face_drooping_suddenly(self) -> None:
        assert check_red_flags("His face is drooping suddenly on the left side.").tier == "emergency"

    def test_arm_limp(self) -> None:
        assert check_red_flags("Her arm has gone limp all of a sudden.").tier == "emergency"

    def test_speech_slurring(self) -> None:
        assert check_red_flags("He is slurring his speech and seems weak on one side.").tier == "emergency"


class TestRF004BleedingParaphrases:
    def test_blood_wont_stop(self) -> None:
        assert check_red_flags("The blood from the cut won't stop flowing.").tier == "emergency"

    def test_bleeding_heavily(self) -> None:
        assert check_red_flags("She is bleeding heavily from her arm.").tier == "emergency"

    def test_blood_pooling(self) -> None:
        assert check_red_flags("There is blood pooling on the floor.").tier == "emergency"


class TestRF005UnconsciousParaphrases:
    def test_not_waking_up(self) -> None:
        assert check_red_flags("He fell and is not waking up.").tier == "emergency"

    def test_collapsed(self) -> None:
        assert check_red_flags("She just collapsed in the kitchen.").tier == "emergency"

    def test_gone_limp(self) -> None:
        assert check_red_flags("He has gone limp and is not responding at all.").tier == "emergency"


class TestRF006SeizureParaphrases:
    def test_convulsing(self) -> None:
        assert check_red_flags("She is convulsing on the floor.").tier == "emergency"

    def test_whole_body_shaking(self) -> None:
        assert check_red_flags("His whole body is shaking uncontrollably.").tier == "emergency"

    def test_went_into_a_fit(self) -> None:
        assert check_red_flags("She went into a fit a few minutes ago.").tier == "emergency"


class TestRF007AllergicReactionParaphrases:
    def test_throat_swollen(self) -> None:
        assert check_red_flags("His throat is swollen and he can't swallow.").tier == "emergency"

    def test_lips_swelling(self) -> None:
        assert check_red_flags("Her lips are swelling up fast after eating.").tier == "emergency"

    def test_tongue_puffing_up(self) -> None:
        assert check_red_flags("His tongue is puffing up and his airway feels tight.").tier == "emergency"


class TestRF008FeverConfusionParaphrases:
    def test_feverish_and_disoriented(self) -> None:
        result = check_red_flags("She is feverish and seems completely disoriented.")
        assert result.tier in {"urgent", "emergency"}
        assert any(h.rule_id == "RF008" for h in result.hits)

    def test_sweating_and_delirious(self) -> None:
        result = check_red_flags("He is sweating a lot and has become delirious.")
        assert result.tier in {"urgent", "emergency"}
        assert any(h.rule_id == "RF008" for h in result.hits)

    def test_temperature_and_muddled(self) -> None:
        result = check_red_flags(
            "My nan has a high temperature and has been very muddled all afternoon."
        )
        assert result.tier in {"urgent", "emergency"}
        assert any(h.rule_id == "RF008" for h in result.hits)


class TestRF009SelfHarmParaphrases:
    def test_want_to_end_it_all(self) -> None:
        result = check_red_flags("I keep thinking I want to end it all.")
        assert result.tier in {"urgent", "emergency"}
        assert any(h.rule_id == "RF009" for h in result.hits)

    def test_suicidal(self) -> None:
        result = check_red_flags("I've been feeling suicidal for the past week.")
        assert result.tier in {"urgent", "emergency"}

    def test_harm_myself(self) -> None:
        result = check_red_flags("I have thoughts of harming myself.")
        assert result.tier in {"urgent", "emergency"}


class TestRF010PregnancyParaphrases:
    def test_pregnant_and_bleeding(self) -> None:
        result = check_red_flags("I am 28 weeks pregnant and I am bleeding.")
        assert result.tier == "emergency"
        assert any(h.rule_id == "RF010" for h in result.hits)

    def test_expecting_with_contractions(self) -> None:
        result = check_red_flags("She is expecting and is having contractions every two minutes.")
        assert result.tier == "emergency"

    def test_foetus_not_moving(self) -> None:
        # "baby not moving" is a literal phrase in the phrases list
        result = check_red_flags("I am pregnant and the baby not moving at all.")
        assert result.tier == "emergency"


# ---------------------------------------------------------------------------
# Negation tests — these must return routine
# ---------------------------------------------------------------------------

class TestNegation:
    def test_no_fever_no_confusion(self) -> None:
        result = check_red_flags("She has a cough but no fever and no confusion.")
        assert result.tier == "routine", (
            f"Expected routine after negation, got {result.tier!r}. Hits: {result.hits}"
        )

    def test_without_fever(self) -> None:
        result = check_red_flags("The child is unwell without fever and seems fine otherwise.")
        assert result.tier == "routine"

    def test_not_confused(self) -> None:
        result = check_red_flags("He has a temperature but is not confused and is talking normally.")
        # "not confused" should negate confusion; temperature alone should not trigger RF008
        # because the confusion group is negated
        rf008_hits = [h for h in result.hits if h.rule_id == "RF008"]
        assert rf008_hits == [], (
            f"RF008 should not fire when confusion is negated. Hits: {result.hits}"
        )

    def test_no_chest_pain_phrase_path_still_fires(self) -> None:
        """The legacy phrases path does NOT apply negation — that is intentional.
        Better to false-positive on 'no chest pain' than to miss a real case.
        Negation suppression only applies to term_groups matches.
        The term_groups path correctly suppresses when both groups are negated."""
        result = check_red_flags("He says he has no chest pain and no shortness of breath.")
        # phrase "chest pain" fires through the phrases path even though negated
        rf001_phrase_hits = [h for h in result.hits
                             if h.rule_id == "RF001" and not h.matched_phrase.startswith("groups:")]
        assert rf001_phrase_hits, "Legacy phrases path should still fire on 'chest pain'"

    def test_never_had_seizure_phrase_path_still_fires(self) -> None:
        """Same design: 'seizure' as a bare phrase fires regardless of negation context.
        This is intentional safe-side behaviour for the phrases path."""
        result = check_red_flags("She has never had a seizure and there is no family history.")
        rf006_hits = [h for h in result.hits if h.rule_id == "RF006"]
        assert rf006_hits, "Phrases path fires even with 'never had a' prefix"

    def test_negation_does_not_suppress_unrelated_rule(self) -> None:
        """Negating fever should not suppress a genuine stroke match."""
        result = check_red_flags(
            "No fever, but her face is drooping and her speech is slurred."
        )
        assert result.tier == "emergency"
        assert any(h.rule_id == "RF003" for h in result.hits)

    def test_positive_after_negation_in_same_sentence(self) -> None:
        """Second mention not negated should still fire."""
        result = check_red_flags(
            "There was no confusion yesterday but today she is very confused and feverish."
        )
        # "very confused" — "today she is very confused" — no negator in window
        assert result.tier in {"urgent", "emergency"}
        assert any(h.rule_id == "RF008" for h in result.hits)

    def test_not_bleeding_routine(self) -> None:
        result = check_red_flags("She says she is not bleeding and feels fine.")
        rf004_hits = [h for h in result.hits if h.rule_id == "RF004"]
        assert rf004_hits == [], f"RF004 should not fire: {result.hits}"

    def test_without_loss_of_consciousness(self) -> None:
        result = check_red_flags("He recovered without loss of consciousness.")
        # "without" ... doesn't directly negate RF005 synonyms here since
        # "unconscious" / "collapsed" etc. are not present — just sanity check.
        assert result.tier == "routine"


# ---------------------------------------------------------------------------
# Unit tests for _is_negated
# ---------------------------------------------------------------------------

class TestIsNegated:
    def test_single_token_negated_by_no(self) -> None:
        assert _is_negated("fever", "she has no fever") is True

    def test_single_token_not_negated(self) -> None:
        assert _is_negated("fever", "she has a fever") is False

    def test_multi_token_negated(self) -> None:
        assert _is_negated("chest pain", "there is no chest pain reported") is True

    def test_multi_token_not_negated(self) -> None:
        assert _is_negated("chest pain", "he is experiencing chest pain") is False

    def test_negator_outside_window_does_not_negate(self) -> None:
        # Negator 5+ tokens away should not negate
        assert _is_negated("fever", "no issues were reported and she has fever") is False

    def test_not_present_returns_false(self) -> None:
        # Synonym not in text at all → _is_negated returns False (not negated, just absent)
        assert _is_negated("fever", "she has a cough") is False

    def test_negated_by_without(self) -> None:
        assert _is_negated("confusion", "without confusion she seemed fine") is True

    def test_negated_by_never(self) -> None:
        assert _is_negated("seizure", "she has never had a seizure") is True

    def test_two_occurrences_one_negated_one_not(self) -> None:
        # Tokens: ["no","fever","yesterday","but","fever","today"]
        # 1st "fever" at index 1 → window(3) = ["no"] → negated
        # 2nd "fever" at index 4 → window(3) = tokens[1:4] = ["fever","yesterday","but"] → not negated
        assert _is_negated("fever", "no fever yesterday but fever today") is False


# ---------------------------------------------------------------------------
# Unit tests for _match_term_groups
# ---------------------------------------------------------------------------

class TestMatchTermGroups:
    def _rf008_rule(self) -> dict:
        """Minimal mock of RF008 with two groups."""
        return {
            "term_groups": [
                {"synonyms": ["fever", "temperature", "feverish"]},
                {"synonyms": ["confused", "disoriented", "muddled"]},
            ]
        }

    def test_both_groups_present_fires(self) -> None:
        rule = self._rf008_rule()
        result = _match_term_groups(rule, "she has fever and is confused")
        assert result is not None
        assert result.startswith("groups:")

    def test_only_first_group_present_no_fire(self) -> None:
        rule = self._rf008_rule()
        result = _match_term_groups(rule, "she has fever but is thinking clearly")
        assert result is None

    def test_only_second_group_present_no_fire(self) -> None:
        rule = self._rf008_rule()
        result = _match_term_groups(rule, "she is confused but has no fever")
        assert result is None

    def test_both_negated_no_fire(self) -> None:
        rule = self._rf008_rule()
        result = _match_term_groups(rule, "she has no fever and is not confused")
        assert result is None

    def test_synonym_list_in_each_group(self) -> None:
        rule = self._rf008_rule()
        result = _match_term_groups(rule, "the patient is feverish and appears muddled")
        assert result is not None

    def test_empty_term_groups_returns_none(self) -> None:
        rule: dict = {"term_groups": []}
        assert _match_term_groups(rule, "fever and confused") is None

    def test_no_term_groups_key_returns_none(self) -> None:
        assert _match_term_groups({}, "fever and confused") is None
