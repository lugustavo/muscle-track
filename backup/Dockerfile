# Backup service: pg_dump (same major version as the database) + the media folder, with retention and an optional
# off-server copy through rclone. See backup.sh and the "Backup" section of the README.
FROM postgres:16-alpine
RUN apk add --no-cache rclone tzdata
COPY backup.sh /usr/local/bin/backup.sh
RUN chmod +x /usr/local/bin/backup.sh
ENTRYPOINT ["/usr/local/bin/backup.sh"]
CMD ["loop"]
