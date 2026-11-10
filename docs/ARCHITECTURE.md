# Architecture decisions

## First milestone boundaries

The first milestone is a local, independently deployable service. It supports source registration, manual post ingestion, deterministic extraction and review status changes.

## Security boundaries

- No marketplace user, wallet, escrow, payout or contract write access.
- Do not store Telegram session strings or bot tokens in source control.
- Treat collected content as untrusted input.
- Do not automatically publish collected posts.
- Preserve original text and source attribution.

## Database ownership

Yekis Intelligence owns its tables and migrations. It does not share Prisma models or write to marketplace tables. Cross-service integration is through authenticated APIs.

## Extraction strategy

The initial extractor is deterministic and clearly marked `extraction_method=rules`. An LLM provider can be added later behind a validated interface. Extracted values are candidates for human review, not verified facts.
