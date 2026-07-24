# OpenAI Relay Model Switch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch the deployed Agent model from DeepSeek to `gpt-5.5` through the configured OpenAI Responses API relay.

**Architecture:** Keep provider selection environment-driven. Configure only the Agent runtime to use the relay, while leaving query rewrite, embeddings, and reranking on their existing providers.

**Tech Stack:** Pydantic Settings, OpenAI Agents SDK, dotenv

---

### Task 1: Runtime configuration

**Files:**
- Modify: `.env`
- Modify: `.env.example`

- [x] Set `AGENT_MODEL_PROVIDER=openai`.
- [x] Set `AGENT_MODEL_NAME=gpt-5.5`.
- [x] Set `AGENT_OPENAI_BASE_URL=https://www.codex2api.com/v1`.
- [x] Leave `AGENT_OPENAI_API_KEY` empty in `.env` for the administrator to populate locally.

### Task 2: Operator documentation

**Files:**
- Modify: `README.md`

- [x] Document the relay base URL, model name, local secret location, and restart requirement.

### Task 3: Verification

- [x] Load `Settings()` in a fresh process and verify provider, model, and base URL.
- [x] Run focused settings and model factory tests.
- [ ] After the key is configured, query `/v1/models` and perform a live Agent smoke test before restarting production traffic.
