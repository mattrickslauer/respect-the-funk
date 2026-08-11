# The genre classifier

A container-image Lambda that turns a master into Discogs genre labels, so counterparty
discovery has something to search playlists for.

    ./fetch_models.sh     # 20MB of weights, gitignored
    ./fetch_refs.sh       # six reference previews + two synthetic fixtures, gitignored
    ./tests/validate.sh   # build the image and prove it still recognises six records
    ./push.sh             # validate, push to ECR, print the digest URI for Terraform

## Why it exists

`sources.deezer.discover_counterparties` searches playlists by *style term*. Hallow
Youth has two — `Dance` and `Electro`, both inferred from Deezer's top-level labels —
and Deezer's `related` endpoint returns an empty array because the artist has 0 fans.
Discovery is cold-start blocked, and since `77722c6` it refuses rather than searching by
name. The record has to be listened to. That is the whole reason this exists.

## Why the reference tracks are the point

An earlier attempt computed the mel spectrogram in numpy and fed the effnet ONNX export
— no TensorFlow, no container, testable on a laptop. It failed:

| Track | Expected | numpy front end |
|---|---|---|
| Daft Punk — Around The World | house | `Electronic---New Beat` 0.267 ✅ |
| Metallica — Master of Puppets | metal | `Electronic---Hardcore` 0.129 ❌ |
| John Coltrane — Giant Steps | jazz | `Electronic---Speedcore` 0.147 ❌ |
| Bob Marley — Jamming | reggae | `Electronic---Electro` 0.130 ❌ |

Everything collapsed to Electronic, so the one correct answer was a coincidence — and it
emitted confident labels the whole way. With Essentia's own preprocessing, the same
weights give:

| Track | Top prediction | p |
|---|---|---|
| Daft Punk — Around The World | `Electronic---House` | 0.433 |
| Metallica — Master of Puppets | `Rock---Heavy Metal` | 0.844 |
| John Coltrane — Giant Steps | `Jazz---Hard Bop` | 0.621 |
| Bob Marley — Jamming | `Reggae---Roots Reggae` | 0.790 |
| Dr. Dre — Still D.R.E. | `Hip Hop---Horrorcore` | 0.215 |
| Johnny Cash — Folsom Prison Blues | `Folk, World, & Country---Country` | 0.609 |

Six parent genres, six correct. The model was never the problem; the preprocessing was.

The Dr. Dre row is why `handler.STYLE_FLOOR` exists: the parent is right and the style
is wrong, at low confidence. Below the floor only the parent is reported, which is the
true subset of what the model knows.

## Constraints worth knowing before changing anything

* **x86_64, not arm64.** `essentia-tensorflow` ships manylinux wheels for x86_64 only.
  The console next door is arm64 for the Graviton discount; this cannot be.
* **Python 3.11, not newer.** Same package.
* **numpy is pinned to 1.26.4** because the AWS Lambda base image is Amazon Linux 2 with
  glibc 2.26, and numpy ≥2.1 ships manylinux_2_28 wheels only. Resolving unpinned on a
  Debian container gives 2.4.6 and works; the same file fails on the target image with
  `Unknown compiler(s)`. Validate in the runtime image, not a convenient one.
* **ffmpeg is not in the image.** Essentia links its own decoders. The *worker* needs
  ffmpeg for tempo; this does not.
* The image is ~1.5GB against Lambda's 10GB limit, so there is room, but ECR storage is
  $0.10/GB/month and the lifecycle policy keeps only the last ten images.
