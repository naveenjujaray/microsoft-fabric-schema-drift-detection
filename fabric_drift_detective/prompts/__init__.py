"""Prompt templates for the Claude reasoner. Tune freely."""

from .commit_message import COMMIT_MESSAGE_PROMPT
from .fix_suggestion import FIX_SUGGESTION_PROMPT
from .impact_analysis import IMPACT_ANALYSIS_PROMPT

__all__ = [
    "IMPACT_ANALYSIS_PROMPT",
    "FIX_SUGGESTION_PROMPT",
    "COMMIT_MESSAGE_PROMPT",
]
