# infra/terraform — DEPRECATED

This is the original single-file Azure setup (App Service plus Postgres, azurerm 3.x). It is superseded by [`infra/azure/`](../azure/README.md), which splits infrastructure into a persistent `bootstrap` root and a per-session `envs/dev` root and follows [`docs/design/infra.md`](../../docs/design/infra.md).

Do not extend this directory. It stays until Stephen confirms it can be deleted.
