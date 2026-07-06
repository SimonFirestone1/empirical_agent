# Sandbox image for eval trials: Claude Code CLI + scientific Python stack.
# Build:  docker build -t claude-eval-harness .
# Trials run with --network none; all data is pre-staged into /workspace.
FROM python:3.12-slim

# System deps + Node.js (required by the Claude Code CLI)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl git \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# Python deps used by eval tasks
RUN pip install --no-cache-dir \
    pyyaml pandas numpy statsmodels matplotlib scipy scikit-learn

# Non-root user for running trials
RUN useradd -m -u 1000 -s /bin/bash trialuser

# In-container trial helper
COPY docker_run_trial.sh /usr/local/bin/docker_run_trial.sh
RUN chmod +x /usr/local/bin/docker_run_trial.sh

USER trialuser
WORKDIR /workspace
