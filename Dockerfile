FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS maps
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN git init /sqlglot && cd /sqlglot && git remote add origin https://github.com/tobymao/sqlglot.git && git fetch --depth 1 origin eb11aec4be8cb2122afe713a4816662c3f0fb2fa && git checkout FETCH_HEAD
COPY requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r /app/requirements.lock coverage==7.17.4
COPY build_map.py structure.py comparison_data.py /app/
ENV PYTHONPATH=/sqlglot
RUN python /app/build_map.py && chmod 644 /app/coverage-map.json

FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
COPY selector.py semantic.py /app/
COPY --from=maps /app/coverage-map.json /app/coverage-map.json
CMD ["python", "/app/selector.py"]
