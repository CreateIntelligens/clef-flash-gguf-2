FROM local/clef-flash-api:1.0.1-telemetry

USER root
COPY --chown=10001:10001 main.py /app/main.py
USER 10001:10001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--timeout-graceful-shutdown", "10"]
