# B-roll — where these six files come from

Nothing in this directory is committed, and nothing here was made for the video. Each
file is a **byte-identical copy of an asset a real run already wrote**, which is why the
bytes live in object storage and this descriptor lives in git.

Restore them with:

```bash
cp app/.remixkit-data/remixkit/runs/<path> content/demo/broll/
```

…or, if the local mirror is gone, `python app/remixkit/tools/sync_from_b2.py` first —
these are the same keys in the production bucket, under the same run prefixes.

| File | Kind | Run |
|---|---|---|
| `c0b5c9af-db0b-493e-9580-8519985420d6.mp4` | clip, 720×1280, 8s | `runs/dev-local/2026-08-01/25432a1c-8ea9-4e1f-93b5-5ca4332e8690/assets/` |
| `8888da68-432c-4eb6-8b06-f3440b11c3cd.mp4` | clip, 720×1280, 8s | `runs/dev-local/2026-08-01/25432a1c-8ea9-4e1f-93b5-5ca4332e8690/assets/` |
| `2dfa811e-607f-48ff-af71-b6e6306cfb99.mp4` | clip, 720×1280, 8s | `runs/dev-local/2026-08-01/25432a1c-8ea9-4e1f-93b5-5ca4332e8690/assets/` |
| `82745a41-1479-4f79-b62a-8139f22a14dc.mp4` | clip, 720×1280, 8s | `runs/dev-local/2026-08-03/27d8a299-d2c0-4f4f-a0ac-53ca44d8a311/assets/` |
| `9d17650d-3779-453b-93a6-33b2bbe5d5bb.png` | still, 880×1168 | `runs/dev-local/2026-08-01/6c8b19c1-0903-4c3f-8d7d-dbc966d2cb65/assets/` |
| `dc07b58f-f663-4d8c-b09d-6581a7c67c87.png` | still, 880×1168 | `runs/dev-local/2026-08-02/d3afdc28-d64d-4503-8685-4330684271df/assets/` |

Each run directory also holds the `manifest.json` naming the provider, model and prompt
behind every step — which is the thing the video's closing beat is about. The clips are
`sora-2`; the stills are `bria-fibo-edit` via GMI Cloud. Do not re-generate a
replacement and cut it in as if it were one of these: the manifest is what makes the
claim on screen true, and a new file has a different one.
