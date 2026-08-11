# The genre classifier, as a Lambda container image.
#
# x86_64, unlike the console next door, which is arm64 for the ~20% Graviton discount.
# That is not an inconsistency to tidy up: `essentia-tensorflow` publishes manylinux
# wheels for x86_64 only, and there is no aarch64 build to switch to. Forcing arm64 here
# would mean compiling Essentia and TensorFlow from source in a Dockerfile, which is
# hours of build for a function that runs seconds per month. Two functions, two
# architectures, one reason each.
#
# The AWS base image is used rather than python:3.11-slim plus `awslambdaric`, because it
# already carries the Runtime Interface Client and the Runtime Interface Emulator — the
# emulator is what lets `tests/validate.sh` invoke this image locally over HTTP exactly
# the way Lambda will, instead of testing a code path that only resembles production.
FROM public.ecr.aws/lambda/python:3.11

# ffmpeg is NOT installed here, and that is deliberate. Essentia links its own decoders
# through libavcodec, so MonoLoader opens MP3, WAV, FLAC and AIFF without a subprocess.
# The worker needs ffmpeg on PATH for tempo; this image does not.

COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# Baked into the image rather than fetched at cold start. 20MB of weights downloaded on
# every cold start would add latency to exactly the invocations that are already slowest,
# and would make the function depend on essentia.upf.edu being up at runtime — a
# third-party host that has no obligation to this deployment. Vendored, the image is
# reproducible and the only network call at runtime is the S3 download.
#
# Run ./fetch_models.sh first; the files are gitignored because 20MB of weights do not
# belong in git history.
COPY models/ /opt/models/

COPY handler.py ${LAMBDA_TASK_ROOT}/

CMD ["handler.handler"]
