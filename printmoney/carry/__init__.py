"""Delta-neutral funding carry.

The one strategy in this repository that is not a bet on anything.

A perpetual future has no expiry, so the exchange keeps its price tied to spot by
making one side pay the other every eight hours.  When the funding rate is
positive, longs pay shorts.  Hold the coin on spot and short the same size of
perpetual, and the position has no exposure to the price at all - it just
collects that payment.

That is a real, published, contractual cash flow, and it is the honest answer to
"leave a computer running and have income arrive".  What this module mostly does
is tell you how *small* that cash flow is, and which of the enormous headline
rates are traps rather than opportunities.
"""
