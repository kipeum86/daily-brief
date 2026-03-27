"""Render pipeline — Jinja2 templates to static HTML."""

from pipeline.render.dashboard import render_dashboard
from pipeline.render.email import render_email, render_weekly_email
from pipeline.render.weekly import render_weekly_recap

__all__ = ["render_dashboard", "render_email", "render_weekly_email", "render_weekly_recap"]
