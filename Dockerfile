FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY exporter.py .

RUN useradd --system --uid 1000 exporter
USER exporter

EXPOSE 9134

ENTRYPOINT ["python", "-u", "exporter.py"]
