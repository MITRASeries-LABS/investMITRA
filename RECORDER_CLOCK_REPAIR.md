# Auditable recorder clock handling

## Failure and scope

A Windows clock adjustment moved wall time backward by 1.3063947 seconds. The
last stored receipt was 10:55:07.794395 IST and the next receipt was
10:55:06.736008 IST. The recorder rejected the latter as nonmonotonic and stopped
collecting research data while paper execution continued.

This change retains raw observations across clock changes. It does not change
strategy time, entry gates, risk limits, execution timing, exchange timestamps
or order behavior. It does not repair or backfill an existing incomplete tape.

The engine brackets its existing wall-clock read with monotonic samples. Raw
wall time W and monotonic bounds [B,A] imply an offset interval
[W-A-1 microsecond, W-B+1 microsecond]. Adjacent intervals provide bounded
evidence of offset changes; scheduling jitter widens the evidence instead of
being mistaken for a clock step. The existing 250 ms material-change threshold
has not been widened. The engine still calls its strategy clock once and uses
that same returned value.

Each frame includes contiguous sequence, monotonic bounds, elapsed and raw-wall
deltas, offset-change bounds, and separate clock-change, sampling-uncertainty
and raw-wall-regression flags. A delayed callback after a completed pair does
not distort the evidence. A 251 ms stall inside the pair is uncertain rather
than proof of a clock change. Uncertain samples, material clock changes and raw
wall regressions quarantine portfolio replay while capture continues for audit.

## Recorder integration

The upstream baseline is PR28, commit
`09eb1044ba8e47efe588e1b745165c03d3578604`. The verified local installation also
contained a bounded segmented recorder that was not yet tracked upstream.
This PR includes that necessary dependency (`capture_segments.py`), its
capture integration, and segment/legacy compatibility tests. Unrelated local
breadth, diagnostics, adaptive research and helper changes are excluded.

The reviewed local repair was identified by patch SHA-256
`704e21fda2a5d654389cd03fb81d627e7776d4938037f8b955e9e57e35f12d36` and passed 501
tests on its integrated local baseline. This PR has a narrower baseline and
test scope: upstream regression tests plus recorder and clock regressions.
The three recorder/replay modules and new clock tests match the final installed
repair; the engine receives only its paired-clock hook. The segment tests use
the standard-library IST timezone instead of an unrelated adaptive module.

The segmented writer preserves bounded queue/byte/disk limits, per-segment
checksums and contiguous record ordering. Rejected oversized or queue-limited
frames must not consume accepted-frame sequence numbers: producer clock state
is committed only after successful queue admission. Loss is still recorded and
disqualifies a complete replay; later observations remain available for audit.

The writer validates small companion ordering metadata without a redundant
full-frame JSON parse. The reader binds persisted payloads to that metadata,
checks ordering, checksums, dates and final totals. Replay returns results only
after validation finishes. Legacy captures remain readable with their exact
captured source revision. New captures use `paired-arrival-v2`; unsupported
clock schemas and source mismatches fail closed.

## Verification and bounded performance

Regression coverage includes the exact observed rollback, forward changes,
variable jitter, a 251 ms stall inside and after sampling, wide uncertain
brackets, concurrent callbacks, segment rollover, corrupt order/payload data,
drop-then-recover behavior, legacy compatibility, clean replay equivalence and
the engine's unchanged wall-clock value/call count. CI runs the applicable
suite on Python 3.12 and 3.13 on Windows and Ubuntu.

An offline writer comparison used the same 200 frames (10,381,850 raw bytes) in
three original/revised pairs, including manifest creation, compression,
checkpoints and sealing:

| Pair | Original ms | Revised ms | Elapsed ratio | Original frames/s | Revised frames/s |
|---|---:|---:|---:|---:|---:|
| 1 | 149.23 | 164.08 | 1.099 | 1340 | 1219 |
| 2 | 147.92 | 155.56 | 1.052 | 1352 | 1286 |
| 3 | 155.16 | 198.35 | 1.278 | 1289 | 1008 |

Compressed size increased from 2,496,735 to 2,526,365 bytes (1.19%). These
measurements precede the independently reviewed producer-state admission fix,
which adds a shallow state copy and was regression-tested but not separately
benchmarked. Source capture files and personal session records are not included.
These desktop measurements do not establish full-market latency, callback
contention or queue-saturation guarantees.

## Limits and operation

A change hidden inside a wide sampling interval cannot always be distinguished
from scheduling delay. The recorder preserves that uncertainty and replay is
refused. Subthreshold changes and changes that reverse completely between
observations are not exhaustively detectable. Offset evidence does not identify
an OS cause or prove an instantaneous step rather than accumulated clock slew.

Deployment requires a stopped, flat session and a fresh capture with matching
source hashes. There is no hot swap or resume of an incomplete session. No
strategy-clock substitution, OS-clock modification, fabricated market data or
automatic trading startup is introduced.
