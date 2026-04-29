FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    g++ curl && rm -rf /var/lib/apt/lists/*

COPY services/agents/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /app

EXPOSE 5100

HEALTHCHECK --interval=10s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5100/api/health')"

CMD ["python", "-m", "uvicorn", "services.agents.agent_server:app", "--host", "0.0.0.0", "--port", "5100"]
