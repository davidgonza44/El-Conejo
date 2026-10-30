"""Presentation endpoints for the opt-in offline experimental report."""
from flask import current_app, jsonify, render_template

from app.services import experimental_2025_application_service as service


def _report():
    return service.read_report(current_app.config.get("EXPERIMENTAL_2025_REPORT"))


def page():
    return render_template("experimental_2025.html", report=_report())


def readiness():
    return jsonify(_report())
