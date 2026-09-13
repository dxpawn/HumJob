# Deploying HumJob to Hugging Face Spaces (free Gradio SDK)

This puts the full app online at a public URL like
`https://<your-username>-humjob.hf.space`, over HTTPS (so a phone can use the mic),
on Hugging Face's free CPU tier with the pesto backend.

The free tier does not allow the Docker SDK, so we use the **Gradio SDK** as the
runtime: it runs `python app.py`, which starts the real FastAPI app on port 7860
(no Gradio UI is used). A verified `Dockerfile` is also in the repo as a paid-Docker
alternative; see the note at the end.

Files this uses (already in the repo):
- `app.py` at the root - launcher that runs uvicorn on `server/app.py`.
- `packages.txt` - apt packages (ffmpeg, libsndfile1) HF installs for you.
- `deploy/hf/requirements-hf.txt` - the Space's pip deps (CPU torch + pesto + runtime).
- `deploy/hf/README.md` - the Space's README, carrying the HF config (`sdk: gradio`).

The push uses a throwaway `hf-space` branch, so your GitHub `main` keeps its own
`README.md` and `requirements.txt` (the Space needs different ones).

## 1. Create the Space

1. Go to https://huggingface.co/new-space (sign in / make a free account first).
2. Owner = your username. Space name = `humjob`.
3. SDK = **Gradio**, template = **Blank**. Visibility = **Public**.
4. Create it. You now have an empty Space at
   `https://huggingface.co/spaces/<your-username>/humjob`.

## 2. Add your DeepSeek key (for the coaching button)

In the Space: **Settings -> Variables and secrets -> New secret**
- Name: `DEEPSEEK_API_KEY`
- Value: your DeepSeek key

Skip this to leave coaching off; everything else still works.

## 3. Get a Hugging Face access token (used as your git password)

Profile -> **Settings -> Access Tokens -> Create new token**, type **Write**. Copy it.
When `git push` asks for a password, paste this token (not your account password).

## 4. Push the app to the Space (PowerShell, from the project folder)

```powershell
# One-time: commit the deploy files to main (your GitHub main README/requirements stay clean).
git add -A
git commit -m "Add Hugging Face Space (Gradio SDK) deploy config"

# Create the deploy branch and put the Space's README + requirements at the root.
git checkout -b hf-space
Copy-Item deploy\hf\README.md README.md -Force
Copy-Item deploy\hf\requirements-hf.txt requirements.txt -Force
git commit -am "HF Space README + requirements"

# Point at your Space and push this branch to the Space's main.
git remote add hf https://huggingface.co/spaces/<your-username>/humjob
git push hf hf-space:main --force

# Back to your normal branch.
git checkout main
```

Notes:
- Username = your HF username; password = the **Write token** from step 3. If a browser
  or credential popup appears instead, sign in there.
- `--force` is expected and safe: it overwrites the empty template the Space was created
  with.

To redeploy after later changes: commit them to `main`, then run the one-command helper:

```powershell
./deploy/hf/redeploy.ps1
```

It rebuilds the `hf-space` branch from `main` and force-pushes to the Space (the `hf`
remote and Space secret from the first setup are reused; nothing else to redo). The
equivalent manual steps, if you prefer:

```powershell
git branch -D hf-space
git checkout -b hf-space
Copy-Item deploy\hf\README.md README.md -Force
Copy-Item deploy\hf\requirements-hf.txt requirements.txt -Force
git add -A
git commit -m "deploy update"
git push hf hf-space:main --force
git checkout main
```

## 5. Watch it build

Open the Space page. It shows **Building** for a few minutes (first build installs CPU
torch and the rest). When it flips to **Running**, open the URL on your phone, go to the
Realtime tab -> Range, and allow the mic.

## Notes and gotchas

- **Cold starts / sleep.** Free Spaces sleep after inactivity and cold-start on the next
  visit. Open it a few minutes before a live demo.
- **First hum is slower.** librosa/numba JIT-compile and pesto loads its model on first use.
- **It is public and unauthenticated.** Anyone with the link can use it, including the
  coaching endpoint (spends your DeepSeek credits) and the CPU-heavy transcribe endpoint.
- **Only pesto ships.** Selecting another backend in the dropdown would error. To add one,
  add its install lines (from `requirements.txt`) to `deploy/hf/requirements-hf.txt`.
- **Never commit `.env`.** It is gitignored; the key belongs only in the Space secret.

## If the build fails

- **pip dependency conflict with gradio** (the SDK preinstalls gradio, which also depends on
  fastapi/uvicorn): if the resolver complains, loosen the pins in
  `deploy/hf/requirements-hf.txt` by removing the `==` on `fastapi` and `uvicorn` and rebuild.
- **The Space wants a Gradio object:** the Gradio SDK runs `python app.py`; our `app.py`
  serves the FastAPI app on 7860, which the platform proxies. If a build ever refuses to run
  without a Gradio interface, tell me and I will wrap the app under a one-line Gradio mount.

## Paid alternative: Docker SDK

If you upgrade the Space to allow the Docker SDK, the repo's `Dockerfile` (verified to build
and run) is the cleaner path: create a Docker Space instead, and the same push works (the
Docker SDK reads the `Dockerfile`, ignoring `app.py`/`packages.txt`).
