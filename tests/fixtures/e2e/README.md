# End-to-end fixture repository

This directory is a self-contained source tree for local launcher acceptance
tests. Tests copy it to an isolated temporary Git repository before use.

`notebooks/acceptance.ipynb` contains tagged cells for:

- a dependency imported from the declared environment;
- deterministic success;
- a controlled Python failure;
- a cancellable non-terminating execution;
- oversized text output;
- binary output metadata.

The non-terminating cell is never run by an unbounded notebook-wide test. The
execution-broker acceptance test starts it explicitly and applies its configured
timeout/cancellation contract.

`jupyter_server_config.py` contains a public, test-only token for the local
container smoke test. Production launches generate an owner-only config with a
fresh random token and never expose it through container arguments or
environment variables.
