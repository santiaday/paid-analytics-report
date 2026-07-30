# DeployBay container for the Multi-Channel Paid Analytics report.
# Pure Python (the pull + build.py pipeline are all Python) — no Node, no AWS. DeployBay builds this
# Dockerfile, injects the creds as env vars, and gates access with its ingress grant. The app serves
# the report on $PORT and rebuilds it on startup + daily + on /refresh (see server.py).
FROM python:3.12-slim

WORKDIR /app

# ca-certificates for the fetches that use urllib (Google/Meta/checkip); requests bundles its own.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Deps first for layer caching (boto3 is NOT needed here — no AWS; just requests + tzdata).
COPY app/requirements.txt ./app/requirements.txt
RUN pip install --no-cache-dir -r app/requirements.txt

# App code: the Python modules + the vendored report pipeline + the server.
COPY app ./app
COPY server.py ./server.py

ENV PORT=8080 \
    SITE_DIR=/tmp/pa/site \
    WORK_DIR=/tmp/pa
EXPOSE 8080

CMD ["python", "server.py"]
