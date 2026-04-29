FROM python:3.13-slim

WORKDIR /app

COPY services/frontend/ /app/

EXPOSE 5000

HEALTHCHECK --interval=10s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')"

CMD ["python", "server.py"]
