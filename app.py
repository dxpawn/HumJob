"""Entry point for hosting HumJob on a Hugging Face Space (Gradio SDK, free CPU).

The free tier does not allow the Docker SDK, but the Gradio SDK runs on the same
free 16 GB CPU box and simply runs `python app.py`. We do not use a Gradio UI: we
start uvicorn on the existing FastAPI app (server/app.py) so the full site is served
at the root, exactly as it is locally. The Gradio SDK is only the free runtime that
provides the machine and installs requirements.txt + packages.txt.

Locally this also works as a plain (no-reload) launcher: `python app.py`.
"""

import os

import uvicorn

from server.app import app

if __name__ == "__main__":
    # HF Spaces expect the app on 7860; honour $PORT if the platform sets one.
    port = int(os.environ.get("PORT", "7860"))
    uvicorn.run(app, host="0.0.0.0", port=port)
