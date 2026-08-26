"""The contamination probe: is the quiz fair, and does the verdict mean anything?

The measurement only works if the quiz cannot be beaten by a bias. Real markets
rise about 59% of the time over a month, so an unbalanced quiz would hand any
subject that always answers "up" a 59% score and the harness would call it
memory. Half of these tests exist to stop that.
"""
from __future__ import annotations

import json

from printmoney.research import contamination as C
from printmoney.research.data import Bar, Series


def _series(symbol: str, rets, start=100.0, day0=1_500_000_000) -> Series:
    bars, price = [], start
    bars.append(Bar(ts=day0, open=price, high=price, low=price, close=price,
                    volume=1e6, raw_close=price))
    for i, r in enumerate(rets, 1):
        prev = price
        price *= 1.0 + r
        bars.append(Bar(ts=day0 + i * 86_400, open=prev, high=max(prev, price),
                        low=min(prev, price), close=price, volume=1e6,
                        raw_close=price))
    return Series(symbol=symbol, name=symbol, bars=bars)


def _wave(n: int, up: float = 0.01, down: float = -0.01, period: int = 60):
    """Something that genuinely rises and falls, so both pools fill."""
    return [up if (i // period) % 2 == 0 else down for i in range(n)]


def _universe(k: int = 4, n: int = 900):
    return [_series(f"M{i}", _wave(n, period=40 + 11 * i)) for i in range(k)]


# --------------------------------------------------------------------------- #
class TestTheQuizIsFair:
    def test_it_is_balanced_fifty_fifty(self):
        """Otherwise 'always up' scores the base rate and looks like memory."""
        qs = C.build(_universe(), n=40)
        assert C.spread(qs)["share_up"] == 0.5

    def test_always_answering_up_scores_exactly_a_coin(self):
        qs = C.build(_universe(), n=40)
        rep = C.grade(qs, {q.qid: "up" for q in qs}, cutoff="1900-01-01")
        assert C.Report.rate(rep.answered()) == 0.5

    def test_always_answering_down_also_scores_a_coin(self):
        qs = C.build(_universe(), n=40)
        rep = C.grade(qs, {q.qid: "down" for q in qs}, cutoff="1900-01-01")
        assert C.Report.rate(rep.answered()) == 0.5

    def test_the_real_base_rate_is_well_above_a_coin(self):
        """The number the balancing exists to neutralise."""
        rising = [_series("R", [0.0006] * 900)]
        assert C.base_rate(rising) > 0.9

    def test_flat_months_are_not_asked_about(self):
        qs = C.build(_universe(), n=40)
        assert all(abs(q.forward) >= 0.005 for q in qs)

    def test_the_same_seed_gives_the_same_quiz(self):
        a = C.build(_universe(), n=20, seed=5)
        b = C.build(_universe(), n=20, seed=5)
        assert [(q.symbol, q.day) for q in a] == [(q.symbol, q.day) for q in b]

    def test_a_different_seed_gives_a_different_quiz(self):
        a = C.build(_universe(), n=20, seed=5)
        b = C.build(_universe(), n=20, seed=6)
        assert [(q.symbol, q.day) for q in a] != [(q.symbol, q.day) for q in b]

    def test_the_blank_sheet_carries_no_answers(self):
        qs = C.build(_universe(), n=20)
        for row in C.blank(qs):
            assert "truth" not in row and "forward" not in row

    def test_the_printed_sheet_never_leaks_the_outcome(self):
        qs = C.build(_universe(), n=20)
        text = C.sheet(qs)
        for q in qs:
            assert f"{q.forward:+.1%}" not in text
        assert "truth" not in text


# --------------------------------------------------------------------------- #
class TestGrading:
    def _graded(self, answers, cutoff="2020-01-01"):
        qs = C.build(_universe(), n=40)
        return C.grade(qs, answers, cutoff=cutoff)

    def test_an_unanswered_question_is_not_counted_either_way(self):
        qs = C.build(_universe(), n=40)
        rep = C.grade(qs, {qs[0].qid: "up"}, cutoff="1900-01-01")
        assert len(rep.answered()) == 1

    def test_answers_are_case_and_space_insensitive(self):
        qs = C.build(_universe(), n=10)
        rep = C.grade(qs, {q.qid: f"  {q.truth.upper()} " for q in qs},
                      cutoff="1900-01-01")
        assert C.Report.rate(rep.answered()) == 1.0

    def test_the_split_is_on_the_stated_cutoff(self):
        qs = C.build(_universe(), n=40)
        mid = sorted(q.day for q in qs)[len(qs) // 2]
        rep = C.grade(qs, {q.qid: "up" for q in qs}, cutoff=mid)
        before, after = rep.split()
        assert before and after
        assert max(q.day for q in before) < mid <= min(q.day for q in after)

    def test_a_perfect_score_before_the_cutoff_is_flagged(self):
        qs = C.build(_universe(), n=40)
        rep = C.grade(qs, {q.qid: q.truth for q in qs}, cutoff="2099-01-01")
        assert rep.contaminated

    def test_a_coin_flip_before_the_cutoff_is_not_flagged(self):
        qs = C.build(_universe(), n=40)
        answers = {q.qid: (q.truth if i % 2 else
                           ("down" if q.truth == "up" else "up"))
                   for i, q in enumerate(qs)}
        rep = C.grade(qs, answers, cutoff="2099-01-01")
        assert not rep.contaminated

    def test_a_tiny_sample_is_never_flagged_however_good_it_looks(self):
        qs = C.build(_universe(), n=40)[:6]
        rep = C.grade(qs, {q.qid: q.truth for q in qs}, cutoff="2099-01-01")
        assert C.Report.rate(rep.answered()) == 1.0
        assert not rep.contaminated          # n < 20

    def test_scoring_well_after_the_cutoff_is_not_called_contamination(self):
        """Skill after the cutoff would be the interesting result, not this flag."""
        qs = C.build(_universe(), n=40)
        rep = C.grade(qs, {q.qid: q.truth for q in qs}, cutoff="1900-01-01")
        assert C.Report.rate(rep.answered()) == 1.0
        assert not rep.contaminated          # everything landed in "after"


class TestRoundTrip:
    def test_a_report_survives_being_saved_and_loaded(self, tmp_path):
        qs = C.build(_universe(), n=20)
        rep = C.grade(qs, {q.qid: q.truth for q in qs}, cutoff="2020-01-01",
                      model="test-model")
        path = C.save(rep, tmp_path / "c.json")
        back = C.load(path)
        assert back.model == "test-model" and back.cutoff == "2020-01-01"
        assert C.Report.rate(back.answered()) == 1.0

    def test_a_missing_file_loads_as_nothing(self, tmp_path):
        assert C.load(tmp_path / "nope.json") is None

    def test_the_saved_file_is_plain_readable_json(self, tmp_path):
        qs = C.build(_universe(), n=20)
        rep = C.grade(qs, {q.qid: "up" for q in qs}, cutoff="2020-01-01")
        blob = json.loads(C.save(rep, tmp_path / "c.json").read_text("utf-8"))
        assert blob["asked"] == 20 and "questions" in blob


class TestPublishedResult:
    """The committed record of this model sitting its own test."""

    def _payload(self):
        from printmoney.util import DATA_DIR

        return json.loads((DATA_DIR / "contamination.json").read_text("utf-8"))

    def test_the_result_is_committed_and_readable(self):
        p = self._payload()
        assert p["model"] and p["stated_cutoff"]

    def test_both_halves_have_a_real_sample(self):
        p = self._payload()
        assert p["before_cutoff"]["n"] >= 20
        assert p["after_cutoff"]["n"] >= 20

    def test_the_gap_between_the_halves_is_the_whole_finding(self):
        p = self._payload()
        before, after = p["before_cutoff"]["rate"], p["after_cutoff"]["rate"]
        assert before > 0.85          # near-perfect recall of its own training era
        assert after < 0.60           # and nothing at all afterwards
        assert before - after > 0.40

    def test_the_questions_are_kept_so_the_result_can_be_checked(self):
        p = self._payload()
        assert len(p["questions_before"]) == p["before_cutoff"]["n"]
        assert len(p["questions_after"]) == p["after_cutoff"]["n"]
        for q in p["questions_before"] + p["questions_after"]:
            assert q["answer"] and q["truth"] and "correct" in q
