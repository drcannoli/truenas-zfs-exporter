FROM python:3.12-slim AS build

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim

COPY --from=build /venv /venv
ENV PATH="/venv/bin:$PATH"

WORKDIR /app
COPY exporter.py .

RUN useradd --system --uid 1000 exporter
USER exporter

EXPOSE 9134

ENTRYPOINT ["python", "-u", "exporter.py"]
