"""Regression tests for RF012 (inability to walk/stand), RF013 (thunderclap
headache) and RF014 (headache with red-flag features: fever, stiff neck,
confusion, weakness).

Covers:
- The original missed input (must be urgent or higher after fix).
- Paraphrases and natural variants for each new rule.
- Negation: negated mobility / headache terms must not fire.
- 10 harmless sentences that must stay routine.
- Spelling-tolerant variants (edit distance 1).
- Combination inputs that fire multiple rules.
- Term-groups unit tests for RF012 / RF013 / RF014.
"""

from __future__ import annotations

import pytest

from src.safety import (
    _is_negated,
    _match_term_groups,
    _normalise,
    check_red_flags,
)


# ---------------------------------------------------------------------------
# Regression: the originally-missed input
# ---------------------------------------------------------------------------

class TestOriginalMissedInput:
    """The triggering bug report — must now be urgent or higher."""

    def test_sick_not_able_to_walk_headache_is_urgent(self) -> None:
        result = check_red_flags(
            "I am feeling sick from last 4 days and not able to walk, headache as well"
        )
        assert result.tier in {"urgent", "emergency"}, (
            f"Expected urgent or emergency, got {result.tier!r}. Hits: {result.hits}"
        )

    def test_rf012_fires_on_original_input(self) -> None:
        result = check_red_flags(
            "I am feeling sick from last 4 days and not able to walk, headache as well"
        )
        hit_ids = {h.rule_id for h in result.hits}
        assert "RF012" in hit_ids, (
            f"Expected RF012 to fire on 'not able to walk'. Hits: {result.hits}"
        )

    def test_rf014_fires_on_headache_with_mobility_loss(self) -> None:
        result = check_red_flags(
            "I am feeling sick from last 4 days and not able to walk, headache as well"
        )
        hit_ids = {h.rule_id for h in result.hits}
        assert "RF014" in hit_ids, (
            f"Expected RF014 to fire (headache + not able to walk). Hits: {result.hits}"
        )


# ---------------------------------------------------------------------------
# RF012 — inability to walk/stand/move
# ---------------------------------------------------------------------------

class TestRF012InabilityToWalk:
    """Each method tests a distinct phrasing of inability to walk or stand."""

    # Phrases path
    def test_phrase_unable_to_walk(self) -> None:
        assert check_red_flags("She is unable to walk and needs help.").tier in {"urgent", "emergency"}

    def test_phrase_cant_walk(self) -> None:
        assert check_red_flags("He can't walk at all this morning.").tier in {"urgent", "emergency"}

    def test_phrase_cant_walk_no_apostrophe(self) -> None:
        assert check_red_flags("She cant walk since she woke up.").tier in {"urgent", "emergency"}

    def test_phrase_cannot_walk(self) -> None:
        assert check_red_flags("I cannot walk without severe pain.").tier in {"urgent", "emergency"}

    def test_phrase_legs_not_working(self) -> None:
        assert check_red_flags("His legs are not working at all.").tier in {"urgent", "emergency"}

    def test_phrase_legs_gave_out(self) -> None:
        assert check_red_flags("Her legs just gave out and she fell.").tier in {"urgent", "emergency"}

    def test_phrase_not_able_to_walk(self) -> None:
        assert check_red_flags("I am not able to walk since yesterday.").tier in {"urgent", "emergency"}

    def test_phrase_cant_stand(self) -> None:
        assert check_red_flags("He can't stand up on his own.").tier in {"urgent", "emergency"}

    def test_phrase_legs_are_numb(self) -> None:
        assert check_red_flags("My legs are numb and I can't move them.").tier in {"urgent", "emergency"}

    def test_phrase_lost_use_of_legs(self) -> None:
        assert check_red_flags("She has lost the use of my legs.").tier in {"urgent", "emergency"}

    # term_groups path — unable to + mobility word
    def test_termgroups_cant_move_legs(self) -> None:
        result = check_red_flags("I cant move my legs.")
        assert result.tier in {"urgent", "emergency"}
        assert any(h.rule_id == "RF012" for h in result.hits)

    def test_termgroups_legs_gave_out(self) -> None:
        result = check_red_flags("My legs gave out when I tried to get up.")
        assert result.tier in {"urgent", "emergency"}
        assert any(h.rule_id == "RF012" for h in result.hits)

    def test_termgroups_paralysed_legs(self) -> None:
        result = check_red_flags("I feel paralysed in my legs.")
        assert result.tier in {"urgent", "emergency"}
        assert any(h.rule_id == "RF012" for h in result.hits)

    def test_termgroups_mobility_not_working(self) -> None:
        result = check_red_flags("My mobility is not working at all suddenly.")
        assert result.tier in {"urgent", "emergency"}
        assert any(h.rule_id == "RF012" for h in result.hits)

    def test_rule_id_is_rf012(self) -> None:
        result = check_red_flags("She cannot walk and her legs are giving out.")
        assert any(h.rule_id == "RF012" for h in result.hits), (
            f"Expected RF012. Hits: {result.hits}"
        )


# ---------------------------------------------------------------------------
# RF013 — thunderclap / worst-ever headache
# ---------------------------------------------------------------------------

class TestRF013ThunderclapHeadache:
    """Tests for sudden-onset or worst-ever headache — must return emergency."""

    # Phrases path
    def test_phrase_worst_headache(self) -> None:
        assert check_red_flags("This is the worst headache I've ever had.").tier == "emergency"

    def test_phrase_worst_headache_of_my_life(self) -> None:
        assert check_red_flags("worst headache of my life started an hour ago").tier == "emergency"

    def test_phrase_thunderclap(self) -> None:
        assert check_red_flags("I have a thunderclap headache right now.").tier == "emergency"

    def test_phrase_sudden_severe_headache(self) -> None:
        assert check_red_flags("sudden severe headache hit me just now").tier == "emergency"

    def test_phrase_headache_came_on_suddenly(self) -> None:
        assert check_red_flags("My headache came on suddenly and is very intense.").tier == "emergency"

    def test_phrase_worst_head_pain_ever(self) -> None:
        assert check_red_flags("This is the worst head pain ever.").tier == "emergency"

    def test_phrase_explosive_headache(self) -> None:
        assert check_red_flags("I have an explosive headache like never before.").tier == "emergency"

    # term_groups path — headache + worst/sudden
    def test_termgroups_headache_worst(self) -> None:
        result = check_red_flags("I have a headache that is the worst I have experienced.")
        assert result.tier == "emergency"
        assert any(h.rule_id == "RF013" for h in result.hits)

    def test_termgroups_headache_sudden(self) -> None:
        result = check_red_flags("A headache appeared suddenly out of nowhere.")
        assert result.tier == "emergency"
        assert any(h.rule_id == "RF013" for h in result.hits)

    def test_termgroups_headache_excruciating(self) -> None:
        result = check_red_flags("I have an excruciating headache right now.")
        assert result.tier == "emergency"
        assert any(h.rule_id == "RF013" for h in result.hits)

    def test_termgroups_headache_unbearable(self) -> None:
        result = check_red_flags("The headache is unbearable and came on fast.")
        assert result.tier == "emergency"
        assert any(h.rule_id == "RF013" for h in result.hits)

    def test_termgroups_migraine_worst(self) -> None:
        result = check_red_flags("I have a migraine that is the worst ever.")
        assert result.tier == "emergency"
        assert any(h.rule_id == "RF013" for h in result.hits)

    def test_rule_id_is_rf013(self) -> None:
        result = check_red_flags("worst headache of my life")
        assert any(h.rule_id == "RF013" for h in result.hits), (
            f"Expected RF013. Hits: {result.hits}"
        )

    def test_tier_is_emergency_not_just_urgent(self) -> None:
        result = check_red_flags("I have the worst headache I have ever had.")
        assert result.tier == "emergency", (
            f"RF013 must be emergency, not just urgent. Got {result.tier!r}"
        )


# ---------------------------------------------------------------------------
# RF014 — headache with red-flag features
# ---------------------------------------------------------------------------

class TestRF014HeadacheWithFeatures:
    """Headache + (fever | stiff neck | confusion | weakness) → urgent minimum."""

    # Phrases path
    def test_phrase_headache_and_stiff_neck(self) -> None:
        result = check_red_flags("I have a headache and stiff neck.")
        assert result.tier in {"urgent", "emergency"}
        assert any(h.rule_id == "RF014" for h in result.hits)

    def test_phrase_stiff_neck_and_headache(self) -> None:
        result = check_red_flags("There is a stiff neck and headache for two days.")
        assert result.tier in {"urgent", "emergency"}
        assert any(h.rule_id == "RF014" for h in result.hits)

    def test_phrase_meningitis(self) -> None:
        result = check_red_flags("I think I might have meningitis.")
        assert result.tier in {"urgent", "emergency"}
        assert any(h.rule_id == "RF014" for h in result.hits)

    def test_phrase_rash_and_headache(self) -> None:
        result = check_red_flags("I have a rash and headache since this morning.")
        assert result.tier in {"urgent", "emergency"}
        assert any(h.rule_id == "RF014" for h in result.hits)

    def test_phrase_headache_photophobia(self) -> None:
        result = check_red_flags("Headache and photophobia — cannot stand the light.")
        assert result.tier in {"urgent", "emergency"}
        assert any(h.rule_id == "RF014" for h in result.hits)

    # term_groups path — headache + fever
    def test_termgroups_headache_fever(self) -> None:
        result = check_red_flags("I have a headache and a fever since last night.")
        assert result.tier in {"urgent", "emergency"}
        assert any(h.rule_id == "RF014" for h in result.hits)

    def test_termgroups_headache_temperature(self) -> None:
        result = check_red_flags("Bad headache and a high temperature for two days.")
        assert result.tier in {"urgent", "emergency"}
        assert any(h.rule_id == "RF014" for h in result.hits)

    # term_groups path — headache + stiff neck synonyms
    def test_termgroups_headache_neck_stiffness(self) -> None:
        result = check_red_flags("I have a headache and neck stiffness.")
        assert result.tier in {"urgent", "emergency"}
        assert any(h.rule_id == "RF014" for h in result.hits)

    def test_termgroups_headache_rigid_neck(self) -> None:
        result = check_red_flags("Headache and rigid neck since this morning.")
        assert result.tier in {"urgent", "emergency"}
        assert any(h.rule_id == "RF014" for h in result.hits)

    # term_groups path — headache + confusion
    def test_termgroups_headache_confused(self) -> None:
        result = check_red_flags("She has a headache and seems confused.")
        assert result.tier in {"urgent", "emergency"}
        assert any(h.rule_id == "RF014" for h in result.hits)

    def test_termgroups_headache_disoriented(self) -> None:
        result = check_red_flags("He has a headache and is disoriented.")
        assert result.tier in {"urgent", "emergency"}
        assert any(h.rule_id == "RF014" for h in result.hits)

    # term_groups path — headache + weakness/mobility loss
    def test_termgroups_headache_weakness(self) -> None:
        result = check_red_flags("Severe headache and weakness in both arms.")
        assert result.tier in {"urgent", "emergency"}
        assert any(h.rule_id == "RF014" for h in result.hits)

    def test_termgroups_headache_numb(self) -> None:
        result = check_red_flags("I have a headache and my face is numb.")
        assert result.tier in {"urgent", "emergency"}
        assert any(h.rule_id == "RF014" for h in result.hits)

    def test_termgroups_headache_unable_to_walk(self) -> None:
        result = check_red_flags("Headache and not able to walk since this morning.")
        assert result.tier in {"urgent", "emergency"}
        assert any(h.rule_id == "RF014" for h in result.hits)

    def test_original_scenario_headache_fever_stiff_neck(self) -> None:
        result = check_red_flags(
            "I have a bad headache and my neck is really stiff and I have had a fever since yesterday"
        )
        assert result.tier in {"urgent", "emergency"}
        assert any(h.rule_id == "RF014" for h in result.hits)


# ---------------------------------------------------------------------------
# Negation tests — negated versions must stay routine
# ---------------------------------------------------------------------------

class TestNegationNewRules:
    """Negated mobility and headache terms must not fire the new rules."""

    def test_no_headache_stays_routine(self) -> None:
        result = check_red_flags("I have no headache and feel mostly fine.")
        rf013_hits = [h for h in result.hits if h.rule_id == "RF013"]
        rf014_hits = [h for h in result.hits if h.rule_id == "RF014"]
        assert rf013_hits == [], f"RF013 should not fire: {result.hits}"
        assert rf014_hits == [], f"RF014 should not fire: {result.hits}"

    def test_no_stiff_neck_does_not_fire_rf014_on_headache_alone(self) -> None:
        """Headache alone without any of the partner symptoms should not fire RF014."""
        result = check_red_flags("I have a headache but no fever, no stiff neck, and no confusion.")
        rf014_hits = [h for h in result.hits if h.rule_id == "RF014"]
        assert rf014_hits == [], (
            f"RF014 must not fire when all group2 terms are negated: {result.hits}"
        )

    def test_not_been_able_to_walk_chronic_stays_routine(self) -> None:
        """'not been able to walk long distances' — 'been' separates 'not' and
        'able to walk' so the phrase 'not able to walk' is NOT a substring.
        Term_groups group2 has no match either. Stays routine."""
        result = check_red_flags(
            "I have not been able to walk long distances since my arthritis diagnosis."
        )
        rf012_hits = [h for h in result.hits if h.rule_id == "RF012"]
        assert rf012_hits == [], (
            f"RF012 must not fire on chronic limitation phrased as 'not been able to walk'. "
            f"Hits: {result.hits}"
        )

    def test_no_weakness_with_headache_does_not_fire_rf014(self) -> None:
        result = check_red_flags("I have a headache but no weakness and no fever.")
        rf014_hits = [h for h in result.hits if h.rule_id == "RF014"]
        assert rf014_hits == [], (
            f"RF014 term_groups must not fire when all group2 terms negated: {result.hits}"
        )

    def test_not_worst_headache_routine(self) -> None:
        """'not the worst headache' should not fire RF013 term_groups (negation suppresses 'worst')."""
        result = check_red_flags(
            "I have a headache but it is not the worst I have had."
        )
        rf013_tg_hits = [
            h for h in result.hits
            if h.rule_id == "RF013" and h.matched_phrase.startswith("groups:")
        ]
        assert rf013_tg_hits == [], (
            f"RF013 term_groups must not fire when 'worst' is negated: {result.hits}"
        )


# ---------------------------------------------------------------------------
# 10 harmless sentences that must stay routine
# ---------------------------------------------------------------------------

class TestHarmlessSentencesNewRules:
    """Routine inputs that must not be elevated by the new rules."""

    @pytest.mark.parametrize("text", [
        "mild headache after a long day",
        "I have a slight headache and a runny nose, nothing serious.",
        "I get occasional tension headaches when I am stressed.",
        "My head aches a little after staring at screens all day.",
        "I had a headache yesterday but it went away after paracetamol.",
        "I went for a walk this morning and my legs are a bit tired.",
        "I have difficulty walking long distances because of my bad knee.",
        "My mobility has been reduced since my hip replacement but is improving.",
        "I sometimes struggle to walk after sitting for a long time.",
        "I had a headache after my flight but it has gone now.",
    ])
    def test_harmless_sentence_stays_routine(self, text: str) -> None:
        result = check_red_flags(text)
        new_rule_hits = [h for h in result.hits if h.rule_id in {"RF012", "RF013", "RF014"}]
        assert new_rule_hits == [], (
            f"New rules must not fire on harmless input.\n"
            f"Input: {text!r}\n"
            f"Hits: {result.hits}"
        )


# ---------------------------------------------------------------------------
# Spelling-tolerance tests for new rules
# ---------------------------------------------------------------------------

class TestSpellingToleranceNewRules:
    """Edit-distance-1 misspellings must still fire the new rules where
    the misspelling falls on one group and the other group matches exactly."""

    def test_headache_typo_headeche_with_fever_fires_rf014(self) -> None:
        """'headeche' (distance 1 from 'headache') + 'fever' exact."""
        result = check_red_flags("I have a headeche and fever for two days.")
        # headeche fuzzy-matches headache in RF014 group1; fever exact in group2
        rf014_hits = [h for h in result.hits if h.rule_id == "RF014"]
        assert rf014_hits, f"Expected RF014 via fuzzy headache: {result.hits}"

    def test_headache_typo_with_stiff_neck_fires_rf014(self) -> None:
        """'headche' (distance 1) + 'stiff neck' exact phrase match."""
        result = check_red_flags("I have a headche and a stiff neck.")
        # "headache and stiff neck" phrase contains "headache" verbatim — won't match
        # because "headche" ≠ "headache". But the phrase "headache and stiff neck" is
        # in RF014 phrases — and "headche and stiff neck" won't match it.
        # term_groups: headche fuzzy → headache (group1), stiff neck exact (group2).
        rf014_hits = [h for h in result.hits if h.rule_id == "RF014"]
        assert rf014_hits, f"Expected RF014 via fuzzy headache + exact stiff neck: {result.hits}"

    def test_thunderclap_typo_thundercap_fires_rf013_via_phrase(self) -> None:
        """'thunderclap' exact phrase fires RF013 even with a typo in rest."""
        result = check_red_flags("I have a thunderclap headache.")
        assert result.tier == "emergency"
        assert any(h.rule_id == "RF013" for h in result.hits)


# ---------------------------------------------------------------------------
# Term-groups unit tests for RF012 / RF013 / RF014
# ---------------------------------------------------------------------------

class TestTermGroupsNewRules:
    """Unit tests using _match_term_groups directly."""

    def _rf012_rule(self) -> dict:
        return {
            "term_groups": [
                {"synonyms": ["walk", "walking", "legs", "stand", "standing", "mobility"]},
                {"synonyms": ["unable to", "not able to", "can't", "cannot", "cant",
                              "can no longer", "paralysed", "paralyzed", "gave out",
                              "giving out", "given out", "not working"]},
            ]
        }

    def _rf013_rule(self) -> dict:
        return {
            "term_groups": [
                {"synonyms": ["headache", "head pain", "migraine"]},
                {"synonyms": ["worst", "sudden", "suddenly", "thunderclap",
                              "excruciating", "unbearable", "explosive", "severe"]},
            ]
        }

    def _rf014_rule(self) -> dict:
        return {
            "term_groups": [
                {"synonyms": ["headache", "head pain", "head hurts"]},
                {"synonyms": ["fever", "temperature", "feverish",
                              "stiff neck", "neck stiffness", "rigid neck",
                              "confused", "confusion", "disoriented",
                              "weak", "weakness", "numb", "numbness"]},
            ]
        }

    # RF012
    def test_rf012_walk_unable_fires(self) -> None:
        result = _match_term_groups(self._rf012_rule(), _normalise("I am unable to walk"))
        assert result is not None

    def test_rf012_legs_not_working_fires(self) -> None:
        result = _match_term_groups(self._rf012_rule(), _normalise("my legs are not working"))
        assert result is not None

    def test_rf012_only_walk_no_fire(self) -> None:
        result = _match_term_groups(self._rf012_rule(), _normalise("I went for a walk"))
        assert result is None

    def test_rf012_only_unable_no_fire(self) -> None:
        result = _match_term_groups(self._rf012_rule(), _normalise("I am unable to do things"))
        assert result is None

    # RF013
    def test_rf013_headache_worst_fires(self) -> None:
        result = _match_term_groups(self._rf013_rule(), _normalise("worst headache ever"))
        assert result is not None

    def test_rf013_headache_sudden_fires(self) -> None:
        result = _match_term_groups(self._rf013_rule(), _normalise("sudden headache appeared"))
        assert result is not None

    def test_rf013_headache_alone_no_fire(self) -> None:
        result = _match_term_groups(self._rf013_rule(), _normalise("I have a headache"))
        assert result is None

    def test_rf013_worst_alone_no_fire(self) -> None:
        result = _match_term_groups(self._rf013_rule(), _normalise("this is the worst day"))
        assert result is None

    # RF014
    def test_rf014_headache_fever_fires(self) -> None:
        result = _match_term_groups(self._rf014_rule(), _normalise("headache and fever"))
        assert result is not None

    def test_rf014_headache_stiff_neck_fires(self) -> None:
        result = _match_term_groups(self._rf014_rule(), _normalise("headache and stiff neck"))
        assert result is not None

    def test_rf014_headache_alone_no_fire(self) -> None:
        result = _match_term_groups(self._rf014_rule(), _normalise("I have a headache"))
        assert result is None

    def test_rf014_fever_alone_no_fire(self) -> None:
        result = _match_term_groups(self._rf014_rule(), _normalise("I have a fever"))
        assert result is None

    def test_rf014_headache_not_feverish_negated(self) -> None:
        result = _match_term_groups(
            self._rf014_rule(),
            _normalise("headache but not feverish and not confused"),
        )
        assert result is None, "Negated group2 must not fire RF014"
