@echo off
netsh advfirewall firewall add rule name="LocalCam Web 8765" dir=in action=allow protocol=TCP localport=8765 profile=private
pause
