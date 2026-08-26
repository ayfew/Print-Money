"""Market making against Polymarket's liquidity reward programme.

This is a different business from the rest of the codebase, and a more honest one
to describe.  The arbitrage engine looks for prices that are wrong; most days
there are none, and it says so.  A market maker is not paid for being right - it
is paid a published, contractual amount for keeping two-sided quotes on the book,
and it pays for that income in adverse selection when somebody who knows more
lifts one of those quotes.

So the income is real and measurable in advance.  The cost is real too, and it is
not measurable in advance.  Everything in this package exists to keep the second
number smaller than the first.
"""
