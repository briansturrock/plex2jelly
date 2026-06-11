from __future__ import annotations

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
                    INSERT INTO library_mappings(
                        name, plex_library_key, plex_library_name,
                        jellyfin_library_id, jellyfin_library_name, media_type, enabled
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
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
                    f"Scan complete: {result['matched']} matched, {result['warnings']} warnings, "
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
            where: list[str] = []
            if selected_mapping_id:
                where.append("library_mapping_id = ?")
                params.append(int(selected_mapping_id))
            if status_filter == "issues":
                where.append("match_status != 'matched' OR COALESCE(match_warning, '') != ''")
            elif status_filter in {"matched", "warnings", "unmatched_plex", "unmatched_jellyfin", "path_failure"}:
                if status_filter == "warnings":
                    where.append("COALESCE(match_warning, '') != ''")
                else:
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
                       SUM(CASE WHEN match_status = 'path_failure' THEN 1 ELSE 0 END) AS path_failure
                FROM sync_items{where_sql}
                """,
                params,
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
        )

    @app.post("/identity/apply")
    def apply_identity():
        cfg = AppConfig.load()
        selected_ids = [int(value) for value in request.form.getlist("sync_item_id") if value.isdigit()]
        return_mapping_id = request.form.get("library_mapping_id", "").strip()
        if not selected_ids:
            flash("Select at least one matched item to overwrite.", "error")
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
                result = _apply_identity_overwrite(sync_item_id, plex, jellyfin)
                if result == "applied":
                    applied += 1
                elif result == "skipped":
                    skipped += 1
            except Exception as exc:
                failed += 1
                error_text = str(exc)
                errors.append(error_text)
                current_app.logger.exception("Identity overwrite failed for sync_item_id=%s: %s", sync_item_id, error_text)
                _record_identity_failure(sync_item_id, error_text)
        category = "success" if failed == 0 else "error"
        message = f"Identity overwrite complete: {applied} applied, {skipped} skipped, {failed} failed."
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
    plex_items = PlexClient(cfg.plex_base_url, cfg.plex_token, timeout=30).library_items(mapping["plex_library_key"])
    jellyfin_items = JellyfinClient(cfg.jellyfin_base_url, cfg.jellyfin_api_key, timeout=30).library_items(
        mapping["jellyfin_library_id"], mapping["media_type"]
    )

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
        warning = ", ".join(warning_reasons(row_for_warning))
        results.append({"status": "matched", "canonical": canonical, "plex": plex_item, "jellyfin": jellyfin_item, "warning": warning})

    for canonical, jellyfin_item in jellyfin_by_path.items():
        if canonical not in plex_by_path:
            results.append({"status": "unmatched_jellyfin", "canonical": canonical, "plex": None, "jellyfin": jellyfin_item, "warning": ""})

    with connect() as conn:
        conn.execute("DELETE FROM sync_items WHERE library_mapping_id = ?", (library_mapping_id,))
        for result in results:
            plex_item = result.get("plex") or {}
            jellyfin_item = result.get("jellyfin") or {}
            conn.execute(
                """
                INSERT INTO sync_items(
                    library_mapping_id, plex_rating_key, plex_guid, jellyfin_item_id, media_type,
                    canonical_path, plex_path, jellyfin_path, plex_title, plex_year, plex_duration_ms,
                    jellyfin_title, jellyfin_year, jellyfin_duration_ms, match_status, match_warning, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    library_mapping_id,
                    plex_item.get("rating_key"),
                    plex_item.get("guid"),
                    jellyfin_item.get("item_id"),
                    mapping["media_type"],
                    result.get("canonical"),
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
                ),
            )

    return {
        "matched": sum(1 for r in results if r["status"] == "matched"),
        "warnings": sum(1 for r in results if r.get("warning")),
        "unmatched_plex": sum(1 for r in results if r["status"] == "unmatched_plex"),
        "unmatched_jellyfin": sum(1 for r in results if r["status"] == "unmatched_jellyfin"),
        "path_failure": sum(1 for r in results if r["status"] == "path_failure"),
    }


def _apply_identity_overwrite(sync_item_id: int, plex: PlexClient, jellyfin: JellyfinClient) -> str:
    with connect() as conn:
        row = conn.execute("SELECT * FROM sync_items WHERE id = ?", (sync_item_id,)).fetchone()

    if not row or row["match_status"] != "matched" or not row["plex_rating_key"] or not row["jellyfin_item_id"]:
        _record_identity_audit(sync_item_id, "skipped", "Item is not a path-matched Plex/Jellyfin pair.")
        return "skipped"

    plex_meta = plex.item_metadata(row["plex_rating_key"])
    if not plex_meta.get("title"):
        raise RuntimeError("Plex metadata did not include a title.")

    # Do not call GET /Items/{id} before updating. On some Jellyfin 10.11 builds that endpoint
    # returns 400 unless called in a user-scoped form. For identity overwrite we already have
    # the stable Jellyfin item id from the library scan, so build an editor-style DTO directly.
    payload: dict[str, object] = {
        "Id": row["jellyfin_item_id"],
        "Type": "Movie" if (row["media_type"] or "movie") == "movie" else row["media_type"],
        "Name": plex_meta.get("title") or row["jellyfin_title"] or row["plex_title"],
        "OriginalTitle": plex_meta.get("original_title") or plex_meta.get("title") or "",
        "ForcedSortName": plex_meta.get("sort_title") or plex_meta.get("title") or "",
        "SortName": plex_meta.get("sort_title") or plex_meta.get("title") or "",
        "Overview": plex_meta.get("summary") or "",
        "Tagline": plex_meta.get("tagline") or "",
        "Genres": [],
        "Tags": [],
        "Studios": [],
        "People": [],
        "ProviderIds": {},
        "LockedFields": [],
    }

    if plex_meta.get("year") is not None:
        payload["ProductionYear"] = plex_meta.get("year")
    if plex_meta.get("originally_available_at"):
        payload["PremiereDate"] = f"{plex_meta['originally_available_at']}T00:00:00.0000000Z"
    if plex_meta.get("content_rating"):
        payload["OfficialRating"] = plex_meta.get("content_rating")
    if plex_meta.get("audience_rating") is not None:
        payload["CommunityRating"] = plex_meta.get("audience_rating")

    jellyfin.update_item(row["jellyfin_item_id"], payload)

    with connect() as conn:
        conn.execute(
            """
            UPDATE sync_items
            SET jellyfin_title = ?, jellyfin_year = ?, match_warning = '',
                identity_synced_at = CURRENT_TIMESTAMP,
                identity_sync_status = 'applied', identity_sync_error = ''
            WHERE id = ?
            """,
            (plex_meta.get("title"), plex_meta.get("year"), sync_item_id),
        )
        conn.execute(
            """
            INSERT INTO sync_audit(sync_item_id, operation, status, details)
            VALUES (?, 'identity_overwrite', 'applied', ?)
            """,
            (sync_item_id, f"Applied Plex identity: {plex_meta.get('title')} ({plex_meta.get('year') or ''})"),
        )
    return "applied"


def _record_identity_failure(sync_item_id: int, error: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE sync_items
            SET identity_sync_status = 'failed', identity_sync_error = ?
            WHERE id = ?
            """,
            (error, sync_item_id),
        )
        conn.execute(
            """
            INSERT INTO sync_audit(sync_item_id, operation, status, details)
            VALUES (?, 'identity_overwrite', 'failed', ?)
            """,
            (sync_item_id, error),
        )


def _record_identity_audit(sync_item_id: int, status: str, details: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO sync_audit(sync_item_id, operation, status, details)
            VALUES (?, 'identity_overwrite', ?, ?)
            """,
            (sync_item_id, status, details),
        )
