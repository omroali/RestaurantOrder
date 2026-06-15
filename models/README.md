# Whisper Models

Models are downloaded from HuggingFace automatically when the container first
launches Whisper.  To pre-download for offline use or to persist across
container restarts, run the download script on your **host**:

```bash
# Default: small + base
./download_models.sh

# Specific models
./download_models.sh tiny base
```

Files land in `models/huggingface/` and are mounted into the container by
`run_robot_docker.sh` automatically.
