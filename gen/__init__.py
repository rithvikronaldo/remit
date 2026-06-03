"""Phase 0 — synthetic data generator.

Emits reproducible, conservation-valid triples: an X12 835 file, an optional PDF
EOB of the same data, and a ground-truth JSON (the golden-set seed). Because Remit
cannot touch real PHI, this generator *is* the data supply and the test oracle.
"""
