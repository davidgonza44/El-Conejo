# Reuse the installed, immutable dependency runtime; never retag its image.
FROM conejo-portable:manual-cuts-0007-20261010
USER root
COPY --chown=10001:10001 app/ /app/app/
COPY --chown=10001:10001 docker_support/ /app/docker_support/
COPY --chown=10001:10001 scripts/migrate_real_2026_history.py /app/scripts/migrate_real_2026_history.py
USER 10001:10001
CMD ["python", "-B", "-m", "docker_support.real2026_lab", "start"]
