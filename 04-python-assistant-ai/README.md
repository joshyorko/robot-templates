# Python AI Chat Assistant

This template is for a local, human-operated assistant robot. It opens an
RPA Assistant window, sends prompts to OpenAI only when the operator clicks
`Send`, and keeps conversation state only for that local run.

It does not publish, delete, update, or sync business records. It needs an
OpenAI API key for the real assistant and credential check. The no-secret smoke
task only verifies the RCC/Python task wiring and writes a local artifact.

## Local Secret Boundary

Use either a shell environment variable or a local `.env` file in this template
root. The `.env` file is ignored by git.

```zsh
export OPENAI_API_KEY="your-api-key"
export OPENAI_MODEL="gpt-4.1"
```

or:

```zsh
printf 'OPENAI_API_KEY=your-api-key\nOPENAI_MODEL=gpt-4.1\n' > .env
```

Do not commit `.env`, API keys, task logs that reveal secrets, or generated
outputs that contain sensitive prompts.

## RCC Commands

Run these from the `04-python-assistant-ai` directory.

No-secret local smoke:

```zsh
rcc run -t SmokeLocal --dev
```

Expected result:

- Does not call OpenAI.
- Does not require `OPENAI_API_KEY`.
- Writes `output/local-smoke.json`.
- Reports whether a local key is present without printing the key.

Credential check:

```zsh
rcc run -t TestKeys --dev
```

Expected result:

- Requires `OPENAI_API_KEY` from the shell or local `.env`.
- Makes one minimal OpenAI API request.
- Writes `output/openai-credential-check.txt`.
- Logs the model name and artifact path, but never prints the API key.

Run the assistant:

```zsh
rcc run -t RunAssistant
```

Expected result:

- Requires `OPENAI_API_KEY`.
- Opens the local assistant GUI.
- Calls OpenAI only after the operator enters a prompt and clicks `Send`.
- Keeps the conversation in memory for that run.

Inspect the RCC environment without running a task:

```zsh
rcc ht vars -r robot.yaml
```

## First Files To Edit

- `tasks.py`: assistant behavior, default model, prompt handling, UI copy.
- `test-openai.py`: local smoke and credential-check tasks.
- `robot.yaml`: RCC task names and task command wiring.
- `conda.yaml`: Python and package pins when the template really needs a
  dependency change.
- `.env`: local-only secrets and model overrides. This file must stay untracked.

## Current Defaults

- Default model: `gpt-4.1`
- Optional override: `OPENAI_MODEL`
- Artifacts directory: `output`
- Dependency management: `conda.yaml`
