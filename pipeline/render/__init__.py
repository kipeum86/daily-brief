"""Render pipeline — Jinja2 templates to static HTML."""

from pipeline.render.dashboard import render_dashboard
from pipeline.render.email import render_email

__all__ = ["render_dashboard", "render_email"]
