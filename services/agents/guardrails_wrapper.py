"""NeMo Guardrails wrapper with graceful degradation.

Runs input/output rails against user messages and agent responses.
Falls back to keyword-based checks if NeMo is unavailable.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

GUARDRAILS_DIR = Path(__file__).parent / "guardrails"

# Prompt injection patterns
_INJECTION_PATTERNS = [
    r"ignore your instructions",
    r"ignore previous instructions",
    r"forget your system prompt",
    r"override your (rules|guidelines|instructions)",
    r"you are now",
    r"pretend you are",
    r"act as if",
    r"disregard (all|your|the) (previous|above|prior)",
    r"new instructions:",
    r"system:\s*you are",
    r"jailbreak",
    r"do anything now",
    r"developer mode",
    r"sudo mode",
    r"bypass (your|all) (restrictions|filters|rules)",
    r"dump all (customer|user) data",
    r"reveal your (prompt|instructions|system)",
    r"what is your system prompt",
    r"output your (initial|system) (prompt|instructions)",
    r"repeat (the|your) (instructions|prompt) (above|verbatim)",
]
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)

# PII patterns
_PII_PATTERNS = [
    r"\b\d{3}-\d{2}-\d{4}\b",  # SSN
    r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b",  # Credit card
    r"\bpassword\s*[:=]\s*\S+",  # Password
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z]{2,}\b",  # Email (flagged in context)
]
_PII_RE = re.compile("|".join(_PII_PATTERNS), re.IGNORECASE)

# Off-topic patterns
_OFFTOPIC_PATTERNS = [
    r"\b(tell me a joke|knock knock|what's funny)\b",
    r"\b(weather|forecast|temperature)\s+(in|for|today)\b",
    r"\b(sports?|football|basketball|soccer|baseball)\s+(score|game|match)\b",
    r"\b(recipe|cook|bake|ingredient)\b",
    r"\b(play|movie|song|music|lyrics)\b",
    r"\b(who won|world cup|super bowl|olympics)\b",
]
_OFFTOPIC_RE = re.compile("|".join(_OFFTOPIC_PATTERNS), re.IGNORECASE)

# Output secret patterns
_SECRET_PATTERNS = [
    r"password\s*[:=]\s*\S+",
    r"client_secret\s*[:=]\s*\S+",
    r"api_key\s*[:=]\s*\S+",
    r"bearer\s+eyj[a-zA-Z0-9._-]+",
    r"private_key",
    r"-----BEGIN (RSA |EC )?PRIVATE KEY-----",
    r"SNOWFLAKE_PRIVATE_KEY",
    r"(aws_secret_access_key|aws_session_token)\s*[:=]",
]
_SECRET_RE = re.compile("|".join(_SECRET_PATTERNS), re.IGNORECASE)

# DDL patterns
_DDL_PATTERNS = [
    r"\b(CREATE|ALTER|DROP)\s+(TABLE|VIEW|SCHEMA|DATABASE|INDEX)\b",
    r"\bINSERT\s+INTO\b",
    r"\bDELETE\s+FROM\b",
    r"\bTRUNCATE\s+(TABLE\s+)?\w+",
    r"\bUPDATE\s+\w+\s+SET\b",
]
_DDL_RE = re.compile("|".join(_DDL_PATTERNS), re.IGNORECASE)


@dataclass
class GuardrailEvent:
    agent_name: str
    rail_type: str  # input, output
    rail_name: str
    action: str  # allowed, blocked
    reason: str = ""
    original_text: str = ""
    layer: str = "guardrails"
    timestamp: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )

    def to_dict(self) -> dict:
        d = asdict(self)
        # Truncate original_text for events
        if len(d.get("original_text", "")) > 200:
            d["original_text"] = d["original_text"][:200] + "..."
        return d


class GuardrailsEvaluator:
    """Evaluate input/output against NeMo Guardrails with keyword fallback."""

    def __init__(
        self,
        agent_name: str = "system",
        event_callback: Callable[[dict], None] | None = None,
    ):
        self.agent_name = agent_name
        self.event_callback = event_callback
        self._rails = None
        self._nemo_available = False
        self._init_nemo()

    def _init_nemo(self) -> None:
        try:
            from nemoguardrails import RailsConfig, LLMRails
            config = RailsConfig.from_path(str(GUARDRAILS_DIR))
            self._rails = LLMRails(config)
            self._nemo_available = True
            logger.info("NeMo Guardrails initialized for %s", self.agent_name)
        except ImportError:
            logger.warning("nemoguardrails not installed — using keyword fallback")
        except Exception as exc:
            logger.warning("NeMo init failed (%s) — using keyword fallback", exc)

    def _emit(self, event: GuardrailEvent) -> None:
        if self.event_callback:
            self.event_callback(event.to_dict())

    async def check_input(self, user_message: str) -> tuple[bool, str]:
        """Check user input against rails.

        Returns (allowed: bool, message_or_reason: str).
        """
        # Pre-check: prompt injection
        if _INJECTION_RE.search(user_message):
            event = GuardrailEvent(
                agent_name=self.agent_name,
                rail_type="input",
                rail_name="prompt_injection",
                action="blocked",
                reason="Prompt injection pattern detected",
                original_text=user_message,
            )
            self._emit(event)
            return False, "Your message was blocked: potential prompt injection detected."

        # Pre-check: PII
        if _PII_RE.search(user_message):
            event = GuardrailEvent(
                agent_name=self.agent_name,
                rail_type="input",
                rail_name="pii_detection",
                action="blocked",
                reason="PII pattern detected in input",
                original_text=user_message,
            )
            self._emit(event)
            return False, "Your message was blocked: it appears to contain sensitive personal information."

        # Pre-check: off-topic
        if _OFFTOPIC_RE.search(user_message):
            event = GuardrailEvent(
                agent_name=self.agent_name,
                rail_type="input",
                rail_name="topic_boundary",
                action="blocked",
                reason="Off-topic request detected",
                original_text=user_message,
            )
            self._emit(event)
            return False, (
                "I'm focused on financial analysis and customer support data. "
                "Please ask a business data question."
            )

        # NeMo check (if available)
        if self._nemo_available and self._rails:
            try:
                result = await self._rails.generate_async(
                    messages=[{"role": "user", "content": user_message}]
                )
                response = result.get("content", "")
                blocked_indicators = [
                    "prompt injection",
                    "cannot comply",
                    "i cannot",
                    "i'm unable",
                    "not allowed",
                    "off-topic",
                ]
                if any(ind in response.lower() for ind in blocked_indicators):
                    event = GuardrailEvent(
                        agent_name=self.agent_name,
                        rail_type="input",
                        rail_name="nemo_input_rail",
                        action="blocked",
                        reason=f"NeMo blocked: {response[:100]}",
                        original_text=user_message,
                    )
                    self._emit(event)
                    return False, response
            except Exception as exc:
                logger.warning("NeMo input check failed: %s", exc)

        # Allowed
        event = GuardrailEvent(
            agent_name=self.agent_name,
            rail_type="input",
            rail_name="all_input_rails",
            action="allowed",
            original_text=user_message,
        )
        self._emit(event)
        return True, user_message

    async def check_output(self, agent_response: str) -> tuple[bool, str]:
        """Check agent output against rails.

        Returns (allowed: bool, response_or_reason: str).
        """
        # Pre-check: secrets
        if _SECRET_RE.search(agent_response):
            event = GuardrailEvent(
                agent_name=self.agent_name,
                rail_type="output",
                rail_name="data_leak_prevention",
                action="blocked",
                reason="Credential/secret pattern detected in output",
                original_text=agent_response,
            )
            self._emit(event)
            return False, (
                "The response was blocked because it contained sensitive information "
                "(credentials, API keys, or secrets). Please rephrase your question."
            )

        # Pre-check: DDL
        if _DDL_RE.search(agent_response):
            event = GuardrailEvent(
                agent_name=self.agent_name,
                rail_type="output",
                rail_name="ddl_prevention",
                action="blocked",
                reason="DDL/DML statement detected in output",
                original_text=agent_response,
            )
            self._emit(event)
            return False, (
                "The response was blocked because it contained database modification "
                "statements. I can only provide data query results, not schema changes."
            )

        # NeMo check (if available)
        if self._nemo_available and self._rails:
            try:
                result = await self._rails.generate_async(
                    messages=[
                        {"role": "user", "content": "Please provide the analysis."},
                        {"role": "assistant", "content": agent_response},
                    ]
                )
                response = result.get("content", "")
                # If NeMo significantly modified the response, treat as blocked
                if len(response) < len(agent_response) * 0.5:
                    event = GuardrailEvent(
                        agent_name=self.agent_name,
                        rail_type="output",
                        rail_name="nemo_output_rail",
                        action="blocked",
                        reason="NeMo significantly modified the response",
                        original_text=agent_response,
                    )
                    self._emit(event)
                    return False, response
            except Exception as exc:
                logger.warning("NeMo output check failed: %s", exc)

        # Allowed
        event = GuardrailEvent(
            agent_name=self.agent_name,
            rail_type="output",
            rail_name="all_output_rails",
            action="allowed",
        )
        self._emit(event)
        return True, agent_response
