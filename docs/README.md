# Yekis Intelligence Documentation

Start with the guide that matches your task.

| I need to... | Read |
|---|---|
| Understand the system end to end | [Project Guide](PROJECT_GUIDE.md) |
| Call or integrate with the API | [API Reference](API_REFERENCE.md) |
| Run, deploy, monitor, or troubleshoot it | [Operations Runbook](OPERATIONS.md) |
| See what was built and the evidence for it | [Milestone Execution Audit](audit/MILESTONE_EXECUTION.md) |
| Review the original architectural intent | [Architecture Decisions](ARCHITECTURE.md) |

## Reading order for a new contributor

1. [Project Guide](PROJECT_GUIDE.md) — purpose, boundaries, architecture, data model, workflows, configuration, development workflow, and known limitations.
2. [API Reference](API_REFERENCE.md) — authentication, every route, request/response examples, review transitions, publication states, and error codes.
3. [Operations Runbook](OPERATIONS.md) — local and Docker setup, migrations, collector/publisher runbooks, monitoring, backup, and incident playbooks.

## Documentation status

The files under [`docs/audit/`](audit/) **other than [`MILESTONE_EXECUTION.md`](audit/MILESTONE_EXECUTION.md)** are the **historical baseline** written before the six implementation milestones. They intentionally record the "before" state (missing auth, missing collector, missing publisher, etc.). Do not use them as a description of the current system.

The current sources of truth are:

- this documentation set;
- [`docs/audit/MILESTONE_EXECUTION.md`](audit/MILESTONE_EXECUTION.md) for executed evidence and known gaps;
- the code and tests.

Where documentation and behavior disagree, the tests and code win. Update the relevant guide in the same change that alters behavior.

## Documentation maintenance rules

- Keep the [Project Guide](PROJECT_GUIDE.md) architecture and configuration tables current whenever a component or setting changes.
- Keep the [API Reference](API_REFERENCE.md) in sync with route decorators, auth dependencies, request/response models, and error codes.
- Keep the [Operations Runbook](OPERATIONS.md) current with Docker Compose, migration, and release procedures.
- Do not rewrite historical audit findings; append status notes instead.
- Never put real API keys, Telegram credentials, session files, or `.env` contents in documentation examples.
