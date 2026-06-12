from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from flask import Flask, current_app, flash, redirect, render_template, request, url_for

from app import __version__
from app.config import AppConfig
from app.database import (
    connect,
    get_discovered_libraries,
    init_db,
    replace_discovered_libraries,
    set_setting,
)
from app.jellyfin_client import JellyfinClient
from app.matcher import canonicalise_path, warning_reasons
from app.plex_client import PlexClient

METADATA_SCOPE_VERSION = "movie_core_v5_value_normalisation"
METADATA_FIELDS = [
    "Name",
    "OriginalTitle",
    "ForcedSortName",
    "ProductionYear",
    "PremiereDate",
    "Overview",
    "Taglines",
    "OfficialRating",
    "CommunityRating",
    "CriticRating",
    "Genres",
    "Studios",
    "ProductionLocations",
    "ProviderIds",
]


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = "plex2jelly-local-setup"
    init_db()

    @app.context_processor
    def inject_version():
        return {"app_version": __version__}

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
        with connect() as conn:
            library_mappings = conn.execute(
                """
                SELECT id, name, plex_library_name, jellyfin_library_name, media_type, enabled
                FROM library_mappings
                ORDER BY name COLLATE NOCASE
                """
            ).fetchall()
        if request.method == "POST":
            library_mapping_id = request.form.get("library_mapping_id", "").strip()
            plex_path = request.form.get("plex_path", "").strip().rstrip("/")
            jellyfin_path = request.form.get("jellyfin_path", "").strip().rstrip("/")
            if not library_mapping_id:
                flash("Select a library mapping first.", "error")
            elif plex_path and jellyfin_path:
                with connect() as conn:
                    conn.execute(
                        """
                        INSERT INTO path_mappings(library_mapping_id, plex_path, jellyfin_path, enabled)
                        VALUES (?, ?, ?, 1)
                        """,
                        (int(library_mapping_id), plex_path, jellyfin_path),
                    )
                flash("Path mapping added.", "success")
            else:
                flash("Both paths are required.", "error")
            return redirect(url_for("paths"))
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT pm.id, pm.library_mapping_id, pm.plex_path, pm.jellyfin_path, pm.enabled,
                       lm.name AS library_name, lm.plex_library_name, lm.jellyfin_library_name, lm.media_type
                FROM path_mappings pm
                LEFT JOIN library_mappings lm ON lm.id = pm.library_mapping_id
                ORDER BY COALESCE(lm.name, 'Unscoped') COLLATE NOCASE, pm.id
                """
            ).fetchall()
        return render_template("paths.html", rows=rows, library_mappings=library_mappings)

    @app.post("/paths/<int:mapping_id>/delete")
    def delete_path(mapping_id: int):
        with connect() as conn:
            conn.execute("DELETE FROM path_mappings WHERE id = ?", (mapping_id,))
        flash("Path mapping deleted.", "success")
        return redirect(url_for("paths"))

    @app.route("/libraries", methods=["GET", "POST"])
    def libraries():
        cfg = AppConfig.load()
        if request.method == "POST":
            plex_value = request.form.get("plex_library", "")
            jellyfin_value = request.form.get("jellyfin_library", "")
            name = request.form.get("name", "").strip()
            media_type = request.form.get("media_type", "unknown").strip() or "unknown"
            enabled = 1 if request.form.get("enabled") == "on" else 0
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
                    INSERT INTO library_mappings(name, plex_library_key, plex_library_name, jellyfin_library_id,
                                                 jellyfin_library_name, media_type, enabled)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (name, plex_key, plex_name, jellyfin_id, jellyfin_name, media_type, enabled),
                )
            flash("Library mapping added.", "success")
            return redirect(url_for("libraries"))
        with connect() as conn:
            mappings = conn.execute("SELECT * FROM library_mappings ORDER BY id").fetchall()
        return render_template(
            "libraries.html",
            cfg=cfg,
            plex_libraries=get_discovered_libraries("plex"),
            jellyfin_libraries=get_discovered_libraries("jellyfin"),
            mappings=mappings,
        )

    @app.post("/libraries/discover/<source>")
    def discover_libraries(source: str):
        cfg = AppConfig.load()
        try:
            if source == "plex":
                if not cfg.has_plex:
                    flash("Plex is not configured.", "error")
                else:
                    libraries = PlexClient(cfg.plex_base_url, cfg.plex_token, timeout=10).libraries()
                    replace_discovered_libraries("plex", libraries)
                    flash(f"Discovered {len(libraries)} Plex libraries.", "success")
            elif source == "jellyfin":
                if not cfg.has_jellyfin:
                    flash("Jellyfin is not configured.", "error")
                else:
                    libraries = JellyfinClient(cfg.jellyfin_base_url, cfg.jellyfin_api_key, timeout=10).libraries()
                    replace_discovered_libraries("jellyfin", libraries)
                    flash(f"Discovered {len(libraries)} Jellyfin libraries.", "success")
            else:
                flash("Unknown library source.", "error")
        except Exception as exc:
            flash(f"Library discovery failed: {exc}", "error")
        return redirect(url_for("libraries"))

    @app.post("/libraries/<int:mapping_id>/delete")
    def delete_library(mapping_id: int):
        with connect() as conn:
            conn.execute("DELETE FROM path_mappings WHERE library_mapping_id = ?", (mapping_id,))
            conn.execute("DELETE FROM sync_items WHERE library_mapping_id = ?", (mapping_id,))
            conn.execute("DELETE FROM library_mappings WHERE id = ?", (mapping_id,))
        flash("Library mapping, linked path mappings and preview rows deleted.", "success")
        return redirect(url_for("libraries"))

    @app.post("/libraries/<int:mapping_id>/toggle")
    def toggle_library(mapping_id: int):
        with connect() as conn:
            conn.execute(
                "UPDATE library_mappings SET enabled = CASE enabled WHEN 1 THEN 0 ELSE 1 END WHERE id = ?",
                (mapping_id,),
            )
        flash("Library mapping updated.", "success")
        return redirect(url_for("libraries"))

    @app.route("/match", methods=["GET", "POST"])
    def match_preview():
        cfg = AppConfig.load()
        if request.method == "POST":
            mapping_id = request.form.get("library_mapping_id", "").strip()
            if not mapping_id:
                flash("Select a library mapping first.", "error")
                return redirect(url_for("match_preview"))
            try:
                result = _run_match_preview(int(mapping_id), cfg)
                flash(
                    f"Scan complete: {result['matched']} matched, {result['warnings']} differences, "
                    f"{result['unmatched_plex']} unmatched Plex, {result['unmatched_jellyfin']} unmatched Jellyfin.",
                    "success",
                )
            except Exception as exc:
                flash(f"Match preview failed: {exc}", "error")
            return redirect(url_for("match_preview", library_mapping_id=mapping_id, filter="issues"))

        selected_mapping_id = request.args.get("library_mapping_id", "").strip()
        status_filter = request.args.get("filter", "all").strip() or "all"
        page_size = _safe_int(request.args.get("page_size"), 50, minimum=10, maximum=500)
        page = _safe_int(request.args.get("page"), 1, minimum=1, maximum=999999)
        offset = (page - 1) * page_size
        with connect() as conn:
            mappings = conn.execute(
                """
                SELECT id, name, plex_library_name, jellyfin_library_name, media_type, enabled
                FROM library_mappings
                ORDER BY name COLLATE NOCASE
                """
            ).fetchall()
            params: list[object] = []
            where = []
            if selected_mapping_id:
                where.append("library_mapping_id = ?")
                params.append(int(selected_mapping_id))
            if status_filter == "issues":
                where.append("match_status != 'matched' OR (match_status = 'matched' AND COALESCE(match_warning, '') != '')")
            elif status_filter == "warnings":
                where.append("match_status = 'matched' AND COALESCE(match_warning, '') != ''")
            elif status_filter == "current":
                where.append("match_status = 'matched' AND COALESCE(match_warning, '') = ''")
            elif status_filter in {"matched", "unmatched_plex", "unmatched_jellyfin", "path_failure"}:
                where.append("match_status = ?")
                params.append(status_filter)
            where_sql = " WHERE " + " AND ".join(f"({w})" for w in where) if where else ""
            stats = conn.execute(
                f"""
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN match_status = 'matched' THEN 1 ELSE 0 END) AS matched,
                       SUM(CASE WHEN COALESCE(match_warning, '') != '' THEN 1 ELSE 0 END) AS warnings,
                       SUM(CASE WHEN match_status = 'unmatched_plex' THEN 1 ELSE 0 END) AS unmatched_plex,
                       SUM(CASE WHEN match_status = 'unmatched_jellyfin' THEN 1 ELSE 0 END) AS unmatched_jellyfin,
                       SUM(CASE WHEN match_status = 'path_failure' THEN 1 ELSE 0 END) AS path_failure,
                       SUM(CASE WHEN metadata_scope_version = ? AND metadata_sync_status = 'applied' THEN 1 ELSE 0 END) AS metadata_current,
                       SUM(CASE WHEN metadata_sync_status = 'failed' THEN 1 ELSE 0 END) AS metadata_failed
                FROM sync_items{where_sql}
                """,
                [METADATA_SCOPE_VERSION, *params],
            ).fetchone()
            total = stats["total"] or 0
            rows = conn.execute(
                f"""
                SELECT si.*, lm.name AS library_name
                FROM sync_items si
                JOIN library_mappings lm ON lm.id = si.library_mapping_id
                {where_sql}
                ORDER BY CASE WHEN match_status = 'matched' AND COALESCE(match_warning, '') = '' THEN 1 ELSE 0 END,
                         COALESCE(plex_title, jellyfin_title, canonical_path) COLLATE NOCASE
                LIMIT ? OFFSET ?
                """,
                [*params, page_size, offset],
            ).fetchall()
        total_pages = max(1, (total + page_size - 1) // page_size)
        return render_template(
            "match.html",
            cfg=cfg,
            mappings=mappings,
            selected_mapping_id=selected_mapping_id,
            status_filter=status_filter,
            page_size=page_size,
            page=page,
            total_pages=total_pages,
            stats=stats,
            rows=rows,
            metadata_scope_version=METADATA_SCOPE_VERSION,
        )

    @app.get("/people/<int:sync_item_id>")
    def people_preview(sync_item_id: int):
        cfg = AppConfig.load()
        if not cfg.has_plex:
            flash("Plex must be configured first.", "error")
            return redirect(url_for("match_preview", filter="matched"))
        with connect() as conn:
            row = conn.execute(
                """
                SELECT si.*, lm.name AS library_name
                FROM sync_items si
                JOIN library_mappings lm ON lm.id = si.library_mapping_id
                WHERE si.id = ?
                """,
                (sync_item_id,),
            ).fetchone()
        if not row or row["match_status"] != "matched" or not row["plex_rating_key"]:
            flash("Select a path-matched Plex/Jellyfin item first.", "error")
            return redirect(url_for("match_preview", filter="matched"))
        try:
            people = PlexClient(cfg.plex_base_url, cfg.plex_token, timeout=15).item_people(row["plex_rating_key"])
        except Exception as exc:
            flash(f"Plex people discovery failed: {exc}", "error")
            return redirect(url_for("match_preview", library_mapping_id=row["library_mapping_id"], filter="matched"))
        return render_template("people.html", row=row, people=people)

    @app.post("/metadata/apply")
    def apply_metadata():
        return _apply_metadata_route()

    @app.post("/identity/apply")
    def apply_identity_legacy_alias():
        return _apply_metadata_route()

    @app.post("/match/reset-sync-state")
    def reset_match_sync_state():
        mapping_id_raw = request.form.get("library_mapping_id", "").strip()
        with connect() as conn:
            if mapping_id_raw.isdigit():
                row_ids = [
                    row["id"]
                    for row in conn.execute(
                        "SELECT id FROM sync_items WHERE library_mapping_id = ?",
                        (int(mapping_id_raw),),
                    ).fetchall()
                ]
                if row_ids:
                    placeholders = ",".join("?" for _ in row_ids)
                    conn.execute(f"DELETE FROM sync_audit WHERE sync_item_id IN ({placeholders})", row_ids)
                deleted = conn.execute(
                    "DELETE FROM sync_items WHERE library_mapping_id = ?",
                    (int(mapping_id_raw),),
                ).rowcount
                flash(f"Reset preview/sync state for selected library mapping ({deleted} rows removed). Settings, libraries and path mappings were kept.", "success")
                return redirect(url_for("match_preview", library_mapping_id=mapping_id_raw, filter="all"))

            conn.execute("DELETE FROM sync_audit")
            deleted = conn.execute("DELETE FROM sync_items").rowcount
            flash(f"Reset all preview/sync state ({deleted} rows removed). Settings, libraries and path mappings were kept.", "success")
            return redirect(url_for("match_preview"))

    def _apply_metadata_route():
        cfg = AppConfig.load()
        selected_ids = [int(value) for value in request.form.getlist("sync_item_id") if value.isdigit()]
        return_mapping_id = request.form.get("library_mapping_id", "").strip()
        if not selected_ids:
            flash("Select at least one path-matched item to update.", "error")
            return redirect(url_for("match_preview", library_mapping_id=return_mapping_id, filter="issues"))
        if not cfg.has_plex or not cfg.has_jellyfin:
            flash("Plex and Jellyfin must be configured first.", "error")
            return redirect(url_for("match_preview", library_mapping_id=return_mapping_id, filter="issues"))
        plex = PlexClient(cfg.plex_base_url, cfg.plex_token, timeout=15)
        jellyfin = JellyfinClient(cfg.jellyfin_base_url, cfg.jellyfin_api_key, timeout=15)
        applied = failed = skipped = 0
        errors: list[str] = []
        for sync_item_id in selected_ids:
            try:
                result = _apply_metadata_overwrite(sync_item_id, plex, jellyfin)
                if result == "applied":
                    applied += 1
                elif result == "skipped":
                    skipped += 1
            except Exception as exc:
                failed += 1
                error_text = str(exc)
                errors.append(error_text)
                current_app.logger.exception("Metadata overwrite failed for sync_item_id=%s: %s", sync_item_id, error_text)
                _record_metadata_failure(sync_item_id, error_text)
        category = "success" if failed == 0 else "error"
        message = f"Metadata overwrite complete: {applied} applied, {skipped} skipped, {failed} failed."
        if errors:
            message += f" First error: {errors[0]}"
        flash(message, category)
        return redirect(url_for("match_preview", library_mapping_id=return_mapping_id, filter="issues"))

    return app


def _safe_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _run_match_preview(library_mapping_id: int, cfg: AppConfig) -> dict[str, int]:
    if not cfg.has_plex or not cfg.has_jellyfin:
        raise RuntimeError("Plex and Jellyfin must be configured first.")
    with connect() as conn:
        mapping = conn.execute("SELECT * FROM library_mappings WHERE id = ?", (library_mapping_id,)).fetchone()
        if not mapping:
            raise RuntimeError("Library mapping not found.")
        path_rows = conn.execute(
            "SELECT plex_path, jellyfin_path FROM path_mappings WHERE library_mapping_id = ? AND enabled = 1",
            (library_mapping_id,),
        ).fetchall()
    if not path_rows:
        raise RuntimeError("Add a path mapping for this library first.")
    path_mappings = [dict(row) for row in path_rows]
    plex_client = PlexClient(cfg.plex_base_url, cfg.plex_token, timeout=30)
    jellyfin_client = JellyfinClient(cfg.jellyfin_base_url, cfg.jellyfin_api_key, timeout=30)
    plex_items = plex_client.library_items(mapping["plex_library_key"])
    jellyfin_items = jellyfin_client.library_items(mapping["jellyfin_library_id"], mapping["media_type"])
    plex_by_path: dict[str, dict[str, object]] = {}
    jellyfin_by_path: dict[str, dict[str, object]] = {}
    results: list[dict[str, object]] = []
    for item in plex_items:
        canonical = canonicalise_path(str(item.get("path") or ""), path_mappings, "plex")
        if not item.get("path") or canonical == item.get("path"):
            results.append({"status": "path_failure", "canonical": canonical, "plex": item, "jellyfin": None, "warning": "plex path not mapped"})
        else:
            plex_by_path[canonical] = item
    for item in jellyfin_items:
        canonical = canonicalise_path(str(item.get("path") or ""), path_mappings, "jellyfin")
        if not item.get("path") or canonical == item.get("path"):
            results.append({"status": "path_failure", "canonical": canonical, "plex": None, "jellyfin": item, "warning": "jellyfin path not mapped"})
        else:
            jellyfin_by_path[canonical] = item
    for canonical, plex_item in plex_by_path.items():
        jellyfin_item = jellyfin_by_path.get(canonical)
        if not jellyfin_item:
            results.append({"status": "unmatched_plex", "canonical": canonical, "plex": plex_item, "jellyfin": None, "warning": ""})
            continue
        row_for_warning = {
            "plex_title": plex_item.get("title"),
            "jellyfin_title": jellyfin_item.get("title"),
            "plex_year": plex_item.get("year"),
            "jellyfin_year": jellyfin_item.get("year"),
            "plex_duration_ms": plex_item.get("duration_ms"),
            "jellyfin_duration_ms": jellyfin_item.get("duration_ms"),
        }
        warning_parts = warning_reasons(row_for_warning)
        metadata_warning = _metadata_difference_warning(plex_item, jellyfin_item)
        if metadata_warning:
            warning_parts.append(metadata_warning)
        warning = ", ".join(warning_parts)
        results.append({"status": "matched", "canonical": canonical, "plex": plex_item, "jellyfin": jellyfin_item, "warning": warning})
    for canonical, jellyfin_item in jellyfin_by_path.items():
        if canonical not in plex_by_path:
            results.append({"status": "unmatched_jellyfin", "canonical": canonical, "plex": None, "jellyfin": jellyfin_item, "warning": ""})
    with connect() as conn:
        previous_sync_state = {
            row["canonical_path"]: row
            for row in conn.execute(
                """
                SELECT canonical_path, metadata_synced_at, metadata_sync_status, metadata_sync_error,
                       metadata_scope_version, metadata_fields,
                       identity_synced_at, identity_sync_status, identity_sync_error
                FROM sync_items
                WHERE library_mapping_id = ?
                """,
                (library_mapping_id,),
            ).fetchall()
        }
        conn.execute("DELETE FROM sync_items WHERE library_mapping_id = ?", (library_mapping_id,))
        for result in results:
            plex_item = result.get("plex") or {}
            jellyfin_item = result.get("jellyfin") or {}
            canonical = str(result.get("canonical") or "")
            previous = previous_sync_state.get(canonical)
            conn.execute(
                """
                INSERT INTO sync_items(
                    library_mapping_id, plex_rating_key, plex_guid, jellyfin_item_id,
                    media_type, canonical_path, plex_path, jellyfin_path,
                    plex_title, plex_year, plex_duration_ms,
                    jellyfin_title, jellyfin_year, jellyfin_duration_ms,
                    match_status, match_warning,
                    metadata_synced_at, metadata_sync_status, metadata_sync_error,
                    metadata_scope_version, metadata_fields,
                    identity_synced_at, identity_sync_status, identity_sync_error,
                    last_seen_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    library_mapping_id,
                    plex_item.get("rating_key"),
                    plex_item.get("guid"),
                    jellyfin_item.get("item_id"),
                    mapping["media_type"],
                    canonical,
                    plex_item.get("path"),
                    jellyfin_item.get("path"),
                    plex_item.get("title"),
                    plex_item.get("year"),
                    plex_item.get("duration_ms"),
                    jellyfin_item.get("title"),
                    jellyfin_item.get("year"),
                    jellyfin_item.get("duration_ms"),
                    result.get("status"),
                    result.get("warning") or "",
                    # 0.3.9: failed metadata state is intentionally not preserved across a fresh preview scan.
                    previous["metadata_synced_at"] if previous and previous["metadata_sync_status"] != "failed" else None,
                    previous["metadata_sync_status"] if previous and previous["metadata_sync_status"] != "failed" else None,
                    previous["metadata_sync_error"] if previous and previous["metadata_sync_status"] != "failed" else None,
                    previous["metadata_scope_version"] if previous and previous["metadata_sync_status"] != "failed" else None,
                    previous["metadata_fields"] if previous and previous["metadata_sync_status"] != "failed" else None,
                    previous["identity_synced_at"] if previous else None,
                    previous["identity_sync_status"] if previous else None,
                    previous["identity_sync_error"] if previous else None,
                ),
            )
    return {
        "matched": sum(1 for r in results if r["status"] == "matched"),
        "warnings": sum(1 for r in results if r.get("warning")),
        "unmatched_plex": sum(1 for r in results if r["status"] == "unmatched_plex"),
        "unmatched_jellyfin": sum(1 for r in results if r["status"] == "unmatched_jellyfin"),
        "path_failure": sum(1 for r in results if r["status"] == "path_failure"),
    }


def _apply_metadata_overwrite(sync_item_id: int, plex: PlexClient, jellyfin: JellyfinClient) -> str:
    with connect() as conn:
        row = conn.execute("SELECT * FROM sync_items WHERE id = ?", (sync_item_id,)).fetchone()
    if not row or row["match_status"] != "matched" or not row["plex_rating_key"] or not row["canonical_path"]:
        _record_metadata_audit(sync_item_id, "skipped", "Item is not a path-matched Plex/Jellyfin pair.")
        return "skipped"
    plex_meta = plex.item_metadata(row["plex_rating_key"])
    if not plex_meta.get("title"):
        raise RuntimeError("Plex metadata did not include a title.")
    current_jellyfin_id = _find_current_jellyfin_item_id_by_path(row, jellyfin)
    if not current_jellyfin_id:
        raise RuntimeError("Could not rebind Jellyfin item by canonical path before metadata write.")
    payload = _build_jellyfin_web_metadata_payload(current_jellyfin_id, plex_meta)
    jellyfin.update_item_metadata_editor_payload(current_jellyfin_id, payload)
    metadata_fields = json.dumps([field for field in METADATA_FIELDS if field in payload], sort_keys=True)
    with connect() as conn:
        conn.execute(
            """
            UPDATE sync_items
            SET jellyfin_item_id = ?, jellyfin_title = ?, jellyfin_year = ?, match_warning = '',
                metadata_synced_at = CURRENT_TIMESTAMP, metadata_sync_status = 'applied', metadata_sync_error = NULL,
                metadata_scope_version = ?, metadata_fields = ?,
                identity_synced_at = CURRENT_TIMESTAMP, identity_sync_status = 'applied', identity_sync_error = NULL,
                last_synced_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (current_jellyfin_id, plex_meta.get("title"), plex_meta.get("year"), METADATA_SCOPE_VERSION, metadata_fields, sync_item_id),
        )
        conn.execute(
            """
            INSERT INTO sync_audit(sync_item_id, operation, status, details)
            VALUES (?, 'metadata_overwrite', 'applied', ?)
            """,
            (
                sync_item_id,
                f"Applied {METADATA_SCOPE_VERSION}: {plex_meta.get('title')} ({plex_meta.get('year') or ''}); fields={metadata_fields}",
            ),
        )
    return "applied"


def _find_current_jellyfin_item_id_by_path(row: Any, jellyfin: JellyfinClient) -> str | None:
    with connect() as conn:
        mapping = conn.execute("SELECT * FROM library_mappings WHERE id = ?", (row["library_mapping_id"],)).fetchone()
        path_rows = conn.execute(
            "SELECT plex_path, jellyfin_path FROM path_mappings WHERE library_mapping_id = ? AND enabled = 1",
            (row["library_mapping_id"],),
        ).fetchall()
    if not mapping or not path_rows:
        return row["jellyfin_item_id"]
    path_mappings = [dict(path_row) for path_row in path_rows]
    matches: list[str] = []
    for item in jellyfin.library_items(mapping["jellyfin_library_id"], mapping["media_type"]):
        canonical = canonicalise_path(str(item.get("path") or ""), path_mappings, "jellyfin")
        if canonical == row["canonical_path"]:
            item_id = str(item.get("item_id") or "")
            if item_id:
                matches.append(item_id)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise RuntimeError(f"Multiple current Jellyfin items match canonical path {row['canonical_path']}.")
    return None



def _metadata_difference_warning(plex_meta: dict[str, object], jellyfin_item: dict[str, object]) -> str:
    differences: list[str] = []

    _diff_text(differences, "Name", plex_meta.get("title"), jellyfin_item.get("title"))
    _diff_int(differences, "ProductionYear", plex_meta.get("year"), jellyfin_item.get("year"))
    _diff_date(differences, "PremiereDate", plex_meta.get("originally_available_at"), jellyfin_item.get("premiere_date"))
    _diff_text(differences, "Overview", plex_meta.get("summary"), jellyfin_item.get("overview"))
    _diff_list(differences, "Taglines", [plex_meta.get("tagline")] if plex_meta.get("tagline") else [], jellyfin_item.get("taglines"))
    _diff_text(differences, "OfficialRating", plex_meta.get("content_rating"), jellyfin_item.get("official_rating"))
    _diff_rating(differences, "CommunityRating", plex_meta.get("audience_rating"), jellyfin_item.get("community_rating"), multiplier=1)
    _diff_rating(differences, "CriticRating", plex_meta.get("critic_rating"), jellyfin_item.get("critic_rating"), multiplier=10)
    _diff_list(differences, "Genres", plex_meta.get("genres"), jellyfin_item.get("genres"))
    _diff_list(differences, "Studios", plex_meta.get("studios"), jellyfin_item.get("studios"))
    _diff_list(differences, "ProductionLocations", plex_meta.get("countries"), jellyfin_item.get("production_locations"))
    _diff_provider_ids(differences, plex_meta.get("provider_ids"), jellyfin_item.get("provider_ids"))

    if not differences:
        return ""
    shown = ", ".join(differences[:8])
    if len(differences) > 8:
        shown += f", +{len(differences) - 8} more"
    return f"core metadata differs: {shown}"


def _diff_text(differences: list[str], field: str, plex_value: object, jellyfin_value: object) -> None:
    if _normalise_text(plex_value) != _normalise_text(jellyfin_value):
        differences.append(field)


def _diff_int(differences: list[str], field: str, plex_value: object, jellyfin_value: object) -> None:
    if _normalise_int(plex_value) != _normalise_int(jellyfin_value):
        differences.append(field)


def _diff_date(differences: list[str], field: str, plex_value: object, jellyfin_value: object) -> None:
    if _date_prefix(plex_value) != _date_prefix(jellyfin_value):
        differences.append(field)


def _diff_list(differences: list[str], field: str, plex_value: object, jellyfin_value: object) -> None:
    if _normalise_list(plex_value) != _normalise_list(jellyfin_value):
        differences.append(field)


def _diff_rating(differences: list[str], field: str, plex_value: object, jellyfin_value: object, multiplier: int) -> None:
    plex_rating = _normalise_float(plex_value)
    jellyfin_rating = _normalise_float(jellyfin_value)
    if plex_rating is not None:
        plex_rating *= multiplier
    if plex_rating is None and jellyfin_rating is None:
        return
    if plex_rating is None or jellyfin_rating is None or abs(plex_rating - jellyfin_rating) > 0.1:
        differences.append(field)


def _diff_provider_ids(differences: list[str], plex_value: object, jellyfin_value: object) -> None:
    plex_ids = plex_value if isinstance(plex_value, dict) else {}
    jellyfin_ids = jellyfin_value if isinstance(jellyfin_value, dict) else {}
    for plex_key, jellyfin_key in (("imdb", "Imdb"), ("tmdb", "Tmdb"), ("tvdb", "Tvdb")):
        if _normalise_text(plex_ids.get(plex_key)) != _normalise_text(jellyfin_ids.get(jellyfin_key)):
            differences.append(f"ProviderIds.{jellyfin_key}")


def _normalise_text(value: object) -> str:
    return str(value or "").strip()


def _normalise_int(value: object) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalise_float(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalise_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        key = _metadata_value_key(item)
        if key and key not in result:
            result.append(key)
    return sorted(result)


def _metadata_value(value: object) -> str:
    """Clean list-style metadata values without ASCII-folding user-visible text.

    This intentionally preserves accents, apostrophes, hyphens, and non-English
    alphabets while fixing common spacing issues that can create duplicate
    Jellyfin ItemValues, e.g. "Action &Adventure" vs "Action & Adventure".
    """
    text = _string(value)
    text = unicodedata.normalize("NFC", text)
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    # Only normalise ampersand spacing when a space already exists on one side.
    # This avoids changing compact names such as AT&T.
    text = re.sub(r"\s+&\s*", " & ", text)
    text = re.sub(r"\s*&\s+", " & ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _metadata_value_key(value: object) -> str:
    return _metadata_value(value).casefold()


def _date_prefix(value: object) -> str:
    text = _normalise_text(value)
    return text[:10] if len(text) >= 10 else text

def _build_jellyfin_web_metadata_payload(jellyfin_item_id: str, plex_meta: dict[str, object]) -> dict[str, object]:
    title = _string(plex_meta.get("title"))
    year = plex_meta.get("year")
    tagline = _metadata_value(plex_meta.get("tagline"))
    return {
        "Id": jellyfin_item_id,
        "Name": title,
        "OriginalTitle": _string(plex_meta.get("original_title")),
        "ForcedSortName": _string(plex_meta.get("sort_title") or title),
        "CommunityRating": _rating_or_blank(plex_meta.get("audience_rating")),
        "CriticRating": _critic_rating_or_blank(plex_meta.get("critic_rating")),
        "IndexNumber": None,
        "AirsBeforeSeasonNumber": "",
        "AirsAfterSeasonNumber": "",
        "AirsBeforeEpisodeNumber": "",
        "ParentIndexNumber": None,
        "DisplayOrder": "",
        "Album": "",
        "AlbumArtists": [],
        "ArtistItems": [],
        "Overview": _string(plex_meta.get("summary")),
        "Status": "",
        "AirDays": [],
        "AirTime": "",
        "Genres": _string_list(plex_meta.get("genres")),
        "Tags": [],
        "Studios": _studio_items(plex_meta.get("studios")),
        "PremiereDate": _premiere_date_or_none(plex_meta.get("originally_available_at")),
        "DateCreated": None,
        "EndDate": None,
        "ProductionYear": str(year) if year is not None else "",
        "Height": "",
        "AspectRatio": "",
        "Video3DFormat": "",
        "OfficialRating": _string(plex_meta.get("content_rating")),
        "CustomRating": "",
        "People": [],
        "LockData": False,
        "LockedFields": [],
        "ProviderIds": _provider_ids(plex_meta.get("provider_ids")),
        "ProductionLocations": _string_list(plex_meta.get("countries")),
        "PreferredMetadataLanguage": "",
        "PreferredMetadataCountryCode": "",
        "Taglines": [tagline] if tagline else [],
    }


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _metadata_value(item)
        key = text.casefold()
        if text and key not in seen:
            result.append(text)
            seen.add(key)
    return result


def _studio_items(value: object) -> list[dict[str, str]]:
    return [{"Name": name} for name in _string_list(value)]


def _provider_ids(value: object) -> dict[str, str]:
    provider_ids = {
        "Imdb": "",
        "Tmdb": "",
        "TmdbCollection": "",
        "TvdbCollection": "",
        "Tvdb": "",
        "TvdbSlug": "",
    }
    if not isinstance(value, dict):
        return provider_ids
    for key, target in (("imdb", "Imdb"), ("tmdb", "Tmdb"), ("tvdb", "Tvdb")):
        text = _string(value.get(key)).strip()
        if text:
            provider_ids[target] = text
    return provider_ids


def _string(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _rating_or_blank(value: object) -> object:
    try:
        rating = float(value)
    except (TypeError, ValueError):
        return ""
    if 0 <= rating <= 10:
        return rating
    return ""


def _critic_rating_or_blank(value: object) -> object:
    try:
        rating = float(value)
    except (TypeError, ValueError):
        return ""
    if 0 <= rating <= 10:
        return rating * 10
    if 0 <= rating <= 100:
        return rating
    return ""


def _premiere_date_or_none(value: object) -> str | None:
    if not _valid_plex_date(value):
        return None
    return f"{value}T00:00:00.0000000Z"


def _valid_plex_date(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parts = value.split("-")
    if len(parts) != 3:
        return False
    year, month, day = parts
    return len(year) == 4 and len(month) == 2 and len(day) == 2 and all(part.isdigit() for part in parts)


def _record_metadata_failure(sync_item_id: int, error: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE sync_items
            SET metadata_sync_status = 'failed', metadata_sync_error = ?,
                identity_sync_status = 'failed', identity_sync_error = ?
            WHERE id = ?
            """,
            (error, error, sync_item_id),
        )
        conn.execute(
            """
            INSERT INTO sync_audit(sync_item_id, operation, status, details)
            VALUES (?, 'metadata_overwrite', 'failed', ?)
            """,
            (sync_item_id, error),
        )


def _record_metadata_audit(sync_item_id: int, status: str, details: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO sync_audit(sync_item_id, operation, status, details)
            VALUES (?, 'metadata_overwrite', ?, ?)
            """,
            (sync_item_id, status, details),
        )
