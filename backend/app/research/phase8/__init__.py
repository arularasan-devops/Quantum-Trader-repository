"""Phase 8 — why today's production signals won or lost. RESEARCH ONLY.

Phase 8 adds questions to the Phase 7 pipeline and reuses that pipeline for
everything else. It owns no path arithmetic and no policy simulation: every MFE,
MAE, give-back, chase and exit number it reports comes from
``app.research.phase7.paths`` / ``policies``, so a Phase 8 table can never
disagree with the Phase 7 table it was derived from. The extra exit levels and
trail widths in ``capture`` are new *names* in the existing Phase 7 policy
grammar, executed by the Phase 7 code.

``findings``      the evidence label every block carries (OBSERVATION /
                  HYPOTHESIS / REQUIRES MORE DATA / VALIDATED), from sample size
``calibration``   does the displayed confidence predict anything? monotonicity
``spread``        real bid/ask: what execution friction costs, gross R vs net R
``causes``        one primary cause per resolved trade, ranked; wins too
``expiry``        contract expiry from the tradingsymbol; expiry vs non-expiry
``capture``       profit capture and give-back per exit policy, net of the book
``continuation``  hold or protect, judged only on information available then
``contract``      what the chain said about the leg bought (descriptive only)
``gates8``        the extra acceptance gates the expiry study needs

Nothing here is imported by the production decision path, nothing here can place
an order, and no threshold in the production engine is read for anything other
than reporting what it did.
"""
