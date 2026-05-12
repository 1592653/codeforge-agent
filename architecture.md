# CodeForge Agent — Architecture

## Overview

CodeForge Agent is a multi-agent system for automated code review and refactoring. It uses 5 specialized AI agents orchestrated through a sequential pipeline with shared context management.

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLI / REST API                           │
│                    (Click + Rich / FastAPI)                      │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Orchestrator                                │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                   Pipeline Manager                        │   │
│  │  Stage 1 → Stage 2 → Stage 3 → Stage 4 → Stage 5        │   │
│  └──────────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                   Shared Context                          │   │
│  │  (agent outputs, metrics, messages, token tracking)       │   │
│  └──────────────────────────────────────────────────────────┘   │
└───────────────────────────┬─────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  Agents Layer │  │ Analyzers    │  │  Storage     │
│              │  │              │  │              │
│  Scanner     │  │  AST Parser  │  │  Git Handler │
│  Analyzer    │  │  Complexity  │  │  Reports     │
│  Planner     │  │  Dependency  │  │              │
│  Refactorer  │  │              │  │              │
│  Validator   │  │              │  │              │
└──────────────┘  └──────────────┘  └──────────────┘
        │
        ▼
┌──────────────┐
│  LLM Backend │
│              │
│  Anthropic   │
│  OpenAI      │
│  Ollama      │
└──────────────┘
```

## Agent Communication

Agents communicate through two mechanisms:

1. **Shared Context**: All agents read from and write to a central context store. Downstream agents access upstream outputs via `context.get_agent_output("agent_name")`.

2. **Message Passing**: Agents can send structured messages to each other for fine-grained coordination.

## Data Flow

```
Scanner Agent
  │ outputs: {files: [{path, language, size}], patterns: [...]}
  ▼
Analyzer Agent
  │ outputs: {complexity: {...}, dependencies: {...}, issues: [...]}
  ▼
Planner Agent
  │ outputs: {tasks: [{id, file, pattern, description, risk}], execution_order: [...]}
  ▼
Refactorer Agent
  │ outputs: {patches_applied: N, patches_failed: M, results: [...]}
  ▼
Validator Agent
  │ outputs: {test_result: {...}, lint_result: {...}, all_passed: bool}
  ▼
Pipeline Result
```

## Token Management

Each agent tracks its own token usage through a shared `TokenCounter`. The counter provides:
- Per-run tracking
- Daily budget management with alerts
- Cost estimation
- History export

## Configuration

The system is configured through:
- `config/settings.yaml` — Pipeline and agent settings
- `.env` — API keys and environment variables
- CLI flags — Per-run overrides

## Key Design Decisions

1. **Sequential pipeline** rather than parallel agents — ensures data dependencies are met and simplifies error handling.

2. **AST + LLM hybrid analysis** — static metrics provide reliable baselines while LLM adds semantic understanding.

3. **Auto-rollback on test failure** — the Validator agent can revert changes if tests fail, providing a safety net.

4. **Pluggable LLM backends** — supports Anthropic, OpenAI, and local models via Ollama.
