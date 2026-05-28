@echo off
netsh interface ipv4 set address name="以太网" static 192.168.2.40 255.255.255.0
echo.
echo IP changed to 192.168.2.40
pause
