@echo off
echo Starting AI-DA-Agents API on http://localhost:8000 ...
REM --reload watches for code changes, but the app writes to .cache/ on every
REM chat (SQL cache, chart PNGs, entity index). Without these excludes, those
REM writes restart the server mid-stream and the request fails with a 502.
uvicorn api:app --host 0.0.0.0 --port 8000 --reload ^
  --reload-exclude ".cache/*" ^
  --reload-exclude "*.sqlite3" ^
  --reload-exclude "*.json" ^
  --reload-exclude "*.png"
