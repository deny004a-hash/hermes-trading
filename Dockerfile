FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"
COPY pyproject.toml uv.lock ./
COPY hermes_trading ./hermes_trading
COPY state ./state
RUN cp -a /app/state /app/state_seed && uv sync --frozen --no-dev
ENV HERMES_TRADING_MODE=paper
ENV HERMES_TRADING_I_ACCEPT_RISK=false
ENV HERMES_STATE_DIR=/app/state
ENV HERMES_STATE_SEED_DIR=/app/state_seed
CMD ["sh", "-c", "if [ ! -f \"$HERMES_STATE_DIR/goal.yaml\" ]; then cp -a \"$HERMES_STATE_SEED_DIR/.\" \"$HERMES_STATE_DIR/\"; fi; if [ ! -f \"$HERMES_STATE_DIR/.initial_reflection_done\" ]; then uv run python -m hermes_trading.reflect --fallback --state-dir \"$HERMES_STATE_DIR\" && touch \"$HERMES_STATE_DIR/.initial_reflection_done\"; fi; exec uv run python -m hermes_trading.run"]
