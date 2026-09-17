@echo off
netsh advfirewall firewall add rule name="LocalCam 8765 LAN" dir=in action=allow protocol=TCP localport=8765 profile=private
pause
