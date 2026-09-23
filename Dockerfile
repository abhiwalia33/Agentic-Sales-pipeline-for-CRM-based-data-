# Runtime image for the Sales Pipeline Q&A Streamlit app.
#
# data/ is intentionally NOT generated at build time - it's git-ignored
# and self-heals via ensure_database_exists() (src/pipeline_agent.py) on
# the app's first request inside the running container. That function
# imports and calls src/generate_data.py and src/clean_data.py directly,
# so this image only needs to include them - not pre-run them.

FROM python:3.12-slim

WORKDIR /app

# Dependencies first, as their own layer, so a rebuild that only touches
# app code doesn't reinstall every package.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code. Deliberately selective COPYs (not `COPY . .`) so nothing
# outside this list - including .env - ever enters the image.
COPY app.py .
COPY src/ src/
COPY .streamlit/ .streamlit/

# Streamlit's default port.
EXPOSE 8501

# No .env is present in the image; supply the key at run time instead:
#   docker run -p 8501:8501 -e OPENAI_API_KEY=sk-... <image>
#
# --server.address=0.0.0.0 - Streamlit binds localhost-only by default,
#   which is unreachable from outside the container without this.
# --server.headless=true - skips Streamlit's interactive first-run
#   prompt, which would otherwise hang a container with no stdin attached.
CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true"]
