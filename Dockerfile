FROM python:3.11-alpine AS base

WORKDIR /AgHub

# Setup non-root user for safety
RUN addgroup --system python
RUN adduser --system fastapi
RUN usermod -a -G dialout fastapi

# Setup virtualenv
RUN pip install --no-cache-dir poetry
COPY --chown=fastapi:python pyproject.toml .
RUN python -m poetry config virtualenvs.create false
RUN poetry install

# Copy source code into container
COPY --chown=fastapi:python src ./src

FROM base AS production
EXPOSE 8000
USER fastapi
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
