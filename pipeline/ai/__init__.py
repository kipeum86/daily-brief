"""AI analysis module for daily-brief."""

from pipeline.ai.briefing import generate_briefing
from pipeline.ai.weekly import generate_weekly_recap

__all__ = ["generate_briefing", "generate_weekly_recap"]
