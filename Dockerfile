# ── PULSE-AI Backend — FastAPI + Python ──────────────────────────────────────
# Base: Python 3.11 slim (Debian Bookworm)
# Installs: Microsoft ODBC Driver 18 (required by pyodbc → Microsoft Fabric SQL)

FROM python:3.11-slim-bookworm

# Install ODBC Driver 18 for SQL Server
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg2 unixodbc-dev \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && curl -fsSL https://packages.microsoft.com/config/debian/12/prod.list \
        -o /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer-cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY api.py app.py ./
COPY src/ ./src/
COPY business_context.json chart_theme.json ./

# .cache dir — schema, SQL cache, entity index, charts, memory threads
# Mounted as a volume in production so data survives container restarts
RUN mkdir -p .cache/charts

EXPOSE 8000

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
