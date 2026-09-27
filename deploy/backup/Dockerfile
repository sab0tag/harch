# syntax=docker/dockerfile:1
# Backup container (compose.prod.yml, docs/06_deploy.md): pg_dump/psql from the matching
# Postgres image, rclone for off-site copies, supercronic for the schedule. No apt needed.
FROM postgres:16

COPY --from=rclone/rclone:1 /usr/local/bin/rclone /usr/local/bin/rclone

# Same pinned supercronic as the app image (Dockerfile).
ARG TARGETARCH
ARG SUPERCRONIC_VERSION=v0.2.33
ADD --chmod=755 https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH} /usr/local/bin/supercronic
RUN case "${TARGETARCH}" in \
      amd64) sum=71b0d58cc53f6bd72cf2f293e09e294b79c666d8 ;; \
      arm64) sum=e0f0c06ebc5627e43b25475711e694450489ab00 ;; \
      *) echo "unsupported arch: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && echo "${sum}  /usr/local/bin/supercronic" | sha1sum -c -

COPY --chmod=755 backup.sh restore.sh /usr/local/bin/
COPY crontab /etc/backup.crontab

ENTRYPOINT []
# Absolute path: as PID 1 supercronic re-execs itself via argv[0] to reap children (see #10).
CMD ["/usr/local/bin/supercronic", "/etc/backup.crontab"]
