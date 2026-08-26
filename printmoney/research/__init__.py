"""Cross-asset research: does a trading idea survive its own costs?

Every strategy in this package has to answer one question before anything else,
and it is not "does it predict".  It is:

    gross edge per trade  >  cost per trade ?

The cost side is knowable in advance and is not a matter of opinion.  The edge
side almost never clears it.  This package exists to make that comparison
automatic, so an idea gets killed by arithmetic in ten seconds instead of by the
market over six months.
"""
