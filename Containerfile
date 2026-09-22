FROM docker.io/library/python:3.12-alpine@sha256:4c47124a8391cb7a9f571164147d154777cf012a4ece5f86097130d7a4478111

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SUB_BASES_FILE=/config/sub_bases.json \
    SUB_METADATA_FILE=/config/sub_metadata.json

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY submerge/ ./submerge/
USER 65532:65532
EXPOSE 18080
CMD ["python", "-m", "submerge"]
