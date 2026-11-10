# IceNet source overlay

`multiple-nic-and-loopback.patch` records the two IceNet source changes used by
the four passing NIC representatives. Its base is the parent repository's pinned
IceNet commit `61d2925b9d57df189ab8d801eb81185040251249`.

The patch adds the two-interface configuration and handles the target NIC port
composition needed by the FireSim integration. The parent fork stores the patch
so reproducing these changes does not require unpublished nested working-tree
edits or a new third-party fork.

`scripts/apply-firesim-circt-patches.sh` verifies the base revision, checks patch
applicability and applies it idempotently. The normal submodule initialization
script calls it automatically. The resulting IceNet submodule has an intentional,
deterministic source delta; do not discard that delta before building the targets.
