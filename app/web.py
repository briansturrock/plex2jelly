from __future__ import annotations

from flask import Flask, flash, redirect, render_template, request, url_for

from app.config import AppConfig
from app.database import connect, init_db, set_setting
from app.jellyfin_client import JellyfinClient
from app.plex_client import PlexClient


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = "plex2jelly-local-setup"
    init_db()

    @app.route("/")
    def index():
        cfg = AppConfig.load()
        with connect() as conn:
            path_count = conn.execute("SELECT COUNT(*) AS c FROM path_mappings WHERE enabled = 1").fetchone()["c"]
            lib_count = conn.execute("SELECT COUNT(*) AS c FROM library_mappings WHERE enabled = 1").fetchone()["c"]
        return render_template("index.html", cfg=cfg, path_count=path_count, lib_count=lib_count)

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        if request.method == "POST":
            set_setting("plex_base_url", request.form.get("plex_base_url", "").strip())
            set_setting("plex_token", request.form.get("plex_token", "").strip())
            set_setting("jellyfin_base_url", request.form.get("jellyfin_base_url", "").strip())
            set_setting("jellyfin_api_key", request.form.get("jellyfin_api_key", "").strip())
            flash("Settings saved.", "success")
            return redirect(url_for("settings"))
        return render_template("settings.html", cfg=AppConfig.load())

    @app.route("/health")
    def health():
        cfg = AppConfig.load()
        checks = [
            ("Database", True, "OK"),
            ("Plex", cfg.has_plex, "Configured" if cfg.has_plex else "Not configured"),
            ("Jellyfin", cfg.has_jellyfin, "Configured" if cfg.has_jellyfin else "Not configured"),
        ]
        return render_template("health.html", checks=checks)

    @app.post("/health/test/<service>")
    def test_health_service(service: str):
        cfg = AppConfig.load()
        if service == "plex":
            if not cfg.has_plex:
                flash("Plex is not configured.", "error")
            else:
                ok, msg = PlexClient(cfg.plex_base_url, cfg.plex_token, timeout=5).test()
                flash(f"Plex: {msg}", "success" if ok else "error")
        elif service == "jellyfin":
            if not cfg.has_jellyfin:
                flash("Jellyfin is not configured.", "error")
            else:
                ok, msg = JellyfinClient(cfg.jellyfin_base_url, cfg.jellyfin_api_key, timeout=5).test()
                flash(f"Jellyfin: {msg}", "success" if ok else "error")
        else:
            flash("Unknown health check.", "error")
        return redirect(url_for("health"))

    @app.route("/paths", methods=["GET", "POST"])
    def paths():
        if request.method == "POST":
            plex_path = request.form.get("plex_path", "").strip()
            jellyfin_path = request.form.get("jellyfin_path", "").strip()
            if plex_path and jellyfin_path:
                with connect() as conn:
                    conn.execute(
                        "INSERT INTO path_mappings(plex_path, jellyfin_path, enabled) VALUES (?, ?, 1)",
                        (plex_path, jellyfin_path),
                    )
                flash("Path mapping added.", "success")
            else:
                flash("Both paths are required.", "error")
            return redirect(url_for("paths"))
        with connect() as conn:
            rows = conn.execute("SELECT * FROM path_mappings ORDER BY id").fetchall()
        return render_template("paths.html", rows=rows)

    @app.post("/paths/<int:mapping_id>/delete")
    def delete_path(mapping_id: int):
        with connect() as conn:
            conn.execute("DELETE FROM path_mappings WHERE id = ?", (mapping_id,))
        flash("Path mapping deleted.", "success")
        return redirect(url_for("paths"))

    @app.route("/libraries", methods=["GET", "POST"])
    def libraries():
        cfg = AppConfig.load()
        plex_libraries = []
        jellyfin_libraries = []
        discovery_error = None
        if cfg.has_plex and cfg.has_jellyfin:
            try:
                plex_libraries = PlexClient(cfg.plex_base_url, cfg.plex_token).libraries()
                jellyfin_libraries = JellyfinClient(cfg.jellyfin_base_url, cfg.jellyfin_api_key).libraries()
            except Exception as exc:
                discovery_error = str(exc)

        if request.method == "POST":
            plex_value = request.form.get("plex_library", "")
            jellyfin_value = request.form.get("jellyfin_library", "")
            name = request.form.get("name", "").strip()
            media_type = request.form.get("media_type", "unknown").strip() or "unknown"
            if "|" not in plex_value or "|" not in jellyfin_value:
                flash("Select both a Plex and Jellyfin library.", "error")
                return redirect(url_for("libraries"))
            plex_key, plex_name = plex_value.split("|", 1)
            jellyfin_id, jellyfin_name = jellyfin_value.split("|", 1)
            if not name:
                name = plex_name
            with connect() as conn:
                conn.execute(
                    """
                    INSERT INTO library_mappings(
                        name, plex_library_key, plex_library_name,
                        jellyfin_library_id, jellyfin_library_name, media_type, enabled
                    ) VALUES (?, ?, ?, ?, ?, ?, 1)
                    """,
                    (name, plex_key, plex_name, jellyfin_id, jellyfin_name, media_type),
                )
            flash("Library mapping added.", "success")
            return redirect(url_for("libraries"))

        with connect() as conn:
            mappings = conn.execute("SELECT * FROM library_mappings ORDER BY id").fetchall()
        return render_template(
            "libraries.html",
            cfg=cfg,
            plex_libraries=plex_libraries,
            jellyfin_libraries=jellyfin_libraries,
            mappings=mappings,
            discovery_error=discovery_error,
        )

    @app.post("/libraries/<int:mapping_id>/delete")
    def delete_library(mapping_id: int):
        with connect() as conn:
            conn.execute("DELETE FROM library_mappings WHERE id = ?", (mapping_id,))
        flash("Library mapping deleted.", "success")
        return redirect(url_for("libraries"))

    return app
