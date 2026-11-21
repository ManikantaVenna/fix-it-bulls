import importlib
import importlib.util
from pathlib import Path

from flask import Flask, jsonify, redirect, url_for
from flask_wtf.csrf import CSRFProtect, generate_csrf

from config import default_config
from db import close_db, init_db

csrf = CSRFProtect()


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(default_config())
    app.config.update(config or {})
    if not app.config.get("SECRET_KEY"):
        raise RuntimeError("Set SECRET_KEY in your private .env before starting the app.")
    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
    csrf.init_app(app)
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()

    @app.get("/health")
    def health():
        return jsonify(project="Fix It Bulls", stage="foundation", gmail_connected=False)

    @app.get("/")
    def index():
        if "reports.dashboard" in app.view_functions:
            return redirect(url_for("reports.dashboard"))
        return jsonify(
            project="Fix It Bulls",
            stage="Shared foundation; feature modules are pending.",
            next="Read README.md and the shared schemas before starting your assigned branch.",
        )

    @app.get("/api/csrf")
    def csrf_token():
        return jsonify(csrf_token=generate_csrf())

    @app.after_request
    def protect_responses(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    # Feature owners provide a Flask Blueprint named bp. Missing modules are
    # expected in the foundation; errors in an existing module are not hidden.
    for module_name in ("routes.auth", "routes.reports", "routes.gmail"):
        if importlib.util.find_spec(module_name) is not None:
            app.register_blueprint(importlib.import_module(module_name).bp)
    return app
