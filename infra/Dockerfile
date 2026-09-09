FROM python:3.12-slim

ENV MDREVIEW_DATA=/data \
    PORT=8080 \
    PYTHONUNBUFFERED=1 \
    MDREVIEW_WEB_DIR=/app/web \
    PYTHONPATH=/app/src

# git (#379): MDREVIEW_ENABLE_GIT_HISTORY shells out to the system `git http-backend` CGI binary
# to serve clones; python:3.12-slim ships none. stdlib-only rule stays intact — this is the system
# binary via subprocess, not a pip package (dulwich/pygit2 were ruled out in the design).
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY src/ ./src/
COPY web/app/ ./web/

RUN mkdir -p /data
VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').status==200 else 1)"

CMD ["python", "-m", "mdreview"]
