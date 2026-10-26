---
type: repository
status: active
owner_repo: MohammedAbdelshafy/agentops-command
product_name: Workflow Checks
source_of_truth: https://github.com/MohammedAbdelshafy/agentops-command
---

# Workflow Checks CONTEXT

One job: keep a simple record of automated task runs and show which completed, failed or needs attention.

## Inputs
- Usage and commands: [README](README.md)
- Implementation: [agentops.py](agentops.py)
- Tests: [tests](tests/)

## Process
1. Register a uniquely named run in the local store.
2. Append timestamped logs and optional usage/cost details.
3. Set the final run state when the task ends.
4. Generate a summary from recorded data.
5. Keep secrets and customer data out of logs.

## Output
A run record and a readable Markdown or JSON summary.

## Check
Run the repository's tests and verify that duplicate run IDs, unknown IDs, and malformed state updates fail clearly.
