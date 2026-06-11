# plex2jelly

`plex2jelly` keeps Jellyfin as a warm-standby replica of Plex.

Current direction of travel:

- Plex is the catalogue source of truth.
- Metadata, artwork and collections will sync Plex → Jellyfin.
- Watched status will sync two-way.
- Matching is based on mapped media file paths.
- A local SQLite database stores sync state.

## v0.1

Initial container, configuration and health checks.

## Commands

```bash
python -m app.main health
python -m app.main libraries
```

In Docker:

```bash
docker compose run --rm plex2jelly health
docker compose run --rm plex2jelly libraries
```
