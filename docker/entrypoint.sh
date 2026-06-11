#!/usr/bin/env sh
set -eu

if [ "${1:-web}" = "web" ]; then
  exec gunicorn --bind 0.0.0.0:8099 --workers 1 --threads 4 'app.web:create_app()'
fi

exec python -m app.main "$@"
