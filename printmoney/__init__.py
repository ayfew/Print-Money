"""
printmoney - a probability-surface arbitrage engine for Polymarket BTC markets.

The engine does NOT try to predict whether Bitcoin goes up or down.  It reads the
*whole strip* of BTC price markets on Polymarket, turns them into an implied
probability distribution over the settlement price, builds an independent model
distribution from real BTC tape, and buys/sells only where the two disagree by
more than fees + spread.  Positions are chosen by a linear program that maximises
expected value subject to a hard floor on the worst case across every possible
settlement outcome - a self-hedging grid, solved rather than hand-waved.

Default mode is paper trading.  Live trading is off unless you explicitly turn it
on and supply your own credentials.
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
