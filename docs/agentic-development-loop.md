# Engineering Loop

The Hyrule Engineering Loop has moved out of `network-operations` and now lives
in its own repository:

- <https://github.com/AS215932/engineering-loop>

This repository no longer carries the loop runtime package, prompt/skill
library, model policy, Pi `/loop` extension, or loop test suite. Use the
`engineering-loop` repository for architecture, CLI usage, model routing, Pi
integration, and acceptance-gate documentation.

`network-operations` keeps only infrastructure deployment state for the
dedicated `loop` VM under `ansible/` until that service is moved or
decommissioned.
