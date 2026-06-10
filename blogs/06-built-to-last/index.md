---
title: "Built to Last"
subtitle: "Infrastructure as code for AI systems: Terraform/OpenTofu, Azure, and the auth-at-infra pattern"
author: Stephen Henderson
date: "TBD"
post_number: "06"
description: "Post 6 of Anchoring AI: walking through the Terraform configuration for Azure App Service + PostgreSQL Flexible Server, the Entra Easy Auth pattern, and OpenTofu as an open-source Terraform alternative."
prev_url: "../05-real-data-in/"
prev_title: "Real Data In"
next_url: "../07-the-developers-toolkit/"
next_title: "The Developer's Toolkit"
---

> **Status: Placeholder.** This post is planned. The outline and key concepts below describe what it will cover.

---

## What This Post Covers

The code works locally. Getting it to production involves infrastructure decisions that compound quickly for AI systems: Where does the model API key live? How is authentication handled? What does the vector database look like at scale? This post walks through `infra/terraform/main.tf` — the Azure infrastructure for this system — and explores OpenTofu as an open-source Terraform alternative that's increasingly relevant for enterprises nervous about HashiCorp's licensing change.

The "auth at infra layer" pattern is the most architecturally interesting piece here: Microsoft Entra Easy Auth intercepts every HTTP request before it reaches the Python application. The Python code contains zero authentication logic. That's a deliberate choice with significant security and maintenance implications.

---

## Key Concepts

- **Azure App Service** — why it's the right host for an MCP server that runs Python; the Linux stack; how stdio becomes HTTP
- **Azure PostgreSQL Flexible Server** — managed pgvector in production; the difference from the local Docker container
- **Terraform `azurerm` provider** — the main resources, how they relate, what `terraform apply` actually does
- **Microsoft Entra Easy Auth** — intercepting requests at the infrastructure layer; OAuth 2.0 Bearer token validation without a single line of Python auth code
- **OpenTofu** — the open-source Terraform fork; what changed with HashiCorp's BSL license; when you'd choose OpenTofu over Terraform
- **Secrets management** — where `OPENAI_API_KEY` and `DATABASE_URL` live in App Service; why they're not in `main.tf`
- **Randomized passwords** — why `random_password` in Terraform is the right pattern for database credentials

---

## Planned Outline

1. **Why IaC matters more for AI systems** — reproducibility, auditability, the API key problem
2. **The Azure architecture** — App Service + PostgreSQL Flexible + Entra: how the three services relate
3. **Walking `main.tf`** — resource by resource; what each block does and why
4. **The Easy Auth pattern** — how Entra intercepts requests before they reach Python; what the Python app sees (and doesn't see); why this is better than auth middleware in the app
5. **Secrets in App Service settings** — how environment variables become App Service configuration; what to never put in Terraform files
6. **Running it** — `terraform init`, `terraform plan`, `terraform apply`; what to expect; estimated costs
7. **OpenTofu** — what it is, why it exists, how to swap from Terraform with near-zero friction; the `tofu` CLI
8. **What's missing** — no staging environment, no modules, no tfvars split — acceptable trade-offs for a portfolio project, not for production at scale

---

## Code Changes for This Post

Possibly: add OpenTofu-compatible aliases or a `.terraform.lock.hcl` comment for OpenTofu users. TBD.

---

## Outstanding Questions / TBD

- Include estimated Azure costs for the deployed configuration?
- Show `terraform plan` output?
- Add a `staging.tfvars` as an example of environment separation?
- Explore Azure Container Apps as an alternative to App Service for this workload?
