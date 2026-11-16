# Contributing

Thanks for your interest in `asr-engine`. This page says how the project is run today, so you know what to expect before you spend time on it.

## How the project is run

`asr-engine` is developed by one person, in the open, as a spec-driven project: every change starts as a design in [specs/](specs/_index.md), becomes an implementation plan in [plans/](plans/_index.md), and only then code. [AGENTS.md](AGENTS.md) describes that workflow and the checks every change passes. The backends follow their providers, whose models and APIs move often.

## Pull requests: not for now

External pull requests are not accepted for now. This is not a judgement on contributions: every change here goes through a spec, a plan and the live tier against paid provider accounts, and reviewing an outside change to that standard takes the time that would otherwise go into the project itself. A pull request will be closed with thanks and, when the idea is a good one, turned into an issue so it is not lost.

This file will say so when that changes.

## Feedback: yes, please

Issues are the way in, at [github.com/funwithagents/asr-engine/issues](https://github.com/funwithagents/asr-engine/issues), and every kind of feedback is welcome:

- **Bug reports.** Say what you ran: the module and its config with secrets removed, `start`, `listen` or dictation, the OS and Python version, the audio input (a device, a file, your own source); what you expected, what happened, and the log output. A small script that reproduces it is the best possible report.
- **Reports from setups the author does not test on.** Development happens on an Apple Silicon Mac, and CI has no sound device. A report that live capture works, or does not, on Windows, Linux or an Intel Mac is valuable even when everything went fine. So is a report on the `kyutai` PyTorch backend, which has never been run on real hardware, or on a provider that behaves differently on another account tier or region.
- **Module requests.** A provider or a local model you would like to see as a module, and why: its languages, its latency, its license, its platform.
- **API and design feedback.** What you would want the engine, a module's config or the MCP server to do differently. Pointing at the spec you are reacting to helps.
- **Questions.** If the README, a page under [docs/](docs/index.md) or a spec left you guessing, that is a documentation bug worth an issue too.

## Working with the code anyway

The code is Apache-2.0-licensed, so forking it and building on it is welcome. The commands to lint, type check and run the two test tiers are in [AGENTS.md](AGENTS.md); the checklist for adding a module is in [specs/asr-module-interface.md](specs/asr-module-interface.md#adding-a-module-checklist); the user documentation starts at [docs/index.md](docs/index.md). If your fork grows something you think belongs upstream, open an issue describing it.
