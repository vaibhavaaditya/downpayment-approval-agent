FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY evals ./evals
COPY tests ./tests

EXPOSE 8000
ENV DPA_LLM_PROVIDER=auto

CMD ["uvicorn", "dpa_agent.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
