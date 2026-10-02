# core

Resource group, Log Analytics (0.5 GB/day cap), and the shared app identity with `Key Vault Secrets User` on the bootstrap vault.

The vault and the budget live in `bootstrap`, not here, so `destroy` of `envs/dev` leaves them alone ([design](../../../../docs/design/infra.md)).
