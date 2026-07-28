# One image, two entrypoints.
#
# The API and the generator run the same code — the only difference is what starts.
# That is deliberate: a worker built from a different image than the API is a worker
# that drifts from it, and the thing it runs (KitService.run) is the same call the
# inline queue makes in dev.
#
#   API     → Lambda, via the Lambda Web Adapter extension (below)
#   Worker  → AWS Batch on Fargate Spot, `python worker.py --kit-id …`
#
# The Web Adapter is what lets an ordinary uvicorn process serve a Lambda Function URL
# without the app knowing it is on Lambda. No Mangum, no handler shim, and the same
# image runs locally with `docker run -p 8000:8000`.

# ffmpeg, copied from a pinned multi-arch static image rather than downloaded.
#
# The generator container needs it for compositing, and the mock provider needs it to
# synthesise media (absent it, modalities are skipped rather than faked — see
# adapters/mock_media.py).
#
# The first attempt fetched johnvansickle's static tarball and 404'd: those filenames
# use `amd64`/`arm64`, while `uname -m` reports `x86_64`/`aarch64`. Rather than carry
# an arch-translation table, copy the binaries out of an image that already publishes
# both architectures — no network fetch in the build, no arch mapping, and the version
# is pinned instead of tracking whatever "release" currently points at.
FROM mwader/static-ffmpeg:7.1 AS ffmpeg

FROM public.ecr.aws/lambda/python:3.12 AS base

COPY --from=ffmpeg /ffmpeg /usr/local/bin/ffmpeg
COPY --from=ffmpeg /ffprobe /usr/local/bin/ffprobe

WORKDIR /app
COPY pyproject.toml ./
COPY remixkit ./remixkit
COPY worker.py ./

# `audio` goes in the shared base rather than only in the worker, even though only the
# worker measures anything. The API process checks whether an analyser exists so it can
# refuse an upload's measurement up front with the reason named, instead of queueing a job
# that fails in a container nobody is watching — and one image with two entrypoints is the
# premise of this file, so the two cannot answer that question differently.
RUN pip install --no-cache-dir ".[b2,queue,audio]"

# ---------------------------------------------------------------- api
FROM base AS api
# The adapter runs as a Lambda extension and proxies the Function URL to a local port.
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter
ENV AWS_LWA_PORT=8000 \
    AWS_LWA_READINESS_CHECK_PATH=/healthz \
    RK_ENV=prod \
    RK_STORAGE_BACKEND=b2 \
    RK_GENERATOR_BACKEND=genblaze \
    RK_QUEUE_BACKEND=sqs
ENTRYPOINT []
CMD ["uvicorn", "remixkit.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ---------------------------------------------------------------- worker
FROM base AS worker
ENV RK_ENV=prod \
    RK_STORAGE_BACKEND=b2 \
    RK_GENERATOR_BACKEND=genblaze \
    RK_QUEUE_BACKEND=sqs
ENTRYPOINT ["python", "worker.py"]
CMD ["--poll"]
