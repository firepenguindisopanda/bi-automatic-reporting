# The frontend is NOT built here. It is built on your machine and copied in.
#
# Why: the deploy target is s390x, and Vite 8 bundles with rolldown, a Rust
# bundler shipped as per-platform native bindings. On s390x every route is
# closed - node:22-alpine has the arch but is musl and rolldown publishes
# gnu-only; node:22-slim and node:22-bookworm are glibc but have no s390x
# image at all; node:22-trixie-slim has both and then segfaults mid-build
# (exit 139), which is a big-endian bug in the binding itself.
#
# None of that matters, because `frontend/dist` is static HTML/CSS/JS with no
# architecture in it. Building it on x86_64 and copying the output is correct,
# not a workaround - it also drops a whole Node stage off the image build.
#
# Build it first, or the COPY below fails:
#     cd frontend && npm ci && npm run build
# See SPEC.md section 4.

FROM python:3.13-slim AS builder
# Several dependencies have no s390x wheel and compile from source here:
# asyncpg (never published one) and Pillow, via weasyprint (likewise never).
# Bare gcc is not enough - --no-install-recommends leaves out libc6-dev and
# the build dies on `Python.h: fatal error: assert.h`. Pillow additionally
# hard-requires zlib and jpeg headers: `RequiredDependencyException: zlib`.
# Builder stage only - the runtime image below never gets a toolchain, but it
# does need the matching shared libraries. See SPEC.md section 4.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    zlib1g-dev \
    libjpeg-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

FROM python:3.13-slim
# fonts-inter is the reports' sans (labels, figures). The serif ships with the
# app in app/report/assets and is loaded by the PDF itself.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libjpeg62-turbo \
    fonts-inter \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r app && useradd -r -g app -d /app -s /sbin/nologin app

COPY --from=builder --chown=app:app /root/.local /home/app/.local

# useradd gives app a home of /app, which is root-owned, so fontconfig finds no
# writable cache and rebuilds it on every render - noisy, and paid per report.
# weasyprint still produces a valid PDF without this; it is just slower.
RUN mkdir -p /home/app/.cache && chown app:app /home/app/.cache
ENV HOME=/home/app

WORKDIR /app

COPY --chown=app:app app/ ./app/
COPY --chown=app:app pyproject.toml ./
COPY --chown=app:app requirements.txt ./

COPY --chown=app:app frontend/dist ./static

ENV PATH=/home/app/.local/bin:$PATH \
    PYTHONPATH=/home/app/.local/lib/python3.13/site-packages:$PYTHONPATH

RUN mkdir -p /data && chown app:app /data

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
