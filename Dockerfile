FROM python:3.11-slim AS base

WORKDIR /AgHub

# Setup virtualenv
RUN pip install --no-cache-dir poetry
COPY pyproject.toml .
RUN python -m poetry config virtualenvs.create false
RUN poetry install

# Copy source code into container
COPY src ./src

EXPOSE 8000
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
