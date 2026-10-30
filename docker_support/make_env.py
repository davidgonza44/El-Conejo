"""Generate ONLY new disposable/demo environment files, without reading .env."""
import argparse
import base64
import os
from pathlib import Path
import secrets
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("disposable", "demo"))
    args = parser.parse_args()
    demo = args.kind == "demo"
    path = Path(".env.demo" if demo else ".env.docker")
    identity = uuid.uuid4().hex
    values = {
        "COMPOSE_PROJECT_NAME": "conejo-" + args.kind + "-" + identity[:8],
        "DOCKER_ENV_KIND": args.kind, "ENVIRONMENT_ID": identity,
        "APP_PORT": "5001" if demo else "5000", "DB_HOST": "db", "DB_PORT": "3306",
        "DB_NAME": "conejo_" + args.kind + "_" + identity[:8], "DB_USER": "conejo_app",
        "DB_PASSWORD": secrets.token_hex(32), "MARIADB_ROOT_PASSWORD": secrets.token_hex(32),
        "SECRET_KEY": secrets.token_hex(32),
        "SECRETS_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        "ADMIN_USERNAME": "admin_" + identity[:8], "ADMIN_EMAIL": "admin_" + identity[:8] + "@example.invalid",
        "ADMIN_PASSWORD": "Aa1!" + secrets.token_urlsafe(24), "DEMO_MODE": "true" if demo else "false",
    }
    if demo:
        values.update(DOCKER_APP_ENV="demo", DEMO_SEED_VERSION="2",
                      DEMO_VOLUME_SUFFIX="_seed2", DOCKER_IMAGE_TAG="demo-seed2")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        stream.write("# Generated private environment. Never commit or copy into an image.\n")
        stream.write("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
    print(f"Created {path}; credentials remain in that file. Existing files are never overwritten.")


if __name__ == "__main__":
    main()
