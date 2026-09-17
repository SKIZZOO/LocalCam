# Security

LocalCam is designed for a trusted private LAN.

## Protections

- PBKDF2-SHA256 password hashing
- HttpOnly + SameSite session cookie
- Login attempt throttling
- Role-based access control (viewer, operator, admin)
- Same-origin checks for mutating web requests
- Path traversal protection for recordings and snapshots
- Backup archive validation
- Secrets excluded from Git by default

## Important limitations

The web server uses plain HTTP by default. Do not expose the LocalCam port directly to the public internet. For remote access, use a VPN or a properly configured reverse proxy with TLS.

Backup archives can contain camera credentials and the event database. Store backups securely.

RTSP and ONVIF credentials are stored locally because the application needs them to connect to the cameras. Never publish `config.json`.
