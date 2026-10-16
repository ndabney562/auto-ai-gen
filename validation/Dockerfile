# Build only from a trusted coordinator checkout, before any generated changes.
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
# Freeze OS package indexes as well as the base image and Python packages.
RUN sed -i 's|http://deb.debian.org/debian-security|https://snapshot.debian.org/archive/debian-security/20261015T000000Z|g; s|http://deb.debian.org/debian|https://snapshot.debian.org/archive/debian/20261015T000000Z|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install -y --no-install-recommends git nodejs \
    && rm -rf /var/lib/apt/lists/*
COPY validation/requirements.lock /opt/requirements.lock
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r /opt/requirements.lock
WORKDIR /workspace
ENV PYTHONPATH=/workspace PYTHONDONTWRITEBYTECODE=1
USER 65534:65534
