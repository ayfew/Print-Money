"""Cross-asset research: does a trading idea survive its own costs?

Every strategy in this package has to answer one question before anything else,
and it is not "does it predict".  It is:

    gross edge per trade  >  cost per trade ?

The cost side is knowable in advance and is not a matter of opinion.  The edge
side almost never clears it.  This package exists to make that comparison
automatic, so an idea gets killed by arithmetic in ten seconds instead of by the
market over six months.

The morning brief is built in two layers, deliberately separated.  ``brief.py``
answers *what happened* and stops there; ``decide.py`` answers *what deserves
attention today, and what does not*, which is the question a reader actually
has.  Between them sit ``events.py`` (scheduled releases, each carrying the
measured size of its own historical effect), ``sources.py`` (the allowlist that
makes an uncited claim a crash rather than a footnote) and ``scorecard.py``
(a running, committed, publicly auditable record of how often the brief's own
calls turned out to be right - including the ones that did not).
"""
