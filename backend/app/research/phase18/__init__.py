"""Phase 18 — the Closing Auction Session (CAS) research and paper strategy.

A separate strategy family, ``strategy = CAS``, that observes the 15:10–15:30
window, records both option sides across an OTM ladder at measured timestamps,
runs a paper book priced at the ask on entry and the bid on exit, and reports
what the window is actually worth after costs.

Three properties hold across every module here and are the reason the package is
isolated rather than folded into the live engine:

* nothing in it returns a value the production decision path reads;
* nothing in it imports a broker client, and :mod:`app.research.phase18.safety`
  proves that by reading this package's own source;
* every result is separated into THEORETICAL, BID-EXECUTABLE and NET, because
  the headline that motivated the phase — Rs 1 lakh to Rs 44 lakh in five
  minutes — is a theoretical number and the whole question is what survives the
  other two columns.

CAS began on 3 August. No part of the five-year replay contains it, so the
historical pool is labelled ``PRE_CAS_REGIME`` and is never used as CAS
evidence. Until the sample in §26 exists, the honest verdict is
``REQUIRES_MORE_DATA`` and the modules say so rather than ranking noise.
"""
