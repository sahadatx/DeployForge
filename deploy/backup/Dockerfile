FROM postgres:16-alpine

RUN apk add --no-cache age aws-cli bash coreutils python3
COPY scripts/backup-postgres-offsite.sh /usr/local/bin/deployforge-backup
COPY scripts/lib/postgres-url-to-env.py /usr/local/bin/lib/postgres-url-to-env.py
RUN chmod 0555 /usr/local/bin/deployforge-backup

USER 70:70
ENTRYPOINT ["/usr/local/bin/deployforge-backup"]
