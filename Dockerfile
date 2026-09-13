# HumJob on Hugging Face Spaces (Docker SDK).
# Full FastAPI app, pesto backend only (the web default). Other neural backends
# (crepe / fcnf0 / basic_pitch) are intentionally NOT installed to keep the image
# small; their imports are lazy, so the app runs fine without them.
#
# Build/run notes:
#   - HF Docker Spaces run the container as uid 1000 and expect the app on the
#     port declared by `app_port` in the Space README (7860 here).
#   - The DeepSeek coaching key is read from the env at request time. Set it as a
#     Space secret named DEEPSEEK_API_KEY (never bake it into the image).

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System deps: ffmpeg decodes uploaded audio; libsndfile backs soundfile.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Python deps. Order matters and mirrors requirements.txt's documented recipe:
#   1. runtime deps with numpy pinned to 2.0.2,
#   2. CPU-only torch (pesto reuses it) from the official PyTorch CPU index,
#   3. pesto-pitch with numpy constrained so its deps can't bump it.
COPY deploy/hf/requirements-hf.txt /tmp/requirements-hf.txt
RUN pip install -r /tmp/requirements-hf.txt \
    && pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu \
    && printf 'numpy==2.0.2\n' > /tmp/np.txt \
    && pip install pesto-pitch -c /tmp/np.txt

# Fail the build now (not at first request) if the pesto backend cannot import.
RUN python -c "import pesto, torch; print('pesto ok, torch', torch.__version__)"

# Run as the non-root user HF provides, with all caches under a writable HOME.
RUN useradd -m -u 1000 user
ENV HOME=/home/user \
    XDG_CACHE_HOME=/home/user/.cache \
    NUMBA_CACHE_DIR=/home/user/.cache/numba \
    MPLCONFIGDIR=/home/user/.cache/mpl
WORKDIR /home/user/app
COPY --chown=user:user . /home/user/app
USER user

EXPOSE 7860
CMD ["python", "-m", "uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "7860"]
