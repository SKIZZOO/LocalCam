# Backup and restore

Administrators can create a backup from **Settings → Backup**.

The archive contains:

- `config.json`
- `localcam.sqlite3`
- backup metadata

Camera credentials are included because the restored system must be able to reconnect to the cameras. Keep backup files private.

## Restore

1. Choose a LocalCam backup archive.
2. Confirm the restore operation.
3. LocalCam validates the ZIP structure and rejects unexpected files.
4. The local configuration and event database are replaced.
5. Active sessions are invalidated and camera streams are rebuilt.

Do not commit backups to Git or upload them to public issue trackers.
