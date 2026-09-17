# Security

LocalCam is intended for trusted home or small-office LANs.

- Keep `config.json` private.
- Use a strong LocalCam web password.
- Do not expose port 8765 directly to the internet.
- Use a VPN for remote access instead of port forwarding whenever possible.
- Camera RTSP passwords are stored only in the local configuration file and are never required in the Git repository.
- The web application uses an HttpOnly session cookie and PBKDF2-SHA256 password hashes.
- Keep the Windows network profile Private when using the built-in LAN firewall rule.
