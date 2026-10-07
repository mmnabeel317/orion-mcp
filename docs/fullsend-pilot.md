# Fullsend pilot: upstream mixed-model configuration

This pilot targets the personal fork `mmnabeel317/orion-mcp`. It configures
repository development automation; it does not change the models used by an
MCP client or change Orion-MCP's runtime behavior.

The model/runtime settings in [`.fullsend/config.yaml`](../.fullsend/config.yaml)
mirror the committed
[Fullsend configuration at `0d999c277`](https://github.com/fullsend-ai/fullsend/blob/0d999c2772fadc47cba4662594fd754cc4b552f2/.fullsend/config.yaml),
not that checkout's conflicted working file. The workflow calls are pinned to
that same revision, which includes support for OpenAI review sub-agents under
a Vertex parent.

Repository-specific differences are deliberate:

- Keep the existing GitHub App roles, Vertex project/WIF settings, and issue
  creation allowlist. Do not copy Fullsend's QualityFlow registration or its
  organization-wide issue-creation permissions.
- Preserve the human coding gate through [the derived triage harness](../.fullsend/triage.yaml).
  `TRIAGE_AUTO_CODE: "off"` is set for both runner and sandbox. Triage may
  comment and label issues, but must not promote them to `ready-to-code`.
  Existing routing labels and explicit `/fs-code` commands can still start code.
- Match upstream's disabled retro agent while duplicate findings are being
  reduced. The retro App role remains installed.
- Use a static OpenAI API key rather than OpenAI WIF. GCP WIF remains required
  for the Claude and Gemini parts of the pilot.

## Inference prerequisites

In Vertex AI Model Garden, enable these **exact API model IDs** for the
existing inference project and `global` region:

- `claude-sonnet-5-5` — triage/fix and the Pi review coordinator.
- `claude-opus-5-5` — code and the Pi review challenger.
- `gemini-3.8-flash` — Gemini work in the mixed-model pipeline.

One-output-token requests on 2026-10-07 returned HTTP 404 (not found or no
access) for the two Claude 5.5 IDs and HTTP 200 for Gemini 3.8 Flash. Claude
Sonnet 4.6, Opus 4.6, and Haiku 4.5 also answered successfully. Those checks
used local ADC, not the GitHub Actions identity; a CI run must still verify
access through GCP WIF. If 5.5 is not offered in this project's Model Garden,
check availability with the platform team rather than assuming IAM is the
cause or silently substituting a different generation.

Add the OpenAI key to the fork at **Settings → Secrets and variables → Actions
→ New repository secret**, named **`FULLSEND_OPENAI_API_KEY`**. Its OpenAI
project must have access to `gpt-6.1-sol`, the exact model selected for GPT
review personas. A ChatGPT subscription alone is not API access. Never store
or paste the key in this repository, an issue, a workflow input, or chat.

Both workflow callers forward that secret. Fullsend exports it as
`OPENAI_API_KEY` on the runner and configures the OpenShell provider; the real
key is not passed into the sandbox. No OpenAI WIF enrollment or
`inference.openai` block is needed. Keep these Actions variables unset for
the static-key route: `FULLSEND_OPENAI_AUDIENCE`,
`FULLSEND_OPENAI_IDENTITY_PROVIDER_ID`, and
`FULLSEND_OPENAI_SERVICE_ACCOUNT_ID`. WIF takes precedence when configured,
and a partial trio fails rather than falling back to the key.

Use a project-scoped key with only the required API permissions, rotate it,
and apply approved budget/rate limits. This pipeline sends review context to
both OpenAI and Google/Anthropic; confirm that this is acceptable for the
repository. Budget alerts are not hard spending caps.

## Inspect the effective setup

Do not treat a static inventory of upstream agents or skills as permanent.
From the rebased Fullsend source checkout, inspect this repository's configured
entries with the current CLI:

```sh
go run ./cmd/fullsend agent list --fullsend-dir ../orion-mcp/.fullsend
```

Discover upstream harnesses, skills, and persona definitions from
[`fullsend-ai/agents`](https://github.com/fullsend-ai/agents) at the revision
reported by the run. The derived triage harness records its base SHA and
integrity hash explicitly. Other built-in harnesses are resolved by Fullsend;
record their resolved agents revision when comparing runs.

Before testing, check Actions variables for `FULLSEND_MODEL`,
`FULLSEND_RUNTIME`, `FULLSEND_EFFORT`, runtime-specific model settings, and
role-prefixed equivalents. They override `.fullsend/config.yaml` and can
invalidate the comparison.

## Controlled retest

1. Review and merge these configuration/workflow changes into the fork's
   default branch. Pull-request events use trusted base-branch configuration;
   a historical run or an unmerged config PR does not exercise the new setup.
   The local `main` was diverged from `origin/main` during preparation; resolve
   that Git state without discarding local commits before publishing.
2. Enable the required Claude models and add `FULLSEND_OPENAI_API_KEY`.
3. On a small, open pilot issue, use `/fs-triage`. Confirm the resolved Sonnet
   model, valid `agent-result.json`, and no automatic `ready-to-code` promotion
   or code run.
4. Use `/fs-code` explicitly when ready. Check the actual code diff and run
   its offline tests; workflow success alone does not establish correctness.
5. On an open same-repository PR, use a **fresh** `/fs-review` comment. Confirm
   `runtime: pi`, the Sonnet coordinator, and the configured GPT/Gemini/Opus
   persona models in Bootstrap logs. Check whether the review finds actionable
   problems and whether the challenger rejects weak findings.
6. Record issue/PR/run links, Fullsend and agents revisions, actual model IDs,
   latency, tokens, cost, retries, tool calls, and result quality. Pi's
   `metrics.json` includes `per_model_usage`; compare child costs as well as
   the coordinator's cost.

Skip prioritize until its GitHub App has `organization_projects:write` if the
previous permission error still occurs. Changing models cannot fix that App
permission failure. Retro is intentionally disabled.

For an emergency stop, disable the workflow and cancel active jobs. Merging
`kill_switch: true` prevents future dispatches but does not cancel active runs.
