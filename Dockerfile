# Container-image Lambda (DECISIONS.md §3): LangGraph + deps exceed the zip limit,
# so the bot ships as an image. Build context is the repo root; CDK builds this.
FROM public.ecr.aws/lambda/python:3.13

# Install the package with the extras the handler needs (broker + aws + reasoning).
# Copy only what's needed for a clean, cache-friendly install.
COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir ".[broker,aws,reasoning]"

# LAMBDA_TASK_ROOT is the function code dir; the handler is importable from the
# installed package. Point Lambda at it: module.path:function.
CMD ["trading_bot.aws.handler.handler"]
