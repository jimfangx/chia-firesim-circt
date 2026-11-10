The formal adapter compares F2 AXI output payloads while the producer's `valid`
is asserted, including every stalled cycle. It always compares `valid`, `ready`,
clock/reset and other outputs. Only the explicit AXI interface/channel/field
allowlist in `jasper_contract.py` authorizes qualification. Other similarly named
outputs stay strict. The staged flow rejects a failed conditional mapping;
post-run configuration must match the complete manifest. Preconditions require
actual covered results; unreachable/bounded/unprocessed obligations do not pass.

No RAM is cleared. Normal policy is `zero_out_dram: false`; this code changes no
runtime configuration. The contract records that RTL alone does not establish
the image's loaded regions. A live read of unwritten memory must denote the same
abstract initial logical memory on both sides, with a checked representation
relation. It cannot be replaced by two independently arbitrary live samples.
Masked writes must preserve the relation for every byte, and initialized/loaded
bytes retain their specified contents. Producer-valid qualification never masks
any live memory data.

Production runs disable automatic reset-X and undriven mappings and currently
add no initial-memory mappings or classified X exceptions. Incomplete X/reset signoff, unclassified sources, helper/cutpoint
failures and unknown memory relations remain blocking. The scoped relation audit
can recognize a separately established, hash-bound construction certificate: it
requires the exhaustive global initial-map list, no unmatched X/undriven source,
and proven memory-preservation properties. The control runner supplies such a
certificate only after checking both tiny fixture sources against their exact
construction. No production memory is classified by the tiny-fixture rule.

`jasper_contract_controls.py` is a bounded supporting experiment, separate from
CHIA's baseline and feature nodes. Its fixture contains one shared symbolic
initial word, one explicitly initialized word (`0xcafe`), byte-masked writes,
one-cycle read responses and backpressure. Positive checks permit different
invalid payloads. Negative checks corrupt written/initialized data, valid/ready,
transaction delivery/order, byte masks and the shared live unwritten relation.
LEC requires all actual state cutpoint obligations; SEC negative controls require
boundary counterexamples. A timeout, setup error or bounded result is incomplete.
An X exception applies only to the exhaustive classified shared initial word,
related by one explicit, checked initial mapping; automatic mappings stay off;
the raw Jasper X category is retained as incomplete in evidence.

The installed Jasper 2026.06p002 SEC User Guide pp.53–54 documents Basic as a
sequential strategy on original targets without auxiliary mappings. The Proof
strategy requires at least four jobs (actual error EMTR415). Production dispatch
uses Basic when the resource cap permits fewer than four jobs and Proof at four
or more. This preserves the real resource cap and separate LEC/SEC modes. Basic
results must still be fully proven; a long bounded exploration does not pass.
The tiny controls may use four jobs under an 8 GiB aggregate RSS guard for Proof;
this does not authorize a four-job baseline job on a smaller memory/license cap.
