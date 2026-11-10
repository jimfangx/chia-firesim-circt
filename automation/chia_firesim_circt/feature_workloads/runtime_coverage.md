# Enabled Linux runtime checks

These workloads run inside a private Linux rootfs and complete before explicit
poweroff. The adapter separately requires a Linux banner, userspace boot marker,
actual kernel power-down and a successful FireSim driver/script exit. DRAM zeroing
remains disabled. Every memory checksum starts from bytes written by the workload;
no checker assumes arbitrary unwritten RAM is equal or zero.

| Representative | Required observations |
| --- | --- |
| FAME5 four logical tiles | Four workers pinned to distinct guest CPUs 0–3; five independent software epochs; private initialized/written 4093-word memory; deterministic arithmetic/memory digests recomputed by the host checker. |
| AutoCounter and Print | Actual emitted domain monitor identity; nonempty CSV sampling; rational cycle progress; separately derived target-reset and source-acquisition prefixes; active accumulation; narrow identity wrap; ordered Print payloads from the declared first unmasked event; successful guest arithmetic and written-memory work. |
| Multiple domains/models | The same CSV/Print checks for every domain declared by emitted metadata, with at least two domains required by the execution config. Production model-count/clock contracts are checked separately from the Linux observations. |
| Normal single NIC | 128 independently reconstructed variable-length packets; guest response payloads and sequence checks; actual host-peer request/reply logs; joined peer; guest worker launch/join and zero exit. |
| Two independent NICs | The normal-NIC checks for both widget identities and distinct guest interfaces; all guest workers launched before any join; independent packet ledgers and joined host peers. |
| Compile-time NIC loopback | 128 exact outgoing-frame loopback packets, correct widget identity and sequence; emitted bridge mode checked separately; guest worker launch/join. |
| Per-widget runtime loopback | Concurrent normal and loopback guest workers; correct per-widget direction, interface and complete packet ledgers; actual host peer only for the normal widget; all workers joined successfully. |

`observed_checks` returned by each checker describes what the actual output
establishes. These checks do not by themselves establish hardware-reset behavior,
arbitrary channel stalls/backpressure, arbitrary masked-memory geometries, model
sharing correctness or complete formal equivalence. Those require the retained
production contracts, supporting RTL tests, matched SFC comparisons and Jasper
proofs. Native execution, checker fixtures and negative controls validate the test
machinery; they are never labeled as FPGA execution or full feature acceptance.
