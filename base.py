"""Base agent with LLM integration and shared functionality."""

from __future__ import annotations

import asyncio
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import anthropic
import openai

from codeforge.utils.logger import get_logger
from codeforge.utils.token_counter import TokenCounter


class AgentStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class AgentMessage:
    """Message passed between agents in the pipeline."""

    sender: str
    receiver: str
    content: dict[str, Any]
    message_type: str = "data"
    timestamp: float = field(default_factory=time.time)


@dataclass
class AgentResult:
    """Result produced by an agent after execution."""

    agent_name: str
    status: AgentStatus
    data: dict[str, Any]
    token_usage: dict[str, int] = field(default_factory=dict)
    duration_ms: float = 0
    errors: list[str] = field(default_factory=list)


class BaseAgent(ABC):
    """Base class for all CodeForge agents.

    Provides LLM integration, token tracking, message passing,
    and lifecycle management for specialized agents.
    """

    def __init__(
        self,
        name: str,
        model: str = "claude-sonnet-4-20250514",
        provider: str = "anthropic",
        token_counter: TokenCounter | None = None,
        max_retries: int = 2,
        timeout: float = 300,
    ) -> None:
        self.name = name
        self.model = model
        self.provider = provider
        self.token_counter = token_counter or TokenCounter()
        self.max_retries = max_retries
        self.timeout = timeout
        self.status = AgentStatus.IDLE
        self.message_queue: list[AgentMessage] = []
        self.logger = get_logger(f"agent.{name}")
        self._client: Any = None
        self._init_client()

    def _init_client(self) -> None:
        """Initialize the LLM client based on provider."""
        if self.provider == "anthropic":
            self._client = anthropic.Anthropic()
        elif self.provider == "openai":
            self._client = openai.AsyncOpenAI()
        else:
            raise ValueError(f"Unsupported LLM provider: {self.provider}")

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Return the system prompt for this agent."""
        ...

    @abstractmethod
    async def execute(self, context: dict[str, Any]) -> AgentResult:
        """Execute the agent's main task."""
        ...

    async def call_llm(
        self,
        messages: list[dict[str, str]],
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        """Call the LLM with retry logic and token tracking."""
        system = system or self.get_system_prompt()
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                if self.provider == "anthropic":
                    return await self._call_anthropic(messages, system, max_tokens, temperature)
                elif self.provider == "openai":
                    return await self._call_openai(messages, system, max_tokens, temperature)
            except (anthropic.RateLimitError, openai.RateLimitError) as e:
                last_error = e
                wait_time = 2 ** attempt * 1.0
                self.logger.warning(
                    f"Rate limited on attempt {attempt + 1}, waiting {wait_time}s"
                )
                await asyncio.sleep(wait_time)
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    self.logger.warning(f"LLM call failed on attempt {attempt + 1}: {e}")
                    await asyncio.sleep(1.0)
                else:
                    break

        raise RuntimeError(
            f"Agent '{self.name}' LLM call failed after {self.max_retries + 1} attempts: {last_error}"
        )

    async def _call_anthropic(
        self,
        messages: list[dict[str, str]],
        system: str,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        """Call Anthropic Claude API."""
        response = await asyncio.to_thread(
            self._client.messages.create,
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=messages,
        )

        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
        self.token_counter.record(usage["input_tokens"], usage["output_tokens"])

        return {
            "content": response.content[0].text,
            "usage": usage,
            "model": response.model,
        }

    async def _call_openai(
        self,
        messages: list[dict[str, str]],
        system: str,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        """Call OpenAI-compatible API."""
        formatted_messages = [{"role": "system", "content": system}] + messages
        response = await self._client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=formatted_messages,
        )

        usage = {
            "input_tokens": response.usage.prompt_tokens,
            "output_tokens": response.usage.completion_tokens,
        }
        self.token_counter.record(usage["input_tokens"], usage["output_tokens"])

        return {
            "content": response.choices[0].message.content,
            "usage": usage,
            "model": response.model,
        }

    async def call_llm_structured(
        self,
        messages: list[dict[str, str]],
        system: str | None = None,
        schema: dict[str, Any] | None = None,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        """Call LLM and parse response as structured JSON."""
        prompt_addition = ""
        if schema:
            prompt_addition = (
                "\n\nYou MUST respond with valid JSON matching this schema:\n"
                f"{json.dumps(schema, indent=2)}\n"
                "Return ONLY the JSON object, no other text."
            )

        if prompt_addition and messages:
            messages = messages.copy()
            messages[-1] = {
                "role": messages[-1]["role"],
                "content": messages[-1]["content"] + prompt_addition,
            }

        result = await self.call_llm(messages, system=system, max_tokens=max_tokens)

        try:
            parsed = json.loads(result["content"])
            result["parsed"] = parsed
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code block
            content = result["content"]
            if "```json" in content:
                json_str = content.split("```json")[1].split("```")[0].strip()
                parsed = json.loads(json_str)
                result["parsed"] = parsed
            elif "```" in content:
                json_str = content.split("```")[1].split("```")[0].strip()
                parsed = json.loads(json_str)
                result["parsed"] = parsed
            else:
                raise

        return result

    def send_message(self, receiver: str, content: dict[str, Any], msg_type: str = "data") -> AgentMessage:
        """Send a message to another agent."""
        msg = AgentMessage(
            sender=self.name,
            receiver=receiver,
            content=content,
            message_type=msg_type,
        )
        self.logger.debug(f"Sending message to {receiver}: {msg_type}")
        return msg

    def receive_message(self, message: AgentMessage) -> None:
        """Receive a message from another agent."""
        self.message_queue.append(message)
        self.logger.debug(f"Received message from {message.sender}: {message.message_type}")

    def get_messages(self, sender: str | None = None) -> list[AgentMessage]:
        """Get messages from the queue, optionally filtered by sender."""
        if sender:
            return [m for m in self.message_queue if m.sender == sender]
        return list(self.message_queue)

    def clear_messages(self) -> None:
        """Clear the message queue."""
        self.message_queue.clear()

    async def run(self, context: dict[str, Any]) -> AgentResult:
        """Run the agent with lifecycle management."""
        self.status = AgentStatus.RUNNING
        start_time = time.time()

        try:
            self.logger.info(f"Agent '{self.name}' starting execution")
            result = await asyncio.wait_for(
                self.execute(context),
                timeout=self.timeout,
            )
            result.duration_ms = (time.time() - start_time) * 1000
            self.status = AgentStatus.COMPLETED
            self.logger.info(
                f"Agent '{self.name}' completed in {result.duration_ms:.0f}ms "
                f"(tokens: {result.token_usage})"
            )
            return result

        except asyncio.TimeoutError:
            self.status = AgentStatus.FAILED
            duration = (time.time() - start_time) * 1000
            self.logger.error(f"Agent '{self.name}' timed out after {duration:.0f}ms")
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                data={},
                duration_ms=duration,
                errors=[f"Agent timed out after {self.timeout}s"],
            )
        except Exception as e:
            self.status = AgentStatus.FAILED
            duration = (time.time() - start_time) * 1000
            self.logger.error(f"Agent '{self.name}' failed: {e}")
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                data={},
                duration_ms=duration,
                errors=[str(e)],
            )

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(name='{self.name}', status='{self.status}')>"
