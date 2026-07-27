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

FROM public.ecr.aws/lambda/python:3.12 AS base

# ffmpeg: the generator container needs it for compositing, and the mock provider
# needs it to synthesise media. Absent it, mock modalities are skipped rather than
# failing — see adapters/mock_media.py.
RUN dnf install -y tar xz findutils && \
    ARCH=$(uname -m) && \
    curl -fsSL "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-${ARCH}-static.tar.xz" -o /tmp/ffmpeg.tar.xz && \
    tar -xJf /tmp/ffmpeg.tar.xz -C /tmp && \
    find /tmp -name ffmpeg -type f -exec cp {} /usr/local/bin/ffmpeg \; && \
    find /tmp -name ffprobe -type f -exec cp {} /usr/local/bin/ffprobe \; && \
    chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe && \
    rm -rf /tmp/ffmpeg* && dnf clean all

WORKDIR /app
COPY pyproject.toml ./
COPY remixkit ./remixkit
COPY worker.py ./

RUN pip install --no-cache-dir ".[b2,queue]"

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
