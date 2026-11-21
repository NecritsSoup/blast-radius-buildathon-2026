FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
# CPU seconds are charged, so keep numeric libraries single-threaded.
ENV PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
# Test dependencies (pinned by the organisers' lock) and sqlglot at the pinned
# commit, used only to re-run cheap candidate tests against each patch. The
# coverage map itself is generated offline and committed; no tests run here.
COPY requirements.lock fetch_sqlglot.py /app/build/
RUN pip install --no-cache-dir --only-binary=:all: -c /app/build/requirements.lock \
        duckdb pandas numpy python-dateutil pytz tzdata typing_extensions \
    && python /app/build/fetch_sqlglot.py /app/sqlglot \
    && if [ -d /app/sqlglot ]; then python -m compileall -q -j 4 /app/sqlglot/sqlglot /app/sqlglot/tests; fi
COPY selector.py semantic.py verify.py coverage-map.json /app/
CMD ["python", "/app/selector.py"]
