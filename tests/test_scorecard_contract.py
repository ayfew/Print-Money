"""Check the evidence-selection contract independently of generated sample sizes."""
import pytest

from printmoney.research import scorecard


@pytest.mark.parametrize("n,basis", [(29,"backtest"),(30,"live"),(84,"live")])
def test_prospective_headline_includes_losing_live_calls_at_the_threshold(n,basis):
    summary = {"backtest":{"n":1000,"hits":800,"rate":.8},
               "live":{"n":n,"hits":int(n*.45),"rate":.45}}
    selected = scorecard.headline(summary)
    assert selected["basis"] == basis
    assert selected["n"] == summary[basis]["n"]
    assert selected["rate"] == summary[basis]["rate"]
