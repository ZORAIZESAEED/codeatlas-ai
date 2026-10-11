"""Token-budgeted context assembly.

The budget is split explicitly: system instructions, question, conversation summary and the
reserved output come first; evidence gets what is left, in rank order. Evidence that does not
fit is truncated (when enough lines still fit) or dropped, and both are recorded. Token counts
are estimates (no tokenizer dependency); they are never reported as provider usage.
"""
import math
from dataclasses import dataclass, field

from app.services.chunking import split_lines
from app.services.rag.evidence import Evidence
from app.services.rag.prompts import build_user_message, fence_token, render_evidence

# Source code tokenizes densely; 3 characters per token over-estimates rather than overflows.
CHARS_PER_TOKEN = 3.0
MESSAGE_OVERHEAD_TOKENS = 200
MIN_TRUNCATED_LINES = 8


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


@dataclass
class AssembledContext:
    user_message: str
    included: list[Evidence]
    dropped: list[str] = field(default_factory=list)  # evidence references left out
    truncated: list[str] = field(default_factory=list)  # evidence IDs cut to fit
    budget: dict[str, int] = field(default_factory=dict)

    @property
    def provided_ids(self) -> set[str]:
        return {e.id for e in self.included}


def assemble_context(
    *,
    system: str,
    question: str,
    evidence: list[Evidence],
    history_summary: str,
    notes: list[str],
    context_tokens: int,
    output_tokens: int,
) -> AssembledContext:
    token = fence_token()
    output_tokens = min(output_tokens, context_tokens // 2)
    fixed = {
        "system": estimate_tokens(system),
        "question": estimate_tokens(question),
        "history": estimate_tokens(history_summary),
        "notes": estimate_tokens("\n".join(notes)),
        "overhead": MESSAGE_OVERHEAD_TOKENS,
    }
    available = context_tokens - output_tokens - sum(fixed.values())
    result = AssembledContext(user_message="", included=[])
    blocks: list[str] = []
    used = 0
    for item in evidence:
        block = render_evidence(item, token)
        cost = estimate_tokens(block) + 4
        if used + cost <= available:
            blocks.append(block)
            result.included.append(item)
            used += cost
            continue
        # Truncate to the lines that still fit, if that leaves a useful amount of code.
        room = available - used - estimate_tokens(block.split("\n", 2)[0]) - 60
        keep, spent = 0, 0
        for line in split_lines(item.text):
            line_cost = estimate_tokens(line) + 3
            if spent + line_cost > room:
                break
            spent += line_cost
            keep += 1
        if keep >= MIN_TRUNCATED_LINES:
            item.truncate(keep)
            block = render_evidence(item, token)
            blocks.append(block)
            result.included.append(item)
            result.truncated.append(item.id)
            used += estimate_tokens(block) + 4
        else:
            result.dropped.append(f"{item.id} {item.reference}")
    notes = list(notes)
    if result.dropped:
        notes.append(f"{len(result.dropped)} further evidence block(s) did not fit the context "
                     "budget and were left out.")
    result.user_message = build_user_message(question, blocks, history_summary, notes)
    result.budget = {
        "context_tokens": context_tokens, "reserved_output_tokens": output_tokens,
        **{f"{k}_tokens": v for k, v in fixed.items()},
        "evidence_tokens": used, "evidence_available_tokens": max(available, 0),
        "estimated_prompt_tokens": estimate_tokens(system) + estimate_tokens(result.user_message),
    }
    return result
