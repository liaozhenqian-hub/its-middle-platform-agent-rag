# Hybrid Manager Reasoning Design

## Goal

Improve complex cross-domain answer synthesis without making a reasoning model responsible for tool selection or execution.

## Routing And Model Responsibilities

- High-confidence single-domain questions continue to bypass Manager and run the matching Flash specialist directly.
- Bug questions continue to use the existing LangGraph flow: Flash for intake and evidence collection, Pro thinking only for final diagnosis.
- Cross-domain questions use the Flash Manager to call specialists and collect evidence.
- After Flash has completed tool execution, a tool-free Pro synthesizer may rewrite the grounded draft into the final answer.
- Unknown questions that do not produce evidence or do not use multiple specialists are not upgraded to Pro.

## Pro Synthesis Gate

Pro synthesis runs only when all conditions are true:

1. Manager reasoning is enabled.
2. The run completed without an approval interruption or clarification response.
3. The request routed to multiple domains, or at least two domain specialists actually ran.
4. At least one public citation exists.
5. The Bug Graph did not handle the request.

The synthesizer receives only the user question, the Flash draft, routed domain names, and bounded public citation summaries. It has no tools and must preserve evidence boundaries, unknown items, URLs, and citation meaning. It must not introduce new internal facts.

## Runtime And Streaming

- Non-streaming chat waits for Pro synthesis and returns the synthesized answer.
- Streaming chat exposes normal agent/tool lifecycle events while Flash gathers evidence, then streams only the final Pro answer.
- Flash draft prose is buffered for eligible cross-domain runs so users do not see one answer replaced by another.
- Pro timeout, provider failure, invalid empty output, or evidence-policy rejection falls back to the Flash draft.
- A quality span records `manager.reasoning_synthesis` status and duration without recording prompts or answer bodies.

## Configuration

- `AGENT_MANAGER_REASONING_ENABLED=true`
- `AGENT_MANAGER_REASONING_TIMEOUT_SECONDS=60`

The feature uses the existing `DEEPSEEK_REASONING_MODEL`, `DEEPSEEK_REASONING_ENABLED`, and model factory. When the reasoning model is disabled or the provider is not DeepSeek, the service keeps the Flash/current-model result.

## Safety And Evidence

- The Pro synthesizer never receives credentials, raw logs, embeddings, chunk IDs, source IDs, or complete tool output.
- Existing citation sanitization and evidence-policy safeguards run after synthesis.
- No new retrieval or MCP calls occur during synthesis.
- Failure is fail-open to the already grounded Flash answer, not to an ungrounded generic response.

## Tests

- Single-domain approval questions do not invoke Pro.
- Cross-domain runs with citations invoke Pro once with thinking enabled.
- Cross-domain runs without citations do not invoke Pro.
- Clarification and Bug Graph responses do not invoke Pro.
- Timeout, exception, and empty Pro output fall back to Flash.
- Streaming emits the Pro answer once and does not leak the buffered Flash draft.
- Runtime spans identify completed, timed-out, and failed synthesis.

